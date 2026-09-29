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
