"""Unit tests for interactive OAuth prompt flow."""

from __future__ import annotations

from unittest.mock import AsyncMock, Mock, patch

import pytest

from oneshotmcp.oauth import OAuthConfig
from oneshotmcp.orchestrator import DynamicOrchestrator
from oneshotmcp.registry import OAuthRequired


@pytest.mark.asyncio
async def test_oauth_user_accepts_authorization() -> None:
    """Test OAuth flow when user accepts browser authorization."""
    orchestrator = DynamicOrchestrator(
        model="openai:gpt-4.1-nano",
        initial_servers={},
        smithery_key="test-key",
        verbose=True,
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
        auth_url="https://oauth.test/authorize?client_id=test",
    )

    with patch("builtins.input", return_value="yes"):
        with patch("oneshotmcp.oauth.BrowserAuthHandler") as mock_handler_class:
            with patch("oneshotmcp.oauth.PKCEAuthenticator") as mock_auth_class:
                # Mock handler
                mock_handler = AsyncMock()
                mock_handler.authorize = AsyncMock(return_value="test-code-123")
                mock_handler_class.return_value = mock_handler

                # Mock authenticator
                mock_auth = Mock()
                mock_auth.generate_pkce_pair = Mock(return_value=("verifier", "challenge"))
                mock_auth.build_authorization_url = Mock(return_value="https://oauth.test/authorize?...")
                mock_auth.exchange_code_for_token = AsyncMock(
                    return_value={
                        "access_token": "token-123",
                        "refresh_token": "refresh-456",
                        "token_type": "Bearer",
                        "expires_in": 3600,
                    }
                )
                mock_auth_class.return_value = mock_auth

                # Mock registry to return server spec after auth
                orchestrator.smithery.get_server = AsyncMock(
                    return_value=Mock(url="https://server.test/mcp")
                )

                # Execute
                result = await orchestrator._handle_oauth_flow(oauth_exc, "testcap")

                # Verify
                assert result is True
                mock_handler.authorize.assert_called_once()
                mock_auth.exchange_code_for_token.assert_called_once()


@pytest.mark.asyncio
async def test_oauth_user_declines_authorization() -> None:
    """Test OAuth flow when user declines browser authorization."""
    orchestrator = DynamicOrchestrator(
        model="openai:gpt-4.1-nano",
        initial_servers={},
        smithery_key="test-key",
        verbose=False,
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
        auth_url="https://oauth.test/authorize?client_id=test",
    )

    with patch("builtins.input", return_value="no"):
        with patch("oneshotmcp.oauth.BrowserAuthHandler") as mock_handler_class:
            # Execute
            result = await orchestrator._handle_oauth_flow(oauth_exc, "testcap")

            # Verify
            assert result is False
            mock_handler_class.assert_not_called()  # Should NOT open browser


@pytest.mark.asyncio
async def test_oauth_accepts_various_yes_inputs() -> None:
    """Test OAuth accepts various forms of 'yes' input."""
    orchestrator = DynamicOrchestrator(
        model="openai:gpt-4.1-nano",
        initial_servers={},
        smithery_key="test-key",
        verbose=False,
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
        auth_url="https://oauth.test/authorize?client_id=test",
    )

    yes_variations = ["yes", "YES", "Yes", "y", "Y"]

    for user_input in yes_variations:
        with patch("builtins.input", return_value=user_input):
            with patch("oneshotmcp.oauth.BrowserAuthHandler") as mock_handler_class:
                with patch("oneshotmcp.oauth.PKCEAuthenticator") as mock_auth_class:
                    # Mock setup
                    mock_handler = AsyncMock()
                    mock_handler.authorize = AsyncMock(return_value="code")
                    mock_handler_class.return_value = mock_handler

                    mock_auth = Mock()
                    mock_auth.generate_pkce_pair = Mock(return_value=("v", "c"))
                    mock_auth.build_authorization_url = Mock(return_value="url")
                    mock_auth.exchange_code_for_token = AsyncMock(
                        return_value={"access_token": "token", "token_type": "Bearer", "expires_in": 3600}
                    )
                    mock_auth_class.return_value = mock_auth

                    orchestrator.smithery.get_server = AsyncMock(return_value=Mock(url="url"))

                    # Execute
                    result = await orchestrator._handle_oauth_flow(oauth_exc, "testcap")

                    # Verify - should accept all variations
                    assert result is True, f"Failed to accept '{user_input}'"
                    mock_handler.authorize.assert_called_once()


