import pytest

from app.workflows.graph import body_graph, ready_nodes
from app.workflows.loops import aggregate_children, coerce_items, next_children_to_start


@pytest.mark.parametrize("value", ["abc", {"a": 1}, None, 5])
def test_items_must_be_list(value):
    with pytest.raises(ValueError, match="must evaluate to a list"):
        coerce_items(value, 100)


def test_items_cap():
    assert coerce_items([1, 2], None) == [1, 2]
    with pytest.raises(ValueError, match="max_items"):
        coerce_items(list(range(11)), 10)
    with pytest.raises(ValueError, match="max_items"):
        coerce_items(list(range(501)), 10_000)  # hard cap 500


def test_next_children_respects_concurrency():
    assert next_children_to_start(["queued"] * 5, 2) == [0, 1]
    assert next_children_to_start(["running", "waiting", "queued", "queued"], 3) == [2]
    assert next_children_to_start(["succeeded", "running", "queued", "queued"], 2) == [2]
    assert next_children_to_start(["running", "running"], 2) == []


def test_aggregate_waits_for_all_terminal():
    assert aggregate_children([{"index": 0, "status": "succeeded", "steps": {}},
                               {"index": 1, "status": "running", "steps": {}}]) is None


def test_aggregate_counts_and_orders():
    agg = aggregate_children([
        {"index": 1, "status": "failed", "steps": {"a": None}},
        {"index": 0, "status": "succeeded", "steps": {"a": {"ok": 1}}},
        {"index": 2, "status": "cancelled", "steps": {}},
    ])
    assert agg["succeeded"] == 1 and agg["failed"] == 2
    assert [r["index"] for r in agg["results"]] == [0, 1, 2]
    assert agg["results"][0]["steps"] == {"a": {"ok": 1}}


def test_body_graph_adds_start_to_roots():
    g = {"nodes": [{"id": "t", "type": "trigger"}, {"id": "loop", "type": "for_each"},
                   {"id": "a", "type": "case_add_note", "parent_id": "loop"},
                   {"id": "b", "type": "case_add_note", "parent_id": "loop"},
                   {"id": "c", "type": "case_add_note", "parent_id": "loop"},
                   {"id": "after", "type": "case_add_note"}],
         "edges": [{"id": "1", "source": "t", "target": "loop", "source_handle": None},
                   {"id": "2", "source": "a", "target": "b", "source_handle": None},
                   {"id": "3", "source": "loop", "target": "after", "source_handle": None}]}
    body = body_graph(g, "loop")
    ids = {n["id"] for n in body["nodes"]}
    assert ids == {"__start__", "a", "b", "c"}
    assert all(n.get("parent_id") is None for n in body["nodes"])
    starts = {e["target"] for e in body["edges"] if e["source"] == "__start__"}
    assert starts == {"a", "c"}
    ready, _ = ready_nodes(body, {"__start__": {"status": "succeeded", "output": {}}})
    assert ready == {"a", "c"}
