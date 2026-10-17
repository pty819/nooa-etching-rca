"""Application policy for the native NOOA capabilities (model limits stay separate)."""

from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field


class Capabilities(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    loop_guard: bool = True
    loop_repeat_threshold: int = Field(default=7, ge=2)
    loop_window: int = Field(default=15, ge=2)
    validation_retries: int = Field(default=3, ge=2, le=10)
    review_enabled: bool = True
    max_revisions: int = Field(default=2, ge=0, le=5)
    reflexion_enabled: bool = True
    reflexion_iterations: int = Field(default=2, ge=1, le=3)
    max_concurrent_branches: int = Field(default=2, ge=1, le=8)
    max_branches_per_turn: int = Field(default=8, ge=1, le=32)
    max_model_in_flight: int = Field(default=2, ge=1, le=16)
    max_model_calls: int = Field(default=256, ge=1)
    max_total_tokens: int = Field(default=4_000_000, ge=1000)
    branch_timeout_s: int = Field(default=1800, ge=10, le=7200)

    @classmethod
    def load(cls, path: Path = Path("configs/capabilities.json")):
        return cls.model_validate_json(path.read_text()) if path.exists() else cls()
