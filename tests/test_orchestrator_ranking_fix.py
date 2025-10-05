"""Test for server ranking algorithm fix.

This test reproduces the bug where irrelevant servers (score=0) from fuzzy
Smithery search were attempted, causing wrong servers to be installed.

Bug fix: /Users/duy/Documents/build/DeepMCPAgent/src/oneshotmcp/orchestrator.py
- Added filter to exclude servers with score=0
- Prevents attempting completely unrelated servers from fuzzy search results
"""

import pytest
from unittest.mock import AsyncMock, MagicMock

from oneshotmcp.orchestrator import DynamicOrchestrator
from oneshotmcp.config import HTTPServerSpec


class TestRankingFilter:
    """Test the server ranking and filtering logic."""

    def test_rank_servers_filters_irrelevant(self):
        """Test that servers with score=0 are filtered out."""
        # Create minimal orchestrator (doesn't need real model/servers for this test)
        orchestrator = DynamicOrchestrator(
            model=MagicMock(),
            initial_servers={},
            smithery_key="fake-key",
            verbose=True,
        )

        # Simulate Smithery search results for "vercel" that include irrelevant servers
        servers = [
            {
                "qualifiedName": "@cloudflare/playwright-mcp",
                "name": "playwright-mcp",
                "description": "Playwright MCP server for browser automation",
            },
            {
                "qualifiedName": "@modelcontextprotocol/server-vercel",
                "name": "vercel",
                "description": "Vercel MCP server for deployment",
            },
            {
                "qualifiedName": "@vercel/mcp-server",
                "name": "vercel-mcp",
                "description": "Official Vercel MCP server",
            },
            {
                "qualifiedName": "@random/unrelated-server",
                "name": "random",
                "description": "Some random server",
            },
        ]

        # Rank servers for "vercel" capability
        ranked = orchestrator._rank_servers(
            capability="vercel",
            servers=servers,
            research={},  # No research data
        )

        # Should only include servers that match "vercel"
        assert len(ranked) == 2, "Should filter out servers with score=0"

        # Check that only Vercel servers are included
        qualified_names = [s.get("qualifiedName") for s in ranked]
        assert "@modelcontextprotocol/server-vercel" in qualified_names
        assert "@vercel/mcp-server" in qualified_names

        # Irrelevant servers should be excluded
        assert "@cloudflare/playwright-mcp" not in qualified_names
        assert "@random/unrelated-server" not in qualified_names

    def test_rank_servers_exact_match_priority(self):
        """Test that exact matches in qualified name get highest priority."""
        orchestrator = DynamicOrchestrator(
            model=MagicMock(),
            initial_servers={},
            smithery_key="fake-key",
            verbose=False,
        )

        servers = [
            {
                "qualifiedName": "@other/unrelated",
                "name": "github-mcp",  # Match in name only
                "description": "Some GitHub server",
            },
            {
                "qualifiedName": "@official/github",  # Match in qualified name
                "name": "official",
                "description": "Official GitHub MCP server",
            },
        ]

        ranked = orchestrator._rank_servers(
            capability="github",
            servers=servers,
            research={},
        )

        # Both should be included (one matches in qualified name, one in name)
        assert len(ranked) == 2

        # Qualified name match (score=100) should be first, name match (score=80) second
        assert ranked[0]["qualifiedName"] == "@official/github"

    def test_rank_servers_name_match(self):
        """Test that matches in server name are ranked correctly."""
        orchestrator = DynamicOrchestrator(
            model=MagicMock(),
            initial_servers={},
            smithery_key="fake-key",
            verbose=False,
        )

        servers = [
            {
                "qualifiedName": "@company/mcp-weather",
                "name": "weather",
                "description": "Weather data server",
            },
            {
                "qualifiedName": "@other/climate",
                "name": "climate",
                "description": "Weather and climate forecasting",
            },
        ]

        ranked = orchestrator._rank_servers(
            capability="weather",
            servers=servers,
            research={},
        )

        # Both match (name and description respectively)
        assert len(ranked) == 2

        # Name match (score=80) should rank higher than description match (score=60)
        assert ranked[0]["name"] == "weather"

    def test_rank_servers_with_research_keywords(self):
        """Test that research keywords influence ranking."""
        orchestrator = DynamicOrchestrator(
            model=MagicMock(),
            initial_servers={},
            smithery_key="fake-key",
            verbose=False,
        )

        servers = [
            {
                "qualifiedName": "@company/server1",
                "name": "server1",
                "description": "Documentation search and indexing tool",
            },
            {
                "qualifiedName": "@company/server2",
                "name": "server2",
                "description": "Random server for testing",
            },
        ]

        # Research found that context7 is about documentation
        research = {
            "description": "Context7 is a documentation search tool",
            "keywords": ["documentation", "search", "indexing"],
        }

        ranked = orchestrator._rank_servers(
            capability="context7",
            servers=servers,
            research=research,
        )

        # Only server1 should match (has research keywords in description)
        # server2 has no match and should be filtered (score=0)
        assert len(ranked) == 1
        assert ranked[0]["qualifiedName"] == "@company/server1"

    def test_rank_servers_empty_list(self):
        """Test that empty server list returns empty ranked list."""
        orchestrator = DynamicOrchestrator(
            model=MagicMock(),
            initial_servers={},
            smithery_key="fake-key",
            verbose=False,
        )

        ranked = orchestrator._rank_servers(
            capability="anything",
            servers=[],
            research={},
        )

        assert ranked == []

    def test_rank_servers_all_irrelevant(self):
        """Test that all-irrelevant servers result in empty list."""
        orchestrator = DynamicOrchestrator(
            model=MagicMock(),
            initial_servers={},
            smithery_key="fake-key",
            verbose=False,
        )

        servers = [
            {
                "qualifiedName": "@random/server1",
                "name": "server1",
                "description": "Unrelated server",
            },
            {
                "qualifiedName": "@random/server2",
                "name": "server2",
                "description": "Another unrelated server",
            },
        ]

        ranked = orchestrator._rank_servers(
            capability="vercel",
            servers=servers,
            research={},
        )

        # All servers have score=0, should be filtered out
        assert ranked == []
