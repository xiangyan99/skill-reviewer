from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import yaml

from skill_reviewer.azure_client import build_openai_client
from skill_reviewer.config import ReviewerConfig
from skill_reviewer.loader import load_skill_package
from skill_reviewer.models import (
    AggregateReport,
    CaseGrade,
    CaseResult,
    GeneratedCaseSet,
    ReviewCase,
    ReviewReport,
    SkillPackage,
    SkillProfile,
    SkillStaticReview,
)
from skill_reviewer.prompts import (
    case_generation_messages,
    executor_instructions,
    grade_messages,
    profile_messages,
    static_review_messages,
)


def _dedupe_preserve_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        normalized = value.strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        result.append(normalized)
    return result


def _average(values: list[float]) -> float:
    return round(sum(values) / len(values), 2) if values else 0.0


def _render_markdown(report: ReviewReport) -> str:
    lines = [
        "# Skill Review Report",
        "",
        f"- Run ID: `{report.run_id}`",
        f"- Skill: `{report.skill_path}`",
        f"- Final verdict: `{report.aggregate.final_verdict}`",
        f"- Static score: `{report.aggregate.static_score:.2f}`",
        f"- Average case score: `{report.aggregate.average_case_score:.2f}`",
        f"- Cases: `{report.aggregate.passes}` pass / `{report.aggregate.warnings}` warning / `{report.aggregate.failures}` fail",
        "",
    ]

    if report.aggregate.final_verdict != "approve":
        lines.extend(
            [
                f"### Why `{report.aggregate.final_verdict}`",
                "",
            ]
        )
        for reason in report.aggregate.verdict_reasons:
            lines.append(f"- {reason}")
        lines.append("")

        if report.aggregate.action_items:
            lines.extend(["### Action Items", ""])
            for i, item in enumerate(report.aggregate.action_items, 1):
                lines.append(f"{i}. {item}")
            lines.append("")

    lines.extend(
        [
            "## Skill Profile",
            "",
            f"- Title: {report.profile.title}",
            f"- Summary: {report.profile.summary}",
            f"- Services: {', '.join(report.profile.claimed_services) or 'n/a'}",
            f"- SDKs: {', '.join(report.profile.claimed_sdks) or 'n/a'}",
            f"- Languages: {', '.join(report.profile.programming_languages) or 'n/a'}",
            "",
            "## Static Review",
            "",
            f"- Verdict: `{report.static_review.verdict}`",
            f"- Summary: {report.static_review.summary}",
            f"- Scores: correctness={report.static_review.scores.technical_correctness}, completeness={report.static_review.scores.completeness}, safety={report.static_review.scores.safety}, clarity={report.static_review.scores.clarity}, actionability={report.static_review.scores.actionability}",
            "",
            "### Findings",
            "",
        ]
    )

    if report.static_review.findings:
        for finding in report.static_review.findings:
            lines.extend(
                [
                    f"- [{finding.severity}] {finding.category}: {finding.problem}",
                    f"  - Why it matters: {finding.why_it_matters}",
                    f"  - Suggested fix: {finding.suggested_fix}",
                ]
            )
    else:
        lines.append("- No static findings.")

    lines.extend(["", "## Case Results", ""])
    for result in report.case_results:
        lines.extend(
            [
                f"### {result.case.case_id} - {result.case.name}",
                "",
                f"- Verdict: `{result.grade.verdict}`",
                f"- Summary: {result.grade.summary}",
                f"- Scores: correctness={result.grade.scores.technical_correctness}, completeness={result.grade.scores.completeness}, safety={result.grade.scores.safety}, clarity={result.grade.scores.clarity}, actionability={result.grade.scores.actionability}",
                f"- Prompt: {result.case.user_prompt}",
                f"- Issues: {'; '.join(result.grade.issues) or 'none'}",
                f"- Recommended edits: {'; '.join(result.grade.recommended_edits) or 'none'}",
                "",
            ]
        )

    lines.extend(["## Recommendations", ""])
    if report.aggregate.top_recommendations:
        for item in report.aggregate.top_recommendations:
            lines.append(f"- {item}")
    else:
        lines.append("- No additional recommendations.")

    return "\n".join(lines).strip() + "\n"


