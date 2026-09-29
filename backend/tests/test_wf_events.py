from app.workflows.events import should_emit, trigger_matches, emit_event, MAX_DEPTH

CTX = {"case": {"tags": ["x"], "severity": "high"}, "alert": None,
       "trigger": {"changes": {"tags": {"from": "[]", "to": "['x']"}}}}


def test_depth_cap():
    assert MAX_DEPTH == 3
    assert should_emit(0) and should_emit(3)
    assert not should_emit(4)


def test_empty_filter_matches():
    assert trigger_matches(None, CTX) and trigger_matches("  ", CTX)


def test_filter_evaluates():
    assert trigger_matches("'x' in case.tags", CTX)
    assert not trigger_matches("case.severity == 'low'", CTX)


def test_filter_error_means_no_match():
    assert not trigger_matches("alert.payload.severity >= 7", CTX)  # alert is None


def test_emit_event_enqueues_with_args(monkeypatch):
    from app.tasks import workflows
    calls = []
    monkeypatch.setattr(workflows.dispatch_event_task, "delay", lambda *a: calls.append(a))
    emit_event(1, "case.updated", case_id=5, changes={"a": 1}, depth=2)
    emit_event(1, "alert.ingested", alert_id=9)
    assert calls == [(1, "case.updated", 5, None, {"a": 1}, 2),
                     (1, "alert.ingested", None, 9, {}, 0)]


def test_emit_event_dropped_at_depth_4(monkeypatch):
    from app.tasks import workflows
    calls = []
    monkeypatch.setattr(workflows.dispatch_event_task, "delay", lambda *a: calls.append(a))
    emit_event(1, "case.updated", case_id=5, depth=4)
    assert calls == []


def test_emit_event_swallows_broker_errors(monkeypatch):
    from app.tasks import workflows

    def boom(*a):
        raise ConnectionError("broker down")
    monkeypatch.setattr(workflows.dispatch_event_task, "delay", boom)
    emit_event(1, "case.created", case_id=5)  # must not raise
