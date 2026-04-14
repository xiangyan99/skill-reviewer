from __future__ import annotations

import argparse
import asyncio
import sys

from skill_reviewer.config import ReviewerConfig, load_config_file
from skill_reviewer.reviewer import HarnessSkillReviewer


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="skill-reviewer",
        description="Review Azure SDK skills with a harness-based GitHub Copilot evaluator.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    review = subparsers.add_parser("review", help="Review a skill file or directory.")
    review.add_argument("--skill", required=True, help="Path to a SKILL.md file or skill directory.")
    review.add_argument("--scenario", help="Optional YAML scenario path with predefined review cases.")
    review.add_argument("--config", help="Optional YAML config file for model and review settings.")
    review.add_argument("--out", default="artifacts", help="Output directory for reports.")
    review.add_argument("--language", help="Report language, for example zh-CN or en-US.")
    review.add_argument("--review-model", help="Override SKILL_REVIEW_MODEL.")
    review.add_argument("--judge-model", help="Override SKILL_JUDGE_MODEL.")
    review.add_argument(
        "--require-verdict",
        choices=("approve", "needs_revision", "reject"),
        help="Fail the command if the final verdict is lower than this threshold.",
    )
    review.add_argument(
        "--grade-rounds",
        type=int,
        default=1,
        help="Number of grading rounds per case for majority-vote consensus (default: 1).",
    )
    review.add_argument(
        "--case-cache-dir",
        help="Directory to cache generated cases. Reuses cases when skill content is unchanged.",
    )
    return parser


def _verdict_rank(value: str) -> int:
    order = {
        "reject": 0,
        "needs_revision": 1,
        "approve": 2,
    }
    return order[value]


def _format_error(exc: Exception) -> str:
    return f"{exc.__class__.__name__}: {exc}"


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()

    if args.command != "review":
        parser.error(f"Unsupported command: {args.command}")

    try:
        file_overrides = load_config_file(args.config) if args.config else {}
        config = ReviewerConfig.from_env(
            language=args.language or file_overrides.get("language"),
            output_dir=args.out,
            review_model=args.review_model or file_overrides.get("review_model"),
            judge_model=args.judge_model or file_overrides.get("judge_model"),
            grade_rounds=args.grade_rounds or file_overrides.get("grade_rounds"),
            case_cache_dir=args.case_cache_dir or file_overrides.get("case_cache_dir"),
        )
        reviewer = HarnessSkillReviewer(config)
        report, artifact_dir = asyncio.run(reviewer.review(args.skill, args.scenario))
    except Exception as exc:
        print(f"review failed: {_format_error(exc)}", file=sys.stderr)
        raise SystemExit(1) from exc

    print(f"final_verdict={report.aggregate.final_verdict}")
    print(f"artifact_dir={artifact_dir}")
    print(f"report_json={artifact_dir / 'report.json'}")
    print(f"report_md={artifact_dir / 'report.md'}")

    if args.require_verdict and _verdict_rank(report.aggregate.final_verdict) < _verdict_rank(args.require_verdict):
        raise SystemExit(2)
