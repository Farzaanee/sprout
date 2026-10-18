.PHONY: run ingest test lint hooks

run:
	uv run streamlit run streamlit_app.py

ingest:
	uv run python -m sprout.ingest

test:
	uv run pytest

lint:
	uv run ruff check

hooks:
	git config core.hooksPath .githooks
