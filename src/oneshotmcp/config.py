"""Typed server specifications and conversion helpers for FastMCP configuration."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# Context length management: limit tools loaded per server
# This prevents context window overflow with models like gpt-4.1-nano (8K tokens)
# Adjust based on your model's context window:
# - Small models (gpt-4.1-nano, 8K): 20-30 tools
# - Medium models (gpt-4-turbo, 128K): 50-100 tools
# - Large models (claude-sonnet-3.5-v2, 200K): 100+ tools
MAX_TOOLS_PER_SERVER = 30


class _BaseServer(BaseModel):
    """Base model for server specs."""

    model_config = ConfigDict(extra="forbid")


class StdioServerSpec(_BaseServer):
    """Specification for a local MCP server launched via stdio.

    NOTE:
        The FastMCP Python client typically expects HTTP/SSE transports. Using
        `StdioServerSpec` requires a different adapter or an HTTP shim in front
        of the stdio server. Keep this for future expansion or custom runners.

    Attributes:
        command: Executable to launch (e.g., "python").
        args: Positional arguments for the process.
        env: Environment variables to set for the process.
        cwd: Optional working directory.
        keep_alive: Whether the client should try to keep a persistent session.
    """

    command: str
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    cwd: str | None = None
    keep_alive: bool = True


class HTTPServerSpec(_BaseServer):
    """Specification for a remote MCP server reachable via HTTP/SSE.

    Attributes:
        url: Full endpoint URL for the MCP server (e.g., http://127.0.0.1:8000/mcp).
        transport: The transport mechanism ("http", "streamable-http", or "sse").
        headers: Optional request headers (e.g., Authorization tokens).
        auth: Optional auth hint if your FastMCP deployment consumes it.
    """

    url: str
    transport: Literal["http", "streamable-http", "sse"] = "http"
    headers: dict[str, str] = Field(default_factory=dict)
    auth: str | None = None


ServerSpec = StdioServerSpec | HTTPServerSpec
"""Union of supported server specifications."""


def servers_to_mcp_config(servers: Mapping[str, ServerSpec]) -> dict[str, dict[str, object]]:
    """Convert programmatic server specs to the FastMCP configuration dict.

    Args:
        servers: Mapping of server name to specification.

    Returns:
        Dict suitable for initializing `fastmcp.Client({"mcpServers": ...})`.
    """
    cfg: dict[str, dict[str, object]] = {}
    for name, s in servers.items():
        if isinstance(s, StdioServerSpec):
            stdio_entry: dict[str, object] = {
                "transport": "stdio",
                "command": s.command,
                "args": s.args,
                "keep_alive": s.keep_alive,
            }
            # Only include env if it has values (FastMCP doesn't accept None)
            if s.env:
                stdio_entry["env"] = s.env
            # Only include cwd if set
            if s.cwd is not None:
                stdio_entry["cwd"] = s.cwd
            cfg[name] = stdio_entry
        else:
            http_entry: dict[str, object] = {
                "transport": s.transport,
                "url": s.url,
            }
            if s.headers:
                http_entry["headers"] = s.headers
            if s.auth is not None:
                http_entry["auth"] = s.auth
            cfg[name] = http_entry
    return cfg
