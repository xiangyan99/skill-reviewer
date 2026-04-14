from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import yaml
from dotenv import load_dotenv


def load_config_file(path: str | Path) -> dict:
    """Load a YAML config file and return its contents as a dict."""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Config file not found: {p}")
    data = yaml.safe_load(p.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"Config file must be a YAML mapping: {p}")
    return data


@dataclass(slots=True)
class ReviewerConfig:
    review_model: str
    judge_model: str
    language: str
    output_dir: Path
    github_token: str | None = None
    max_generated_cases: int = 6
    max_reference_files: int = 8
    grade_rounds: int = 1
    case_cache_dir: Path | None = None

    @classmethod
    def from_env(
        cls,
        *,
        language: str | None = None,
        output_dir: str | None = None,
        review_model: str | None = None,
        judge_model: str | None = None,
        grade_rounds: int | None = None,
        case_cache_dir: str | None = None,
    ) -> "ReviewerConfig":
        load_dotenv(override=True)

        resolved_review_model = (
            review_model
            or os.getenv("SKILL_REVIEW_MODEL")
        )
        if not resolved_review_model:
            raise ValueError(
                "Missing review model. Set SKILL_REVIEW_MODEL or pass --review-model."
            )

        resolved_judge_model = (
            judge_model
            or os.getenv("SKILL_JUDGE_MODEL")
            or resolved_review_model
        )
        resolved_language = language or os.getenv("SKILL_REVIEW_LANGUAGE") or "en"
        resolved_output_dir = Path(output_dir or "artifacts")

        resolved_grade_rounds = grade_rounds or int(os.getenv("SKILL_REVIEW_GRADE_ROUNDS", "1"))
        resolved_cache_dir = case_cache_dir or os.getenv("SKILL_REVIEW_CASE_CACHE_DIR")
        github_token = os.getenv("GH_TOKEN") or os.getenv("GITHUB_TOKEN")

        return cls(
            review_model=resolved_review_model,
            judge_model=resolved_judge_model,
            language=resolved_language,
            output_dir=resolved_output_dir,
            github_token=github_token,
            grade_rounds=resolved_grade_rounds,
            case_cache_dir=Path(resolved_cache_dir) if resolved_cache_dir else None,
        )
