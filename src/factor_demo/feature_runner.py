"""Standalone user-operator runner: a fit sees training only; a transform sees one sample."""

import inspect
import json
import sys
import types
from pathlib import Path

import joblib
import numpy as np
import polars as pl


def run():
    config = json.loads(Path("../input/job.json").read_text())
    module = types.ModuleType("registered_formula")
    module.__file__ = str(Path("../input/formula.py").resolve())
    sys.modules[module.__name__] = module
    namespace = module.__dict__
    exec(compile(Path("../input/formula.py").read_text(), "formula.py", "exec"), namespace)  # noqa: S102 - sandboxed registered operator

    def payload(item):
        path = Path("../input") / item["file"]
        return pl.read_parquet(path) if item["kind"] == "frame" else path

    def steps(item):
        return pl.read_parquet(Path("../input") / item["steps"])

    if config["operation"] == "fit":
        samples = {k: payload(v) for k, v in config["samples"].items()}
        timings = {k: steps(v) for k, v in config["samples"].items()}
        args = [samples, timings, config["params"]]
        if len(inspect.signature(namespace["fit"]).parameters) >= 4:
            args.append(config["labels"])
        state = namespace["fit"](*args)
        import cloudpickle

        with Path("state.joblib").open("wb") as output:
            cloudpickle.dump(state, output)
    else:
        item = config["sample"]
        sample = payload(item)
        timing = steps(item)
        if config["operation"] == "extract":
            value = namespace["extract"](sample, timing, config["params"])
        else:
            value = namespace["transform"](
                joblib.load("../input/state.joblib"), sample, timing, config["params"]
            )
        if value is not None:
            array = np.asarray(value)
            if array.ndim != 0 or array.dtype.kind not in "iuf" or not np.isfinite(array):
                raise ValueError("Operator must return one finite numeric scalar or None")
            value = float(value)
        Path("value.json").write_text(json.dumps({"value": value}, allow_nan=False))
