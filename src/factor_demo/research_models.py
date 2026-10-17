"""Evidence and review contracts, independent of model prose."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .formula_models import FormulaHandoff


class EvidenceRef(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    claim: str
    artifact: str = Field(
        description="Existing CSV/Parquet evidence table path. PNG/images belong in ResearchReply.artifacts, never here. Store file metadata as table columns if needed."
    )
    column: str = Field(description="An existing column in the selected CSV/Parquet table")
    where: dict[str, str | int | float | bool] = Field(default_factory=dict)
    statistic: Literal["value", "count", "mean", "median", "sum", "min", "max", "std"] = "value"
    value: float | int | str | bool | None
    unit: str
    run_ids: list[str] = Field(default_factory=list)
    recipe_id: str | None = None
    sha256: str | None = None


class Hypothesis(BaseModel):
    id: str
    title: str
    status: Literal["supported", "weak", "rejected", "unresolved"]
    mathematical_evidence: str
    evidence_files: list[str]
    evidence_ids: list[str] = Field(default_factory=list)
    alternative_explanation: str
    falsification_test: str


class ResearchReply(BaseModel):
    summary: str
    hypotheses: list[Hypothesis] = Field(default_factory=list)
    evidence: list[EvidenceRef] = Field(default_factory=list)
    artifacts: list[str] = Field(default_factory=list)
    next_experiments: list[str] = Field(default_factory=list)
    questions_for_expert: list[str] = Field(default_factory=list)
    formula_comparison: FormulaHandoff | None = None


class ReviewIssue(BaseModel):
    category: Literal["evidence", "statistics", "physics", "causality", "scope", "reproducibility"]
    severity: Literal["blocking", "warning"]
    claim_id: str = Field(
        description="Exactly ONE existing hypothesis/evidence ID, or summary/scope. Never concatenate IDs; put multiple references in evidence_ids."
    )
    evidence_ids: list[str] = Field(
        default_factory=list, description="Existing evidence IDs as separate array entries"
    )
    problem: str
    counterevidence: str
    suggested_check: str


class ReviewResult(BaseModel):
    issues: list[ReviewIssue] = Field(default_factory=list)
    strengths: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)


class CandidateAssessment(BaseModel):
    assessment: str
    issues: list[str] = Field(default_factory=list)
    suggested_checks: list[str] = Field(default_factory=list)
