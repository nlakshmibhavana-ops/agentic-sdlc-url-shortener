import pytest

from agentflow.graph import Graph, GraphError
from agentflow.models import Node, NodeStatus


def n(nid, *deps):
    return Node(nid, "stage", "x", nid, deps=list(deps))


def test_rejects_cycles_and_unknown_dependencies():
    with pytest.raises(GraphError, match="cycle"):
        Graph([n("a", "b"), n("b", "a")])
    with pytest.raises(GraphError, match="unknown"):
        Graph([n("a", "missing")])


def test_ready_respects_dependencies_and_allows_parallel_branches():
    g = Graph([n("a"), n("b", "a"), n("c", "a"), n("d", "b", "c")])
    assert [x.id for x in g.ready()] == ["a"]
    g["a"].status = NodeStatus.SUCCEEDED
    assert {x.id for x in g.ready()} == {"b", "c"}  # parallel
    g["b"].status = NodeStatus.SUCCEEDED
    assert {x.id for x in g.ready()} == {"c"}  # d waits for the join
    g["c"].status = NodeStatus.SKIPPED
    assert [x.id for x in g.ready()] == ["d"]


def test_block_downstream_marks_transitive_dependents():
    g = Graph([n("a"), n("b", "a"), n("c", "b"), n("x")])
    assert g.block_downstream("a", "boom") == ["b", "c"]
    assert g["x"].status == NodeStatus.PENDING


def test_mermaid_renders_every_edge():
    text = Graph([n("a"), n("b.c", "a")]).to_mermaid()
    assert "a --> b_c" in text
