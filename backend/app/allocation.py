"""Shared deterministic allocation; candidate eligibility remains the caller's policy."""

import hashlib
from dataclasses import dataclass
from typing import Protocol, Sequence

import networkx as nx

from app.contracts import PracticeRequest, Shortfall


class Candidate(Protocol):
    id: str
    revision: str
    canonical_id: str
    content_fingerprint: str | None
    point_ids: list[str]
    difficulty_score: int | None

    @property
    def difficulty(self) -> str | None: ...


@dataclass
class AllocationResult:
    selections: list[tuple[int, str]]
    shortfalls: list[Shortfall]


def identity_groups(
    questions: Sequence[Candidate], request: PracticeRequest
) -> dict[int, tuple[str, str, str]]:
    """Build transitive identities across all rows, including excluded/blocked copies."""
    identities = nx.Graph()
    for i, question in enumerate(questions):
        node = ("row", i)
        identities.add_node(node)
        for kind, value in (
            ("id", question.id),
            ("canonical", question.canonical_id),
            ("fingerprint", question.content_fingerprint),
        ):
            if value:
                identities.add_edge(node, (kind, value))
    groups = {}
    for component in nx.connected_components(identities):
        rows = sorted(node[1] for node in component if node[0] == "row")
        if any(
            questions[i].id in request.excluded_question_ids
            or questions[i].canonical_id in request.excluded_canonical_ids
            for i in rows
        ):
            continue
        key = min(
            (questions[i].canonical_id, questions[i].content_fingerprint or "", questions[i].id)
            for i in rows
        )
        groups.update(dict.fromkeys(rows, key))
    return groups


def allocate_candidates(
    questions: Sequence[Candidate], request: PracticeRequest, eligible_indices: set[int]
) -> AllocationResult:
    groups = identity_groups(questions, request)
    graph = nx.DiGraph()
    source, sink = ("source",), ("sink",)
    graph.add_nodes_from([source, sink])
    slots = []
    total = sum(quota.count for quota in request.quotas)
    scale = max(total, 1) * 1000 + 1
    for quota in request.quotas:
        allocation = quota.by_difficulty if quota.by_difficulty is not None else {None: quota.count}
        for level, count in allocation.items():
            slot = ("slot", quota.point_id, level)
            slots.append((slot, quota, level, count))
            graph.add_edge(source, slot, capacity=count, weight=0)
    for i in sorted(eligible_indices, key=lambda i: (questions[i].id, questions[i].revision)):
        if i not in groups:
            continue
        question = questions[i]
        if question.difficulty_score is None:
            raise ValueError("eligible allocation candidate requires a difficulty score")
        row, group = ("question", i), ("group", groups[i])
        graph.add_edge(row, group, capacity=1, weight=0)
        graph.add_edge(group, sink, capacity=1, weight=0)
        for slot, quota, level, _ in slots:
            if quota.point_id not in question.point_ids or (level and question.difficulty != level):
                continue
            tie = (
                int.from_bytes(
                    hashlib.sha256(
                        f"{request.seed}|{quota.point_id}|{question.id}|{question.revision}".encode()
                    ).digest()[:4],
                    "big",
                )
                % 1000
            )
            cost = abs(question.difficulty_score - quota.reference_score) * scale + tie
            graph.add_edge(slot, row, capacity=1, weight=cost)
    flow = nx.max_flow_min_cost(graph, source, sink)
    selections, shortfalls = [], []
    for slot, quota, level, count in slots:
        chosen = [node[1] for node, amount in flow[slot].items() if amount]
        chosen.sort(
            key=lambda i: (questions[i].difficulty_score, questions[i].id, questions[i].revision)
        )
        selections.extend((i, quota.point_id) for i in chosen)
        if len(chosen) < count:
            shortfalls.append(
                Shortfall(
                    point_id=quota.point_id, difficulty=level, requested=count, selected=len(chosen)
                )
            )
    return AllocationResult(selections=selections, shortfalls=shortfalls)
