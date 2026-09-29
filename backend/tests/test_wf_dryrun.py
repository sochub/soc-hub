from app.workflows.dryrun import dry_run_behaviour, simulated_output

NO_MOCKS = {"mocks": {}, "ask_user_answers": {}}


def test_read_only_nodes_execute():
    for t in ("trigger", "condition", "for_each", "case_search"):
        assert dry_run_behaviour({"id": "x", "type": t}) == "execute"


def test_side_effect_nodes_simulate():
    for t in ("http_request", "case_update", "case_add_note", "case_add_artifact",
              "case_apply_playbook", "alert_promote", "alert_dismiss",
              "slack_post_message", "slack_ask_user"):
        assert dry_run_behaviour({"id": "x", "type": t}) == "simulate"


def test_execute_in_dry_run_only_honoured_on_http():
    assert dry_run_behaviour({"id": "x", "type": "http_request", "execute_in_dry_run": True}) == "execute"
    assert dry_run_behaviour({"id": "x", "type": "case_update", "execute_in_dry_run": True}) == "simulate"


def test_mock_precedence():
    node = {"id": "h", "type": "http_request", "mock_output": {"status": 404}}
    out = simulated_output(node, {"url": "u"}, {"mocks": {"h": {"status": 201}}, "ask_user_answers": {}})
    assert out["status"] == 201 and out["simulated"] is True and out["would_do"] == {"url": "u"}
    assert simulated_output(node, {}, NO_MOCKS)["status"] == 404
    bare = {"id": "h", "type": "http_request"}
    assert simulated_output(bare, {}, NO_MOCKS)["status"] == 200


def test_alert_promote_stub():
    out = simulated_output({"id": "p", "type": "alert_promote"}, {"mode": "new"}, NO_MOCKS)
    assert out["case_id"] is None and out["created"] is True


def test_ask_user_dry_run_answers():
    node = {"id": "ask", "type": "slack_ask_user"}
    rendered = {"email": "a@x.com", "message": "ok?", "buttons": "Yes, No"}
    default = simulated_output(node, rendered, NO_MOCKS)
    assert default["response"] == "Yes" and default["timed_out"] is False and default["simulated"] is True
    no = simulated_output(node, rendered, {"mocks": {}, "ask_user_answers": {"ask": "No"}})
    assert no["response"] == "No"
    t = simulated_output(node, rendered, {"mocks": {}, "ask_user_answers": {"ask": "timeout"}})
    assert t["response"] is None and t["timed_out"] is True


def test_ask_user_dry_run_bad_buttons_falls_back():
    node = {"id": "ask", "type": "slack_ask_user"}
    out = simulated_output(node, {"buttons": 5}, NO_MOCKS)
    assert out["response"] == "Yes" and out["timed_out"] is False


def test_post_message_stub_has_output_shape():
    from app.workflows.dryrun import simulated_output
    out = simulated_output({"id": "p", "type": "slack_post_message"}, {"text": "hi"}, {})
    assert out["channel"] == "" and out["ts"] == "" and out["simulated"] is True
