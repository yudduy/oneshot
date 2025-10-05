"""Integration test for stdio server installation and agent rebuild.

This test reproduces the full bug scenario:
1. Local installer creates StdioServerSpec with empty env dict
2. Config conversion to FastMCP format
3. Agent rebuild with FastMCP validation

Bug reproduction: User runs "install vercel mcp", system installs @cloudflare/playwright-mcp
locally, creates StdioServerSpec with env={}, conversion fails with Pydantic validation error.

Fixed in:
- /Users/duy/Documents/build/DeepMCPAgent/src/oneshotmcp/config.py (env field handling)
- /Users/duy/Documents/build/DeepMCPAgent/src/oneshotmcp/orchestrator.py (ranking filter)
"""

import pytest
from fastmcp.mcp_config import MCPConfig

from oneshotmcp.config import StdioServerSpec, servers_to_mcp_config
from oneshotmcp.local_installer import LocalMCPInstaller


class TestStdioInstallationIntegration:
    """End-to-end test for stdio server installation."""

    def test_local_installer_creates_valid_spec(self):
        """Test that LocalMCPInstaller creates FastMCP-compatible specs."""
        installer = LocalMCPInstaller()

        # Simulate creating a spec for a package with no config requirements
        spec = installer.create_stdio_server_spec(
            package_name="@cloudflare/playwright-mcp",
            config_requirements={"required": [], "properties": {}},
            user_config={},  # No user config
        )

        # Verify spec is created
        assert spec.command == "npx"
        assert spec.args == ["-y", "@cloudflare/playwright-mcp"]
        assert spec.env == {}  # Empty dict from default
        assert spec.keep_alive is True

        # Convert to FastMCP config
        config = servers_to_mcp_config({"test": spec})

        # Verify env is omitted (not set to None)
        assert "env" not in config["test"]
        assert config["test"]["command"] == "npx"
        assert config["test"]["args"] == ["-y", "@cloudflare/playwright-mcp"]

        # Verify FastMCP validation passes (this was failing before fix)
        mcp_config = MCPConfig.from_dict({"mcpServers": config})
        assert mcp_config is not None

    def test_local_installer_with_env_vars(self):
        """Test that env vars from config requirements are handled correctly."""
        installer = LocalMCPInstaller()

        # Simulate a package that requires API key via env var
        config_requirements = {
            "required": ["apiKey"],
            "properties": {
                "apiKey": {
                    "type": "string",
                    "description": "API key for authentication",
                    "envVar": "SERVICE_API_KEY",
                }
            },
        }

        spec = installer.create_stdio_server_spec(
            package_name="@example/service-mcp",
            config_requirements=config_requirements,
            user_config={"apiKey": "secret123"},
        )

        # Verify env var is set
        assert spec.env == {"SERVICE_API_KEY": "secret123"}

        # Convert to FastMCP config
        config = servers_to_mcp_config({"test": spec})

        # Verify env is included (has values)
        assert config["test"]["env"] == {"SERVICE_API_KEY": "secret123"}

        # Verify FastMCP validation passes
        mcp_config = MCPConfig.from_dict({"mcpServers": config})
        assert mcp_config is not None

    def test_vercel_scenario_simulation(self):
        """Simulate the exact bug scenario: install vercel mcp → wrong server selected."""
        # This simulates what would happen if Playwright was selected
        # (before ranking fix, score=0 servers were attempted)

        installer = LocalMCPInstaller()

        # Create spec for Playwright (wrong server, but npm-installable)
        playwright_spec = installer.create_stdio_server_spec(
            package_name="@cloudflare/playwright-mcp",
            config_requirements={"required": [], "properties": {}},
            user_config={},
        )

        # This was causing the validation error
        servers = {"vercel": playwright_spec}  # Named "vercel" but is actually playwright
        config = servers_to_mcp_config(servers)

        # Before fix: This would fail with "env: Input should be a valid dictionary"
        # After fix: This passes (env is omitted)
        mcp_config = MCPConfig.from_dict({"mcpServers": config})
        assert mcp_config is not None

        # Verify config structure
        assert "env" not in config["vercel"]  # Empty env omitted
        assert config["vercel"]["command"] == "npx"

    def test_multiple_stdio_servers_rebuild(self):
        """Test agent rebuild scenario with multiple stdio servers."""
        installer = LocalMCPInstaller()

        # Create multiple servers with different env configurations
        server1 = installer.create_stdio_server_spec(
            package_name="@package/server1",
            config_requirements={"required": [], "properties": {}},
            user_config={},  # No env
        )

        server2 = installer.create_stdio_server_spec(
            package_name="@package/server2",
            config_requirements={
                "required": ["key"],
                "properties": {"key": {"envVar": "API_KEY"}},
            },
            user_config={"key": "value123"},  # Has env
        )

        servers = {"server1": server1, "server2": server2}
        config = servers_to_mcp_config(servers)

        # Verify mixed env handling
        assert "env" not in config["server1"]  # Empty omitted
        assert config["server2"]["env"] == {"API_KEY": "value123"}  # Populated included

        # Verify FastMCP validation passes for all servers
        mcp_config = MCPConfig.from_dict({"mcpServers": config})
        assert mcp_config is not None

        # Verify we can access both servers
        assert "server1" in mcp_config.mcpServers
        assert "server2" in mcp_config.mcpServers
