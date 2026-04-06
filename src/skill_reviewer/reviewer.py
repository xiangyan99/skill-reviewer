from __future__ import annotations

import hashlib
import json
import logging
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import yaml

from skill_reviewer.azure_client import build_openai_client
from skill_reviewer.config import ReviewerConfig
from skill_reviewer.loader import load_skill_package, preflight_check

logger = logging.getLogger(__name__)
from skill_reviewer.code_validator import validate_answer_code
from skill_reviewer.models import (
    AggregateReport,
    CaseGrade,
    CaseResult,
    CodeValidation,
    GeneratedCaseSet,
    MustCoverResult,
    RedFlagResult,
    ReviewCase,
    ReviewReport,
    RubricScores,
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


def _skill_fingerprint(content: str) -> str:
    """Stable SHA-256 fingerprint of skill content for cache keying."""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]


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
        f"- Fingerprint: `{report.skill_fingerprint}`",
        f"- Final verdict: `{report.aggregate.final_verdict}`",
        f"- Static score: `{report.aggregate.static_score:.2f}`",
        f"- Average case score: `{report.aggregate.average_case_score:.2f}`",
        f"- Cases: `{report.aggregate.passes}` pass / `{report.aggregate.warnings}` warning / `{report.aggregate.failures}` fail",
        f"- Must-cover: `{report.aggregate.must_cover_met}` / `{report.aggregate.must_cover_total}` met",
        f"- Red flags: `{report.aggregate.red_flags_triggered}` / `{report.aggregate.red_flags_total}` triggered",
        f"- Code blocks: `{report.aggregate.code_blocks_total}` found, `{report.aggregate.code_syntax_errors}` syntax errors, `{report.aggregate.code_security_issues}` security issues",
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
                f"- Category: `{result.case.category}` | Difficulty: `{result.case.difficulty}`",
                f"- Verdict: `{result.grade.verdict}`",
                f"- Summary: {result.grade.summary}",
                f"- Scores: correctness={result.grade.scores.technical_correctness}, completeness={result.grade.scores.completeness}, safety={result.grade.scores.safety}, clarity={result.grade.scores.clarity}, actionability={result.grade.scores.actionability}",
                f"- Prompt: {result.case.user_prompt}",
                "",
            ]
        )

        # Must-cover results
        if result.grade.must_cover_results:
            lines.append("**Must-cover checklist:**")
            lines.append("")
            for mc in result.grade.must_cover_results:
                icon = "PASS" if mc.met else "MISS"
                lines.append(f"- [{icon}] {mc.criterion}")
                if mc.evidence:
                    lines.append(f"  - Evidence: {mc.evidence}")
            lines.append("")

        # Red flag results
        if result.grade.red_flag_results:
            lines.append("**Red-flag checklist:**")
            lines.append("")
            for rf in result.grade.red_flag_results:
                icon = "TRIGGERED" if rf.triggered else "OK"
                lines.append(f"- [{icon}] {rf.flag}")
                if rf.evidence:
                    lines.append(f"  - Evidence: {rf.evidence}")
            lines.append("")

        # Code validation results
        if result.code_validation is not None:
            cv = result.code_validation
            lines.append("**Code validation:**")
            lines.append("")
            lines.append(
                f"- Blocks: {cv.blocks_found} | "
                f"Syntax: {'valid' if cv.syntax_valid else 'ERRORS'} | "
                f"Security: {'issues found' if cv.has_security_issues else 'clean'}"
            )
            if cv.blocks:
                langs = [b.language or "unknown" for b in cv.blocks]
                lines.append(f"- Languages: {', '.join(langs)}")
            if cv.issues:
                for issue in cv.issues:
                    lines.append(f"- [{issue.severity}] [{issue.category}] {issue.message}")
            lines.append("")

        lines.extend(
            [
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

    def _parse_structured(self, model: str, messages: list[dict[str, str]], schema, *, seed: int = 42):
        completion = self.client.beta.chat.completions.parse(
            model=model,
            messages=messages,
            temperature=0,
            seed=seed,
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

    def _case_cache_path(self, fingerprint: str) -> Path | None:
        cache_dir = self.config.case_cache_dir
        if cache_dir is None:
            return None
        return cache_dir / f"cases_{fingerprint}.yaml"

    def _load_cached_cases(self, fingerprint: str) -> list[ReviewCase] | None:
        path = self._case_cache_path(fingerprint)
        if path is None or not path.exists():
            return None
        try:
            payload = yaml.safe_load(path.read_text(encoding="utf-8"))
            case_set = GeneratedCaseSet.model_validate(payload)
            logger.info("Loaded %d cached cases from %s", len(case_set.cases), path)
            return case_set.cases
        except Exception:
            logger.warning("Failed to load case cache %s, regenerating", path)
            return None

    def _save_cached_cases(self, fingerprint: str, cases: list[ReviewCase]) -> None:
        path = self._case_cache_path(fingerprint)
        if path is None:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = GeneratedCaseSet(cases=cases).model_dump(mode="json")
        path.write_text(
            yaml.dump(payload, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )
        logger.info("Saved %d cases to cache %s", len(cases), path)

    def _grade_with_consensus(
        self,
        skill: SkillPackage,
        case: ReviewCase,
        assistant_answer: str,
        code_validation: CodeValidation | None = None,
    ) -> CaseGrade:
        """Run grading `grade_rounds` times and return the consensus result.

        When grade_rounds == 1, this is equivalent to a single grading call.
        When grade_rounds > 1, the verdict is decided by majority vote and
        scores are taken from the median-scoring round.
        """
        rounds = max(1, self.config.grade_rounds)
        grades: list[CaseGrade] = []
        for i in range(rounds):
            grade = self._parse_structured(
                self.config.judge_model,
                grade_messages(
                    skill, case, assistant_answer, self.config.language,
                    code_validation=code_validation,
                ),
                CaseGrade,
                seed=42 + i,
            )
            grades.append(grade)

        if rounds == 1:
            return grades[0]

        # Majority vote on verdict
        verdict_counts = Counter(g.verdict for g in grades)
        consensus_verdict = verdict_counts.most_common(1)[0][0]

        # Pick the grade whose scores are closest to the median
        median_avg = sorted(g.scores.average for g in grades)[rounds // 2]
        best = min(grades, key=lambda g: abs(g.scores.average - median_avg))

        # Override verdict with consensus if it differs
        if best.verdict != consensus_verdict:
            best = best.model_copy(update={"verdict": consensus_verdict})

        logger.info(
            "Case %s: %d rounds, verdicts=%s, consensus=%s",
            case.case_id,
            rounds,
            dict(verdict_counts),
            consensus_verdict,
        )
        return best

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

        # Surface missed must_cover items as issues
        missed_must_cover = []
        for result in case_results:
            for mc in result.grade.must_cover_results:
                if not mc.met:
                    missed_must_cover.append(
                        f"[{result.case.case_id}] Must-cover missed: {mc.criterion}"
                    )

        # Surface triggered red flags as issues
        triggered_red_flags = []
        for result in case_results:
            for rf in result.grade.red_flag_results:
                if rf.triggered:
                    triggered_red_flags.append(
                        f"[{result.case.case_id}] Red flag triggered: {rf.flag}"
                    )

        if missed_must_cover:
            verdict_reasons.append(
                f"{len(missed_must_cover)} must-cover criteria missed across cases"
            )
            action_items.extend(missed_must_cover[:5])

        if triggered_red_flags:
            verdict_reasons.append(
                f"{len(triggered_red_flags)} red flag(s) triggered across cases"
            )
            action_items.extend(triggered_red_flags[:5])

        top_issues = _dedupe_preserve_order(
            [finding.problem for finding in static_review.findings]
            + triggered_red_flags
            + missed_must_cover
            + [issue for result in case_results for issue in result.grade.issues]
        )[:10]
        top_recommendations = _dedupe_preserve_order(
            static_review.improvement_ideas
            + [edit for result in case_results for edit in result.grade.recommended_edits]
        )[:8]

        # Compute must-cover / red-flag aggregate stats
        mc_total = sum(
            len(r.grade.must_cover_results) for r in case_results
        )
        mc_met = sum(
            1 for r in case_results for mc in r.grade.must_cover_results if mc.met
        )
        rf_total = sum(
            len(r.grade.red_flag_results) for r in case_results
        )
        rf_triggered = sum(
            1 for r in case_results for rf in r.grade.red_flag_results if rf.triggered
        )

        # Compute code validation aggregate stats
        code_blocks_total = sum(
            r.code_validation.blocks_found
            for r in case_results if r.code_validation
        )
        code_syntax_errors = sum(
            1 for r in case_results
            if r.code_validation and not r.code_validation.syntax_valid
        )
        code_security_issues = sum(
            1 for r in case_results
            if r.code_validation and r.code_validation.has_security_issues
        )

        # Surface code validation problems
        if code_syntax_errors:
            syntax_cases = [
                r.case.name for r in case_results
                if r.code_validation and not r.code_validation.syntax_valid
            ]
            verdict_reasons.append(
                f"Code syntax errors in {code_syntax_errors} case(s): {', '.join(syntax_cases)}"
            )
            action_items.append("Fix code examples to eliminate syntax errors")

        if code_security_issues:
            sec_cases = [
                r.case.name for r in case_results
                if r.code_validation and r.code_validation.has_security_issues
            ]
            verdict_reasons.append(
                f"Code security issues in {code_security_issues} case(s): {', '.join(sec_cases)}"
            )
            action_items.append("Remove hardcoded secrets and insecure patterns from code examples in the skill")

        # Code validation can escalate verdict
        if code_security_issues >= 2 or (code_syntax_errors >= 2 and final_verdict == "approve"):
            final_verdict = max(
                final_verdict,
                "needs_revision",
                key=lambda v: {"approve": 0, "needs_revision": 1, "reject": 2}[v],
            )

        return AggregateReport(
            final_verdict=final_verdict,
            verdict_reasons=verdict_reasons,
            action_items=action_items,
            average_case_score=_average(case_scores),
            static_score=round(static_review.scores.average, 2),
            passes=passes,
            warnings=warnings,
            failures=failures,
            must_cover_total=mc_total,
            must_cover_met=mc_met,
            red_flags_total=rf_total,
            red_flags_triggered=rf_triggered,
            code_blocks_total=code_blocks_total,
            code_syntax_errors=code_syntax_errors,
            code_security_issues=code_security_issues,
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

    def _build_preflight_reject_report(
        self,
        skill_path: str | Path,
        skill: SkillPackage,
        reasons: list[str],
    ) -> ReviewReport:
        """Build a minimal ReviewReport for a pre-flight rejection (no LLM calls)."""
        zero_scores = RubricScores(
            technical_correctness=1,
            completeness=1,
            safety=1,
            clarity=1,
            actionability=1,
        )
        return ReviewReport(
            run_id=datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S"),
            generated_at=datetime.now(timezone.utc),
            skill_path=str(Path(skill_path).resolve()),
            language=self.config.language,
            profile=SkillProfile(
                title="(pre-flight rejected)",
                summary="Skill was rejected by static pre-flight checks before LLM review.",
            ),
            static_review=SkillStaticReview(
                verdict="reject",
                summary="Pre-flight static analysis detected critical issues. LLM review was skipped.",
                scores=zero_scores,
            ),
            cases=[],
            case_results=[],
            aggregate=AggregateReport(
                final_verdict="reject",
                verdict_reasons=[f"[pre-flight] {r}" for r in reasons],
                action_items=[
                    "Remove or fix all flagged content before resubmitting",
                    "Ensure the skill does not contain prompt injection, obfuscation, or instructions that manipulate the reviewer",
                ],
                average_case_score=0.0,
                static_score=1.0,
                passes=0,
                warnings=0,
                failures=0,
            ),
        )

    def review(self, skill_path: str | Path, dataset_path: str | Path | None = None) -> tuple[ReviewReport, Path]:
        skill = load_skill_package(
            skill_path,
            max_reference_files=self.config.max_reference_files,
        )
        fingerprint = _skill_fingerprint(skill.content)

        # Pre-flight static check: reject immediately without LLM calls
        print("[1/6] Running pre-flight security checks...")
        rejection = preflight_check(skill.content)
        if rejection:
            print(f"  REJECTED: {len(rejection.reasons)} critical issue(s) found")
            report = self._build_preflight_reject_report(
                skill_path, skill, rejection.reasons,
            )
            artifact_dir = self._write_artifacts(report)
            return report, artifact_dir
        print("  Passed")

        print("[2/6] Extracting skill profile...")
        profile = self._parse_structured(
            self.config.judge_model,
            profile_messages(skill, self.config.language),
            SkillProfile,
        )
        print(f"  Title: {profile.title}")

        print("[3/6] Running static review...")
        static_review = self._parse_structured(
            self.config.judge_model,
            static_review_messages(skill, profile, self.config.language),
            SkillStaticReview,
        )
        print(f"  Verdict: {static_review.verdict} | Score: {static_review.scores.average:.2f} | Findings: {len(static_review.findings)}")

        print("[4/6] Loading test cases...")
        if dataset_path:
            cases = self._load_dataset(Path(dataset_path).resolve())
            print(f"  Loaded {len(cases)} case(s) from dataset")
        else:
            # Try loading cached cases for this skill fingerprint
            cases = self._load_cached_cases(fingerprint)
            if cases is None:
                print("  Generating cases...")
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
                self._save_cached_cases(fingerprint, cases)
                print(f"  Generated {len(cases)} case(s)")
            else:
                print(f"  Loaded {len(cases)} cached case(s)")

        print(f"[5/6] Executing and grading {len(cases)} case(s)...")
        case_results: list[CaseResult] = []
        for i, case in enumerate(cases, 1):
            print(f"  [{i}/{len(cases)}] {case.case_id}: {case.name}", end="", flush=True)
            assistant_answer = self._run_case(skill, case)

            # Run code validation for cases that require or contain code
            code_validation = None
            if case.requires_code or case.category == "code_generation" or "```" in assistant_answer:
                requires = case.requires_code or case.category == "code_generation"
                code_validation = validate_answer_code(
                    answer=assistant_answer,
                    expected_sdks=profile.claimed_sdks,
                    requires_code=requires,
                )

            grade = self._grade_with_consensus(
                skill, case, assistant_answer,
                code_validation=code_validation,
            )
            case_results.append(
                CaseResult(
                    case=case,
                    assistant_answer=assistant_answer,
                    code_validation=code_validation,
                    grade=grade,
                )
            )
            print(f" -> {grade.verdict} ({grade.scores.average:.1f})")

        print("[6/6] Aggregating results...")
        report = ReviewReport(
            run_id=datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S"),
            generated_at=datetime.now(timezone.utc),
            skill_path=str(Path(skill_path).resolve()),
            skill_fingerprint=fingerprint,
            language=self.config.language,
            profile=profile,
            static_review=static_review,
            cases=cases,
            case_results=case_results,
            aggregate=self._aggregate(static_review, case_results),
        )
        artifact_dir = self._write_artifacts(report)
        agg = report.aggregate
        print(
            f"\nDone! Verdict: {agg.final_verdict} | "
            f"Score: {agg.average_case_score:.2f} | "
            f"{agg.passes}P/{agg.warnings}W/{agg.failures}F | "
            f"Report: {artifact_dir}"
        )
        return report, artifact_dir
