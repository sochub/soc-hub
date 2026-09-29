from app.workflows.events import should_emit, trigger_matches, MAX_DEPTH

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
