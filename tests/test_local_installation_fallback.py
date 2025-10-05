"""Integration tests for local MCP installation fallback."""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

import pytest

from oneshotmcp.config import StdioServerSpec
from oneshotmcp.oauth import OAuthConfig, TokenStore
from oneshotmcp.orchestrator import DynamicOrchestrator
from oneshotmcp.registry import OAuthRequired


@pytest.mark.asyncio
async def test_orchestrator_oauth_fails_then_local_install_succeeds() -> None:
    """Test orchestrator falls back to local install when OAuth fails."""
    with tempfile.TemporaryDirectory() as tmpdir:
        token_file = Path(tmpdir) / "tokens.json"
        token_store = TokenStore(token_file=token_file)

        orchestrator = DynamicOrchestrator(
            model="openai:gpt-4.1-nano",
            initial_servers={},
            smithery_key="test-key",
            verbose=True,
            token_store=token_store,
        )

        # Mock search results
        search_results = [
            {
                "qualifiedName": "@upstash/context7-mcp",
                "displayName": "Context7",
                "description": "Context7 documentation server",
            }
        ]

        # Mock OAuth exception
        oauth_exc = OAuthRequired(
            message="OAuth required",
            server_name="@upstash/context7-mcp",
            oauth_config=OAuthConfig(
                authorization_endpoint="https://oauth.smithery.ai/authorize",
                token_endpoint="https://oauth.smithery.ai/token",
                resource="https://server.smithery.ai/context7/mcp",
                scopes=["read"],
            ),
            auth_url="https://oauth.smithery.ai/authorize?...",
        )

        # Mock Smithery metadata response
        smithery_metadata = {
            "qualifiedName": "@upstash/context7-mcp",
            "displayName": "Context7",
            "connections": [
                {
                    "type": "http",
                    "deploymentUrl": "https://server.smithery.ai/@upstash/context7-mcp/mcp",
                    "configSchema": {
                        "type": "object",
                        "properties": {},
                        "required": [],
                    },
                }
            ],
        }

        # User declines OAuth
        with patch("builtins.input", return_value="no"):
            # Mock smithery.get_server to raise OAuthRequired
            with patch.object(orchestrator.smithery, "get_server", side_effect=oauth_exc):
                # Mock httpx for metadata fetch in local install
                with patch("httpx.AsyncClient") as mock_httpx_class:
                    mock_httpx = AsyncMock()
                    mock_httpx.__aenter__.return_value = mock_httpx

                    # Mock metadata response
                    metadata_response = Mock()
                    metadata_response.status_code = 200
                    metadata_response.json.return_value = smithery_metadata
                    metadata_response.raise_for_status = Mock()
                    mock_httpx.get = AsyncMock(return_value=metadata_response)

                    mock_httpx_class.return_value = mock_httpx

                    # Mock LocalMCPInstaller
                    with patch("oneshotmcp.local_installer.LocalMCPInstaller") as MockInstaller:
                        mock_installer = Mock()
                        mock_installer.attempt_local_installation = AsyncMock(
                            return_value=StdioServerSpec(
                                command="npx",
                                args=["-y", "@upstash/context7-mcp"],
                                keep_alive=True,
                            )
                        )
                        MockInstaller.return_value = mock_installer

                        # Try adding server
                        success = await orchestrator._try_candidates(
                            ranked_servers=search_results,
                            capability="context7",
                        )

                        # Should succeed via local installation
                        assert success is True

                        # Verify server was added as stdio
                        assert "context7" in orchestrator.servers
                        spec = orchestrator.servers["context7"]
                        assert isinstance(spec, StdioServerSpec)
                        assert spec.command == "npx"
                        assert "@upstash/context7-mcp" in spec.args


