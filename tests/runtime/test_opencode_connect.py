from openloom.runtime.opencode import _health_error_message, format_opencode_unreachable_help


def test_health_error_message_connection_refused() -> None:
    msg = _health_error_message("http://127.0.0.1:4096", ConnectionRefusedError("[Errno 61] Connection refused"))
    assert "connection refused" in msg
    assert "4096" in msg


def test_unreachable_help_includes_url_and_commands() -> None:
    text = format_opencode_unreachable_help("http://127.0.0.1:4096", detail="connection refused")
    assert "opencode serve" in text
    assert "127.0.0.1:4096" in text
    assert "OPENLOOM_OPENCODE_URL" in text
    assert "connection refused" in text
