"""Builds one question's export from a run: the summary strips, the graph and the method."""

from pathlib import Path

from claimgraph.config import QuestionConfig
from claimgraph.export import Export
from claimgraph.models import ClassifiedPairs
from claimgraph.normalize import OutcomeMapping


def build_export(
    config: QuestionConfig,
    run_dir: Path,
    mapping: OutcomeMapping,
    classified: ClassifiedPairs,
    gold_dir: Path,
) -> Export:
    """The export for `config` from the run in `run_dir`, with its contradictions from
    `classified`. A dimension counts as evaluated when the gold labels in `gold_dir` cover it.
    The same inputs always give the same export, byte for byte."""
    raise NotImplementedError
