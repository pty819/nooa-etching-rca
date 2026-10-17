"""Versioned raw-trace formulas and a frozen, lot-aware comparison contract."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class FormulaSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_-]{0,59}$")
    expression: str = Field(min_length=1, max_length=2000)
    description: str = Field(min_length=1, max_length=4000)
    change_reason: str = Field(
        default="",
        max_length=4000,
        description="Required when revising an existing formula name; explain what was corrected.",
    )
    source: str = Field(
        description="Existing Python source under artifacts/ defining extract(trace, steps, params)."
    )
    sensors: list[str] = Field(min_length=1)
    steps: list[int] = Field(min_length=1)
    params: dict = Field(default_factory=dict)
    unit: str = Field(min_length=1)


class FormulaPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=200)
    labels: list[str] = Field(min_length=2, max_length=2)
    metric: Literal["balanced_accuracy", "accuracy", "f1_macro"] = "balanced_accuracy"
    folds: int = Field(default=3, ge=2, le=10)
    seed: int = Field(default=42, ge=0, le=2147483647)
    models: list[Literal["logistic", "random_forest", "extra_trees", "hist_gradient_boosting"]] = (
        Field(default_factory=lambda: ["logistic", "random_forest"], min_length=1)
    )


class FormulaHandoff(BaseModel):
    model_config = ConfigDict(extra="forbid")
    study_id: str
    status: Literal["selected", "incomplete"]
    selection_id: str | None = None
    reason: str = ""
