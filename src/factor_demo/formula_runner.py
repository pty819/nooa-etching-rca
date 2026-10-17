"""Standalone trusted runner copied into an isolated scientific worker.

Extraction executes the registered source with label-free, declared raw inputs.
Evaluation never executes that source; it fits preprocessing inside each fold.
"""

import json
from pathlib import Path

import numpy as np
import polars as pl


def extract():
    spec = json.loads(Path("../input/job.json").read_text())
    namespace = {}
    exec(compile(Path("../input/formula.py").read_text(), "formula.py", "exec"), namespace)  # noqa: S102 - registered scientific source in isolated worker
    records = []
    for item in spec["runs"]:
        value = namespace["extract"](
            pl.read_parquet(Path("../input") / item["trace"]),
            pl.read_parquet(Path("../input") / item["steps"]),
            json.loads(json.dumps(spec["params"])),
        )
        if value is not None:
            array = np.asarray(value)
            if array.ndim != 0 or array.dtype.kind not in "iuf":
                raise ValueError("extract must return one numeric scalar or None per run")
            value = float(value)
            if not np.isfinite(value):
                raise ValueError("Nonfinite formula result; use None for an explicit missing value")
        records.append({"run_id": item["id"], "value": value})
    pl.DataFrame(records, schema={"run_id": pl.String, "value": pl.Float64}).write_parquet(
        "features.parquet"
    )


def pipeline(name, seed, params):
    from sklearn.ensemble import (
        ExtraTreesClassifier,
        HistGradientBoostingClassifier,
        RandomForestClassifier,
    )
    from sklearn.impute import SimpleImputer
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import Pipeline
    from sklearn.preprocessing import StandardScaler

    classes = {
        "logistic": LogisticRegression,
        "random_forest": RandomForestClassifier,
        "extra_trees": ExtraTreesClassifier,
        "hist_gradient_boosting": HistGradientBoostingClassifier,
    }
    steps = [("impute", SimpleImputer(strategy="median", keep_empty_features=True))]
    if name == "logistic":
        steps.append(("scale", StandardScaler()))
    steps.append(("model", classes[name](random_state=seed, **params)))
    return Pipeline(steps)


def evaluate():
    import joblib

    spec = json.loads(Path("../input/job.json").read_text())
    frame = pl.read_parquet("../input/matrix.parquet")
    ids = frame["run_id"].to_list()
    positions = {identity: i for i, identity in enumerate(ids)}
    x = frame.select(spec["features"]).to_numpy()
    y = frame["label"].to_numpy()
    rows = []
    for fold in [*spec["folds"], {"id": "dev", "train": spec["train"], "valid": spec["dev"]}]:
        train = [positions[v] for v in fold["train"]]
        valid = [positions[v] for v in fold["valid"]]
        model = pipeline(spec["model"], spec["seed"], spec["params"])
        model.fit(x[train], y[train])
        pred = model.predict(x[valid])
        for i, prediction in zip(valid, pred, strict=True):
            rows.append({"run_id": ids[i], "fold": fold["id"], "prediction": str(prediction)})
        if fold["id"] == "dev":
            joblib.dump(model, "model.joblib")
            fitted = model.named_steps["model"]
            details = {
                "classes": fitted.classes_.tolist(),
                "features": spec["features"],
                "params": fitted.get_params(),
            }
            if hasattr(fitted, "coef_"):
                details.update(
                    coefficients=fitted.coef_.tolist(),
                    intercept=fitted.intercept_.tolist(),
                    scaling_mean=model.named_steps["scale"].mean_.tolist(),
                    scaling_scale=model.named_steps["scale"].scale_.tolist(),
                )
            if hasattr(fitted, "feature_importances_"):
                details["feature_importances"] = fitted.feature_importances_.tolist()
            details["imputation_medians"] = [
                float(v) if np.isfinite(v) else None
                for v in model.named_steps["impute"].statistics_
            ]
            Path("model_details.json").write_text(json.dumps(details, allow_nan=False))
    pl.DataFrame(rows).write_parquet("predictions.parquet")
