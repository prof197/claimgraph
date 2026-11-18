"""The export: everything the page shows for one question, in one JSON file.

The page reads nothing else. `schema/export.schema.json` is generated from these models and the
site's TypeScript types from that schema, so a change here reaches the page only through both.
The graph is NetworkX's node-link form: nodes and edges carry data, never coordinates, since the
page lays the graph out itself.
"""

import json
import math
from collections import Counter
from datetime import date
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Any, Literal, Self

from pydantic import Field, NonNegativeFloat, NonNegativeInt, PositiveInt, model_validator

from claimgraph.compare import Direction
from claimgraph.models import (
    Arm,
    Design,
    DimensionValue,
    FindingId,
    FoundWords,
    NonBlank,
    PairKind,
    PairSelection,
    Pmid,
    Reason,
    Record,
)

SCHEMA_VERSION = 1
SCHEMA_FILE = Path("schema/export.schema.json")
# Under this many counted studies an outcome is thin: counts only, no weighted bar.
THIN_BELOW = 5


class Dimension(Record):
    """A custom dimension from the question config, as the finding card shows it."""

    name: NonBlank
    label: NonBlank
    # What the finding card calls it, where the full label would wrap; None to use the label.
    short_label: NonBlank | None = None
    refines: Reason
    unit: NonBlank | None
    # Evaluated only when the gold labels cover it; otherwise the card says "not evaluated".
    evaluated: bool


class Question(Record):
    id: NonBlank
    title: NonBlank
    # The day the search ran, which the page states.
    retrieved: date
    # The abstracts the findings were extracted from.
    abstracts: NonNegativeInt
    # The hosted build refuses an export from an unreviewed mapping.
    mapping_reviewed: bool
    dimensions: list[Dimension]
    # How each study's badge words its direction, by whether the study is a trial.
    found: FoundWords


class DirectionCounts(Record):
    benefit: NonNegativeInt
    none: NonNegativeInt
    harm: NonNegativeInt

    @property
    def total(self) -> int:
        return self.benefit + self.none + self.harm


class DirectionShares(Record):
    benefit: Annotated[float, Field(ge=0, le=1)]
    none: Annotated[float, Field(ge=0, le=1)]
    harm: Annotated[float, Field(ge=0, le=1)]

    @model_validator(mode="after")
    def _sum_to_one(self) -> Self:
        if not math.isclose(self.benefit + self.none + self.harm, 1, abs_tol=1e-9):
            raise ValueError("shares must sum to 1")
        return self


class DesignCount(Record):
    design: Design
    studies: PositiveInt


class Weighting(StrEnum):
    # The headline weighting: the design weight table.
    DESIGN = "design"
    # Every study weighted 1.
    UNWEIGHTED = "unweighted"
    # Weight = sample size, standing in for inverse variance; studies without one are left out.
    SAMPLE_SIZE = "sample_size"


class WeightedView(Record):
    weighting: Weighting
    # The studies this weighting counts: for sample size, only those reporting one.
    studies: PositiveInt
    shares: DirectionShares
    # None when the top shares tie.
    leading: Direction | None
    # True when this weighting's leading direction differs from the design-weighted one.
    changes_leading: bool


class Summary(Record):
    """The summary strip for one outcome. Studies, not findings, are the unit."""

    # Primary studies with a counted design and a headline finding on the outcome.
    studies: NonNegativeInt
    counts: DirectionCounts
    # Counted studies by design, most first.
    designs: list[DesignCount]
    thin: bool
    # Design-weighted first, then unweighted and sample size. Empty when thin. Sample size is
    # left out when no counted study reports one.
    views: list[WeightedView]
    # Studies whose only findings on the outcome are subgroup results: shown, never counted.
    subgroup_only: NonNegativeInt
    # Studies with a headline finding but a design the strip does not count, such as other.
    uncounted: NonNegativeInt

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if self.counts.total != self.studies:
            raise ValueError("counts must add up to the studies")
        if sum(d.studies for d in self.designs) != self.studies:
            raise ValueError("design counts must add up to the studies")
        if self.thin != (self.studies < THIN_BELOW):
            raise ValueError(f"an outcome is thin exactly when it has under {THIN_BELOW} studies")
        if self.thin and self.views:
            raise ValueError("a thin outcome shows counts only")
        if not self.thin:
            order = [v.weighting for v in self.views]
            if order not in (list(Weighting), [Weighting.DESIGN, Weighting.UNWEIGHTED]):
                raise ValueError("views run design, unweighted, then sample size if any")
            if self.views[0].changes_leading:
                raise ValueError("the design-weighted view is the reference")
        return self