@pytest.mark.asyncio
async def test_orchestrator_both_oauth_and_local_fail() -> None:
    """Test orchestrator tries next candidate when both OAuth and local fail."""
    orchestrator = DynamicOrchestrator(
        model="openai:gpt-4.1-nano",
        initial_servers={},
        smithery_key="test-key",
        verbose=False,
    )

    # Two candidates
    search_results = [
        {"qualifiedName": "@first/server", "displayName": "First"},
        {"qualifiedName": "@second/server", "displayName": "Second"},
    ]

    # Mock OAuth exception for first server
    oauth_exc = OAuthRequired(
        message="OAuth required",
        server_name="@first/server",
        oauth_config=OAuthConfig(
            authorization_endpoint="https://oauth.test/authorize",
            token_endpoint="https://oauth.test/token",
            resource="https://server.test/mcp",
        ),
        auth_url="https://oauth.test/authorize?...",
    )

    # User declines OAuth
    with patch("builtins.input", return_value="no"):
        # First server raises OAuth, second server succeeds
        with patch.object(
            orchestrator.smithery,
            "get_server",
            side_effect=[
                oauth_exc,  # First attempt
                Mock(url="https://second.server/mcp"),  # Second attempt
            ],
        ):
            # Mock LocalMCPInstaller to fail for first server
            with patch("oneshotmcp.local_installer.LocalMCPInstaller") as MockInstaller:
                mock_installer = Mock()
                mock_installer.attempt_local_installation = AsyncMock(return_value=None)
                MockInstaller.return_value = mock_installer

                # Mock httpx for metadata fetch
                with patch("httpx.AsyncClient") as mock_httpx_class:
                    mock_httpx = AsyncMock()
                    mock_httpx.__aenter__.return_value = mock_httpx
                    metadata_response = Mock()
                    metadata_response.json.return_value = {"qualifiedName": "@first/server"}
                    metadata_response.raise_for_status = Mock()
                    mock_httpx.get = AsyncMock(return_value=metadata_response)
                    mock_httpx_class.return_value = mock_httpx

                    # Try adding
                    success = await orchestrator._try_candidates(
                        ranked_servers=search_results,
                        capability="test",
                    )

                    # Should succeed with second server
                    assert success is True
                    assert "test" in orchestrator.servers


@pytest.mark.asyncio
async def test_local_installation_with_api_key_from_env() -> None:
    """Test local installation extracts API key from environment."""
    orchestrator = DynamicOrchestrator(
        model="openai:gpt-4.1-nano",
        initial_servers={},
        smithery_key="test-key",
        verbose=True,
    )

    # Smithery metadata with API key requirement
    smithery_metadata = {
        "qualifiedName": "@upstash/context7-mcp",
        "connections": [
            {
                "configSchema": {
                    "type": "object",
                    "properties": {
                        "apiKey": {
                            "type": "string",
                            "envVar": "CONTEXT7_API_KEY",
                        }
                    },
                    "required": ["apiKey"],
                }
            }
        ],
    }

    # Mock httpx for metadata fetch
    with patch("httpx.AsyncClient") as mock_httpx_class:
        mock_httpx = AsyncMock()
        mock_httpx.__aenter__.return_value = mock_httpx

        metadata_response = Mock()
        metadata_response.json.return_value = smithery_metadata
        metadata_response.raise_for_status = Mock()
        mock_httpx.get = AsyncMock(return_value=metadata_response)

        mock_httpx_class.return_value = mock_httpx

        # Mock environment variable
        with patch("os.getenv", return_value="test-api-key-from-env"):
            # Mock LocalMCPInstaller
            with patch("oneshotmcp.local_installer.subprocess.run") as mock_subprocess:
                # Mock npm available
                mock_subprocess.return_value = Mock(returncode=0)

                # Try local installation
                success = await orchestrator._try_local_installation(
                    qualified_name="@upstash/context7-mcp",
                    capability="context7",
                )

                # Should succeed
                assert success is True
                assert "context7" in orchestrator.servers

                spec = orchestrator.servers["context7"]
                assert isinstance(spec, StdioServerSpec)
                # Should have env var set
                assert spec.env.get("CONTEXT7_API_KEY") == "test-api-key-from-env"
