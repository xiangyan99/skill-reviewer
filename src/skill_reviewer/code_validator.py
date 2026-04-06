"""Extract code blocks from assistant answers and validate them."""

from __future__ import annotations

import ast
import re

from skill_reviewer.models import CodeBlock, CodeValidation, CodeValidationIssue


_CODE_BLOCK_RE = re.compile(
    r"```(\w*)\n(.*?)```",
    re.DOTALL,
)

_LANG_ALIASES: dict[str, str] = {
    "py": "python",
    "python3": "python",
    "js": "javascript",
    "ts": "typescript",
    "sh": "bash",
    "shell": "bash",
    "yml": "yaml",
    "cs": "csharp",
    "c#": "csharp",
}

# Security anti-patterns to check in any language
_SECURITY_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (
        re.compile(
            r"""(?:api[_-]?key|password|secret|token|credential)\s*=\s*["'][^"']{8,}["']""",
            re.IGNORECASE,
        ),
        "Hardcoded secret or credential value",
    ),
    (
        re.compile(
            r"verify\s*=\s*False|VERIFY_SSL\s*=\s*False|ssl\s*=\s*False|rejectUnauthorized\s*:\s*false",
            re.IGNORECASE,
        ),
        "SSL/TLS verification disabled",
    ),
    (
        re.compile(
            r"""connection[_-]?string\s*=\s*["'][^"']{20,}["']""",
            re.IGNORECASE,
        ),
        "Hardcoded connection string",
    ),
    (
        re.compile(r"\*\s+FROM\s+", re.IGNORECASE),
        "SELECT * pattern — consider selecting specific columns",
    ),
]

# Placeholder patterns that indicate incomplete code
_PLACEHOLDER_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (
        re.compile(r"<(your[_-]?\w+|INSERT[_-]\w+|REPLACE[_-]\w+|TODO)>", re.IGNORECASE),
        "Angle-bracket placeholder found",
    ),
    (
        re.compile(r"\.\.\.\s*#|#\s*\.\.\.|\.\.\.\s*//|//\s*\.\.\.", re.IGNORECASE),
        "Ellipsis comment indicating omitted code",
    ),
    (
        re.compile(r"pass\s*#\s*(?:TODO|FIXME|implement)", re.IGNORECASE),
        "Stub with TODO/FIXME",
    ),
]


def _normalize_language(lang: str) -> str:
    lang = lang.strip().lower()
    return _LANG_ALIASES.get(lang, lang)


def extract_code_blocks(text: str) -> list[CodeBlock]:
    """Extract fenced code blocks from markdown text."""
    blocks: list[CodeBlock] = []
    for match in _CODE_BLOCK_RE.finditer(text):
        raw_lang = match.group(1)
        code = match.group(2).strip()
        if not code:
            continue
        language = _normalize_language(raw_lang) if raw_lang else ""
        blocks.append(CodeBlock(language=language, code=code))
    return blocks


def _check_python_syntax(code: str) -> list[CodeValidationIssue]:
    """Check Python code for syntax errors using ast.parse."""
    issues: list[CodeValidationIssue] = []
    try:
        ast.parse(code)
    except SyntaxError as exc:
        line_info = f" (line {exc.lineno})" if exc.lineno else ""
        issues.append(
            CodeValidationIssue(
                severity="error",
                category="syntax",
                message=f"Python syntax error{line_info}: {exc.msg}",
            )
        )
    return issues


def _check_python_imports(code: str, expected_sdks: list[str]) -> list[CodeValidationIssue]:
    """Check if Python code imports the expected SDK packages."""
    issues: list[CodeValidationIssue] = []
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return issues

    imported_modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported_modules.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imported_modules.add(node.module.split(".")[0])

    if not imported_modules:
        issues.append(
            CodeValidationIssue(
                severity="warning",
                category="imports",
                message="No import statements found — code may be incomplete",
            )
        )
    return issues