class ApparentPair(Record):
    """A pair that looked contradictory but is not, with the one line that says why."""

    pair_id: NonBlank
    pair_kind: PairKind
    study_a: NonBlank
    study_b: NonBlank
    finding_a: FindingId
    finding_b: FindingId
    reason: Reason
    detail: NonBlank
    # Alike in every field the comparison checks: design, outcome measure, each custom
    # dimension, and sample size; the page shows only these.
    closely_matched: bool = False


class Outcome(Record):
    name: NonBlank
    # The node id of the outcome's hub in the graph.
    hub: NonBlank
    higher_is_better: bool
    summary: Summary
    # Candidate pairs on this outcome and how many were classified.
    candidate_pairs: NonNegativeInt
    classified_pairs: NonNegativeInt
    apparent: list[ApparentPair]


class Study(Record):
    """A primary study: one paper, or the papers of one trial, the earliest leading."""

    key: NonBlank
    pmids: Annotated[list[Pmid], Field(min_length=1)]
    title: NonBlank
    first_author: str | None
    journal: NonBlank
    year: int
    design: Design
    # None for a design the strip does not count.
    weight: NonNegativeFloat | None
    # Enrolled or randomized, not analyzed.
    sample_size: PositiveInt | None
    trial_ids: list[str]
    # A randomized or non-randomized trial, whose badge reads as a difference, not a link.
    trial: bool


class Review(Record):
    """Listed for context and never counted; only reviews whose title or summary names both
    the exposure and the outcome are listed."""

    pmid: Pmid
    title: NonBlank
    first_author: str | None
    journal: NonBlank
    year: int
    design: Design
    summary: str | None


class HubNode(Record):
    kind: Literal["hub"]
    id: NonBlank
    outcome: NonBlank


class FindingNode(Record):
    """One finding, with every field the finding card shows."""

    kind: Literal["finding"]
    id: FindingId
    outcome: NonBlank
    study: NonBlank
    pmid: Pmid
    direction: Direction
    # True for the one finding that stands for its study on this outcome: pairs use it, and the
    # strip counts it when the study's design is counted. Other findings show in node detail.
    headline: bool
    claim: NonBlank
    # The sentence from the abstract, verbatim: every finding on the page shows its own.
    quote: NonBlank
    outcome_reported: NonBlank
    is_primary: bool
    subgroup: str | None
    population: str
    intervention: str
    comparator: str
    dimensions: dict[str, DimensionValue]


class OutcomeEdge(Record):
    kind: Literal["outcome"]
    # A finding node to its outcome's hub.
    source: NonBlank
    target: NonBlank


class ContradictionEdge(Record):
    """A genuine contradiction between two studies' headline findings."""

    kind: Literal["contradiction"]
    # Study a's finding, then study b's, in the candidate pair's order.
    source: FindingId
    target: FindingId
    pair_id: NonBlank
    pair_kind: PairKind
    reason: Reason
    also_differs: list[Reason]
    detail: NonBlank
    rationale: NonBlank
    # The panel orders pairs by these: the two studies' weights added, then their sample sizes
    # added, which is None when either study reports none.
    design_weight: NonNegativeFloat
    sample_size: PositiveInt | None
    # Alike in every field the comparison checks: design, outcome measure, each custom
    # dimension, and sample size; the page shows only these.
    closely_matched: bool = False


