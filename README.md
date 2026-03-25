# Azure SDK Skill Reviewer

This is a skill reviewer that breaks review into four reproducible stages, rather than simply asking a model "is this skill good?":

1. `Profile`: Extract the skill's declared scope, target audience, and covered Azure services & SDKs.
2. `Static Review`: Review the skill text directly, checking for correctness, completeness, security, clarity, and actionability.
3. `Execution Harness`: Treat the skill as a developer prompt under test, run a set of user scenarios, and observe whether the model can produce trustworthy answers.
4. `Judge`: Use a stronger review model to score each scenario, outputting pass/fail status, issues found, and improvement suggestions.

The core benefit: every time a new skill is submitted, you get an evidence-backed review report instead of subjective comments that are hard to regression-test.

## Use Cases

- Gate new Azure SDK skill submissions with an admission check
- Run regression evaluations after modifying an existing skill
- Block "looks right but is actually unreliable" skills from merging in CI

## Current Implementation

- Written in `Python`
- Uses the `AzureOpenAI` client on Azure OpenAI
- Supports `API Key` or `Microsoft Entra ID` authentication
- Default outputs:
  - `report.json`
  - `report.md`

## Installation

```bash
python -m venv .venv
. .venv/Scripts/Activate.ps1
pip install -e .
```

## Configuration

Copy `.env.example` to `.env` and fill in at least these values:

```env
AZURE_OPENAI_ENDPOINT=https://YOUR-RESOURCE-NAME.openai.azure.com
AZURE_OPENAI_API_KEY=...
AZURE_OPENAI_API_VERSION=2025-03-01-preview
AZURE_OPENAI_REVIEW_MODEL=gpt-4.1-mini
AZURE_OPENAI_JUDGE_MODEL=gpt-4.1
SKILL_REVIEW_LANGUAGE=en
```

If you use Entra ID, you can omit `AZURE_OPENAI_API_KEY` and instead sign in to Azure so that `DefaultAzureCredential` takes effect.

`AZURE_OPENAI_TOKEN_SCOPE` defaults to `https://ai.azure.com/.default`. If your environment requires a different scope, you can override this environment variable directly.

The current implementation uses this client pattern:

```python
from openai import AzureOpenAI

client = AzureOpenAI(
    api_version="2025-03-01-preview",
    azure_endpoint="https://YOUR-RESOURCE-NAME.openai.azure.com/",
    api_key=os.getenv("AZURE_OPENAI_API_KEY"),
)
```

## Usage

### 1. Review a single skill file

```bash
skill-reviewer review --skill C:\path\to\SKILL.md
```

### 2. Review a skill directory

If the directory contains `SKILL.md` and `references/*.md`, the reviewer will load them together.

```bash
skill-reviewer review --skill C:\path\to\my-skill
```

If the skill directory also contains `md / py / json / yml / yaml` files under `examples/`, `docs/`, or `references/`, the reviewer will bundle them into the review context as well.

### 3. Specify a baseline scenario dataset

```bash
skill-reviewer review --skill C:\path\to\my-skill --dataset datasets\azure_sdk_baseline.yaml
```

### 4. Specify output directory and language

```bash
skill-reviewer review --skill C:\path\to\my-skill --out artifacts --language en
```

### 5. Use as a CI gate

```bash
skill-reviewer review --skill C:\path\to\my-skill --require-verdict approve
```

If the final result falls below the required verdict, the command returns a non-zero exit code, making it easy to plug into a PR pipeline.

## Understanding Review Results

The final report produces one of three verdicts:

- `approve`
- `needs_revision`
- `reject`

The verdict is based on two factors:

- Static review scores and high-risk findings
- Dynamic scenario execution pass rate, failure count, and average score

## Recommended Integration

For production use, we recommend integrating this reviewer into your skill repository's PR workflow:

1. Trigger the reviewer when a new skill is submitted
2. Generate `report.md`
3. If the result is `reject`, block the merge
4. If the result is `needs_revision`, require the author to revise
5. Only `approve` allows merging into the main branch

## Key Files

- `src/skill_reviewer/cli.py`: CLI entry point
- `src/skill_reviewer/reviewer.py`: Main workflow
- `src/skill_reviewer/prompts.py`: Planner / runner / judge prompts
- `src/skill_reviewer/models.py`: Structured schemas
- `datasets/azure_sdk_baseline.yaml`: Optional baseline scenarios
