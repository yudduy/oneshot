"""Test for StdioServerSpec env field conversion fix.

This test reproduces the bug where empty env dict was converted to None,
causing FastMCP validation errors.

Bug fix: /Users/duy/Documents/build/DeepMCPAgent/src/oneshotmcp/config.py
- Changed: "env": s.env or None
- To: Conditionally include env only if it has values
"""

import pytest
from fastmcp.mcp_config import MCPConfig

from oneshotmcp.config import StdioServerSpec, servers_to_mcp_config


def test_empty_env_dict_conversion():
    """Test that empty env dict is handled correctly (not converted to None)."""
    spec = StdioServerSpec(
        command="npx",
        args=["-y", "@cloudflare/playwright-mcp"],
        env={},  # Empty dict - this was causing the bug
        keep_alive=True,
    )

    servers = {"test": spec}
    config = servers_to_mcp_config(servers)

    # env should be omitted entirely, not set to None
    assert "env" not in config["test"], "Empty env dict should be omitted from config"
    assert config["test"]["command"] == "npx"
    assert config["test"]["args"] == ["-y", "@cloudflare/playwright-mcp"]
    assert config["test"]["keep_alive"] is True

    # Verify FastMCP validation passes
    mcp_config = MCPConfig.from_dict({"mcpServers": config})
    assert mcp_config is not None


def test_populated_env_dict_conversion():
    """Test that env dict with values is included correctly."""
    spec = StdioServerSpec(
        command="npx",
        args=["-y", "some-package"],
        env={"API_KEY": "secret123", "DEBUG": "true"},
        keep_alive=True,
    )

    servers = {"test": spec}
    config = servers_to_mcp_config(servers)

    # env should be included with values
    assert "env" in config["test"]
    assert config["test"]["env"] == {"API_KEY": "secret123", "DEBUG": "true"}

    # Verify FastMCP validation passes
    mcp_config = MCPConfig.from_dict({"mcpServers": config})
    assert mcp_config is not None


def test_default_env_conversion():
    """Test that default env (empty dict from default_factory) is handled correctly."""
    spec = StdioServerSpec(
        command="node",
        args=["server.js"],
        # env not specified, uses default_factory=dict → {}
    )

    servers = {"test": spec}
    config = servers_to_mcp_config(servers)

    # env should be omitted (empty default dict)
    assert "env" not in config["test"]

    # Verify FastMCP validation passes
    mcp_config = MCPConfig.from_dict({"mcpServers": config})
    assert mcp_config is not None


def test_cwd_none_conversion():
    """Test that cwd=None is handled correctly (omitted, not included as None)."""
    spec = StdioServerSpec(
        command="python",
        args=["app.py"],
        cwd=None,  # Explicitly None
        keep_alive=False,
    )

    servers = {"test": spec}
    config = servers_to_mcp_config(servers)

    # cwd should be omitted when None
    assert "cwd" not in config["test"]

    # Verify FastMCP validation passes
    mcp_config = MCPConfig.from_dict({"mcpServers": config})
    assert mcp_config is not None


def test_cwd_with_value():
    """Test that cwd with value is included correctly."""
    spec = StdioServerSpec(
        command="python",
        args=["app.py"],
        cwd="/tmp/project",
        keep_alive=False,
    )

    servers = {"test": spec}
    config = servers_to_mcp_config(servers)

    # cwd should be included
    assert config["test"]["cwd"] == "/tmp/project"

    # Verify FastMCP validation passes
    mcp_config = MCPConfig.from_dict({"mcpServers": config})
    assert mcp_config is not None


def test_multiple_servers_mixed_config():
    """Test multiple servers with mixed env/cwd configurations."""
    servers = {
        "server1": StdioServerSpec(
            command="npx",
            args=["-y", "package1"],
            env={},  # Empty
            cwd=None,  # None
        ),
        "server2": StdioServerSpec(
            command="npx",
            args=["-y", "package2"],
            env={"KEY": "value"},  # Populated
            cwd="/tmp",  # Set
        ),
        "server3": StdioServerSpec(
            command="node",
            args=["script.js"],
            # Defaults
        ),
    }

    config = servers_to_mcp_config(servers)

    # Server 1: no env, no cwd
    assert "env" not in config["server1"]
    assert "cwd" not in config["server1"]

    # Server 2: has env and cwd
    assert config["server2"]["env"] == {"KEY": "value"}
    assert config["server2"]["cwd"] == "/tmp"

    # Server 3: no env, no cwd (defaults)
    assert "env" not in config["server3"]
    assert "cwd" not in config["server3"]

    # Verify FastMCP validation passes for all
    mcp_config = MCPConfig.from_dict({"mcpServers": config})
    assert mcp_config is not None
