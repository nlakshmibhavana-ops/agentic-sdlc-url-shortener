"""The delivery graph: explicit dependencies, readiness, invalidation and plan merging."""

from __future__ import annotations

from collections.abc import Iterable

from .models import TERMINAL, Node, NodeStatus


class GraphError(Exception):
    pass


class Graph:
    def __init__(self, nodes: Iterable[Node] = ()) -> None:
        self.nodes: dict[str, Node] = {}
        for node in nodes:
            self.nodes[node.id] = node
        self.validate()

    def __getitem__(self, node_id: str) -> Node:
        return self.nodes[node_id]

    def __iter__(self):
        return iter(self.nodes.values())

    def add(self, node: Node) -> None:
        if node.id in self.nodes:
            raise GraphError(f"duplicate node {node.id}")
        self.nodes[node.id] = node

    def validate(self) -> None:
        for node in self.nodes.values():
            missing = [d for d in node.deps if d not in self.nodes]
            if missing:
                raise GraphError(f"{node.id} depends on unknown nodes {missing}")
        self.topological_order()  # raises on cycles

    def topological_order(self) -> list[str]:
        indegree = {nid: 0 for nid in self.nodes}
        for node in self.nodes.values():
            for _ in node.deps:
                indegree[node.id] += 1
        ready = sorted(nid for nid, deg in indegree.items() if deg == 0)
        order: list[str] = []
        while ready:
            nid = ready.pop(0)
            order.append(nid)
            for child in sorted(self.dependents(nid)):
                indegree[child] -= 1
                if indegree[child] == 0:
                    ready.append(child)
        if len(order) != len(self.nodes):
            cyclic = sorted(set(self.nodes) - set(order))
            raise GraphError(f"dependency cycle among {cyclic}")
        return order

    def dependents(self, node_id: str) -> list[str]:
        return [n.id for n in self.nodes.values() if node_id in n.deps]

    def downstream(self, node_id: str) -> set[str]:
        out: set[str] = set()
        frontier = [node_id]
        while frontier:
            for child in self.dependents(frontier.pop()):
                if child not in out:
                    out.add(child)
                    frontier.append(child)
        return out

    def ready(self) -> list[Node]:
        """Pending nodes whose dependencies all finished successfully (or were skipped)."""
        ok = {NodeStatus.SUCCEEDED, NodeStatus.SKIPPED}
        return [
            n
            for nid in self.topological_order()
            if (n := self.nodes[nid]).status == NodeStatus.PENDING
            and all(self.nodes[d].status in ok for d in n.deps)
        ]

    def block_downstream(self, node_id: str, reason: str) -> list[str]:
        blocked = []
        for nid in sorted(self.downstream(node_id)):
            node = self.nodes[nid]
            if node.status in (NodeStatus.PENDING, NodeStatus.WAITING, NodeStatus.STOPPED):
                node.status = NodeStatus.BLOCKED
                node.error = reason
                blocked.append(nid)
        return blocked

    def consumers_of(self, artifact: str) -> list[Node]:
        return [n for n in self.nodes.values() if artifact in n.inputs]

    def is_finished(self) -> bool:
        return all(n.status in TERMINAL for n in self.nodes.values())

    def to_mermaid(self) -> str:
        style = {
            NodeStatus.SUCCEEDED: ":::ok",
            NodeStatus.FAILED: ":::bad",
            NodeStatus.BLOCKED: ":::bad",
            NodeStatus.WAITING: ":::wait",
            NodeStatus.SKIPPED: ":::skip",
        }
        lines = ["flowchart TD"]
        for nid in self.topological_order():
            node = self.nodes[nid]
            label = f"{nid}<br/><small>{node.status.value}</small>"
            shape = f'{_mid(nid)}{{"{label}"}}' if node.impact == "high" else f'{_mid(nid)}["{label}"]'
            lines.append(f"  {shape}{style.get(node.status, '')}")
            for dep in node.deps:
                lines.append(f"  {_mid(dep)} --> {_mid(nid)}")
        lines += [
            "  classDef ok fill:#d4edda,stroke:#2e7d32",
            "  classDef bad fill:#f8d7da,stroke:#c62828",
            "  classDef wait fill:#fff3cd,stroke:#f9a825",
            "  classDef skip fill:#eeeeee,stroke:#9e9e9e,stroke-dasharray: 4",
        ]
        return "\n".join(lines)


def _mid(node_id: str) -> str:
    return node_id.replace(".", "_").replace("-", "_")
