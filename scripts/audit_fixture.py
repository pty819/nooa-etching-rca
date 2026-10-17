"""Host-side, post-finalization inspection of this synthetic fixture.

This diagnostic uses private truth and holdout after the run has been frozen.
It must not be presented as a new independent model-selection result.
"""

from __future__ import annotations

import argparse
import json
from itertools import combinations
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import ks_2samp
from sklearn.feature_selection import f_classif

from factor_demo.factors import Factor
from factor_demo.research import score_candidate
from factor_demo.synthetic import write_json


def ks(a, b):
    a, b = np.asarray(a), np.asarray(b)
    return float(ks_2samp(a[np.isfinite(a)], b[np.isfinite(b)], method="asymp").statistic)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", type=Path, default=Path("data/demo"))
    parser.add_argument("--run", type=Path, default=Path("runs/glm-first"))
    args = parser.parse_args()
    if not (args.run / "frozen.json").exists():
        parser.error("Audit requires an already frozen run")
    frames = {s: pd.read_parquet(args.data / f"{s}.parquet") for s in ["train", "dev", "test"]}
    truth = json.loads((args.data / "truth.private.json").read_text())
    final = json.loads((args.run / "final.json").read_text())
    columns = list(frames["train"].columns[5:])
    allowed = set(columns)
    known = {truth["raw_signal"], truth["unstable_signal"], *truth["difference"]["columns"]}
    summary = pd.DataFrame(index=columns)
    for name, df in frames.items():
        x = df[columns].to_numpy(dtype=float)
        x = np.where(np.isnan(x), np.nanmedian(x, axis=0), x)
        f, p = f_classif(x, df.label)
        summary[f"{name}_F"] = f
        summary[f"{name}_p"] = p
    for a, b in combinations(frames, 2):
        summary[f"ks_{a}_{b}"] = [ks(frames[a][c], frames[b][c]) for c in columns]
    nulls = summary.loc[~summary.index.isin(known)]
    # A concrete null example: stable marginal distributions and small class mean separation.
    candidates = nulls[(nulls.filter(like="ks_") < 0.10).all(axis=1)
                       & (nulls.filter(like="_p") > 0.1).all(axis=1)]
    null_example = candidates.index[0]
    role_columns = {"stable_raw": truth["raw_signal"], "unstable": truth["unstable_signal"],
                    "paired_before": truth["difference"]["columns"][1], "null_example": null_example}
    detail = {}
    for role, col in role_columns.items():
        row = {"column": col, "marginal_ks": {k: float(v) for k, v in summary.loc[col].items() if k.startswith("ks_")},
               "per_split": {}}
        for name, df in frames.items():
            row["per_split"][name] = {"F": float(summary.loc[col, f"{name}_F"]),
                "class_means": df.groupby("label")[col].mean().to_dict(),
                "max_class_pair_ks": max(ks(df.loc[df.label == a, col], df.loc[df.label == b, col])
                                         for a, b in combinations(sorted(df.label.unique()), 2))}
        detail[role] = row
    candidates_to_score = {
        "stable_raw": [Factor(name="raw", operation="raw", columns=[truth["raw_signal"]])],
        "injected_difference": [Factor(name="delta", **truth["difference"])],
        "unstable_raw": [Factor(name="unstable", operation="raw", columns=[truth["unstable_signal"]])],
        "unstable_squared": [Factor(name="squared", operation="product", columns=[truth["unstable_signal"]] * 2)],
        "null_example": [Factor(name="null", operation="raw", columns=[null_example])],
    }
    scores = {}
    for name, specs in candidates_to_score.items():
        scores[name] = {}
        for split in ["dev", "test"]:
            m = score_candidate(frames["train"], frames[split], specs, allowed)
            scores[name][split] = {k: v for k, v in m.items() if k in ["balanced_accuracy", "train_balanced_accuracy"]}
    result = {"status": "posthoc diagnostic, not independent candidate selection",
        "column_count": len(columns), "generator_label_independent_columns_excluding_paired_before": len(nulls),
        "unadjusted_null_p_below_005": {s: int((nulls[f"{s}_p"] < .05).sum()) for s in frames},
        "null_train_and_dev_p_below_005": int(((nulls.train_p < .05) & (nulls.dev_p < .05)).sum()),
        "marginal_ks_below_01_all_splits_count": int((summary.filter(like="ks_") < .10).all(axis=1).sum()),
        "heuristic_note": "KS<0.10 is a descriptive effect-size cutoff, not proof of equivalence; nominal p values are unadjusted.",
        "examples": detail, "posthoc_single_factor_scores": scores,
        "frozen_run_score_unchanged": final["holdout"]["balanced_accuracy"]}
    out = Path("docs/fixture-audit.json")
    write_json(out, result)
    summary.to_csv("docs/fixture-feature-audit.csv", index_label="feature")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
