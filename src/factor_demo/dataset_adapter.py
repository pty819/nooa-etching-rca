"""Decode discovery inputs without requiring fake etching metadata on other modalities."""

import json
import shutil
from pathlib import Path

import polars as pl

from .dataset_models import DatasetSchema


def safe_path(root: Path, name: str, *, directory=False) -> Path:
    path = (root / name).resolve()
    if not path.is_relative_to(root.resolve()) or not path.exists():
        raise ValueError("Dataset path is missing or escapes its root")
    if not directory and not path.is_file():
        raise ValueError("Expected a dataset file")
    return path


def read_table(path: Path) -> pl.DataFrame:
    if path.suffix.lower() == ".parquet":
        return pl.read_parquet(path)
    if path.suffix.lower() == ".csv":
        return pl.read_csv(path)
    if path.suffix.lower() in {".jsonl", ".ndjson"}:
        return pl.read_ndjson(path)
    if path.suffix.lower() == ".json":
        return pl.read_json(path)
    raise ValueError("Use Parquet, CSV, JSON or JSONL for an index/table")


def fdc_schema() -> DatasetSchema:
    return DatasetSchema(
        modality="timeseries",
        index_file="runs.parquet",
        id_column="run_id",
        label_column="label",
        group_column="batch_id",
        split_column="split",
        asset_column="trace_path",
        time_column="time_s",
        step_column="step_id",
        steps_file="steps.parquet",
        recipe_column="recipe_id",
        fixed_recipe=False,
        control_column="is_control",
    )


def detect_schema(root: Path) -> DatasetSchema:
    if (root / "dataset.json").is_file():
        return DatasetSchema.model_validate(
            json.loads((root / "dataset.json").read_text())["schema"]
        )
    if (root / "runs.parquet").is_file():
        schema = fdc_schema()
        columns = pl.read_parquet(root / "runs.parquet", n_rows=0).columns
        updates = {
            name: None
            for name in [
                "label_column",
                "group_column",
                "split_column",
                "asset_column",
                "recipe_column",
                "control_column",
            ]
            if getattr(schema, name) not in columns
        }
        if not (root / "steps.parquet").exists():
            updates["steps_file"] = None
        if "trace_path" not in columns:
            updates.update(modality="table", time_column=None, step_column=None)
        manifest = root / "manifest.json"
        if manifest.is_file():
            try:
                kind = json.loads(manifest.read_text()).get("kind")
            except (OSError, ValueError):
                kind = None
            if kind == "physics_inspired_icp_etch":
                updates.update(fixed_recipe=True, knowledge_profile="etch")
        return schema.model_copy(update=updates)
    for name in ["samples.parquet", "samples.csv", "samples.jsonl"]:
        if (root / name).is_file():
            frame = read_table(root / name)
            identity = next(
                (c for c in ["sample_id", "object_id", "run_id", "id"] if c in frame.columns), None
            )
            if not identity:
                raise ValueError("Declare an identity column in dataset.json")
            return DatasetSchema(
                index_file=name,
                id_column=identity,
                label_column="label" if "label" in frame.columns else None,
                group_column=next(
                    (c for c in ["batch_id", "lot", "group"] if c in frame.columns), None
                ),
                split_column="split" if "split" in frame.columns else None,
            )
    raise ValueError("Dataset directory needs dataset.json or a samples index")


def index_frame(root: Path, schema: DatasetSchema) -> pl.DataFrame:
    frame = read_table(safe_path(root, schema.index_file))
    columns = [
        schema.id_column,
        schema.label_column,
        schema.group_column,
        schema.split_column,
        schema.asset_column,
        schema.recipe_column,
        schema.control_column,
    ]
    if {c for c in columns if c} - set(frame.columns):
        raise ValueError("Declared dataset role column is missing")
    ids = frame[schema.id_column]
    if ids.null_count() or ids.n_unique() != frame.height:
        raise ValueError("Dataset identities must be unique and non-null")
    if schema.asset_column:
        for name in frame[schema.asset_column].drop_nulls():
            if (
                not isinstance(name, str)
                or not name
                or name == "."
                or "\\" in name
                or Path(name).is_absolute()
                or ".." in Path(name).parts
            ):
                raise ValueError("Asset paths must be relative to their dataset")
    return frame.with_columns(pl.col(schema.id_column).cast(pl.String))


def canonical_index(root: Path, schema: DatasetSchema) -> pl.DataFrame:
    frame = index_frame(root, schema)
    expressions = [pl.col(schema.id_column).alias("run_id")]
    mapping = {
        "label": schema.label_column,
        "batch_id": schema.group_column,
        "split": schema.split_column,
        "trace_path": schema.asset_column,
        "recipe_id": schema.recipe_column,
        "is_control": schema.control_column,
    }
    for target, source in mapping.items():
        if source:
            expression = pl.col(source)
            if target in {"label", "batch_id"}:
                expression = expression.cast(pl.String)
            if target == "split":
                expression = (
                    pl.when(expression.is_in(schema.train_values))
                    .then(pl.lit("train"))
                    .when(expression.is_in(schema.dev_values))
                    .then(pl.lit("dev"))
                    .otherwise(expression)
                )
            expressions.append(expression.alias(target))
    if not schema.control_column:
        expressions.append(pl.lit(False).alias("is_control"))
    return frame.select(expressions)


