"""Integration tests for orchestrator's seamless OAuth flow."""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

import pytest

from oneshotmcp.oauth import OAuthConfig, TokenStore
from oneshotmcp.orchestrator import DynamicOrchestrator
from oneshotmcp.registry import OAuthRequired
from tests.fixtures.mock_oauth import MockOAuthServer


@pytest.mark.asyncio
async def test_orchestrator_handles_oauth_automatically() -> None:
    """Test that orchestrator automatically triggers OAuth when OAuthRequired is raised."""
    with tempfile.TemporaryDirectory() as tmpdir:
        token_file = Path(tmpdir) / "tokens.json"
        token_store = TokenStore(token_file=token_file)

        # Create orchestrator
        orchestrator = DynamicOrchestrator(
            model="openai:gpt-4.1-nano",
            initial_servers={},
            smithery_key="test-key",
            verbose=True,
            token_store=token_store,
        )

        # Create mock OAuth config
        oauth_config = OAuthConfig(
            authorization_endpoint="https://auth.smithery.ai/authorize",
            token_endpoint="https://auth.smithery.ai/token",
            resource="https://server.smithery.ai/test/mcp",
            scopes=["read", "write"],
        )

        # Create OAuthRequired exception
        oauth_exc = OAuthRequired(
            message="OAuth required",
            server_name="@test/server",
            oauth_config=oauth_config,
            auth_url="https://auth.smithery.ai/authorize?...",
        )

        # Mock user input to accept authorization
        with patch("builtins.input", return_value="yes"):
            # Mock the OAuth flow components (imported inside _handle_oauth_flow)
            with patch("oneshotmcp.oauth.BrowserAuthHandler") as MockBrowserHandler:
                with patch("oneshotmcp.oauth.PKCEAuthenticator") as MockAuthenticator:
                    # Mock BrowserAuthHandler
                    mock_handler = AsyncMock()
                    mock_handler.authorize.return_value = "test-auth-code-123"
                    MockBrowserHandler.return_value = mock_handler

                    # Mock PKCEAuthenticator
                    mock_auth = Mock()
                    mock_auth.generate_pkce_pair.return_value = ("MOCK_VERIFIER_NOT_REAL", "MOCK_CHALLENGE_FAKE")
                    mock_auth.build_authorization_url.return_value = "https://auth.smithery.ai/authorize?..."
                    mock_auth.exchange_code_for_token = AsyncMock(return_value={
                        "access_token": "FAKE_ACCESS_TOKEN_FOR_TESTING",
                        "refresh_token": "FAKE_REFRESH_TOKEN_FOR_TESTING",
                        "token_type": "Bearer",
                        "expires_in": 3600,
                    })
                    MockAuthenticator.return_value = mock_auth

                    # Mock smithery.get_server to succeed after OAuth
                    with patch.object(orchestrator.smithery, "get_server") as mock_get_server:
                        from oneshotmcp.config import HTTPServerSpec

                        mock_get_server.return_value = HTTPServerSpec(
                            url="https://server.smithery.ai/test/mcp",
                            transport="http",
                            headers={"Authorization": "Bearer test-access-token"},
                        )

                        # Call the OAuth handler
                        success = await orchestrator._handle_oauth_flow(oauth_exc, "test")

                        # Verify OAuth flow was successful
                        assert success is True

                        # Verify browser was opened
                        mock_handler.authorize.assert_called_once()

                        # Verify tokens were exchanged
                        mock_auth.exchange_code_for_token.assert_called_once_with(
                            "test-auth-code-123",
                            "MOCK_VERIFIER_NOT_REAL",
                            "http://localhost:8765/callback",
                        )

                        # Verify tokens were saved
                        saved_tokens = token_store.get_tokens("@test/server")
                        assert saved_tokens is not None
                        assert saved_tokens["access_token"] == "FAKE_ACCESS_TOKEN_FOR_TESTING"

                        # Verify server was added to orchestrator
                        assert "test" in orchestrator.servers


@pytest.mark.asyncio
async def test_orchestrator_handles_oauth_failure_gracefully() -> None:
    """Test that orchestrator handles OAuth failures without crashing."""
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

        # Create OAuthRequired exception
        oauth_config = OAuthConfig(
            authorization_endpoint="https://auth.smithery.ai/authorize",
            token_endpoint="https://auth.smithery.ai/token",
            resource="https://server.smithery.ai/test/mcp",
        )

        oauth_exc = OAuthRequired(
            message="OAuth required",
            server_name="@test/server",
            oauth_config=oauth_config,
            auth_url="https://auth.smithery.ai/authorize?...",
        )

        # Mock browser handler to fail (user closes browser)
        with patch("oneshotmcp.oauth.BrowserAuthHandler") as MockBrowserHandler:
            from oneshotmcp.oauth import OAuthError

            mock_handler = AsyncMock()
            mock_handler.authorize.side_effect = OAuthError("User closed browser")
            MockBrowserHandler.return_value = mock_handler

            # Call OAuth handler
            success = await orchestrator._handle_oauth_flow(oauth_exc, "test")

            # Verify it returned False (failure) but didn't crash
            assert success is False

            # Verify server was NOT added
            assert "test" not in orchestrator.servers