def _check_security(code: str) -> list[CodeValidationIssue]:
    """Check code for security anti-patterns."""
    issues: list[CodeValidationIssue] = []
    for pattern, description in _SECURITY_PATTERNS:
        if pattern.search(code):
            issues.append(
                CodeValidationIssue(
                    severity="error",
                    category="security",
                    message=description,
                )
            )
    return issues


def _check_placeholders(code: str) -> list[CodeValidationIssue]:
    """Check for placeholder patterns indicating incomplete code."""
    issues: list[CodeValidationIssue] = []
    for pattern, description in _PLACEHOLDER_PATTERNS:
        matches = pattern.findall(code)
        if matches:
            issues.append(
                CodeValidationIssue(
                    severity="warning",
                    category="completeness",
                    message=f"{description} ({len(matches)} occurrence(s))",
                )
            )
    return issues


def _check_javascript_basic(code: str) -> list[CodeValidationIssue]:
    """Basic structural checks for JavaScript/TypeScript code."""
    issues: list[CodeValidationIssue] = []

    # Check for unmatched braces
    open_count = code.count("{") - code.count("}")
    if open_count > 0:
        issues.append(
            CodeValidationIssue(
                severity="error",
                category="syntax",
                message=f"Unmatched opening braces: {open_count} unclosed",
            )
        )
    elif open_count < 0:
        issues.append(
            CodeValidationIssue(
                severity="error",
                category="syntax",
                message=f"Unmatched closing braces: {abs(open_count)} extra",
            )
        )

    # Check for unmatched parentheses
    paren_count = code.count("(") - code.count(")")
    if paren_count != 0:
        issues.append(
            CodeValidationIssue(
                severity="error",
                category="syntax",
                message=f"Unmatched parentheses: difference of {paren_count}",
            )
        )

    return issues


def validate_code_block(
    block: CodeBlock,
    expected_sdks: list[str],
) -> list[CodeValidationIssue]:
    """Validate a single code block and return issues found."""
    issues: list[CodeValidationIssue] = []

    # Language-specific checks
    if block.language == "python":
        issues.extend(_check_python_syntax(block.code))
        issues.extend(_check_python_imports(block.code, expected_sdks))
    elif block.language in ("javascript", "typescript"):
        issues.extend(_check_javascript_basic(block.code))

    # Universal checks
    issues.extend(_check_security(block.code))
    issues.extend(_check_placeholders(block.code))

    return issues


def validate_answer_code(
    answer: str,
    expected_sdks: list[str],
    requires_code: bool,
) -> CodeValidation:
    """Extract and validate all code blocks from an assistant answer.

    Args:
        answer: The assistant's full response text.
        expected_sdks: SDK package names the skill claims to cover.
        requires_code: Whether this case explicitly requires code output.

    Returns:
        A CodeValidation summarizing all blocks and issues found.
    """
    blocks = extract_code_blocks(answer)
    all_issues: list[CodeValidationIssue] = []

    if requires_code and not blocks:
        all_issues.append(
            CodeValidationIssue(
                severity="error",
                category="completeness",
                message="Case requires code output but no code blocks were found in the answer",
            )
        )
        return CodeValidation(
            blocks_found=0,
            blocks=blocks,
            issues=all_issues,
            syntax_valid=False,
            has_security_issues=False,
        )

    syntax_valid = True
    has_security_issues = False

    for block in blocks:
        block_issues = validate_code_block(block, expected_sdks)
        all_issues.extend(block_issues)

        if any(i.category == "syntax" and i.severity == "error" for i in block_issues):
            syntax_valid = False
        if any(i.category == "security" for i in block_issues):
            has_security_issues = True

    return CodeValidation(
        blocks_found=len(blocks),
        blocks=blocks,
        issues=all_issues,
        syntax_valid=syntax_valid,
        has_security_issues=has_security_issues,
    )
