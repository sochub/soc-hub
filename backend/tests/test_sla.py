from datetime import datetime, timedelta, timezone

from app.utils.sla import compute_sla_state, get_target_minutes, DEFAULT_SLA_MINUTES


class FakeCase:
    def __init__(self, severity, created_minutes_ago, acknowledged_minutes_after=None, resolved_minutes_after=None):
        now = datetime.now(timezone.utc)
        self.severity = severity
        self.created_at = now - timedelta(minutes=created_minutes_ago)
        self.acknowledged_at = (
            self.created_at + timedelta(minutes=acknowledged_minutes_after)
            if acknowledged_minutes_after is not None else None
        )
        self.resolved_at = (
            self.created_at + timedelta(minutes=resolved_minutes_after)
            if resolved_minutes_after is not None else None
        )


def test_default_targets_used_when_no_override():
    assert get_target_minutes("critical", {}) == DEFAULT_SLA_MINUTES["critical"]


def test_override_takes_precedence_over_default():
    override = {"critical": {"response": 5, "resolution": 30}}
    assert get_target_minutes("critical", override) == {"response": 5, "resolution": 30}


def test_unacknowledged_on_track():
    # high: response target 60min. Created 5min ago, well under 80% of target.
    case = FakeCase("high", created_minutes_ago=5)
    state = compute_sla_state(case, {})
    assert state["response_status"] == "on_track"


def test_unacknowledged_at_risk():
    # 50/60 = 83% elapsed, above the 80% at-risk threshold, not yet breached.
    case = FakeCase("high", created_minutes_ago=50)
    state = compute_sla_state(case, {})
    assert state["response_status"] == "at_risk"


def test_unacknowledged_breached():
    case = FakeCase("high", created_minutes_ago=70)
    state = compute_sla_state(case, {})
    assert state["response_status"] == "breached"


def test_acknowledged_within_target_is_met():
    case = FakeCase("high", created_minutes_ago=70, acknowledged_minutes_after=10)
    state = compute_sla_state(case, {})
    assert state["response_status"] == "met"


def test_acknowledged_after_target_is_breached():
    case = FakeCase("high", created_minutes_ago=70, acknowledged_minutes_after=90)
    state = compute_sla_state(case, {})
    assert state["response_status"] == "breached"


def test_info_severity_not_tracked_by_default():
    case = FakeCase("info", created_minutes_ago=100000)
    state = compute_sla_state(case, {})
    assert state["response_status"] == "not_tracked"
    assert state["resolution_status"] == "not_tracked"


def test_overall_status_is_worst_of_response_and_resolution():
    # response breached (created 70min ago, unacknowledged, target 60),
    # resolution still on_track (target 480, only 70min elapsed).
    case = FakeCase("high", created_minutes_ago=70)
    state = compute_sla_state(case, {})
    assert state["response_status"] == "breached"
    assert state["resolution_status"] == "on_track"
    assert state["overall_status"] == "breached"


def test_policy_override_changes_computed_state():
    # Default high response target is 60min; override to 5min so 10min-old
    # case is already breached instead of on_track.
    case = FakeCase("high", created_minutes_ago=10)
    override = {"high": {"response": 5, "resolution": 480}}
    state = compute_sla_state(case, override)
    assert state["response_status"] == "breached"