@pytest.mark.asyncio
async def test_orchestrator_handles_browser_timeout() -> None:
    """Test that orchestrator handles browser timeout gracefully."""
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

        oauth_config = OAuthConfig(
            authorization_endpoint="https://auth.smithery.ai/authorize",
            token_endpoint="https://auth.smithery.ai/token",
            resource="https://server.smithery.ai/test/mcp",
        )

        oauth_exc = OAuthRequired(
            message="OAuth required",
            server_name="@test/server",
            oauth_config=oauth_config,
            auth_url="https://auth.smithery.ai/authorize?...",
        )

        # Mock browser handler to timeout
        with patch("oneshotmcp.oauth.BrowserAuthHandler") as MockBrowserHandler:
            import asyncio

            mock_handler = AsyncMock()
            mock_handler.authorize.side_effect = asyncio.TimeoutError()
            MockBrowserHandler.return_value = mock_handler

            # Call OAuth handler
            success = await orchestrator._handle_oauth_flow(oauth_exc, "test")

            # Verify graceful failure
            assert success is False
            assert "test" not in orchestrator.servers


@pytest.mark.asyncio
async def test_orchestrator_try_candidates_with_oauth() -> None:
    """Test that _try_candidates properly handles OAuth flow for ranked servers."""
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

        # Ranked server list (mock search results)
        ranked_servers = [
            {
                "qualifiedName": "@test/oauth-server",
                "description": "Test server requiring OAuth",
            },
            {
                "qualifiedName": "@test/backup-server",
                "description": "Backup test server",
            },
        ]

        # Mock OAuth config
        oauth_config = OAuthConfig(
            authorization_endpoint="https://auth.smithery.ai/authorize",
            token_endpoint="https://auth.smithery.ai/token",
            resource="https://server.smithery.ai/test/mcp",
        )

        # Mock the registry to raise OAuthRequired for first server
        with patch.object(orchestrator.smithery, "get_server") as mock_get_server:
            # First call raises OAuthRequired
            oauth_exc = OAuthRequired(
                message="OAuth required",
                server_name="@test/oauth-server",
                oauth_config=oauth_config,
                auth_url="https://auth.smithery.ai/authorize?...",
            )

            from oneshotmcp.config import HTTPServerSpec

            # Second call succeeds (backup server)
            backup_spec = HTTPServerSpec(
                url="https://backup.example.com/mcp",
                transport="http",
                headers={},
            )

            mock_get_server.side_effect = [oauth_exc, backup_spec]

            # Mock OAuth flow to fail (user denies)
            with patch.object(orchestrator, "_handle_oauth_flow") as mock_oauth_flow:
                mock_oauth_flow.return_value = False  # OAuth failed

                # Try candidates
                success = await orchestrator._try_candidates(ranked_servers, "test")

                # Should succeed with backup server
                assert success is True
                assert "test" in orchestrator.servers

                # Verify OAuth was attempted for first server
                mock_oauth_flow.assert_called_once()

                # Verify backup server was used
                assert orchestrator.servers["test"].url == "https://backup.example.com/mcp"


