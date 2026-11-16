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
uv run claimgraph run vitamin-d-depression --stage compare  # candidate pairs and block sizes
uv run claimgraph export stub vitamin-d-depression    # stub contradictions, no model
uv run claimgraph export build vitamin-d-depression --stub  # the page's export from a run
uv run claimgraph export publish vitamin-d-depression  # build, upload to the public release with
                                          # gh, pin its hash in site/export.lock.json; commit that
uv run claimgraph export fetch                         # the deploy: download the pinned export
uv run claimgraph eval normalization vitamin-d-depression  # score the recorded outcome proposal
uv run claimgraph eval normalization vitamin-d-depression --readme README.md  # README block
uv run claimgraph gold outcome-pairs sample vitamin-d-depression
                                          # draw the outcome pairs from the recorded proposal
uv run claimgraph gold outcome-pairs template vitamin-d-depression  # the pair file to label
uv run claimgraph gold outcome-pairs check vitamin-d-depression     # validate the pair labels
uv run claimgraph gold outcome-kinds template vitamin-d-depression  # every string, to label by kind
uv run claimgraph gold outcome-kinds review vitamin-d-depression    # label kinds on 127.0.0.1
uv run claimgraph gold outcome-kinds check vitamin-d-depression     # validate the kinds
uv run claimgraph gold outcome-kinds compare vitamin-d-depression   # kinds against entered pairs
uv run claimgraph gold record vitamin-d-depression     # check the gold recordings are current
uv run claimgraph gold record vitamin-d-depression --dry-run  # estimate re-recording them
uv run --env-file .env claimgraph gold record vitamin-d-depression --live
                                          # re-record gold extraction responses (costs money)
uv run pytest                             # tests
npm --prefix site ci                      # site install, from the lockfile
npm --prefix site run check               # site: export types current, typecheck, tests, build
npm --prefix site run dev                 # the page on a fixture: /?fixture=synthetic
npm --prefix site run e2e                 # browser checks in the installed Chrome
npm --prefix site run types               # regenerate the site's types after a schema change
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
- The normalization prompt is frozen too: a test pins its request key. Changing the prompt,
  schema, outcome rules or settings needs the owner's approval, a dry run and a new recording.
- The site is React and TypeScript (strict) on Vite, with D3 for geometry only. Its dependencies
  are pinned exactly; npm 10 cannot resolve the tree, so change them with `npx npm@11 install`.
  Its types come from `schema/export.schema.json`, which comes from `export.py`.
- Secrets come from the environment (see `.env.example`); `.env` is never committed.
- No absolute path is ever committed; the pre-commit hook and CI refuse any file containing a
  home directory path. Real-data exports and screenshots of real data go only under the
  gitignored `real-data/`: their quotes include text that is not openly licensed.
- The gitignored data, `runs/` and `local-data/`, sits under `CLAIMGRAPH_DATA` when it is set to
  an absolute path, so several checkouts share one copy; otherwise in the working directory.
- Commits: one small, reviewable change each, conventional-commit style, imperative subject of
  72 characters or fewer; the body says what changed and why.
