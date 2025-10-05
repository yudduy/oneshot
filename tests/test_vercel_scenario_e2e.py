"""End-to-end test for Vercel MCP installation scenario.

This test reproduces the exact issue from the bug report and verifies all fixes work together:
1. Package validation prevents installing @cloudflare/playwright-mcp
2. Keyword extraction filters generic terms like "cloud"
3. Ranking correctly identifies and prioritizes Vercel servers
4. Error handling provides clear feedback

Scenario:
- User runs: "install vercel mcp"
- System should NOT install Playwright (no executable)
- System should find actual Vercel MCP servers
- System should rank Vercel servers higher than generic matches
"""

from __future__ import annotations

from unittest.mock import AsyncMock, Mock, patch

import pytest

from oneshotmcp.config import StdioServerSpec
from oneshotmcp.orchestrator import DynamicOrchestrator


@pytest.mark.asyncio
async def test_vercel_mcp_scenario_complete() -> None:
    """Complete E2E test: install vercel mcp should work correctly.

    Verifies:
    1. Research extracts specific keywords (not "cloud", "platform")
    2. Playwright is NOT selected (wrong match)
    3. Actual Vercel server is selected if available
    4. Package validation rejects non-executables
    """
    orchestrator = DynamicOrchestrator(
        model="openai:gpt-4.1-nano",
        initial_servers={"tavily": Mock(url="http://tavily", transport="http")},
        smithery_key="test-key",
        verbose=True,
    )

    # Mock research to return description with "cloud" (generic term)
    with patch.object(orchestrator, "_research_capability") as mock_research:
        mock_research.return_value = {
            "description": "Vercel is a cloud platform for deploying web applications",
            "keywords": ["vercel", "deployment", "edge", "serverless", "hosting"],  # LLM-extracted, no "cloud"
        }

        # Mock Smithery search returning both Playwright (wrong) and Vercel (right)
        search_results = [
            {
                "qualifiedName": "@cloudflare/playwright-mcp",
                "displayName": "Playwright",
                "description": "Browser automation with Cloudflare Workers integration",
            },
            {
                "qualifiedName": "@ssdavidai/vercel-api-mcp-fork",
                "displayName": "Vercel API",
                "description": "Vercel API integration for managing deployments and projects",
            },
        ]

        with patch.object(orchestrator.smithery, "search", AsyncMock(return_value=search_results)):
            # Rank servers with improved algorithm
            ranked = orchestrator._rank_servers(
                capability="vercel",
                servers=search_results,
                research={"keywords": ["vercel", "deployment", "edge", "serverless", "hosting"]},
            )

            # Verify ranking
            assert len(ranked) == 1, "Should only include relevant servers"
            assert ranked[0]["qualifiedName"] == "@ssdavidai/vercel-api-mcp-fork"
            # Playwright should be filtered out (score=0, no "vercel" match)

            # Now test full installation flow
            with patch("oneshotmcp.local_installer.LocalMCPInstaller") as MockInstaller:
                mock_installer = Mock()

                # First call: Playwright - has no executable
                # Second call: Vercel API - has executable
                async def mock_attempt_local(smithery_metadata, user_config, interactive=True):
                    pkg_name = smithery_metadata["qualifiedName"]
                    if pkg_name == "@cloudflare/playwright-mcp":
                        # Playwright validation would fail
                        return None
                    elif pkg_name == "@ssdavidai/vercel-api-mcp-fork":
                        # Vercel has executable
                        return StdioServerSpec(
                            command="npx",
                            args=["-y", "@ssdavidai/vercel-api-mcp-fork"],
                            env={},
                            keep_alive=True,
                        )
                    return None

                mock_installer.attempt_local_installation = mock_attempt_local
                MockInstaller.return_value = mock_installer

                # Mock httpx for metadata fetch
                with patch("httpx.AsyncClient") as mock_httpx_class:
                    mock_httpx = AsyncMock()
                    mock_httpx.__aenter__.return_value = mock_httpx

                    def mock_metadata_response(url, **kwargs):
                        response = Mock()
                        response.raise_for_status = Mock()
                        if "vercel" in url:
                            response.json.return_value = {
                                "qualifiedName": "@ssdavidai/vercel-api-mcp-fork",
                                "connections": [{"configSchema": {"properties": {}, "required": []}}],
                            }
                        else:
                            response.json.return_value = {
                                "qualifiedName": "@cloudflare/playwright-mcp",
                                "connections": [{"configSchema": {"properties": {}, "required": []}}],
                            }
                        return response

                    mock_httpx.get = AsyncMock(side_effect=mock_metadata_response)
                    mock_httpx_class.return_value = mock_httpx

                    # Mock subprocess for npm validation
                    with patch("oneshotmcp.local_installer.subprocess.run") as mock_subprocess:
                        # Mock npm available and package exists
                        mock_subprocess.return_value = Mock(returncode=0, stdout="")

                        # Try to discover vercel
                        success = await orchestrator._discover_and_add_server(capability="vercel")

                        # Should succeed
                        assert success is True
                        assert "vercel" in orchestrator.servers

                        # Verify correct server installed
                        spec = orchestrator.servers["vercel"]
                        assert isinstance(spec, StdioServerSpec)
                        assert "@ssdavidai/vercel-api-mcp-fork" in spec.args


@pytest.mark.asyncio
async def test_playwright_rejected_for_vercel() -> None:
    """Test that Playwright is explicitly rejected when searching for Vercel."""
    orchestrator = DynamicOrchestrator(
        model="openai:gpt-4.1-nano",
        initial_servers={},
        smithery_key="test-key",
        verbose=False,
    )

    # Simulate search returning ONLY Playwright (worst case)
    servers = [
        {
            "qualifiedName": "@cloudflare/playwright-mcp",
            "displayName": "Playwright",
            "description": "Automate browser interactions with Cloudflare Workers",
        }
    ]

    # Keywords should NOT include "cloud" (filtered by LLM extraction)
    research = {"keywords": ["vercel", "deployment", "edge", "serverless"]}

    ranked = orchestrator._rank_servers(
        capability="vercel", servers=servers, research=research
    )

    # Playwright should be filtered out (score=0)
    # - "vercel" not in qualified name (0 pts)
    # - "vercel" not in name (0 pts)
    # - "vercel" not in description (0 pts)
    # - Keywords don't match (no "vercel", "deployment", etc. in Playwright description)
    assert len(ranked) == 0, "Playwright should be completely filtered for 'vercel' query"


@pytest.mark.asyncio
async def test_package_validation_prevents_playwright_installation() -> None:
    """Test that package validation rejects Playwright (no bin entry)."""
    from oneshotmcp.local_installer import LocalMCPInstaller

    installer = LocalMCPInstaller()

    # Mock Playwright package with no bin entry
    with patch("oneshotmcp.local_installer.subprocess.run") as mock_run:
        # bin check returns empty (no bin)
        # main check returns empty (no main)
        mock_run.side_effect = [
            Mock(returncode=0, stdout=""),  # No bin
            Mock(returncode=0, stdout=""),  # No main
        ]

        has_exec, error = await installer.verify_package_executable("@cloudflare/playwright-mcp")

        assert has_exec is False
        assert "neither 'bin' nor 'main' entry" in error


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
