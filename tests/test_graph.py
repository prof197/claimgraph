"""The graph from synthetic findings: every record here comes from the synthetic export."""

import pytest
from export_fixtures import CASES, REMISSION, SYMPTOMS, synthetic

from claimgraph import graph
from claimgraph.export import ContradictionEdge, Export, FindingNode, HubNode, OutcomeEdge

OUTCOMES = [SYMPTOMS, CASES, REMISSION]


def parts() -> tuple[list[FindingNode], list[ContradictionEdge]]:
    built = synthetic().graph
    findings = [n for n in built.nodes if isinstance(n, FindingNode)]
    edges = [e for e in built.edges if isinstance(e, ContradictionEdge)]
    return findings, edges


def test_the_node_link_form_holds_the_same_nodes_and_edges_as_the_synthetic_export() -> None:
    findings, contradictions = parts()
    expected = synthetic().graph

    exported = graph.node_link(graph.build(OUTCOMES, findings, contradictions))

    assert not exported.directed and not exported.multigraph and exported.graph == {}
    assert sorted(exported.nodes, key=lambda n: n.id) == sorted(expected.nodes, key=lambda n: n.id)
    assert sorted(exported.edges, key=repr) == sorted(expected.edges, key=repr)


def test_findings_come_first_so_every_edge_keeps_its_direction() -> None:
    findings, contradictions = parts()

    exported = graph.node_link(graph.build(OUTCOMES, findings, contradictions))

    assert [n.id for n in exported.nodes[: len(findings)]] == [f.id for f in findings]
    assert all(isinstance(n, HubNode) for n in exported.nodes[len(findings) :])
    outcome_edges = [e for e in exported.edges if isinstance(e, OutcomeEdge)]
    assert {(e.source, e.target) for e in outcome_edges} == {
        (f.id, graph.hub_id(f.outcome)) for f in findings
    }
    pairs = [(e.source, e.target) for e in exported.edges if isinstance(e, ContradictionEdge)]
    assert sorted(pairs) == sorted((e.source, e.target) for e in contradictions)


def test_the_exported_graph_passes_the_export_s_reference_checks() -> None:
    findings, contradictions = parts()
    export = synthetic()

    rebuilt = graph.node_link(graph.build(OUTCOMES, findings, contradictions))

    assert Export.model_validate(export.model_copy(update={"graph": rebuilt}).model_dump())


def test_the_same_findings_give_the_same_graph() -> None:
    findings, contradictions = parts()

    def text() -> str:
        return graph.node_link(graph.build(OUTCOMES, findings, contradictions)).model_dump_json()

    assert text() == text()


def test_a_contradiction_whose_target_comes_first_is_refused() -> None:
    findings, contradictions = parts()
    edge = contradictions[0]
    flipped = edge.model_copy(update={"source": edge.target, "target": edge.source})

    with pytest.raises(ValueError, match="source must come first"):
        graph.build(OUTCOMES, findings, [flipped])


def test_a_finding_on_an_outcome_without_a_hub_is_refused() -> None:
    findings, _ = parts()

    with pytest.raises(ValueError, match="has no hub"):
        graph.build([SYMPTOMS], findings, [])