@pytest.mark.asyncio
async def test_orchestrator_multiple_oauth_servers_in_session() -> None:
    """Test that orchestrator can handle OAuth for multiple servers in one session."""
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

        # Mock successful OAuth for two different servers
        oauth_configs = [
            OAuthConfig(
                authorization_endpoint="https://auth.smithery.ai/authorize",
                token_endpoint="https://auth.smithery.ai/token",
                resource="https://server.smithery.ai/github/mcp",
            ),
            OAuthConfig(
                authorization_endpoint="https://auth.smithery.ai/authorize",
                token_endpoint="https://auth.smithery.ai/token",
                resource="https://server.smithery.ai/context7/mcp",
            ),
        ]

        oauth_exceptions = [
            OAuthRequired(
                message="OAuth required",
                server_name="@smithery/github",
                oauth_config=oauth_configs[0],
                auth_url="https://auth.smithery.ai/authorize?server=github",
            ),
            OAuthRequired(
                message="OAuth required",
                server_name="@upstash/context7-mcp",
                oauth_config=oauth_configs[1],
                auth_url="https://auth.smithery.ai/authorize?server=context7",
            ),
        ]

        # Mock user input to accept authorization for both servers
        with patch("builtins.input", return_value="yes"):
            # Mock OAuth flow to succeed for both
            with patch("oneshotmcp.oauth.BrowserAuthHandler") as MockBrowserHandler:
                with patch("oneshotmcp.oauth.PKCEAuthenticator") as MockAuthenticator:
                    # Mock successful OAuth for both servers
                    mock_handler = AsyncMock()
                    mock_handler.authorize.side_effect = ["code1", "code2"]
                    MockBrowserHandler.return_value = mock_handler

                    mock_auth = Mock()
                    mock_auth.generate_pkce_pair.return_value = ("MOCK_VERIFIER_NOT_REAL", "MOCK_CHALLENGE_FAKE")
                    mock_auth.build_authorization_url.return_value = "https://auth..."
                    mock_auth.exchange_code_for_token = AsyncMock(side_effect=[
                        {
                            "access_token": "FAKE_GITHUB_TOKEN_FOR_TESTING",
                            "refresh_token": "FAKE_GITHUB_REFRESH_FOR_TESTING",
                            "token_type": "Bearer",
                            "expires_in": 3600,
                        },
                        {
                            "access_token": "FAKE_CONTEXT7_TOKEN_FOR_TESTING",
                            "refresh_token": "FAKE_CONTEXT7_REFRESH_FOR_TESTING",
                            "token_type": "Bearer",
                            "expires_in": 3600,
                        },
                    ])
                    MockAuthenticator.return_value = mock_auth

                    # Mock smithery.get_server to return specs after OAuth
                    with patch.object(orchestrator.smithery, "get_server") as mock_get_server:
                        from oneshotmcp.config import HTTPServerSpec

                        mock_get_server.side_effect = [
                            HTTPServerSpec(
                                url="https://server.smithery.ai/github/mcp",
                                transport="http",
                                headers={"Authorization": "Bearer github-token"},
                            ),
                            HTTPServerSpec(
                                url="https://server.smithery.ai/context7/mcp",
                                transport="http",
                                headers={"Authorization": "Bearer context7-token"},
                            ),
                        ]

                        # Handle OAuth for both servers
                        success1 = await orchestrator._handle_oauth_flow(oauth_exceptions[0], "github")
                        success2 = await orchestrator._handle_oauth_flow(oauth_exceptions[1], "context7")

                        # Both should succeed
                        assert success1 is True
                        assert success2 is True

                        # Verify both servers added
                        assert "github" in orchestrator.servers
                        assert "context7" in orchestrator.servers

                        # Verify both tokens saved
                        github_tokens = token_store.get_tokens("@smithery/github")
                        context7_tokens = token_store.get_tokens("@upstash/context7-mcp")

                        assert github_tokens is not None
                        assert github_tokens["access_token"] == "FAKE_GITHUB_TOKEN_FOR_TESTING"

                        assert context7_tokens is not None
                        assert context7_tokens["access_token"] == "FAKE_CONTEXT7_TOKEN_FOR_TESTING"


@pytest.mark.asyncio
async def test_orchestrator_oauth_token_exchange_failure() -> None:
    """Test that orchestrator handles token exchange failures gracefully."""
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

        oauth_config = OAuthConfig(
            authorization_endpoint="https://auth.smithery.ai/authorize",
            token_endpoint="https://auth.smithery.ai/token",
            resource="https://server.smithery.ai/test/mcp",
        )

        oauth_exc = OAuthRequired(
            message="OAuth required",
            server_name="@test/server",
            oauth_config=oauth_config,
            auth_url="https://auth.smithery.ai/authorize?...",
        )

        # Mock browser handler to succeed but token exchange to fail
        with patch("oneshotmcp.oauth.BrowserAuthHandler") as MockBrowserHandler:
            with patch("oneshotmcp.oauth.PKCEAuthenticator") as MockAuthenticator:
                mock_handler = AsyncMock()
                mock_handler.authorize.return_value = "test-code"
                MockBrowserHandler.return_value = mock_handler

                mock_auth = Mock()
                mock_auth.generate_pkce_pair.return_value = ("verifier", "challenge")
                mock_auth.build_authorization_url.return_value = "https://auth..."

                # Token exchange fails
                from oneshotmcp.oauth import OAuthError
                mock_auth.exchange_code_for_token = AsyncMock(
                    side_effect=OAuthError("Invalid authorization code")
                )
                MockAuthenticator.return_value = mock_auth

                # Call OAuth handler
                success = await orchestrator._handle_oauth_flow(oauth_exc, "test")

                # Verify graceful failure
                assert success is False
                assert "test" not in orchestrator.servers

                # Verify no tokens were saved
                assert token_store.get_tokens("@test/server") is None
