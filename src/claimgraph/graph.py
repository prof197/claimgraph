"""The graph the page draws: outcome hubs, finding nodes, and the edges between them.

NetworkX holds the graph and writes its node-link form. It does no layout or analytics here:
the page lays the graph out itself.
"""

from collections.abc import Sequence

import networkx as nx

from claimgraph.export import ContradictionEdge, FindingNode, Graph, HubNode


def hub_id(outcome: str) -> str:
    return f"outcome:{outcome}"


def build(
    outcomes: Sequence[str],
    findings: Sequence[FindingNode],
    contradictions: Sequence[ContradictionEdge],
) -> "nx.Graph[str]":
    """Each finding joined to its outcome's hub, and genuine contradictions joining two findings.

    Node-link data lists an undirected edge from the endpoint added first, so the findings go in
    before the hubs, and each contradiction's source must come before its target in `findings`.
    """
    graph: nx.Graph[str] = nx.Graph()
    for node in findings:
        if node.outcome not in outcomes:
            raise ValueError(f"finding {node.id} is on {node.outcome!r}, which has no hub")
        graph.add_node(node.id, **node.model_dump(exclude={"id"}))
    for outcome in outcomes:
        hub = HubNode(kind="hub", id=hub_id(outcome), outcome=outcome)
        graph.add_node(hub.id, **hub.model_dump(exclude={"id"}))
    for node in findings:
        graph.add_edge(node.id, hub_id(node.outcome), kind="outcome")
    position = {node.id: n for n, node in enumerate(findings)}
    for edge in contradictions:
        source, target = position.get(edge.source), position.get(edge.target)
        if source is None or target is None:
            raise ValueError(f"contradiction {edge.pair_id} joins a finding not in the graph")
        if source >= target:
            raise ValueError(f"contradiction {edge.pair_id}: its source must come first")
        graph.add_edge(edge.source, edge.target, **edge.model_dump(exclude={"source", "target"}))
    return graph


def node_link(graph: "nx.Graph[str]") -> Graph:
    """The graph as the export carries it."""
    return Graph.model_validate(nx.node_link_data(graph, edges="edges"))
