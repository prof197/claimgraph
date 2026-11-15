# Claimgraph

## Commands

```
uv sync                                   # install
uv run claimgraph --help                  # CLI
uv run --env-file .env claimgraph run vitamin-d-depression --stage retrieve
                                          # fetch and parse abstracts into runs/
uv run --env-file .env claimgraph gold sample vitamin-d-depression
                                          # draw the gold and dev eval samples
uv run claimgraph gold template vitamin-d-depression   # label files to fill in by hand
uv run claimgraph gold check vitamin-d-depression      # validate the labels
uv run claimgraph gold review vitamin-d-depression     # local labeling page on 127.0.0.1
uv run claimgraph gold trim vitamin-d-depression       # keep only the labeled gold records
uv run claimgraph gold relabel vitamin-d-depression    # blind second pass: 5 blank templates
uv run claimgraph gold agreement vitamin-d-depression --readme README.md  # agreement table
uv run claimgraph run vitamin-d-depression --stage extract --dev --dry-run
                                          # estimate a dev extraction; sends nothing
uv run --env-file .env claimgraph run vitamin-d-depression --stage extract --dev --live
                                          # extract the dev sample through batches (costs money)
uv run claimgraph run vitamin-d-depression --stage extract --limit 200 --dry-run
                                          # estimate a first stage of the corpus; sends nothing
uv run claimgraph run vitamin-d-depression --stage normalize --dry-run
                                          # estimate the outcome proposal; sends nothing
uv run --env-file .env claimgraph run vitamin-d-depression --stage normalize --live
                                          # propose the outcome mapping, unreviewed (costs money)
uv run claimgraph mapping check vitamin-d-depression   # every outcome reviewed, every string mapped
uv run claimgraph gold outcome-pairs sample vitamin-d-depression
                                          # draw the outcome pairs from the recorded proposal
uv run claimgraph gold record vitamin-d-depression     # check the gold recordings are current
uv run claimgraph gold record vitamin-d-depression --dry-run  # estimate re-recording them
uv run --env-file .env claimgraph gold record vitamin-d-depression --live
                                          # re-record gold extraction responses (costs money)
uv run pytest                             # tests
UPDATE_SNAPSHOTS=1 uv run pytest          # rewrite schema snapshots after an intended change
uv run ruff format . && uv run ruff check --fix .
```

Checks that must pass before every commit:

```
uv run ruff format --check . && uv run ruff check . && uv run pyright && uv run pytest
```

`.githooks/pre-commit` runs them, with the site's checks once `site/` exists, and blocks the
commit if any fails. Enable it once per clone with `git config core.hooksPath .githooks`.

## Conventions

- Python 3.12, fully typed. Pyright strict on `src/`; ruff clean.
- Pydantic v2 models at every boundary and for all model output. `StrEnum` for closed sets,
  `X | None` unions, `pathlib`. Small, plain functions; no speculative abstraction.
- Direct SDK calls only; no LLM or agent frameworks.
- Comments are sparse and explain the domain (why), not the language (what).
- Tests make no network calls. PubMed XML and model responses come from recorded fixtures.
  Fixtures that are not real records are visibly synthetic.
- Replay is the default everywhere. Live model calls are opt-in (`--live`) and are run by hand.
- A recording is keyed by its whole request, so any change to the extraction prompt, schema or
  model settings leaves the gold recordings in `replay/` behind, and `gold record` exits 1.
  Re-record them with `gold record --live`, which runs Sonnet 5.5, the extraction model, and
  again with `--model haiku` for the comparison row, only
  after the owner approves a dry-run estimate, and commit the new recordings with the change.
- Secrets come from the environment (see `.env.example`); `.env` is never committed.
- Commits: one small, reviewable change each, conventional-commit style, imperative subject of
  72 characters or fewer; the body says what changed and why.
