import pytest

from reporter import security


@pytest.mark.parametrize("text", [
    "here is a token: Bearer abcdefghijklmnop",
    "api_key=sk-abcdefghijklmnopqrstuvwxyz",
    "password: hunter2VeryLongPassword",
    "OPENAI_API_KEY=sk-proj-abcdefghijklmnopqrstuvwxyz",
    "sk-ant-api03-abcdefghijklmnop",
    "key_abcdefghijklmnopqrstuvwxyz",
    "ghp_abcdefghijklmnopqrstuvwxyz",
    "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dQw4w9WgXcQ",
])
def test_obvious_secret_shapes_are_blocked(text):
    with pytest.raises(security.OutboundMessageRejected):
        security.prepare_outbound_text(text, max_len=4000)


def test_ordinary_message_is_not_blocked():
    result = security.prepare_outbound_text("41 migrations applied; 34/34 checks passed.", max_len=4000)
    assert result.text == "41 migrations applied; 34/34 checks passed."
    assert result.truncated is False


def test_control_characters_are_stripped():
    result = security.prepare_outbound_text("hello\x00\x07world", max_len=4000)
    assert result.text == "helloworld"


def test_overlong_message_is_truncated_not_rejected():
    long_text = "x" * 5000
    result = security.prepare_outbound_text(long_text, max_len=100)
    assert len(result.text) <= 100
    assert result.truncated is True
