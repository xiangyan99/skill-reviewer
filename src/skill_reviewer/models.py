from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import AliasChoices, BaseModel, Field


class SkillPackage(BaseModel):
    root_path: str
    primary_file: str
    included_files: list[str]
    content: str
    sanitization_warnings: list[str] = Field(default_factory=list)


class SkillProfile(BaseModel):
    title: str
    summary: str
    intended_audience: list[str] = Field(default_factory=list)
    claimed_services: list[str] = Field(default_factory=list)
    claimed_sdks: list[str] = Field(default_factory=list)
    programming_languages: list[str] = Field(default_factory=list)
    key_tasks: list[str] = Field(default_factory=list)
    strengths: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)


class ReviewCase(BaseModel):
    case_id: str
    name: str
    category: Literal[
        "happy_path",
        "security",
        "troubleshooting",
        "edge_case",
        "adversarial",
        "code_generation",
    ] = "happy_path"
    difficulty: Literal["basic", "intermediate", "advanced"] = "intermediate"
    requires_code: bool = False
    expected_language: str = ""
    scenario: str
    user_prompt: str
    evaluation_focus: list[str] = Field(default_factory=list)
    must_cover: list[str] = Field(default_factory=list)
    red_flags: list[str] = Field(default_factory=list)


class GeneratedCaseSet(BaseModel):
    cases: list[ReviewCase] = Field(default_factory=list)


class RubricScores(BaseModel):
    technical_correctness: int = Field(ge=1, le=5)
    completeness: int = Field(ge=1, le=5)
    safety: int = Field(ge=1, le=5)
    clarity: int = Field(ge=1, le=5)
    actionability: int = Field(ge=1, le=5)

    @property
    def average(self) -> float:
        values = [
            self.technical_correctness,
            self.completeness,
            self.safety,
            self.clarity,
            self.actionability,
        ]
        return sum(values) / len(values)


class StaticFinding(BaseModel):
    severity: Literal["high", "medium", "low"]
    category: str
    problem: str
    why_it_matters: str
    suggested_fix: str


class SkillStaticReview(BaseModel):
    verdict: Literal["approve", "needs_revision", "reject"]
    summary: str
    scores: RubricScores
    findings: list[StaticFinding] = Field(default_factory=list)
    improvement_ideas: list[str] = Field(default_factory=list)


class MustCoverResult(BaseModel):
    criterion: str
    met: bool
    evidence: str = ""


class RedFlagResult(BaseModel):
    flag: str = Field(validation_alias=AliasChoices("flag", "criterion"))
    triggered: bool
    evidence: str = ""


class CaseGrade(BaseModel):
    verdict: Literal["pass", "warning", "fail"]
    summary: str
    scores: RubricScores
    must_cover_results: list[MustCoverResult] = Field(default_factory=list)
    red_flag_results: list[RedFlagResult] = Field(default_factory=list)
    strengths: list[str] = Field(default_factory=list)
    issues: list[str] = Field(default_factory=list)
    recommended_edits: list[str] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)


class CodeBlock(BaseModel):
    language: str = ""
    code: str = ""


class CodeValidationIssue(BaseModel):
    severity: Literal["error", "warning", "info"]
    category: str
    message: str


class CodeValidation(BaseModel):
    blocks_found: int = 0
    blocks: list[CodeBlock] = Field(default_factory=list)
    issues: list[CodeValidationIssue] = Field(default_factory=list)
    syntax_valid: bool = True
    has_security_issues: bool = False


class CaseResult(BaseModel):
    case: ReviewCase
    assistant_answer: str
    code_validation: CodeValidation | None = None
    grade: CaseGrade


class AggregateReport(BaseModel):
    final_verdict: Literal["approve", "needs_revision", "reject"]
    verdict_reasons: list[str] = Field(default_factory=list)
    action_items: list[str] = Field(default_factory=list)
    average_case_score: float
    static_score: float
    passes: int
    warnings: int
    failures: int
    must_cover_total: int = 0
    must_cover_met: int = 0
    red_flags_total: int = 0
    red_flags_triggered: int = 0
    code_blocks_total: int = 0
    code_syntax_errors: int = 0
    code_security_issues: int = 0
    top_issues: list[str] = Field(default_factory=list)
    top_recommendations: list[str] = Field(default_factory=list)


class ReviewReport(BaseModel):
    run_id: str
    generated_at: datetime
    skill_path: str
    skill_fingerprint: str = ""
    review_model: str = ""
    judge_model: str = ""
    language: str
    profile: SkillProfile
    static_review: SkillStaticReview
    cases: list[ReviewCase]
    case_results: list[CaseResult]
    aggregate: AggregateReport
