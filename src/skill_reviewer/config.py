from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


def _normalize_azure_endpoint(endpoint: str) -> str:
    cleaned = endpoint.strip().rstrip("/")
    if cleaned.endswith("/openai/v1"):
        cleaned = cleaned[: -len("/openai/v1")]
    elif cleaned.endswith("/openai"):
        cleaned = cleaned[: -len("/openai")]
    return f"{cleaned}/"


@dataclass(slots=True)
class ReviewerConfig:
    azure_endpoint: str
    api_key: str | None
    api_version: str
    review_model: str
    judge_model: str
    token_scope: str
    language: str
    output_dir: Path
    max_generated_cases: int = 6
    max_reference_files: int = 8

    @classmethod
    def from_env(
        cls,
        *,
        language: str | None = None,
        output_dir: str | None = None,
        review_model: str | None = None,
        judge_model: str | None = None,
    ) -> "ReviewerConfig":
        load_dotenv(override=True)

        endpoint = os.getenv("AZURE_OPENAI_ENDPOINT")
        if not endpoint:
            raise ValueError("Missing AZURE_OPENAI_ENDPOINT.")
        api_version = os.getenv("AZURE_OPENAI_API_VERSION", "2025-03-01-preview")

        resolved_review_model = review_model or os.getenv("AZURE_OPENAI_REVIEW_MODEL")
        if not resolved_review_model:
            raise ValueError("Missing AZURE_OPENAI_REVIEW_MODEL.")

        resolved_judge_model = judge_model or os.getenv("AZURE_OPENAI_JUDGE_MODEL") or resolved_review_model
        resolved_language = language or os.getenv("SKILL_REVIEW_LANGUAGE") or "en"
        resolved_output_dir = Path(output_dir or "artifacts")
        token_scope = os.getenv(
            "AZURE_OPENAI_TOKEN_SCOPE",
            "https://ai.azure.com/.default",
        )

        return cls(
            azure_endpoint=_normalize_azure_endpoint(endpoint),
            api_key=os.getenv("AZURE_OPENAI_API_KEY"),
            api_version=api_version,
            review_model=resolved_review_model,
            judge_model=resolved_judge_model,
            token_scope=token_scope,
            language=resolved_language,
            output_dir=resolved_output_dir,
        )
