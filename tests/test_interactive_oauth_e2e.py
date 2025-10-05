"""End-to-end integration test for interactive OAuth flow."""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

import pytest

from oneshotmcp.oauth import OAuthConfig, TokenStore
from oneshotmcp.orchestrator import DynamicOrchestrator
from oneshotmcp.registry import OAuthRequired


@pytest.mark.asyncio
async def test_end_to_end_interactive_oauth_flow() -> None:
    """Test complete end-to-end interactive OAuth flow with user prompt."""
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

        # Simulate OAuthRequired exception
        oauth_exc = OAuthRequired(
            message="OAuth required",
            server_name="@upstash/context7-mcp",
            oauth_config=OAuthConfig(
                authorization_endpoint="https://oauth.smithery.ai/authorize",
                token_endpoint="https://oauth.smithery.ai/token",
                resource="https://server.smithery.ai/context7/mcp",
                scopes=["read", "write"],
            ),
            auth_url="https://oauth.smithery.ai/authorize?client_id=oneshotmcp-cli",
        )

        # Mock user accepting authorization
        with patch("builtins.input", return_value="yes") as mock_input:
            with patch("oneshotmcp.oauth.BrowserAuthHandler") as MockBrowserHandler:
                with patch("oneshotmcp.oauth.PKCEAuthenticator") as MockAuthenticator:
                    # Mock browser authorization
                    mock_handler = AsyncMock()
                    mock_handler.authorize.return_value = "auth-code-xyz"
                    MockBrowserHandler.return_value = mock_handler

                    # Mock authenticator
                    mock_auth = Mock()
                    mock_auth.generate_pkce_pair.return_value = ("verifier", "challenge")
                    mock_auth.build_authorization_url.return_value = (
                        "https://oauth.smithery.ai/authorize?..."
                    )
                    mock_auth.exchange_code_for_token = AsyncMock(
                        return_value={
                            "access_token": "context7-access-token",
                            "refresh_token": "context7-refresh-token",
                            "token_type": "Bearer",
                            "expires_in": 3600,
                        }
                    )
                    MockAuthenticator.return_value = mock_auth

                    # Mock server spec retrieval after OAuth
                    with patch.object(orchestrator.smithery, "get_server") as mock_get_server:
                        from oneshotmcp.config import HTTPServerSpec

                        mock_get_server.return_value = HTTPServerSpec(
                            url="https://server.smithery.ai/context7/mcp",
                            transport="http",
                            headers={"Authorization": "Bearer context7-access-token"},
                        )

                        # Execute OAuth flow
                        success = await orchestrator._handle_oauth_flow(oauth_exc, "context7")

                        # Verify flow succeeded
                        assert success is True

                        # Verify user was prompted
                        mock_input.assert_called_once()
                        prompt = mock_input.call_args[0][0]
                        assert "authorization" in prompt.lower()
                        assert "yes/no" in prompt.lower()

                        # Verify browser was opened after user accepted
                        mock_handler.authorize.assert_called_once()

                        # Verify tokens were exchanged
                        mock_auth.exchange_code_for_token.assert_called_once_with(
                            "auth-code-xyz",
                            "verifier",
                            "http://localhost:8765/callback",
                        )

                        # Verify tokens persisted
                        saved_tokens = token_store.get_tokens("@upstash/context7-mcp")
                        assert saved_tokens is not None
                        assert saved_tokens["access_token"] == "context7-access-token"

                        # Verify server added to orchestrator
                        assert "context7" in orchestrator.servers
                        assert (
                            orchestrator.servers["context7"].url
                            == "https://server.smithery.ai/context7/mcp"
                        )


@pytest.mark.asyncio
async def test_end_to_end_user_declines_oauth() -> None:
    """Test end-to-end flow when user declines OAuth authorization."""
    with tempfile.TemporaryDirectory() as tmpdir:
        token_file = Path(tmpdir) / "tokens.json"
        token_store = TokenStore(token_file=token_file)

        orchestrator = DynamicOrchestrator(
            model="openai:gpt-4.1-nano",
            initial_servers={},
            smithery_key="test-key",
            verbose=False,
            token_store=token_store,
        )

        oauth_exc = OAuthRequired(
            message="OAuth required",
            server_name="@test/server",
            oauth_config=OAuthConfig(
                authorization_endpoint="https://oauth.test/authorize",
                token_endpoint="https://oauth.test/token",
                resource="https://server.test/mcp",
                scopes=["read"],
            ),
            auth_url="https://oauth.test/authorize?...",
        )

        # Mock user declining authorization
        with patch("builtins.input", return_value="no") as mock_input:
            with patch("oneshotmcp.oauth.BrowserAuthHandler") as MockBrowserHandler:
                # Execute OAuth flow
                success = await orchestrator._handle_oauth_flow(oauth_exc, "test")

                # Verify flow failed due to user declining
                assert success is False

                # Verify user was prompted
                mock_input.assert_called_once()

                # Verify browser was NOT opened
                MockBrowserHandler.assert_not_called()

                # Verify no tokens saved
                saved_tokens = token_store.get_tokens("@test/server")
                assert saved_tokens is None

                # Verify server NOT added
                assert "test" not in orchestrator.servers
