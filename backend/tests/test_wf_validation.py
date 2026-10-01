import pytest

from app.workflows.validation import validate_graph


def n(id, type, **kw):
    return {"id": id, "type": type, "position": {"x": 0, "y": 0}, "config": kw.pop("config", {}), **kw}


def e(src, tgt, handle=None):
    return {"id": f"{src}-{tgt}-{handle}", "source": src, "target": tgt, "source_handle": handle}


def msgs(errors):
    return " | ".join(x["message"] for x in errors)


VALID = {
    "nodes": [
        n("start", "trigger"),
        n("check", "condition", config={"expression": "'x' in case.tags"}),
        n("note", "case_add_note", config={"content": "Tag X on {{ case.title }}"}),
    ],
    "edges": [e("start", "check"), e("check", "note", "true")],
}


def test_valid_graph():
    assert validate_graph(VALID, "case.created") == []


def test_unknown_trigger_type():
    assert "trigger type" in msgs(validate_graph(VALID, "case.deleted"))


def test_requires_exactly_one_trigger():
    g = {"nodes": [n("a", "case_add_note", config={"content": "x"})], "edges": []}
    assert "exactly one trigger" in msgs(validate_graph(g, "manual"))
    g2 = {"nodes": [n("a", "trigger"), n("b", "trigger")], "edges": []}
    assert "exactly one trigger" in msgs(validate_graph(g2, "manual"))


def test_cycle_detected():
    g = {"nodes": [n("start", "trigger"), n("a", "case_add_note", config={"content": "x"}),
                   n("b", "case_add_note", config={"content": "y"})],
         "edges": [e("start", "a"), e("a", "b"), e("b", "a")]}
    assert "cycle" in msgs(validate_graph(g, "manual"))


def test_bad_ids_duplicates_unknown_type_missing_config():
    g = {"nodes": [n("start", "trigger"), n("Bad-Id", "case_add_note", config={"content": "x"}),
                   n("dup", "case_add_note", config={"content": "x"}), n("dup", "case_add_note", config={"content": "x"}),
                   n("zz", "teleport"), n("nocontent", "case_add_note")],
         "edges": []}
    m = msgs(validate_graph(g, "manual"))
    assert "invalid id" in m and "duplicate" in m and "unknown node type" in m and "content" in m


def test_condition_handles():
    g = {"nodes": [n("start", "trigger"), n("c", "condition", config={"expression": "true"}),
                   n("a", "case_add_note", config={"content": "x"})],
         "edges": [e("start", "c"), e("c", "a")]}  # missing handle on condition edge
    assert "true/false" in msgs(validate_graph(g, "manual"))
    g["edges"] = [e("start", "c", "true"), e("c", "a", "true")]  # handle on non-condition source
    assert "only condition" in msgs(validate_graph(g, "manual"))


def test_dangling_edge():
    g = {"nodes": [n("start", "trigger")], "edges": [e("start", "ghost")]}
    assert "unknown node" in msgs(validate_graph(g, "manual"))


def test_template_syntax_error():
    g = {"nodes": [n("start", "trigger"), n("a", "case_add_note", config={"content": "{{ case.title "})],
         "edges": [e("start", "a")]}
    assert "template" in msgs(validate_graph(g, "manual"))


def test_expression_syntax_error():
    g = {"nodes": [n("start", "trigger"), n("c", "condition", config={"expression": "case.id =="})],
         "edges": [e("start", "c")]}
    assert "template" in msgs(validate_graph(g, "manual"))


def test_node_limit():
    nodes = [n("start", "trigger")] + [n(f"n{i}", "case_add_note", config={"content": "x"}) for i in range(50)]
    assert "50" in msgs(validate_graph({"nodes": nodes, "edges": []}, "manual"))


def test_alert_nodes_only_on_alert_trigger():
    g = {"nodes": [n("start", "trigger"), n("p", "alert_promote", config={"mode": "new", "title": "x"})],
         "edges": [e("start", "p")]}
    assert "alert.ingested" in msgs(validate_graph(g, "case.created"))
    assert validate_graph(g, "alert.ingested") == []


