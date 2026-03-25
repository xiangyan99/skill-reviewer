from __future__ import annotations

import re
from pathlib import Path

from skill_reviewer.models import SkillPackage


_INJECTION_PATTERNS = re.compile(
    r"(?:"
    r"ignore\s+(?:all\s+)?(?:previous|above|prior)\s+(?:instructions|prompts|rules)"
    r"|you\s+are\s+now\s+(?:a\s+)?(?:new|different)"
    r"|system\s*:\s*"
    r"|<\s*/?\s*(?:system|instruction|prompt)\s*>"
    r")",
    re.IGNORECASE,
)

_ZERO_WIDTH_CHARS = re.compile(r"[\u200b\u200c\u200d\u2060\ufeff]")

# Severe patterns that warrant immediate rejection before LLM review.
_CRITICAL_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (
        re.compile(
            r"ignore\s+(?:all\s+)?(?:previous|above|prior)\s+(?:instructions|prompts|rules)",
            re.IGNORECASE,
        ),
        "Prompt injection: attempts to override reviewer instructions",
    ),
    (
        re.compile(
            r"you\s+are\s+now\s+(?:a\s+)?(?:new|different)",
            re.IGNORECASE,
        ),
        "Prompt injection: attempts to redefine the model's role",
    ),
    (
        re.compile(
            r"<\s*/?\s*(?:system|instruction|prompt)\s*>",
            re.IGNORECASE,
        ),
        "Prompt injection: fake system/instruction XML tags",
    ),
    (
        re.compile(
            r"(?:return|output|respond\s+with)\s+.*?verdict.*?approve",
            re.IGNORECASE,
        ),
        "Prompt injection: attempts to force an approve verdict",
    ),
    (
        re.compile(
            r"(?:return|output|respond\s+with)\s+.*?score.*?[45]",
            re.IGNORECASE,
        ),
        "Prompt injection: attempts to dictate review scores",
    ),
    (
        re.compile(
            r"(?:repeat|print|echo|output)\s+(?:your\s+)?(?:system\s+)?(?:instructions|prompt|rules)",
            re.IGNORECASE,
        ),
        "Prompt exfiltration: attempts to extract system instructions",
    ),
    (
        re.compile(
            r"(?:disregard|forget|override|bypass)\s+(?:all\s+)?(?:prior|previous|above|safety|review)",
            re.IGNORECASE,
        ),
        "Prompt injection: attempts to bypass safety or review rules",
    ),
]


class PreflightRejection:
    """Result of a failed pre-flight check — skill is rejected before LLM review."""

    def __init__(self, reasons: list[str]) -> None:
        self.reasons = reasons


def preflight_check(content: str) -> PreflightRejection | None:
    """Run static checks on raw skill content before any LLM call.

    Returns a PreflightRejection if critical issues are found, None otherwise.
    """
    violations: list[str] = []

    # Check for hidden content in HTML comments
    html_comments = re.findall(r"<!--(.*?)-->", content, flags=re.DOTALL)
    for comment in html_comments:
        for pattern, description in _CRITICAL_PATTERNS:
            if pattern.search(comment):
                violations.append(f"{description} (hidden in HTML comment)")

    # Check for zero-width character obfuscation at suspicious density
    zw_count = len(_ZERO_WIDTH_CHARS.findall(content))
    if zw_count > 10:
        violations.append(
            f"Suspicious obfuscation: {zw_count} zero-width characters detected"
        )

    # Check visible text for critical injection patterns
    stripped = _strip_html_comments(content)
    for pattern, description in _CRITICAL_PATTERNS:
        if pattern.search(stripped):
            violations.append(description)

    # Dedupe
    violations = list(dict.fromkeys(violations))

    if violations:
        return PreflightRejection(violations)
    return None


def _strip_html_comments(text: str) -> str:
    return re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL)


def _sanitize(text: str) -> tuple[str, list[str]]:
    warnings: list[str] = []
    if re.search(r"<!--.*?-->", text, flags=re.DOTALL):
        warnings.append("HTML comments stripped from skill content")
        text = _strip_html_comments(text)
    if _ZERO_WIDTH_CHARS.search(text):
        warnings.append("Zero-width characters removed from skill content")
        text = _ZERO_WIDTH_CHARS.sub("", text)
    if _INJECTION_PATTERNS.search(text):
        warnings.append("Potential prompt injection pattern detected in skill content")
    return text, warnings


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _collect_markdown_files(root: Path, max_reference_files: int) -> list[Path]:
    if root.is_file():
        return [root]

    files: list[Path] = []
    primary = root / "SKILL.md"
    if primary.exists():
        files.append(primary)

    reference_files: list[Path] = []
    patterns = ("*.md", "*.py", "*.json", "*.yml", "*.yaml")
    for folder_name in ("references", "docs", "examples"):
        folder = root / folder_name
        if folder.exists():
            for pattern in patterns:
                reference_files.extend(sorted(folder.rglob(pattern)))

    if not files:
        fallback: list[Path] = []
        for pattern in patterns:
            fallback.extend(sorted(root.rglob(pattern)))
        if not fallback:
            raise FileNotFoundError(f"No supported skill files found under {root}.")
        files.append(fallback[0])
        reference_files.extend(fallback[1:])

    for path in reference_files[:max_reference_files]:
        if path not in files:
            files.append(path)

    return files


def load_skill_package(path: str | Path, *, max_reference_files: int = 8) -> SkillPackage:
    root = Path(path).resolve()
    if not root.exists():
        raise FileNotFoundError(f"Skill path not found: {root}")

    files = _collect_markdown_files(root, max_reference_files=max_reference_files)
    primary_file = files[0]

    sections: list[str] = []
    for file_path in files:
        relative_name = file_path.name if root.is_file() else str(file_path.relative_to(root))
        sections.append(f"# FILE: {relative_name}\n\n{_read_text(file_path).strip()}")

    raw_content = "\n\n".join(sections).strip()
    content, warnings = _sanitize(raw_content)

    return SkillPackage(
        root_path=str(root),
        primary_file=str(primary_file),
        included_files=[str(file_path) for file_path in files],
        content=content,
        sanitization_warnings=warnings,
    )
