KEEP_BRANCH ?= $(shell git branch --show-current)

.PHONY: run ingest test lint hooks snapshot publish-snapshot amend-push del-branch

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
	uv run python scripts/make_snapshot.py

# Run after an enrichment pass (e.g. `make ingest` or an orchestrator run) to publish the
# refreshed snapshot: rebuilds data/sprout_snapshot.db and pushes it, which triggers the
# `snapshot` CI workflow to verify it.
publish-snapshot: snapshot
	git add data/sprout_snapshot.db
	git diff --cached --quiet data/sprout_snapshot.db || git commit -m "snapshot: refresh after enrichment"
	git push

fpush:
	git add .
	git commit --amend --no-edit
	git push -f

eval-rec:
	uv run python -m evals.run_evals --record

eval-all:
	uv run python -m evals.run_evals --split all --injection --check-urls --calibrate --record

del-branch:
	@git switch "$(KEEP_BRANCH)" && git for-each-ref --format='%(refname:short)' refs/heads | grep -vFx "$(KEEP_BRANCH)" | while IFS= read -r branch; do git branch -D "$$branch"; done
