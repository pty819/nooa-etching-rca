.PHONY: docs docs-clean docs-check test
docs:
	UV_PROJECT_ENVIRONMENT=.venv-docs uv sync --frozen --only-group docs --no-install-project
	UV_PROJECT_ENVIRONMENT=.venv-docs uv run --no-sync sphinx-build -n -W --keep-going -b html docs/sphinx docs/_build/html
docs-clean:
	rm -rf docs/_build
docs-check: docs
	UV_PROJECT_ENVIRONMENT=.venv-docs uv run --no-sync python scripts/check_docs.py
test:
	uv run pytest -q
	uv run ruff check src tests scripts
