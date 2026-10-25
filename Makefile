.PHONY: run ingest test lint hooks snapshot

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

snapshot:
	cp data/sprout.db data/sprout_snapshot.db
