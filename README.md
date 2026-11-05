# Claimgraph

Ask "does vitamin D affect depression?" and you meet hundreds of studies that seem to disagree.
Claimgraph reads their PubMed abstracts, extracts each finding with the sentence it came from,
groups findings that measure the same outcome, and names the likely reason when two studies
disagree: different people, a different dose or comparator, a different measure, or a weaker
design. A static page shows the result.

Research navigation demo, not medical advice.

**Status:** early development. Nothing below is measured yet.

## How it works

1. **Retrieve** abstracts for a fixed PubMed query.
2. **Extract** findings with a model; every quote must match its abstract word for word.
3. **Normalize** outcome names through a mapping a person reviews.
4. **Compare** only findings on the same outcome that point different ways; a model names the
   likely reason.

Runs replay recorded model responses by default, so the pipeline and its tests run without an
API key. See [DESIGN.md](DESIGN.md) for the decisions behind each step.

## Quickstart

```
uv sync
cp .env.example .env    # add a contact email for NCBI
uv run --env-file .env claimgraph run vitamin-d-depression --stage retrieve
```

Retrieval writes `runs/vitamin-d-depression/`: the cached PubMed records, the parsed abstracts
and a manifest of what was found, kept and dropped. A rerun reads the cache and makes no
requests.

## Results

Generated from the evals once they exist.

## Tradeoffs and known failure modes

Abstracts only, one question at a time, and a human-reviewed outcome mapping: cheaper and more
checkable than full text, at the cost of what abstracts leave out. The failure modes the evals
track are listed in [DESIGN.md](DESIGN.md#failure-modes).

Known limitation: retracted papers are excluded using the retraction status in each cached
record, which is only as current as the last fetch. Published runs refresh the cache first.

## Outcomes

Placeholder until a real reader has used the page.

## Licence

MIT. Abstracts and quotes come from PubMed and remain under their publishers' terms.
