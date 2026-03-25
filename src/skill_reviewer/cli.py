from __future__ import annotations

import argparse
import sys

from openai import APIConnectionError, APIError, APIStatusError

from skill_reviewer.config import ReviewerConfig
from skill_reviewer.reviewer import HarnessSkillReviewer


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="skill-reviewer",
        description="Review Azure SDK skills with a harness-based Azure OpenAI evaluator.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    review = subparsers.add_parser("review", help="Review a skill file or directory.")
    review.add_argument("--skill", required=True, help="Path to a SKILL.md file or skill directory.")
    review.add_argument("--dataset", help="Optional YAML dataset path.")
    review.add_argument("--out", default="artifacts", help="Output directory for reports.")
    review.add_argument("--language", help="Report language, for example zh-CN or en-US.")
    review.add_argument("--review-model", help="Override AZURE_OPENAI_REVIEW_MODEL.")
    review.add_argument("--judge-model", help="Override AZURE_OPENAI_JUDGE_MODEL.")
    review.add_argument(
        "--require-verdict",
        choices=("approve", "needs_revision", "reject"),
        help="Fail the command if the final verdict is lower than this threshold.",
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
    if isinstance(exc, APIStatusError):
        body = getattr(exc, "body", None)
        return f"{exc.__class__.__name__} status={exc.status_code} body={body}"
    if isinstance(exc, APIConnectionError):
        cause = getattr(exc, "__cause__", None)
        if cause:
            return f"{exc.__class__.__name__}: {cause.__class__.__name__}: {cause}"
        return f"{exc.__class__.__name__}: {exc}"
    if isinstance(exc, APIError):
        return f"{exc.__class__.__name__}: {exc}"
    return str(exc)


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()

    if args.command != "review":
        parser.error(f"Unsupported command: {args.command}")

    try:
        config = ReviewerConfig.from_env(
            language=args.language,
            output_dir=args.out,
            review_model=args.review_model,
            judge_model=args.judge_model,
        )
        reviewer = HarnessSkillReviewer(config)
        report, artifact_dir = reviewer.review(args.skill, args.dataset)
    except Exception as exc:
        print(f"review failed: {_format_error(exc)}", file=sys.stderr)
        raise SystemExit(1) from exc

    print(f"final_verdict={report.aggregate.final_verdict}")
    print(f"artifact_dir={artifact_dir}")
    print(f"report_json={artifact_dir / 'report.json'}")
    print(f"report_md={artifact_dir / 'report.md'}")

    if args.require_verdict and _verdict_rank(report.aggregate.final_verdict) < _verdict_rank(args.require_verdict):
        raise SystemExit(2)