class HarnessSkillReviewer:
    def __init__(self, config: ReviewerConfig):
        self.config = config
        self.client = build_openai_client(config)

    def _parse_structured(self, model: str, messages: list[dict[str, str]], schema):
        completion = self.client.beta.chat.completions.parse(
            model=model,
            messages=messages,
            temperature=0,
            response_format=schema,
        )
        message = completion.choices[0].message
        if getattr(message, "parsed", None) is None:
            raise RuntimeError("Structured parse failed.")
        return message.parsed

    def _run_case(self, skill: SkillPackage, case: ReviewCase) -> str:
        response = self.client.responses.create(
            model=self.config.review_model,
            instructions=executor_instructions(skill),
            input=case.user_prompt,
            temperature=0,
        )
        answer = (response.output_text or "").strip()
        if not answer:
            raise RuntimeError(f"Empty answer for case {case.case_id}")
        return answer

    def _load_dataset(self, dataset_path: Path) -> list[ReviewCase]:
        payload = yaml.safe_load(dataset_path.read_text(encoding="utf-8"))
        case_set = GeneratedCaseSet.model_validate(payload)
        return case_set.cases

    def _aggregate(
        self,
        static_review: SkillStaticReview,
        case_results: list[CaseResult],
    ) -> AggregateReport:
        case_scores = [result.grade.scores.average for result in case_results]
        passes = sum(1 for result in case_results if result.grade.verdict == "pass")
        warnings = sum(1 for result in case_results if result.grade.verdict == "warning")
        failures = sum(1 for result in case_results if result.grade.verdict == "fail")
        high_findings = [item for item in static_review.findings if item.severity == "high"]

        min_case_safety = min(
            (result.grade.scores.safety for result in case_results),
            default=5,
        )
        static_safety = static_review.scores.safety
        safety_floor = min(static_safety, min_case_safety)

        verdict_reasons: list[str] = []
        action_items: list[str] = []

        # Collect all triggered rules
        if failures >= 2:
            verdict_reasons.append(f"{failures} case failures (threshold: 2)")
            failed_names = [r.case.name for r in case_results if r.grade.verdict == "fail"]
            action_items.append(f"Fix failing cases: {', '.join(failed_names)}")
        if len(high_findings) >= 2:
            verdict_reasons.append(f"{len(high_findings)} high-severity static findings (threshold: 2)")
            for f in high_findings:
                action_items.append(f"[high] {f.category}: {f.suggested_fix}")
        if static_review.scores.average < 2.8:
            verdict_reasons.append(f"Static score {static_review.scores.average:.2f} below minimum 2.80")
            action_items.append("Improve skill quality across all static review dimensions")
        if safety_floor <= 2:
            unsafe_cases = [
                r.case.name for r in case_results if r.grade.scores.safety <= 2
            ]
            sources = []
            if static_safety <= 2:
                sources.append(f"static review safety={static_safety}")
            if unsafe_cases:
                sources.append(f"case(s): {', '.join(unsafe_cases)}")
            verdict_reasons.append(f"Safety floor {safety_floor} (from {'; '.join(sources)})")
            action_items.append("Address safety issues — add guidance to resist unsafe requests and avoid insecure patterns")

        if failures == 1:
            verdict_reasons.append(f"1 case failure")
            failed_names = [r.case.name for r in case_results if r.grade.verdict == "fail"]
            action_items.append(f"Fix failing case: {failed_names[0]}")
        if high_findings and len(high_findings) < 2:
            verdict_reasons.append(f"{len(high_findings)} high-severity static finding(s)")
            for f in high_findings:
                action_items.append(f"[high] {f.category}: {f.suggested_fix}")
        if static_review.verdict != "approve":
            verdict_reasons.append(f"Static review verdict is `{static_review.verdict}`")
            if not any("static" in a.lower() for a in action_items):
                action_items.append("Address static review findings to reach approve")
        if _average(case_scores) < 4.0:
            verdict_reasons.append(f"Average case score {_average(case_scores):.2f} below 4.00")
            action_items.append("Improve skill content to raise case execution quality")
        if safety_floor == 3:
            unsafe_cases = [
                r.case.name for r in case_results if r.grade.scores.safety == 3
            ]
            sources = []
            if static_safety == 3:
                sources.append(f"static review safety={static_safety}")
            if unsafe_cases:
                sources.append(f"case(s): {', '.join(unsafe_cases)}")
            verdict_reasons.append(f"Safety floor {safety_floor} (from {'; '.join(sources)})")
            action_items.append("Strengthen safety guidance to improve safety scores above 3")

        # Determine final verdict from rules
        if (
            failures >= 2
            or len(high_findings) >= 2
            or static_review.scores.average < 2.8
            or safety_floor <= 2
        ):
            final_verdict = "reject"
        elif (
            failures >= 1
            or high_findings
            or static_review.verdict != "approve"
            or _average(case_scores) < 4.0
            or safety_floor <= 3
        ):
            final_verdict = "needs_revision"
        else:
            final_verdict = "approve"

        # Dedupe action items
        action_items = _dedupe_preserve_order(action_items)

        top_issues = _dedupe_preserve_order(
            [finding.problem for finding in static_review.findings]
            + [issue for result in case_results for issue in result.grade.issues]
        )[:8]
        top_recommendations = _dedupe_preserve_order(
            static_review.improvement_ideas
            + [edit for result in case_results for edit in result.grade.recommended_edits]
        )[:8]

        return AggregateReport(
            final_verdict=final_verdict,
            verdict_reasons=verdict_reasons,
            action_items=action_items,
            average_case_score=_average(case_scores),
            static_score=round(static_review.scores.average, 2),
            passes=passes,
            warnings=warnings,
            failures=failures,
            top_issues=top_issues,
            top_recommendations=top_recommendations,
        )

    def _write_artifacts(self, report: ReviewReport) -> Path:
        run_dir = self.config.output_dir / report.run_id
        run_dir.mkdir(parents=True, exist_ok=True)

        json_path = run_dir / "report.json"
        md_path = run_dir / "report.md"

        json_path.write_text(
            json.dumps(report.model_dump(mode="json"), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        md_path.write_text(_render_markdown(report), encoding="utf-8")
        return run_dir

    def review(self, skill_path: str | Path, dataset_path: str | Path | None = None) -> tuple[ReviewReport, Path]:
        skill = load_skill_package(
            skill_path,
            max_reference_files=self.config.max_reference_files,
        )
        profile = self._parse_structured(
            self.config.judge_model,
            profile_messages(skill, self.config.language),
            SkillProfile,
        )
        static_review = self._parse_structured(
            self.config.judge_model,
            static_review_messages(skill, profile, self.config.language),
            SkillStaticReview,
        )

        if dataset_path:
            cases = self._load_dataset(Path(dataset_path).resolve())
        else:
            generated = self._parse_structured(
                self.config.judge_model,
                case_generation_messages(
                    skill,
                    profile,
                    self.config.language,
                    self.config.max_generated_cases,
                ),
                GeneratedCaseSet,
            )
            cases = generated.cases

        case_results: list[CaseResult] = []
        for case in cases:
            assistant_answer = self._run_case(skill, case)
            grade = self._parse_structured(
                self.config.judge_model,
                grade_messages(skill, case, assistant_answer, self.config.language),
                CaseGrade,
            )
            case_results.append(
                CaseResult(
                    case=case,
                    assistant_answer=assistant_answer,
                    grade=grade,
                )
            )

        report = ReviewReport(
            run_id=datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S"),
            generated_at=datetime.now(timezone.utc),
            skill_path=str(Path(skill_path).resolve()),
            language=self.config.language,
            profile=profile,
            static_review=static_review,
            cases=cases,
            case_results=case_results,
            aggregate=self._aggregate(static_review, case_results),
        )
        artifact_dir = self._write_artifacts(report)
        return report, artifact_dir
