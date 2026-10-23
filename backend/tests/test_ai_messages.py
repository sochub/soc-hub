from app.ai.llm import normalize_messages


def test_leading_assistant_folded_into_system():
    out = normalize_messages([
        {"role": "system", "content": "You are the copilot."},
        {"role": "assistant", "content": "Welcome: 3 open cases."},
        {"role": "user", "content": "Which is worst?"},
    ])
    assert [m["role"] for m in out] == ["system", "user"]
    assert "Welcome: 3 open cases." in out[0]["content"]  # briefing kept as context, not dropped


def test_no_system_and_consecutive_roles_merged():
    out = normalize_messages([
        {"role": "assistant", "content": "hi"},
        {"role": "user", "content": "a"},
        {"role": "user", "content": "b"},  # earlier reply failed
        {"role": "assistant", "content": "c"},
        {"role": "user", "content": ""},
    ])
    assert out == [{"role": "system", "content": "Earlier in this conversation you said:\nhi"},
                   {"role": "user", "content": "a\n\nb"}, {"role": "assistant", "content": "c"}]


def test_plain_chat_unchanged():
    msgs = [{"role": "system", "content": "s"}, {"role": "user", "content": "u"}, {"role": "assistant", "content": "a"},
            {"role": "user", "content": "u2"}]
    assert normalize_messages(msgs) == msgs
