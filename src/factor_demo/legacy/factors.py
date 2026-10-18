"""Small explicit expression language; no arbitrary evaluation of factor text."""

from __future__ import annotations

from typing import Literal

import numpy as np
import pandas as pd
from pydantic import BaseModel, ConfigDict, Field, model_validator


class Factor(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=100)
    operation: Literal["raw", "difference", "ratio", "product"]
    columns: list[str] = Field(min_length=1, max_length=2)

    @model_validator(mode="after")
    def arity(self):
        expected = 1 if self.operation == "raw" else 2
        if len(self.columns) != expected:
            raise ValueError(f"{self.operation} needs {expected} columns")
        return self


def transform(df: pd.DataFrame, specs: list[Factor], allowed: set[str]) -> np.ndarray:
    if not 1 <= len(specs) <= 5 or len({s.name for s in specs}) != len(specs):
        raise ValueError("Use 1-5 factors with unique names")
    values = []
    for spec in specs:
        if not set(spec.columns) <= allowed:
            raise ValueError("Only catalogued sensor feature columns may be used")
        a = df[spec.columns[0]].to_numpy(dtype=float)
        with np.errstate(divide="ignore", invalid="ignore", over="ignore"):
            if spec.operation == "raw":
                v = a.copy()
            else:
                b = df[spec.columns[1]].to_numpy(dtype=float)
                if spec.operation == "difference":
                    v = a - b
                elif spec.operation == "ratio":
                    v = np.divide(a, b, out=np.full_like(a, np.nan), where=np.abs(b) > 1e-6)
                else:
                    v = a * b
        v[~np.isfinite(v)] = np.nan
        values.append(v)
    return np.column_stack(values)
