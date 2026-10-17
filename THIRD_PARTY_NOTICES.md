# Third-party components

This repository contains original application and documentation code under the MIT license. Dependencies retain their own licenses; no vendor checkout, node_modules or third-party package source is bundled.

- [NOOA / NVIDIA NeMo Labs OO Agents](https://github.com/NVIDIA-NeMo/labs-OO-Agents), pinned in pyproject.toml and uv.lock. Upstream Apache-2.0; native strategies, context, storage, channels and admission mechanisms are attributed to that project.
- Python scientific stack: NumPy, pandas, Polars, scikit-learn, PyArrow, Matplotlib and transitive dependencies. See the locked package distributions for their respective licenses.
- FastAPI, Uvicorn, Pydantic, httpx and psutil provide service/runtime infrastructure.
- Vue, Vite, TypeScript, marked, DOMPurify and lucide provide the frontend.
- Sphinx, Furo, MyST Parser, sphinxcontrib-mermaid and Mermaid CLI build the documentation.

Lam Research and Applied Materials product names identify public reference materials only. This project is not affiliated with those manufacturers and does not include their proprietary recipes, source code, manuals or calibrated equipment parameters. Literature references are methodological attribution, not a claim of reproducing those research systems.
