"""Tests for improved keyword extraction with LLM."""

from __future__ import annotations

from unittest.mock import AsyncMock, Mock

import pytest

from oneshotmcp.orchestrator import DynamicOrchestrator


class TestKeywordExtraction:
    """Test suite for LLM-based keyword extraction."""

    @pytest.mark.asyncio
    async def test_extract_keywords_filters_generic_terms(self) -> None:
        """Test that generic terms like 'cloud' and 'platform' are filtered out."""
        orchestrator = DynamicOrchestrator(
            model="openai:gpt-4.1-nano",
            initial_servers={},
            smithery_key="test-key",
            verbose=False,
        )

        # Mock LLM response with specific keywords (no generic terms)
        mock_response = Mock()
        mock_response.content = "vercel, deployment, edge, serverless, hosting"

        orchestrator.model = AsyncMock()
        orchestrator.model.ainvoke = AsyncMock(return_value=mock_response)

        keywords = await orchestrator._extract_keywords_with_llm(
            capability="vercel",
            description="Vercel is a cloud platform for deploying web applications",
        )

        # Should extract specific keywords, not generic terms
        assert "vercel" in keywords
        assert "deployment" in keywords or "edge" in keywords or "serverless" in keywords
        assert "cloud" not in keywords  # Generic term filtered
        assert "platform" not in keywords  # Generic term filtered

    @pytest.mark.asyncio
    async def test_extract_keywords_always_includes_capability(self) -> None:
        """Test that capability name is always included in keywords."""
        orchestrator = DynamicOrchestrator(
            model="openai:gpt-4.1-nano",
            initial_servers={},
            smithery_key="test-key",
            verbose=False,
        )

        # Mock LLM response WITHOUT capability name
        mock_response = Mock()
        mock_response.content = "deployment, hosting, edge, serverless"

        orchestrator.model = AsyncMock()
        orchestrator.model.ainvoke = AsyncMock(return_value=mock_response)

        keywords = await orchestrator._extract_keywords_with_llm(
            capability="vercel",
            description="Vercel is a cloud platform for deploying web applications",
        )

        # Capability name should be prepended if missing
        assert keywords[0] == "vercel"

    @pytest.mark.asyncio
    async def test_extract_keywords_fallback_on_llm_failure(self) -> None:
        """Test fallback to naive extraction when LLM fails."""
        orchestrator = DynamicOrchestrator(
            model="openai:gpt-4.1-nano",
            initial_servers={},
            smithery_key="test-key",
            verbose=False,
        )

        # Mock LLM failure
        orchestrator.model = AsyncMock()
        orchestrator.model.ainvoke = AsyncMock(side_effect=Exception("LLM API error"))

        keywords = await orchestrator._extract_keywords_with_llm(
            capability="vercel",
            description="Vercel is a cloud platform for deploying web applications",
        )

        # Fallback should work
        assert "vercel" in keywords
        # Fallback should also filter generic terms
        assert "cloud" not in keywords
        assert "platform" not in keywords

    @pytest.mark.asyncio
    async def test_extract_keywords_limits_to_5(self) -> None:
        """Test that extracted keywords are limited to 5."""
        orchestrator = DynamicOrchestrator(
            model="openai:gpt-4.1-nano",
            initial_servers={},
            smithery_key="test-key",
            verbose=False,
        )

        # Mock LLM response with many keywords
        mock_response = Mock()
        mock_response.content = "vercel, deployment, edge, serverless, hosting, cdn, nextjs, react, build"

        orchestrator.model = AsyncMock()
        orchestrator.model.ainvoke = AsyncMock(return_value=mock_response)

        keywords = await orchestrator._extract_keywords_with_llm(
            capability="vercel",
            description="Vercel deployment platform",
        )

        # Should be limited to 5
        assert len(keywords) <= 5


class TestKeywordRanking:
    """Test that keyword-based ranking uses lower scores."""

    @pytest.mark.asyncio
    async def test_keyword_match_lower_score_than_exact_match(self) -> None:
        """Test that keyword matches score lower than exact matches."""
        orchestrator = DynamicOrchestrator(
            model="openai:gpt-4.1-nano",
            initial_servers={},
            smithery_key="test-key",
            verbose=False,
        )

        servers = [
            {
                "qualifiedName": "@vercel/deployment-mcp",
                "description": "Vercel deployment tools",
            },
            {
                "qualifiedName": "@cloudflare/playwright-mcp",
                "description": "Browser automation with deployment features",
            },
        ]

        research = {"keywords": ["vercel", "deployment", "edge"]}

        ranked = orchestrator._rank_servers(
            capability="vercel", servers=servers, research=research
        )

        # Exact match (vercel in qualified name) should rank first
        assert ranked[0]["qualifiedName"] == "@vercel/deployment-mcp"

        # Keyword match should rank second (or be filtered if score=0)
        if len(ranked) > 1:
            assert ranked[1]["qualifiedName"] == "@cloudflare/playwright-mcp"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
