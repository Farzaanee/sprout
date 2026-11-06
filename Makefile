.PHONY: run ingest test lint hooks snapshot amend-push

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

fpush:
	git add .
	git commit --amend --no-edit
	git push -f

eval-rec:
	uv run python -m evals.run_evals --record