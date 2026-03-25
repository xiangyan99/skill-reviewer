from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


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


class CaseGrade(BaseModel):
    verdict: Literal["pass", "warning", "fail"]
    summary: str
    scores: RubricScores
    strengths: list[str] = Field(default_factory=list)
    issues: list[str] = Field(default_factory=list)
    recommended_edits: list[str] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)


class CaseResult(BaseModel):
    case: ReviewCase
    assistant_answer: str
    grade: CaseGrade


class AggregateReport(BaseModel):
    final_verdict: Literal["approve", "needs_revision", "reject"]
    average_case_score: float
    static_score: float
    passes: int
    warnings: int
    failures: int
    top_issues: list[str] = Field(default_factory=list)
    top_recommendations: list[str] = Field(default_factory=list)


class ReviewReport(BaseModel):
    run_id: str
    generated_at: datetime
    skill_path: str
    language: str
    profile: SkillProfile
    static_review: SkillStaticReview
    cases: list[ReviewCase]
    case_results: list[CaseResult]
    aggregate: AggregateReport
