"""Integration test for Vercel MCP installation bug fix.

This test reproduces the exact scenario from the bug report where:
1. User requests "install vercel mcp"
2. Smithery returns irrelevant servers with score=0 (like playwright)
3. System should filter out score=0 servers and only try relevant ones
4. Config conversion should handle empty env dicts correctly
"""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, Mock, patch

from oneshotmcp.config import StdioServerSpec, servers_to_mcp_config
from oneshotmcp.orchestrator import DynamicOrchestrator


def test_config_empty_env_dict() -> None:
    """Test that empty env dict doesn't cause validation errors."""
    # This was the exact bug: env={} was converted to None
    spec = StdioServerSpec(
        command="npx",
        args=["-y", "@vercel/mcp-server"],
        env={},  # Empty dict - should be omitted in FastMCP config
        keep_alive=True,
    )

    servers = {"vercel": spec}
    config = servers_to_mcp_config(servers)

    # Should omit env field when empty (not include env=None)
    assert "env" not in config["vercel"]
    assert config["vercel"]["command"] == "npx"
    assert config["vercel"]["args"] == ["-y", "@vercel/mcp-server"]


def test_config_populated_env_dict() -> None:
    """Test that populated env dict is included correctly."""
    spec = StdioServerSpec(
        command="npx",
        args=["-y", "@vercel/mcp-server"],
        env={"VERCEL_TOKEN": "token-123"},  # Populated env
        keep_alive=True,
    )

    servers = {"vercel": spec}
    config = servers_to_mcp_config(servers)

    # Should include env when it has values
    assert "env" in config["vercel"]
    assert config["vercel"]["env"] == {"VERCEL_TOKEN": "token-123"}


@pytest.mark.asyncio
async def test_ranking_filters_irrelevant_servers() -> None:
    """Test that score=0 servers are filtered out during ranking."""
    orchestrator = DynamicOrchestrator(
        model="openai:gpt-4.1-nano",
        initial_servers={},
        smithery_key="test-key",
        verbose=False,
    )

    # Simulate Smithery search results with irrelevant servers
    search_results = [
        {
            "qualifiedName": "@cloudflare/playwright-mcp",
            "displayName": "Playwright",
            "description": "Browser automation",
        },
        {
            "qualifiedName": "@vercel/mcp-server",
            "displayName": "Vercel MCP",
            "description": "Vercel deployment tools",
        },
        {
            "qualifiedName": "@some/random-server",
            "displayName": "Random",
            "description": "Unrelated server",
        },
    ]

    # Rank for "vercel" capability
    ranked = orchestrator._rank_servers(
        capability="vercel",
        servers=search_results,
        research={"keywords": ["vercel", "deployment"]},
    )

    # Should filter out irrelevant servers (score=0)
    # Only @vercel/mcp-server should remain
    assert len(ranked) == 1
    assert ranked[0]["qualifiedName"] == "@vercel/mcp-server"


@pytest.mark.asyncio
async def test_vercel_installation_scenario() -> None:
    """Integration test: Full Vercel installation scenario with bug fixes."""
    orchestrator = DynamicOrchestrator(
        model="openai:gpt-4.1-nano",
        initial_servers={},
        smithery_key="test-key",
        verbose=True,
    )

    # Mock Smithery search (returns irrelevant + relevant servers)
    search_results = [
        {
            "qualifiedName": "@cloudflare/playwright-mcp",  # Irrelevant (score=0)
            "displayName": "Playwright",
            "description": "Browser automation",
        },
        {
            "qualifiedName": "@vercel/deployment-mcp",  # Relevant
            "displayName": "Vercel Deployment",
            "description": "Vercel deployment and project management",
        },
    ]

    with patch.object(orchestrator.smithery, "search", AsyncMock(return_value=search_results)):
        # Mock local installation attempt
        with patch("oneshotmcp.local_installer.LocalMCPInstaller") as MockInstaller:
            mock_installer = Mock()

            # First call (playwright) returns None (filtered out before attempt)
            # Second call (vercel) returns valid spec
            mock_installer.attempt_local_installation = AsyncMock(
                return_value=StdioServerSpec(
                    command="npx",
                    args=["-y", "@vercel/deployment-mcp"],
                    env={},  # Empty env - should not cause validation error
                    keep_alive=True,
                )
            )
            MockInstaller.return_value = mock_installer

            # Mock httpx for metadata fetch
            with patch("httpx.AsyncClient") as mock_httpx_class:
                mock_httpx = AsyncMock()
                mock_httpx.__aenter__.return_value = mock_httpx

                metadata_response = Mock()
                metadata_response.json.return_value = {
                    "qualifiedName": "@vercel/deployment-mcp",
                    "connections": [{"configSchema": {"properties": {}, "required": []}}],
                }
                metadata_response.raise_for_status = Mock()
                mock_httpx.get = AsyncMock(return_value=metadata_response)
                mock_httpx_class.return_value = mock_httpx

                # Mock subprocess for npm checks
                with patch("oneshotmcp.local_installer.subprocess.run") as mock_subprocess:
                    mock_subprocess.return_value = Mock(returncode=0)

                    # Try to discover vercel
                    success = await orchestrator._discover_and_add_server(
                        capability="vercel",
                    )

                    # Should succeed
                    assert success is True
                    assert "vercel" in orchestrator.servers

                    # Should have correct spec with empty env (not None)
                    spec = orchestrator.servers["vercel"]
                    assert isinstance(spec, StdioServerSpec)
                    assert spec.command == "npx"
                    assert "@vercel/deployment-mcp" in spec.args
                    # env should be empty dict, not None
                    assert spec.env == {}

                    # Verify config conversion doesn't fail
                    config = servers_to_mcp_config(orchestrator.servers)
                    assert "vercel" in config
                    # env should be omitted (not included as None)
                    assert "env" not in config["vercel"]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
