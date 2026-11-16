"""The classified pairs file the export reads its contradictions from, and its stub.

The stub classifies a fixed share of the real candidate pairs without a model, so the page can
be built against the real extraction before classification runs. Its verdicts are arbitrary,
its depth is None, and the hosted build refuses it.
"""

import hashlib
from pathlib import Path

from claimgraph.compare import Candidates
from claimgraph.models import ClassifiedPairs, PairClassification, Reason, Verdict
from claimgraph.retrieve import write_atomic

STUB_FILE = "contradictions.stub.json"
FILE = "contradictions.json"
STUB_DETAIL = "stub: not a model output"
STUB_RATIONALE = "Stub classification for building the page; no model classified this pair."
# One pair in five is stubbed genuine and one in five apparent; the rest stay unclassified.
_SHARE = 5


def read(path: Path) -> ClassifiedPairs:
    return ClassifiedPairs.model_validate_json(path.read_text())


def write(path: Path, classified: ClassifiedPairs) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    write_atomic(path, (classified.model_dump_json(indent=1) + "\n").encode())


def stub(found: Candidates) -> ClassifiedPairs:
    """The same verdicts for the same pairs on every run, spread over every reason."""
    reasons = list(Reason)
    classified: list[PairClassification] = []
    for pair in found.pairs:
        digest = int.from_bytes(hashlib.sha256(pair.pair_id.encode()).digest()[:8], "big")
        slot, pick = digest % _SHARE, digest // _SHARE
        if slot > 1:
            continue
        genuine = slot == 0
        # An apparent pair is dissolved by a known difference, so it is never unclear.
        choices = reasons if genuine else [r for r in reasons if r is not Reason.UNCLEAR]
        classified.append(
            PairClassification(
                pair_id=pair.pair_id,
                verdict=Verdict.GENUINE if genuine else Verdict.APPARENT,
                reason=choices[pick % len(choices)],
                also_differs=[],
                detail=STUB_DETAIL,
                rationale=STUB_RATIONALE,
            )
        )
    return ClassifiedPairs(depth=None, candidates=len(found.pairs), classifications=classified)
