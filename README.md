# Azure SDK Skill Reviewer


A harness-based reviewer for Azure SDK skills powered by [GitHub Copilot SDK](https://github.com/github/copilot-sdk). Rather than simply asking a model "is this skill good?", it breaks the review into four reproducible stages:

1. **Profile** — Extract the skill's declared scope, target audience, and covered Azure services & SDKs.
2. **Static Review** — Review the skill text directly, scoring technical correctness, completeness, safety, clarity, and actionability.
3. **Execution Harness** — Treat the skill as a developer prompt under test. Run a set of categorized user scenarios (happy path, security, troubleshooting, edge case, adversarial, code generation) and observe whether the model can produce trustworthy answers. For code generation cases, the generated code is automatically validated for syntax correctness, security anti-patterns, and completeness.
4. **Judge** — Use a stronger review model to grade each scenario against a must-cover checklist and red-flag checklist, combining automated code validation results with LLM judgment to output pass/warning/fail status with evidence.

The core benefit: every time a new skill is submitted, you get an evidence-backed review report instead of subjective comments that are hard to regression-test.

## Use Cases

- Gate new Azure SDK skill submissions with an automated admission check
- Run regression evaluations after modifying an existing skill
- Block "looks right but is actually unreliable" skills from merging in CI

## Review Pipeline

```
Skill Package
    │
    ▼
┌──────────────────┐
│  Preflight Check │──▶ Reject (prompt injection, obfuscation)
└────────┬─────────┘
         │
         ▼
┌──────────────────┐
│     Profile      │──▶ SkillProfile (title, services, SDKs, tasks)
└────────┬─────────┘
         │
         ▼
┌──────────────────┐
│  Static Review   │──▶ SkillStaticReview (scores, findings)
└────────┬─────────┘
         │
         ▼
┌──────────────────┐     ┌──────────────────┐
│ Case Generation  │────▶│  Case Caching    │
│  (or Scenario)   │     │  (fingerprint)   │
└────────┬─────────┘     └──────────────────┘
         │
         ▼
┌──────────────────┐
│  Case Execution  │──▶ Assistant answers
└────────┬─────────┘
         │
         ▼
┌──────────────────┐
│ Code Validation  │──▶ Syntax check, security scan, completeness
│ (if code present)│    (automated, no LLM needed)
└────────┬─────────┘
         │
         ▼
┌──────────────────┐
│  Grading (Judge) │──▶ CaseGrade (checklists + code validation evidence)
│  × N rounds      │    Majority-vote consensus
└────────┬─────────┘
         │
         ▼
┌──────────────────┐
│   Aggregation    │──▶ Final verdict: approve / needs_revision / reject
└──────────────────┘
```

## Installation

```bash
python -m venv .venv

# Windows
.venv\Scripts\activate

# Linux / macOS
source .venv/bin/activate

pip install -e .
```

## Configuration

Create a `.env` file in the project root:

```env
GH_TOKEN=ghp_xxxxxxxxxxxxxxxxxxxx
SKILL_REVIEW_MODEL=gpt-4o
SKILL_REVIEW_LANGUAGE=en
```

| Variable | Default | Description |
|----------|---------|-------------|
| `GH_TOKEN` or `GITHUB_TOKEN` | _(required)_ | GitHub token for Copilot authentication |
| `SKILL_REVIEW_MODEL` | _(required)_ | Model for case execution (e.g. `gpt-4o`, `claude-sonnet-4`) |
| `SKILL_JUDGE_MODEL` | same as `SKILL_REVIEW_MODEL` | Model for profile extraction, static review, and grading |
| `SKILL_REVIEW_LANGUAGE` | `en` | Report language |
| `SKILL_REVIEW_GRADE_ROUNDS` | `1` | Number of grading rounds per case (majority vote) |
| `SKILL_REVIEW_CASE_CACHE_DIR` | _(disabled)_ | Directory to cache generated cases |

## Usage

### Review a single skill file

```bash
skill-reviewer review --skill path/to/SKILL.md
```

### Review a skill directory

If the directory contains `SKILL.md` and files under `references/`, `docs/`, or `examples/`, the reviewer loads them together.

```bash
skill-reviewer review --skill path/to/my-skill
```

### Use a predefined scenario

```bash
skill-reviewer review --skill path/to/my-skill --scenario scenarios/azure_sdk_baseline.yaml
```

### Use a config file for model settings

Config files let you test the same scenario against different model combinations:

```bash
# Test with GPT-4o
skill-reviewer review --skill path/to/my-skill --config configs/gpt-4o.yaml

# Test with Claude
skill-reviewer review --skill path/to/my-skill --config configs/claude-sonnet.yaml

# Combine scenario + config
skill-reviewer review --skill path/to/my-skill --scenario scenarios/azure_sdk_baseline.yaml --config configs/mixed.yaml
```

Config file format (`configs/gpt-4o.yaml`):

```yaml
review_model: gpt-4o
judge_model: gpt-4o
language: en
```

### Specify output directory and language

```bash
skill-reviewer review --skill path/to/my-skill --out artifacts
```

### Use as a CI gate

```bash
skill-reviewer review --skill path/to/my-skill --require-verdict approve
```

Exit code `2` if the final verdict is below the required threshold.

### Improve review stability

Enable case caching and multi-round grading for deterministic results:

```bash
skill-reviewer review \
  --skill path/to/my-skill \
  --case-cache-dir .cache/cases \
  --grade-rounds 3
```

- **`--case-cache-dir`** — Caches generated cases keyed by a SHA-256 fingerprint of the skill content. Same skill content = same cases across runs.
- **`--grade-rounds N`** — Runs the grader N times per case with different seeds, then picks the verdict by majority vote and scores from the median round. Recommended value: `3`.

## CLI Reference

```
skill-reviewer review [OPTIONS]

Required:
  --skill PATH              Path to a SKILL.md file or skill directory

Optional:
  --scenario PATH           YAML scenario of predefined review cases
  --config PATH             YAML config file for model and review settings
  --out DIR                 Output directory (default: artifacts)
  --language LANG           Report language, e.g. en, zh-CN
  --review-model MODEL      Override SKILL_REVIEW_MODEL
  --judge-model MODEL       Override SKILL_JUDGE_MODEL
  --require-verdict LEVEL   Fail if verdict < LEVEL (approve|needs_revision|reject)
  --grade-rounds N          Grading rounds per case for majority vote (default: 1)
  --case-cache-dir DIR      Cache directory for generated cases
```

Precedence: CLI flags > config file > environment variables > `.env` file.

## Understanding Review Results

### Verdicts

| Verdict | Meaning |
|---------|---------|
| `approve` | Skill is ready for use |
| `needs_revision` | Issues found that should be addressed |
| `reject` | Critical problems, must not be published |

### Verdict Rules

**Reject** when any of:
- 2+ case failures
- 2+ high-severity static findings
- Static score < 2.80
- Safety floor <= 2

**Needs revision** when any of:
- 1 case failure
- Any high-severity static finding
- Static review verdict is not `approve`
- Average case score < 4.00
- Safety floor <= 3

### Case Categories

Each generated case is classified into one of five categories:

| Category | Focus |
|----------|-------|
| `happy_path` | Standard workflow correctness |
| `security` | Authentication, credential management, secret handling |
| `troubleshooting` | Error diagnosis and resolution guidance |
| `edge_case` | Unusual environments or constraints |
| `adversarial` | Out-of-scope, misleading, or hallucination-probing prompts |
| `code_generation` | Generate working code; validated for syntax, security, and completeness |

### Code Validation

For `code_generation` cases (or any case whose answer contains code blocks), the reviewer runs automated validation before grading:

- **Syntax check** — Python code is parsed with `ast.parse()`; JavaScript/TypeScript code is checked for unmatched braces and parentheses
- **Security scan** — Detects hardcoded secrets, disabled TLS verification, hardcoded connection strings
- **Completeness check** — Detects placeholder patterns (`<YOUR_KEY>`, `... # TODO`, `pass # implement`), missing imports
- **Results fed to judge** — The grading LLM receives code validation results as objective evidence, anchoring its scores to facts rather than subjective impression

Cases with `requires_code: true` will **fail** if no code blocks are found in the answer.

### Report Output

Each run produces two files in the output directory:
- `report.json` — Machine-readable full report
- `report.md` — Human-readable report with must-cover and red-flag checklists

The report includes a `skill_fingerprint` field (SHA-256 of skill content) for traceability.

## Stability Design

The reviewer uses several mechanisms to ensure the same skill produces consistent results across runs:

| Mechanism | What it does |
|-----------|-------------|
| Case caching | Generated cases are cached by skill content fingerprint |
| Score anchoring | Grading prompt uses mechanical scoring rules (subtract from 5 per miss/flag) |
| Multi-round grading | Optional majority vote across N rounds |
| Content fingerprint | SHA-256 hash tracks whether skill content has changed |

## Security

The reviewer includes multiple layers of security protection:

- **Preflight check** — Rejects skills containing prompt injection, fake system tags, obfuscated content, or instructions that manipulate the reviewer. Runs before any LLM call.
- **Content sanitization** — Strips HTML comments and zero-width characters; flags potential injection patterns.
- **Safety scoring** — Both static review and case-based review score safety independently. Skills with hard-coded secrets, disabled TLS, or credential misuse are penalized.

## Key Files

| File | Description |
|------|-------------|
| `src/skill_reviewer/cli.py` | CLI entry point |
| `src/skill_reviewer/reviewer.py` | Main review workflow and aggregation |
| `src/skill_reviewer/prompts.py` | Prompt templates for profile, static review, case generation, and grading |
| `src/skill_reviewer/models.py` | Pydantic schemas for all structured outputs |
| `src/skill_reviewer/code_validator.py` | Code block extraction, syntax checking, and security scanning |
| `src/skill_reviewer/loader.py` | Skill package loading, sanitization, and preflight checks |
| `src/skill_reviewer/config.py` | Configuration from environment variables |
| `src/skill_reviewer/copilot_client.py` | GitHub Copilot SDK client wrapper |
| `scenarios/azure_sdk_baseline.yaml` | Predefined baseline review scenario |
| `configs/` | Model configuration presets |

## License

[MIT](LICENSE)
