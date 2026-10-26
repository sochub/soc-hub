from pathlib import Path

import yaml

MANIFEST = next(
    (p / "docs/slack/slack-app-manifest.yml" for p in Path(__file__).resolve().parents
     if (p / "docs/slack/slack-app-manifest.yml").exists()),
    None,
)


def test_manifest_has_war_room_scopes_and_events():
    assert MANIFEST is not None, "docs/slack/slack-app-manifest.yml not found"
    m = yaml.safe_load(MANIFEST.read_text())
    scopes = set(m["oauth_config"]["scopes"]["bot"])
    events = set(m["settings"]["event_subscriptions"]["bot_events"])
    assert {"groups:write", "groups:read", "groups:history", "channels:history",
            "users:read", "users:read.email"} <= scopes
    assert {"message.groups", "message.channels"} <= events
