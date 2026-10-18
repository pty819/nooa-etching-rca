"""Exercise real scientific algorithms and JIT inside the actual restricted worker."""

import asyncio
import json
import sys

import pytest

from factor_demo.research_workspace import PythonWorkspace


@pytest.mark.skipif(
    sys.platform not in {"darwin", "linux"}, reason="enforced execution backend required"
)
def test_scientific_stack_computes_and_caches_inside_restricted_workspace(tmp_path):
    run = tmp_path / "case"
    (run / "workspace/artifacts").mkdir(parents=True)
    (run / "input").mkdir()
    code = """
import json
from pathlib import Path
from importlib.metadata import version
import numpy as np
import polars as pl
import pandas as pd
from scipy.signal import find_peaks
from sklearn.decomposition import PCA
import statsmodels.api as sm
from aeon.distances import dtw_distance
from aeon.transformations.collection import Normalizer
from joblib import Parallel, delayed
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

x = np.arange(8, dtype=float)
params = sm.OLS(1.5 + 2.25*x, sm.add_constant(x)).fit(cov_type='HC3').params
ratio = PCA(n_components=1).fit(np.column_stack((x, 2*x))).explained_variance_ratio_[0]
peaks = find_peaks(np.array([0., 1., 0., 2., 0.]))[0]
a, b = np.array([0., 1., 2., 3.]), np.array([0., 1., 2., 4.])
distance = dtw_distance(a, b)
collection = np.stack((x, x[::-1]))[:, None, :]
normalized = Normalizer().fit_transform(collection)
squares = Parallel(n_jobs=2, prefer='threads')(delayed(float)(v*v) for v in x)
table = pl.from_pandas(pd.DataFrame({'x': x, 'y': 1.5+2.25*x}))
table.write_csv('artifacts/linear.csv')
plt.plot(x, table['y'].to_numpy())
plt.savefig('artifacts/linear.png')
plt.close()
result = {
  'versions': {p: version(p) for p in ['numpy','scipy','scikit-learn','aeon','statsmodels','polars','pandas','matplotlib']},
  'params': params.tolist(), 'pca_ratio': float(ratio), 'peaks': peaks.tolist(),
  'dtw': float(distance), 'dtw_identity': float(dtw_distance(a, a)),
  'normalized_shape': list(normalized.shape),
  'max_normalized_mean': float(np.max(np.abs(normalized.mean(axis=-1)))),
  'normalized_std': normalized.std(axis=-1).tolist(), 'squares': squares
}
Path('artifacts/analysis_stack.json').write_text(json.dumps(result))
print('SCIENTIFIC_STACK_VERIFIED')
"""

    async def exercise():
        worker = PythonWorkspace(run)
        try:
            result = await worker.execute(code, timeout_s=180)
            assert result["ok"], result["output"]
            assert "SCIENTIFIC_STACK_VERIFIED" in result["output"]
        finally:
            await worker.close()

    asyncio.run(exercise())
    result = json.loads((run / "workspace/artifacts/analysis_stack.json").read_text())
    assert len(result["versions"]) == 8
    assert result["params"] == pytest.approx([1.5, 2.25])
    assert result["pca_ratio"] == pytest.approx(1.0)
    assert result["peaks"] == [1, 3]
    assert result["dtw"] == pytest.approx(1.0) and result["dtw_identity"] == 0
    assert result["normalized_shape"] == [2, 1, 8]
    assert result["max_normalized_mean"] < 1e-12
    assert all(v == pytest.approx(1) for row in result["normalized_std"] for v in row)
    assert result["squares"] == [float(x * x) for x in range(8)]
    assert (run / "workspace/artifacts/linear.png").read_bytes().startswith(b"\x89PNG")
    assert list((run / "workspace/.cache/numba").rglob("*.nbc"))
