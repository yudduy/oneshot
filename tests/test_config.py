from __future__ import annotations

from oneshotmcp.config import HTTPServerSpec, StdioServerSpec, servers_to_mcp_config


def test_servers_to_mcp_config_http_only() -> None:
    servers = {
        "math": HTTPServerSpec(
            url="http://127.0.0.1:8000/mcp",
            transport="http",
            headers={"Authorization": "Bearer X"},
            auth=None,
        )
    }
    cfg = servers_to_mcp_config(servers)
    assert "math" in cfg
    entry = cfg["math"]
    assert entry["transport"] == "http"
    assert entry["url"] == "http://127.0.0.1:8000/mcp"
    assert entry["headers"] == {"Authorization": "Bearer X"}
    assert "auth" not in entry  # None should be omitted


def test_servers_to_mcp_config_stdio() -> None:
    servers = {
        "local": StdioServerSpec(
            command="python",
            args=["-m", "cool.server"],
            env={"API_KEY": "abc"},
            cwd=None,
            keep_alive=False,
        )
    }
    cfg = servers_to_mcp_config(servers)
    entry = cfg["local"]
    assert entry["transport"] == "stdio"
    assert entry["command"] == "python"
    assert entry["args"] == ["-m", "cool.server"]
    # env with values is included
    assert entry["env"] == {"API_KEY": "abc"}
    # cwd=None is omitted (FastMCP doesn't accept None)
    assert "cwd" not in entry
    assert entry["keep_alive"] is False
