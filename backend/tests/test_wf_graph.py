from app.workflows.graph import ready_nodes, run_outcome


def n(id, type="case_add_note", parent_id=None):
    return {"id": id, "type": type, "config": {}, "parent_id": parent_id}


def e(s, t, h=None):
    return {"id": f"{s}{t}", "source": s, "target": t, "source_handle": h}


OK = {"status": "succeeded", "output": {}}


def test_linear_chain():
    g = {"nodes": [n("t", "trigger"), n("a"), n("b")], "edges": [e("t", "a"), e("a", "b")]}
    assert ready_nodes(g, {"t": OK}) == ({"a"}, set())
    assert ready_nodes(g, {"t": OK, "a": {"status": "running"}}) == (set(), set())
    assert ready_nodes(g, {"t": OK, "a": OK}) == ({"b"}, set())


def test_diamond_join_waits_for_both():
    g = {"nodes": [n("t", "trigger"), n("a"), n("b"), n("j")],
         "edges": [e("t", "a"), e("t", "b"), e("a", "j"), e("b", "j")]}
    assert ready_nodes(g, {"t": OK}) == ({"a", "b"}, set())
    assert ready_nodes(g, {"t": OK, "a": OK, "b": {"status": "running"}}) == (set(), set())
    assert ready_nodes(g, {"t": OK, "a": OK, "b": OK}) == ({"j"}, set())


def cond_graph():
    return {"nodes": [n("t", "trigger"), n("c", "condition"), n("yes"), n("no"), n("yes2"), n("merge")],
            "edges": [e("t", "c"), e("c", "yes", "true"), e("c", "no", "false"),
                      e("yes", "yes2"), e("yes2", "merge"), e("no", "merge")]}


def test_condition_true_skips_false_branch():
    ready, skipped = ready_nodes(cond_graph(), {"t": OK, "c": {"status": "succeeded", "output": {"result": True}}})
    assert ready == {"yes"} and skipped == {"no"}


def test_merge_after_condition_runs_once_taken_branch_done():
    states = {"t": OK, "c": {"status": "succeeded", "output": {"result": True}},
              "no": {"status": "skipped"}, "yes": OK, "yes2": OK}
    assert ready_nodes(cond_graph(), states) == ({"merge"}, set())


def test_skip_cascades_through_chain():
    ready, skipped = ready_nodes(cond_graph(), {"t": OK, "c": {"status": "succeeded", "output": {"result": False}}})
    assert ready == {"no"} and skipped == {"yes", "yes2"}


def test_all_parents_skipped_skips_join():
    g = {"nodes": [n("t", "trigger"), n("c", "condition"), n("a"), n("b"), n("j")],
         "edges": [e("t", "c"), e("c", "a", "true"), e("c", "b", "true"), e("a", "j"), e("b", "j")]}
    ready, skipped = ready_nodes(g, {"t": OK, "c": {"status": "succeeded", "output": {"result": False}}})
    assert ready == set() and skipped == {"a", "b", "j"}


def test_continue_on_error_counts_as_success():
    g = {"nodes": [n("t", "trigger"), n("a"), n("b")], "edges": [e("t", "a"), e("a", "b")]}
    states = {"t": OK, "a": {"status": "succeeded", "output": {"error": "boom"}}}
    assert ready_nodes(g, states) == ({"b"}, set())


def test_body_nodes_ignored_at_top_level():
    g = {"nodes": [n("t", "trigger"), n("loop", "for_each"), n("inner", parent_id="loop")],
         "edges": [e("t", "loop")]}
    assert ready_nodes(g, {"t": OK}) == ({"loop"}, set())


def test_run_outcome():
    g = {"nodes": [n("t", "trigger"), n("a"), n("b")], "edges": [e("t", "a"), e("a", "b")]}
    assert run_outcome(g, {"t": OK, "a": OK}) is None
    assert run_outcome(g, {"t": OK, "a": OK, "b": {"status": "skipped"}}) == "succeeded"
    assert run_outcome(g, {"t": OK, "a": {"status": "failed"}}) == "failed"