def test_alert_promote_mode_requirements():
    g = {"nodes": [n("start", "trigger"), n("p", "alert_promote", config={"mode": "group", "title": "x"})],
         "edges": [e("start", "p")]}
    assert "group_key" in msgs(validate_graph(g, "alert.ingested"))


@pytest.mark.parametrize("malformed_graph", [
    # node not a dict
    {"nodes": ["x"], "edges": []},
    # node id null
    {"nodes": [{"id": None, "type": "trigger"}], "edges": []},
    # node id int
    {"nodes": [{"id": 123, "type": "trigger"}], "edges": []},
    # node type a list
    {"nodes": [{"id": "start", "type": ["trigger"]}], "edges": []},
    # config a non-empty list
    {"nodes": [{"id": "start", "type": "trigger", "config": ["x"]}], "edges": []},
    # config a string
    {"nodes": [{"id": "start", "type": "trigger", "config": "x"}], "edges": []},
    # edge not a dict
    {"nodes": [{"id": "start", "type": "trigger"}], "edges": ["x"]},
    # edge missing source
    {"nodes": [{"id": "start", "type": "trigger"}], "edges": [{}]},
    # edge source a list
    {"nodes": [{"id": "start", "type": "trigger"}], "edges": [{"source": ["x"], "target": "y"}]},
    # edge target a list
    {"nodes": [{"id": "start", "type": "trigger"}], "edges": [{"source": "x", "target": ["y"]}]},
    # edge source_handle not string/None
    {"nodes": [{"id": "start", "type": "trigger"}], "edges": [{"source": "x", "target": "y", "source_handle": 123}]},
    # graph not a dict (list)
    [],
    # graph not a dict (string)
    "not a dict",
    # nodes not a list
    {"nodes": "not a list", "edges": []},
    # edges not a list
    {"nodes": [], "edges": "not a list"},
    # deep graph causing RecursionError (3000 nodes chain)
    {
        "nodes": [{"id": f"n{i}", "type": "trigger" if i == 0 else "case_add_note", "config": {} if i > 0 else {}}
                  for i in range(3000)],
        "edges": [{"id": f"e{i}", "source": f"n{i}", "target": f"n{i+1}", "source_handle": None}
                  for i in range(2999)],
    },
])
def test_malformed_input_returns_errors_not_exceptions(malformed_graph):
    """Test that validate_graph returns errors instead of raising on malformed input."""
    errors = validate_graph(malformed_graph, "manual")
    # Should return non-empty error list, not raise
    assert isinstance(errors, list)
    assert len(errors) > 0


def test_orphan_node_rejected():
    """Unconnected non-trigger nodes should be rejected as orphans."""
    g = {"nodes": [n("start", "trigger"), n("orphan", "case_add_note", config={"content": "x"})],
         "edges": []}
    m = msgs(validate_graph(g, "manual"))
    assert "not connected" in m


def test_parent_id_node_exempt_from_orphan_check():
    """Nodes with parent_id (loop bodies) are exempt from orphan check."""
    g = {"nodes": [n("start", "trigger"), n("loop", "for_each", config={"items": "x"}),
                   n("body", "case_add_note", config={"content": "x"}, parent_id="loop")],
         "edges": [e("start", "loop")]}
    m = msgs(validate_graph(g, "manual"))
    # The body node should NOT have "not connected" error (even though it has no incoming edge)
    assert "not connected" not in m


def loop_graph(**overrides):
    g = {"nodes": [n("start", "trigger"), n("loop", "for_each", config={"items": "steps.start.output.users"}),
                   n("inner", "case_add_note", parent_id="loop", config={"content": "{{ loop.item }}"})],
         "edges": [e("start", "loop")]}
    g.update(overrides)
    return g


def test_valid_loop():
    assert validate_graph(loop_graph(), "manual") == []


