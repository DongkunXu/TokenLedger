from __future__ import annotations

import asyncio
import json

from tokenledger.store import connect

from test_views_api import seed


def test_mcp_tools_read_the_configured_ledger(tmp_path, settings, monkeypatch):
    data = tmp_path / "data"
    data.mkdir()
    (data / "settings.json").write_text(
        json.dumps({"timezone": "Europe/London", "grace_days": 3, "machine": {"id": "test-mac", "label": "t", "os": "darwin"}})
    )
    monkeypatch.setenv("TOKENLEDGER_DATA_DIR", str(data))
    conn = connect(data / "ledger.sqlite")
    from tokenledger.store import migrate

    migrate(conn)
    seed(conn, settings)
    conn.close()

    from tokenledger.mcp_server import mcp

    async def call(name, args):
        result = await mcp.call_tool(name, args)
        return json.loads(result.content[0].text)

    tools = {t.name: t for t in asyncio.run(mcp.list_tools())}
    assert tools["usage_summary"].annotations.read_only_hint is True
    assert tools["run_capture"].annotations.read_only_hint is False
    summary = asyncio.run(call("usage_summary", {"start": "2026-09-24", "end": "2026-09-26"}))
    assert summary["tokens"] == 260
    rows = asyncio.run(call("daily_usage", {"start": "2026-09-26", "end": "2026-09-26", "provider": "codex"}))
    assert rows["rows"] == [
        {
            "day": "2026-09-26", "provider": "codex", "tokens": 40, "basis": "account", "machine_tokens": 30,
            "account_tokens": 40, "cost_usd": 0.5, "cost_tokens": 30, "cost_quality": "list_price", "open": True,
        }
    ]