Node = Annotated[HubNode | FindingNode, Field(discriminator="kind")]
Edge = Annotated[OutcomeEdge | ContradictionEdge, Field(discriminator="kind")]


class Graph(Record):
    """NetworkX node-link data: `nx.node_link_data(graph, edges="edges")`."""

    directed: Literal[False]
    multigraph: Literal[False]
    graph: dict[str, str]
    nodes: list[Node]
    edges: list[Edge]


class Drop(Record):
    """Something the pipeline left out, and how many, for the method section."""

    what: NonBlank
    count: NonNegativeInt


class StageCost(Record):
    """One stage's measured spend over every recorded run of the question, evals included."""

    stage: NonBlank
    runs: NonNegativeInt
    dollars: NonNegativeFloat


class EvalResult(Record):
    eval: NonBlank
    metric: NonBlank
    value: Annotated[float, Field(ge=0, le=1)]
    # The labeled items behind the value, and its 95% interval when one is computed.
    n: PositiveInt
    low: Annotated[float, Field(ge=0, le=1)] | None
    high: Annotated[float, Field(ge=0, le=1)] | None


class Method(Record):
    records_found: NonNegativeInt
    abstracts_kept: NonNegativeInt
    findings: NonNegativeInt
    studies: NonNegativeInt
    drops: list[Drop]
    candidate_pairs: NonNegativeInt
    classified_pairs: NonNegativeInt
    # The arm that classified the pairs; None for stub contradictions, never hosted.
    depth: Arm | None
    # How the classified pairs were chosen; None when every candidate pair was classified.
    selection: PairSelection | None = None
    costs: list[StageCost]
    evals: list[EvalResult]


class Export(Record):
    schema_version: Literal[1]
    question: Question
    # In tab order.
    outcomes: list[Outcome]
    studies: list[Study]
    graph: Graph
    reviews: list[Review]
    method: Method

    @model_validator(mode="after")
    def _references_resolve(self) -> Self:
        hubs = {n.id: n for n in self.graph.nodes if isinstance(n, HubNode)}
        findings = {n.id: n for n in self.graph.nodes if isinstance(n, FindingNode)}
        if len(hubs) + len(findings) != len(self.graph.nodes):
            raise ValueError("node ids must be unique")
        outcomes = {o.name: o for o in self.outcomes}
        if len(outcomes) != len(self.outcomes):
            raise ValueError("outcome names must be unique")
        if {o.hub for o in self.outcomes} != set(hubs) or any(
            hubs[o.hub].outcome != o.name for o in self.outcomes
        ):
            raise ValueError("every outcome has exactly one hub, and every hub an outcome")
        studies = {s.key for s in self.studies}
        if len(studies) != len(self.studies):
            raise ValueError("study keys must be unique")
        for node in findings.values():
            if node.outcome not in outcomes or node.study not in studies:
                raise ValueError(f"finding {node.id} names an unknown outcome or study")
        headlines = Counter((n.outcome, n.study) for n in findings.values() if n.headline)
        if any(c > 1 for c in headlines.values()):
            raise ValueError("a study has one headline finding per outcome")
        for edge in self.graph.edges:
            if isinstance(edge, OutcomeEdge):
                node = findings.get(edge.source)
                if node is None or outcomes[node.outcome].hub != edge.target:
                    raise ValueError(f"outcome edge {edge.source} must join a finding to its hub")
            else:
                ends = [findings.get(edge.source), findings.get(edge.target)]
                if any(e is None or not e.headline for e in ends):
                    raise ValueError(f"contradiction {edge.pair_id} must join headline findings")
        return self


def schema() -> dict[str, Any]:
    """The export's JSON Schema, in the shape the page receives."""
    return Export.model_json_schema(mode="serialization")


def schema_text() -> str:
    return json.dumps(schema(), indent=2, ensure_ascii=False) + "\n"
