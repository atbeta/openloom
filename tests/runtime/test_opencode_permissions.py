from __future__ import annotations

import pytest

from openloom.runtime.opencode import OpenCodeClient
from openloom.runtime.prompts import permission_waiting_summary


def test_normalize_permission_maps_session_and_tool_fields() -> None:
    item = {
        "id": "perm_1",
        "sessionID": "ses_abc",
        "permission": "bash",
        "patterns": ["git status*"],
        "metadata": {"command": "git status"},
    }
    out = OpenCodeClient._normalize_permission(item)
    assert out["id"] == "perm_1"
    assert out["sessionId"] == "ses_abc"
    assert out["permission"] == "bash"
    assert out["patterns"] == ["git status*"]


def test_permission_waiting_summary_includes_tool_and_count() -> None:
    pending = [
        {"permission": "bash", "patterns": ["npm test"]},
        {"permission": "edit", "patterns": ["src/foo.ts"]},
    ]
    summary = permission_waiting_summary(pending)
    assert "bash" in summary
    assert "npm test" in summary
    assert "+1 more" in summary


@pytest.mark.asyncio
async def test_list_pending_permissions_filters_by_session() -> None:
    import httpx
    import respx

    client = OpenCodeClient("http://127.0.0.1:4096", "opencode", "")
    with respx.mock(base_url="http://127.0.0.1:4096") as mock:
        mock.get("/permission").mock(
            return_value=httpx.Response(
                200,
                json=[
                    {"id": "p1", "sessionID": "ses_a", "permission": "bash", "patterns": ["ls"]},
                    {"id": "p2", "sessionID": "ses_b", "permission": "edit", "patterns": ["a.ts"]},
                ],
            ),
        )
        all_pending = await client.list_pending_permissions()
        assert len(all_pending) == 2
        filtered = await client.list_pending_permissions("ses_a")
        assert len(filtered) == 1
        assert filtered[0]["id"] == "p1"
