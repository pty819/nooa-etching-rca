"""Dataset-aware arbitrary Python operators with per-sample and per-fold isolation."""

from __future__ import annotations

import asyncio
import json
import shutil
import uuid
from pathlib import Path

import polars as pl

from .dataset_adapter import index_frame, read_table, safe_path
from .datasets import file_hash
from .io_utils import write_json


class FeatureExecutor:
    def __init__(self, lab):
        self.lab = lab
        self.gate = asyncio.Semaphore(4)

    def _columns(self, schema):
        return {
            c
            for c in [
                schema.id_column,
                schema.label_column,
                schema.group_column,
                schema.split_column,
                schema.asset_column,
                schema.recipe_column,
                schema.control_column,
                "label",
                "run_id",
                "sample_id",
                "split",
            ]
            if c
        }

    def validate(self, study, spec):
        schema = self.lab.data_schema(study)
        root = self.lab.input_path(study)
        index = index_frame(root, schema)
        if index.is_empty():
            raise ValueError("No samples available for a formula")
        if schema.modality in {"image", "text", "mixed"}:
            return
        if schema.modality == "timeseries":
            frame = read_table(safe_path(root, index[schema.asset_column][0]))
        else:
            frame = index
        if set(spec["sensors"]) - (set(frame.columns) - self._columns(schema)):
            raise ValueError("Unknown or protected sensor/feature column")
        if spec["steps"]:
            if not schema.steps_file:
                raise ValueError("This dataset has no declared step boundaries")
            steps = read_table(root / schema.steps_file)
            if set(spec["steps"]) - set(steps[schema.step_column or "step_id"]):
                raise ValueError("Unknown step")

    def sample(self, root, schema, index_row, spec, directory, token):
        """Materialize only the declared values/assets belonging to ONE object."""
        steps = pl.DataFrame(
            schema={"step_id": pl.Int64, "start_s": pl.Float64, "end_s": pl.Float64}
        )
        if schema.modality in {"image", "text", "mixed"}:
            source = safe_path(root, index_row[schema.asset_column], directory=True)
            filename = token + source.suffix if source.is_file() else token
            target = directory / filename
            if source.is_dir():
                shutil.copytree(source, target)
            else:
                shutil.copyfile(source, target)
            kind = "asset"
        else:
            frame = (
                read_table(root / index_row[schema.asset_column])
                if schema.modality == "timeseries"
                else pl.DataFrame([index_row])
            )
            sensors = spec["sensors"] or [
                c
                for c in frame.columns
                if c not in self._columns(schema)
                and c not in {schema.time_column, schema.step_column}
            ]
            columns = list(sensors)
            for name in [schema.time_column, schema.step_column]:
                if name and name in frame.columns and name not in columns:
                    columns.append(name)
            if schema.step_column and spec["steps"]:
                frame = frame.filter(pl.col(schema.step_column).is_in(spec["steps"]))
            frame = frame.select(columns)
            if schema.steps_file:
                steps = read_table(root / schema.steps_file)
                identity = schema.id_column if schema.id_column in steps.columns else "run_id"
                steps = steps.filter(
                    pl.col(identity).cast(pl.String) == str(index_row[schema.id_column])
                )
                if spec["steps"]:
                    steps = steps.filter(
                        pl.col(schema.step_column or "step_id").is_in(spec["steps"])
                    )
                steps = steps.select(
                    pl.col(schema.step_column or "step_id").alias("step_id"), "start_s", "end_s"
                )
            filename = token + ".parquet"
            frame.write_parquet(directory / filename)
            kind = "frame"
        steps_name = token + "-steps.parquet"
        steps.write_parquet(directory / steps_name)
        return {"kind": kind, "file": filename, "steps": steps_name}

    def job(self, study, operation, spec):
        folder = self.lab.new_job(study["id"], operation)
        shutil.copyfile(
            Path(__file__).with_name("feature_runner.py"), folder / "workspace/feature_runner.py"
        )
        canonical = self.lab.base / study["id"] / "formulas" / spec["ref"]
        if (
            file_hash(canonical / "source.py") != spec["sha256"]
            or file_hash(canonical / "spec.json") != spec["spec_sha256"]
        ):
            raise ValueError("Canonical formula snapshot changed")
        shutil.copyfile(canonical / "source.py", folder / "input/formula.py")
        return folder

    async def value(self, study, spec, root, schema, row, state=None):
        async with self.gate:
            folder = self.job(study, "sample", spec)
            item = self.sample(root, schema, row, spec, folder / "input", uuid.uuid4().hex)
            if state:
                shutil.copyfile(state, folder / "input/state.joblib")
            write_json(
                folder / "input/job.json",
                {
                    "operation": "transform" if state else "extract",
                    "params": spec["params"],
                    "sample": item,
                },
            )
            try:
                await self.lab.worker(
                    folder, "import runpy\nrunpy.run_path('feature_runner.py')['run']()"
                )
                return json.loads((folder / "workspace/value.json").read_text())["value"]
            finally:
                shutil.rmtree(folder / "input")

    async def fit(self, study, spec, root, schema, rows, labels):
        folder = self.job(study, "fit", spec)
        samples = {}
        targets = {}
        for row in rows:
            token = uuid.uuid4().hex
            samples[token] = self.sample(root, schema, row, spec, folder / "input", token)
            targets[token] = labels[str(row[schema.id_column])]
        write_json(
            folder / "input/job.json",
            {"operation": "fit", "params": spec["params"], "samples": samples, "labels": targets},
        )
        try:
            await self.lab.worker(
                folder, "import runpy\nrunpy.run_path('feature_runner.py')['run']()"
            )
            return folder / "workspace/state.joblib"
        finally:
            shutil.rmtree(folder / "input")

    async def compute(self, study, item, replay=False):
        root = self.lab.input_path(study)
        schema = self.lab.data_schema(study)
        metadata = self.lab.runs(study)
        ids = study["plan"]["train"] + study["plan"]["dev"]
        metadata = metadata.filter(pl.col("run_id").is_in(ids)).sort("run_id")
        source_index = index_frame(root, schema)
        rowmap = {str(r[schema.id_column]): r for r in source_index.iter_rows(named=True)}
        labels = dict(metadata.select("run_id", "label").iter_rows())
        formulas = [
            next(f for f in study["formulas"] if f["ref"] == ref) for ref in item["formulas"]
        ]
        learned = any(f.get("kind", "per_sample") == "learned" for f in formulas)
        folds = (
            [
                *study["plan"]["fold_manifest"],
                {"id": "dev", "train": study["plan"]["train"], "valid": study["plan"]["dev"]},
            ]
            if learned
            else [{"id": "all", "train": study["plan"]["train"], "valid": study["plan"]["dev"]}]
        )
        outputs = []
        for fold in folds:
            chosen = sorted(set(fold["train"] + fold["valid"]))
            frame = metadata.filter(pl.col("run_id").is_in(chosen)).select(
                "run_id", "label", "batch_id", "split"
            )
            for spec in formulas:
                cache_fold = fold["id"] if spec.get("kind", "per_sample") == "learned" else "all"
                cache = (
                    self.lab.base
                    / study["id"]
                    / "features"
                    / spec["ref"]
                    / (cache_fold + ".parquet")
                )
                hashfile = cache.with_suffix(".sha256")
                state_cache = cache.with_suffix(".state.joblib")
                state_hash = cache.with_suffix(".state.sha256")
                if (
                    not replay
                    and cache.exists()
                    and hashfile.exists()
                    and file_hash(cache) == hashfile.read_text()
                    and (
                        spec.get("kind", "per_sample") != "learned"
                        or (
                            state_cache.exists()
                            and state_hash.exists()
                            and file_hash(state_cache) == state_hash.read_text()
                        )
                    )
                ):
                    feature = pl.read_parquet(cache)
                else:
                    sample_ids = sorted(ids if cache_fold == "all" else chosen)
                    state = None
                    if spec.get("kind", "per_sample") == "learned":
                        state = await self.fit(
                            study, spec, root, schema, [rowmap[i] for i in fold["train"]], labels
                        )
                    tasks = [
                        asyncio.create_task(self.value(study, spec, root, schema, rowmap[i], state))
                        for i in sample_ids
                    ]
                    try:
                        values = await asyncio.gather(*tasks)
                    except BaseException:
                        for task in tasks:
                            task.cancel()
                        await asyncio.gather(*tasks, return_exceptions=True)
                        raise
                    feature = pl.DataFrame(
                        {"run_id": sample_ids, spec["ref"]: values},
                        schema={"run_id": pl.String, spec["ref"]: pl.Float64},
                    )
                    if not replay:
                        cache.parent.mkdir(parents=True, exist_ok=True)
                        if state:
                            temporary_state = state_cache.with_name("." + uuid.uuid4().hex)
                            shutil.copyfile(state, temporary_state)
                            temporary_state.replace(state_cache)
                            state_hash.write_text(file_hash(state_cache))
                        temporary = cache.with_name("." + uuid.uuid4().hex + ".parquet")
                        feature.write_parquet(temporary)
                        temporary.replace(cache)
                        hashfile.write_text(file_hash(cache))
                frame = frame.join(feature, on="run_id", validate="1:1")
                training = frame.filter(pl.col("run_id").is_in(fold["train"]))
                if training[spec["ref"]].drop_nulls().is_empty():
                    raise ValueError("Formula has no observed training values")
            if learned:
                frame = frame.with_columns(pl.lit(fold["id"]).alias("fold"))
            outputs.append(frame.sort("run_id"))
        return pl.concat(outputs)