@pytest.mark.asyncio
async def test_oauth_rejects_invalid_inputs() -> None:
    """Test OAuth rejects invalid inputs and treats them as 'no'."""
    orchestrator = DynamicOrchestrator(
        model="openai:gpt-4.1-nano",
        initial_servers={},
        smithery_key="test-key",
        verbose=False,
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
        auth_url="https://oauth.test/authorize?client_id=test",
    )

    invalid_inputs = ["", "maybe", "no", "n", "nope", "skip", "   "]

    for user_input in invalid_inputs:
        with patch("builtins.input", return_value=user_input):
            with patch("oneshotmcp.oauth.BrowserAuthHandler") as mock_handler_class:
                # Execute
                result = await orchestrator._handle_oauth_flow(oauth_exc, "testcap")

                # Verify - should reject all invalid inputs
                assert result is False, f"Incorrectly accepted '{user_input}'"
                mock_handler_class.assert_not_called()


@pytest.mark.asyncio
async def test_oauth_prompt_message_format() -> None:
    """Test OAuth prompt displays correct message to user."""
    orchestrator = DynamicOrchestrator(
        model="openai:gpt-4.1-nano",
        initial_servers={},
        smithery_key="test-key",
        verbose=True,
    )

    oauth_exc = OAuthRequired(
        message="OAuth required",
        server_name="@upstash/context7-mcp",
        oauth_config=OAuthConfig(
            authorization_endpoint="https://oauth.smithery.ai/authorize",
            token_endpoint="https://oauth.smithery.ai/token",
            resource="https://server.smithery.ai/context7/mcp",
            scopes=["read", "write"],
        ),
        auth_url="https://oauth.smithery.ai/authorize?...",
    )

    with patch("builtins.input", return_value="no") as mock_input:
        with patch("builtins.print") as mock_print:
            # Execute
            await orchestrator._handle_oauth_flow(oauth_exc, "context7")

            # Verify prompt was called with expected message
            mock_input.assert_called_once()
            call_args = mock_input.call_args[0][0]
            assert "authorization" in call_args.lower()
            assert "yes/no" in call_args.lower()

            # Verify informational message was printed
            print_calls = [str(call) for call in mock_print.call_args_list]
            assert any("@upstash/context7-mcp" in str(call) for call in print_calls)


@pytest.mark.asyncio
async def test_oauth_browser_failure_after_user_accepts() -> None:
    """Test OAuth handles browser failure gracefully after user accepts."""
    orchestrator = DynamicOrchestrator(
        model="openai:gpt-4.1-nano",
        initial_servers={},
        smithery_key="test-key",
        verbose=True,
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
        auth_url="https://oauth.test/authorize?client_id=test",
    )

    with patch("builtins.input", return_value="yes"):
        with patch("oneshotmcp.oauth.BrowserAuthHandler") as mock_handler_class:
            # Mock browser failure
            mock_handler = AsyncMock()
            mock_handler.authorize = AsyncMock(side_effect=Exception("Browser failed to open"))
            mock_handler_class.return_value = mock_handler

            # Execute
            result = await orchestrator._handle_oauth_flow(oauth_exc, "testcap")

            # Verify - should return False on browser failure
            assert result is False


@pytest.mark.asyncio
async def test_oauth_token_exchange_failure() -> None:
    """Test OAuth handles token exchange failure gracefully."""
    orchestrator = DynamicOrchestrator(
        model="openai:gpt-4.1-nano",
        initial_servers={},
        smithery_key="test-key",
        verbose=False,
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
        auth_url="https://oauth.test/authorize?client_id=test",
    )

    with patch("builtins.input", return_value="yes"):
        with patch("oneshotmcp.oauth.BrowserAuthHandler") as mock_handler_class:
            with patch("oneshotmcp.oauth.PKCEAuthenticator") as mock_auth_class:
                # Mock handler success
                mock_handler = AsyncMock()
                mock_handler.authorize = AsyncMock(return_value="code-123")
                mock_handler_class.return_value = mock_handler

                # Mock token exchange failure
                mock_auth = Mock()
                mock_auth.generate_pkce_pair = Mock(return_value=("v", "c"))
                mock_auth.build_authorization_url = Mock(return_value="url")
                mock_auth.exchange_code_for_token = AsyncMock(
                    side_effect=Exception("Token exchange failed")
                )
                mock_auth_class.return_value = mock_auth

                # Execute
                result = await orchestrator._handle_oauth_flow(oauth_exc, "testcap")

                # Verify - should return False on token exchange failure
                assert result is False