def discovery_copy(
    source: Path, target: Path, schema: DatasetSchema | None = None
) -> DatasetSchema:
    """Copy supplied local data; held-out entries and unrelated private files are excluded."""
    source = source.resolve()
    if source.is_dir() and any(p.is_symlink() for p in source.rglob("*")):
        raise ValueError("Dataset imports cannot contain symbolic links")
    target.mkdir(parents=True, exist_ok=True)
    if source.is_file():
        if source.suffix.lower() in {".csv", ".parquet", ".jsonl", ".ndjson", ".json"}:
            frame = read_table(source)
            if schema is None:
                identity = next(
                    (c for c in ["sample_id", "object_id", "run_id", "id"] if c in frame.columns),
                    None,
                )
                if not identity:
                    frame = frame.with_row_index("sample_id").with_columns(
                        pl.col("sample_id").cast(pl.String)
                    )
                    identity = "sample_id"
                schema = DatasetSchema(
                    id_column=identity,
                    label_column="label" if "label" in frame.columns else None,
                    group_column=next(
                        (c for c in ["batch_id", "lot", "group"] if c in frame.columns), None
                    ),
                    split_column="split" if "split" in frame.columns else None,
                )
            schema = schema.model_copy(update={"index_file": "samples.parquet"})
            if schema.split_column:
                frame = frame.filter(~pl.col(schema.split_column).is_in(schema.excluded_splits))
            frame.write_parquet(target / "samples.parquet")
        else:
            modality = (
                "image"
                if source.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp", ".gif"}
                else "text"
            )
            schema = schema or DatasetSchema(modality=modality, asset_column="asset_path")
            (target / "assets").mkdir()
            shutil.copyfile(source, target / "assets" / source.name)
            (target / schema.index_file).parent.mkdir(parents=True, exist_ok=True)
            pl.DataFrame(
                {"sample_id": [source.stem], "asset_path": ["assets/" + source.name]}
            ).write_parquet(target / schema.index_file)
    else:
        try:
            schema = schema or detect_schema(source)
        except ValueError:
            if any(
                (source / n).exists()
                for n in [
                    "dataset.json",
                    "runs.parquet",
                    "samples.parquet",
                    "samples.csv",
                    "samples.jsonl",
                ]
            ):
                raise
            files = [
                p
                for p in sorted(source.iterdir())
                if p.is_file()
                and p.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp", ".txt", ".md"}
            ]
            if not files:
                raise
            types = {
                "image" if p.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"} else "text"
                for p in files
            }
            schema = DatasetSchema(
                modality=next(iter(types)) if len(types) == 1 else "mixed",
                asset_column="asset_path",
            )
            (target / "assets").mkdir()
            for p in files:
                shutil.copyfile(p, target / "assets" / p.name)
            pl.DataFrame(
                {
                    "sample_id": [p.name for p in files],
                    "asset_path": ["assets/" + p.name for p in files],
                }
            ).write_parquet(target / schema.index_file)
        else:
            frame = index_frame(source, schema)
            if schema.split_column:
                frame = frame.filter(~pl.col(schema.split_column).is_in(schema.excluded_splits))
            original_index = schema.index_file
            if not original_index.endswith((".parquet", ".csv")):
                schema = schema.model_copy(update={"index_file": "samples.parquet"})
            (target / schema.index_file).parent.mkdir(parents=True, exist_ok=True)
            frame.write_parquet(target / schema.index_file) if schema.index_file.endswith(
                ".parquet"
            ) else frame.write_csv(target / schema.index_file)
            ids = frame[schema.id_column]
            if schema.asset_column:
                for name in frame[schema.asset_column].drop_nulls():
                    asset = safe_path(source, name, directory=True)
                    dest = target / name
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    if asset.is_dir():
                        shutil.copytree(asset, dest)
                    else:
                        shutil.copyfile(asset, dest)
            if schema.steps_file:
                steps = read_table(safe_path(source, schema.steps_file))
                (target / schema.steps_file).parent.mkdir(parents=True, exist_ok=True)
                id_column = schema.id_column if schema.id_column in steps.columns else "run_id"
                steps.filter(pl.col(id_column).cast(pl.String).is_in(ids.implode())).write_parquet(
                    target / schema.steps_file
                )
            for name in ["sensors.json", "catalog.json", "recipe.json", "case.json"]:
                if (source / name).is_file():
                    shutil.copyfile(source / name, target / name)
            if schema.index_file == "runs.parquet":
                for name in ["features.parquet", "control_features.parquet", "outcomes.parquet"]:
                    if (source / name).is_file():
                        data = pl.read_parquet(source / name).filter(pl.col("run_id").is_in(ids))
                        data.write_parquet(target / name)
    (target / "dataset.json").write_text(
        json.dumps({"schema": schema.model_dump()}, ensure_ascii=False, indent=2)
    )
    index_frame(target, schema)
    return schema
