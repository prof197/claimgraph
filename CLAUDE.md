# Claimgraph

## Commands

```
uv sync                                   # install
uv run claimgraph --help                  # CLI
uv run pytest                             # tests
UPDATE_SNAPSHOTS=1 uv run pytest          # rewrite schema snapshots after an intended change
uv run ruff format . && uv run ruff check --fix .
```

Checks that must pass before every commit:

```
uv run ruff format --check . && uv run ruff check . && uv run pyright && uv run pytest
```

## Conventions

- Python 3.12, fully typed. Pyright strict on `src/`; ruff clean.
- Pydantic v2 models at every boundary and for all model output. `StrEnum` for closed sets,
  `X | None` unions, `pathlib`. Small, plain functions; no speculative abstraction.
- Direct SDK calls only; no LLM or agent frameworks.
- Comments are sparse and explain the domain (why), not the language (what).
- Tests make no network calls. PubMed XML and model responses come from recorded fixtures.
  Fixtures that are not real records are visibly synthetic.
- Replay is the default everywhere. Live model calls are opt-in (`--live`) and are run by hand.
- Secrets come from the environment (see `.env.example`); `.env` is never committed.
- Commits: one small, reviewable change each, conventional-commit style, imperative subject of
  72 characters or fewer; the body says what changed and why.