def test_edge_cannot_cross_loop_boundary():
    g = loop_graph()
    g["edges"].append(e("start", "inner"))
    assert "loop boundary" in msgs(validate_graph(g, "manual"))


def test_no_nested_loops_and_parent_must_be_loop():
    g = loop_graph()
    g["nodes"].append(n("inner_loop", "for_each", parent_id="loop", config={"items": "[1]"}))
    g["nodes"].append(n("x", "case_add_note", parent_id="inner", config={"content": "x"}))
    m = msgs(validate_graph(g, "manual"))
    assert "nested" in m and "for_each" in m


def test_empty_loop_body():
    g = loop_graph()
    g["nodes"] = g["nodes"][:2]
    assert "empty" in msgs(validate_graph(g, "manual"))


def test_loop_limits():
    g = loop_graph()
    g["nodes"][1]["config"].update({"concurrency": 50, "max_items": 900})
    m = msgs(validate_graph(g, "manual"))
    assert "concurrency" in m and "max_items" in m


@pytest.mark.parametrize("pid", [5, ["loop"], {"a": 1}])
def test_non_string_parent_id_is_error_not_crash(pid):
    g = loop_graph()
    g["nodes"][2]["parent_id"] = pid
    assert "parent_id must be a string" in msgs(validate_graph(g, "manual"))


@pytest.mark.parametrize("key", ["concurrency", "max_items"])
@pytest.mark.parametrize("bad", [float("inf"), True, 0, -1])
def test_loop_limit_bad_values_error_without_raising(key, bad):
    g = loop_graph()
    g["nodes"][1]["config"][key] = bad
    assert "must be between" in msgs(validate_graph(g, "manual"))


@pytest.mark.parametrize("parent", ["ghost", "other"])
def test_parent_must_reference_for_each(parent):
    g = loop_graph()
    g["nodes"].append(n("other", "case_add_note", config={"content": "x"}))
    g["edges"].append(e("start", "other"))
    g["nodes"].append(n("child", "case_add_note", parent_id=parent, config={"content": "x"}))
    assert "parent_id must reference a for_each node" in msgs(validate_graph(g, "manual"))


def test_ask_user_limits():
    g = {"nodes": [n("start", "trigger"),
                   n("ask", "slack_ask_user", config={"email": "{{ case.title }}", "message": "ok?", "timeout_hours": 200,
                                                      "buttons": "a,b,c,d,e,f"})],
         "edges": [e("start", "ask")]}
    m = msgs(validate_graph(g, "manual"))
    assert "timeout_hours" in m and "buttons" in m


@pytest.mark.parametrize("bad", ["abc", True, float("inf"), 10**400, 0, -1])
def test_ask_user_bad_timeout_never_raises(bad):
    g = {"nodes": [n("start", "trigger"),
                   n("ask", "slack_ask_user", config={"email": "a@b.c", "message": "ok?", "timeout_hours": bad})],
         "edges": [e("start", "ask")]}
    assert "timeout_hours" in msgs(validate_graph(g, "manual"))


@pytest.mark.parametrize("bad", [5, True, {"a": 1}, 1.5])
def test_ask_user_bad_buttons_type_never_raises(bad):
    g = {"nodes": [n("start", "trigger"),
                   n("ask", "slack_ask_user", config={"email": "a@b.c", "message": "ok?", "buttons": bad})],
         "edges": [e("start", "ask")]}
    assert "buttons must be a comma-separated string or a list" in msgs(validate_graph(g, "manual"))


def test_trigger_filter_validation():
    from app.workflows.validation import validate_trigger_filter
    assert validate_trigger_filter(None) == [] and validate_trigger_filter("  ") == []
    assert validate_trigger_filter("'phishing' in case.tags") == []
    errs = validate_trigger_filter("case.id ==")
    assert len(errs) == 1 and errs[0]["node_id"] is None and errs[0]["message"].startswith("trigger filter: ")
