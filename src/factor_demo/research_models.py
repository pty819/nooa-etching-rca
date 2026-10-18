"""Evidence and review contracts, independent of model prose."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .formula_models import FormulaHandoff


class RawEvidenceSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,80}$")
    claim: str = Field(min_length=1, max_length=4000)
    dataset_ref: str | None = None
    file: str
    column: str
    where: dict[str, str | int | float | bool] = Field(default_factory=dict)
    statistic: Literal[
        "value", "count", "unique_count", "mean", "median", "sum", "min", "max", "std"
    ] = "value"
    unit: str = Field(min_length=1, max_length=100)
    product_column: str | None = None
    time_column: str | None = None
    start: float | None = None
    end: float | None = None
    step: int | None = None
    start_fraction: float = Field(default=0, ge=0, le=1)
    end_fraction: float = Field(default=1, ge=0, le=1)
    finite_only: bool = False
    ddof: Literal[0, 1] = 0


class EvidenceCheck(BaseModel):
    model_config = ConfigDict(extra="forbid")
    description: str = Field(min_length=1, max_length=2000)
    status: Literal["planned", "completed"] = "planned"
    evidence_id: str | None = None
    subtract_evidence_id: str | None = None
    operator: Literal["lt", "le", "gt", "ge", "eq", "abs_le", "abs_gt"]
    threshold: float


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
    run_ids: list[str] = Field(
        default_factory=list,
        description="Actual sample/run identities from the referenced dataset, NOT DatasetRefs or execution IDs. Leave empty for dataset-level counts.",
    )
    recipe_id: str | None = None
    sha256: str | None = None
    execution_ref: str | None = None
    proof_id: str | None = None
    dataset_ref: str | None = Field(
        default=None,
        description="DatasetRef whose identities and scope this evidence uses; defaults to this report's dataset.",
    )


class Hypothesis(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    title: str
    status: Literal["supported", "weak", "rejected", "unresolved"]
    mathematical_evidence: str
    evidence_files: list[str]
    evidence_ids: list[str] = Field(default_factory=list)
    alternative_explanation: str
    falsification_test: str
    checks: list[EvidenceCheck] = Field(default_factory=list)
    mechanism_id: str | None = None
    sensor_steps: list[dict[str, str | int]] = Field(default_factory=list)


class ResearchReply(BaseModel):
    model_config = ConfigDict(extra="forbid")
    summary: str
    hypotheses: list[Hypothesis] = Field(default_factory=list)
    evidence: list[EvidenceRef] = Field(default_factory=list)
    artifacts: list[str] = Field(default_factory=list)
    next_experiments: list[str] = Field(default_factory=list)
    questions_for_expert: list[str] = Field(default_factory=list)
    formula_comparison: FormulaHandoff | None = None
    dataset_ref: str | None = None
    intent: Literal["visualization", "exploration", "comparison", "report"] = "report"


class ReviewIssue(BaseModel):
    model_config = ConfigDict(extra="forbid")
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
    model_config = ConfigDict(extra="forbid")
    issues: list[ReviewIssue] = Field(default_factory=list)
    strengths: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)


class CandidateAssessment(BaseModel):
    model_config = ConfigDict(extra="forbid")
    assessment: str
    issues: list[str] = Field(default_factory=list)
    suggested_checks: list[str] = Field(default_factory=list)
