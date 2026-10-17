"""Dataset identities, modality adapters and the deferred acquisition boundary."""

from pathlib import PurePosixPath
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class DatasetSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")
    modality: Literal["table", "timeseries", "image", "text", "mixed"] = "table"
    index_file: str = "samples.parquet"
    id_column: str = "sample_id"
    label_column: str | None = None
    group_column: str | None = None
    split_column: str | None = None
    asset_column: str | None = None
    time_column: str | None = None
    step_column: str | None = None
    steps_file: str | None = None
    recipe_column: str | None = None
    fixed_recipe: bool = False
    control_column: str | None = None
    train_values: list[str] = Field(default_factory=lambda: ["train"])
    dev_values: list[str] = Field(default_factory=lambda: ["dev", "validation"])
    excluded_splits: list[str] = Field(default_factory=lambda: ["test", "holdout"])
    units: dict[str, str] = Field(default_factory=dict)

    @field_validator("index_file", "steps_file")
    @classmethod
    def relative_file(cls, value):
        if value is not None and (
            not value
            or PurePosixPath(value).is_absolute()
            or ".." in PurePosixPath(value).parts
            or "\\" in value
        ):
            raise ValueError("Dataset files must use relative paths inside the dataset")
        return value


class DatasetDerivation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=200)
    parents: list[str] = Field(min_length=1, max_length=20)
    output_dir: str = Field(
        description="Directory inside your workspace, containing the new dataset index and assets."
    )
    source: str = Field(
        description="Saved Python source inside your workspace which produced this dataset."
    )
    reason: str = Field(min_length=1, max_length=8000)
    data_schema: DatasetSchema
    params: dict = Field(default_factory=dict)
    relation: Literal["subset", "transform", "merge", "version"] = "subset"
    provenance: dict = Field(default_factory=dict)


class AcquisitionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source: str = Field(min_length=1)
    criteria: dict
    reason: str = Field(min_length=1)
    parent_refs: list[str] = Field(default_factory=list)
    lead_refs: list[str] = Field(default_factory=list)
    request_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,80}$")


class AcquisitionResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["supplied", "pending", "unavailable"]
    message: str = ""
    local_path: str | None = None
    data_schema: DatasetSchema | None = None
    new_entry_ids: list[str] = Field(default_factory=list)
    provenance: dict = Field(default_factory=dict)
