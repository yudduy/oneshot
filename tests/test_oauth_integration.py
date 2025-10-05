"""Integration tests for OAuth 2.1 end-to-end flow."""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

import pytest

from oneshotmcp.config import HTTPServerSpec
from oneshotmcp.oauth import TokenStore, discover_oauth_metadata
from oneshotmcp.registry import OAuthRequired, SmitheryAPIClient
from tests.fixtures.mock_oauth import MockOAuthServer


@pytest.mark.asyncio
async def test_full_oauth_flow_with_mock_server() -> None:
    """Test complete OAuth flow with RFC 8414/9728 discovery (no hardcoded endpoints)."""
    # Mock the RFC discovery endpoints to avoid hitting real servers
    resource_url = "https://server.smithery.ai/mcp"

    # Mock httpx to return proper OAuth discovery metadata
    with patch("httpx.AsyncClient") as mock_client_class:
        mock_client = AsyncMock()
        mock_client_class.return_value.__aenter__.return_value = mock_client

        # Mock RFC 9728 Protected Resource Metadata response
        oauth_discovery_response = Mock()
        oauth_discovery_response.status_code = 200
        oauth_discovery_response.json.return_value = {
            "resource": resource_url,
            "authorization_servers": ["https://auth.example.com"],
            "authorization_endpoint": "https://auth.example.com/oauth/authorize",
            "token_endpoint": "https://auth.example.com/oauth/token",
            "scopes_supported": ["read", "write"],
        }
        oauth_discovery_response.raise_for_status = Mock()

        mock_client.get = AsyncMock(return_value=oauth_discovery_response)

        # Discover OAuth metadata (should use RFC discovery)
        oauth_config = await discover_oauth_metadata(resource_url)

        # Verify discovered endpoints (not hardcoded)
        assert oauth_config.authorization_endpoint == "https://auth.example.com/oauth/authorize"
        assert oauth_config.token_endpoint == "https://auth.example.com/oauth/token"
        assert oauth_config.resource == resource_url


@pytest.mark.asyncio
async def test_registry_oauth_required_exception() -> None:
    """Test that registry raises OAuthRequired for Smithery servers without tokens."""
    with tempfile.TemporaryDirectory() as tmpdir:
        token_file = Path(tmpdir) / "tokens.json"
        token_store = TokenStore(token_file=token_file)

        # Create registry client
        client = SmitheryAPIClient(
            api_key="test-key",
            token_store=token_store,
        )

        # Mock Smithery API response for get_server
        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client_class.return_value.__aenter__.return_value = mock_client

            # Mock server metadata response
            server_metadata_response = Mock()
            server_metadata_response.status_code = 200
            server_metadata_response.json.return_value = {
                "qualified_name": "@test/server",
                "connections": [
                    {
                        "deploymentUrl": "https://server.smithery.ai/test/mcp",
                        "type": "http",
                    }
                ],
            }
            server_metadata_response.raise_for_status = Mock()

            # Mock OAuth discovery response (Smithery uses auth.smithery.ai)
            oauth_discovery_response = Mock()
            oauth_discovery_response.status_code = 200
            oauth_discovery_response.json.return_value = {
                "resource": "https://server.smithery.ai/test/mcp",
                "authorization_endpoint": "https://auth.smithery.ai/oauth/authorize",
                "token_endpoint": "https://auth.smithery.ai/oauth/token",
            }
            oauth_discovery_response.raise_for_status = Mock()

            # Set up get responses (first for server metadata, second for OAuth discovery)
            mock_client.get = AsyncMock(
                side_effect=[server_metadata_response, oauth_discovery_response]
            )

            # Try to get server (should raise OAuthRequired)
            with pytest.raises(OAuthRequired) as exc_info:
                await client.get_server("@test/server")

            # Verify exception contains OAuth config (Smithery hardcoded endpoints)
            assert exc_info.value.server_name == "@test/server"
            assert exc_info.value.oauth_config.authorization_endpoint == "https://auth.smithery.ai/oauth/authorize"
            assert exc_info.value.oauth_config.token_endpoint == "https://auth.smithery.ai/oauth/token"
            # Note: In production, the orchestrator catches this and handles OAuth automatically


@pytest.mark.asyncio
async def test_registry_with_stored_tokens() -> None:
    """Test that registry uses stored tokens for Smithery servers."""
    with tempfile.TemporaryDirectory() as tmpdir:
        token_file = Path(tmpdir) / "tokens.json"
        token_store = TokenStore(token_file=token_file)

        # Store tokens for the server
        token_store.save_tokens(
            "@test/server",
            {
                "access_token": "test-access-token-123",
                "refresh_token": "test-refresh-token-456",
                "token_type": "Bearer",
                "expires_in": 3600,
            },
        )

        # Create registry client
        client = SmitheryAPIClient(
            api_key="test-key",
            token_store=token_store,
        )

        # Mock Smithery API response
        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client_class.return_value.__aenter__.return_value = mock_client

            server_response = Mock()
            server_response.status_code = 200
            server_response.json.return_value = {
                "qualified_name": "@test/server",
                "connections": [
                    {
                        "deploymentUrl": "https://server.smithery.ai/test/mcp",
                        "type": "http",
                    }
                ],
            }
            server_response.raise_for_status = Mock()

            mock_client.get = AsyncMock(return_value=server_response)

            # Get server
            spec = await client.get_server("@test/server")

            # Verify spec has OAuth token in headers
            assert isinstance(spec, HTTPServerSpec)
            assert "Authorization" in spec.headers
            assert spec.headers["Authorization"] == "Bearer test-access-token-123"


@pytest.mark.asyncio
async def test_automatic_token_refresh_scenario() -> None:
    """Test token refresh flow when access token expires."""
    # Setup mock OAuth server
    oauth_server = MockOAuthServer()

    # Get initial tokens
    from oneshotmcp.oauth import PKCEAuthenticator

    auth = PKCEAuthenticator(
        authorization_endpoint=oauth_server.base_url + "/authorize",
        token_endpoint=oauth_server.base_url + "/token",
        client_id="test-client",
    )

    # Simulate initial authorization
    verifier, challenge = auth.generate_pkce_pair()
    code = oauth_server.create_authorization_code(
        "test-client", "http://localhost:8765/callback", challenge
    )
    initial_tokens = oauth_server.exchange_code_for_token(
        code, "test-client", "http://localhost:8765/callback", verifier
    )

    # Store tokens
    with tempfile.TemporaryDirectory() as tmpdir:
        token_file = Path(tmpdir) / "tokens.json"
        token_store = TokenStore(token_file=token_file)
        token_store.save_tokens("test-server", initial_tokens)

        # Simulate token expiration (access token no longer valid)
        # In a real scenario, the MCP server would return 401

        # Refresh the token
        refresh_token = initial_tokens["refresh_token"]
        new_tokens = oauth_server.refresh_access_token(refresh_token)

        # Verify new tokens are different
        assert new_tokens["access_token"] != initial_tokens["access_token"]
        assert new_tokens["refresh_token"] != initial_tokens["refresh_token"]

        # Update stored tokens
        token_store.save_tokens("test-server", new_tokens)

        # Verify updated tokens are persisted
        retrieved = token_store.get_tokens("test-server")
        assert retrieved is not None
        assert retrieved["access_token"] == new_tokens["access_token"]


@pytest.mark.asyncio
async def test_self_hosted_server_no_oauth_required() -> None:
    """Test that self-hosted servers don't trigger OAuth flow."""
    with tempfile.TemporaryDirectory() as tmpdir:
        token_file = Path(tmpdir) / "tokens.json"
        token_store = TokenStore(token_file=token_file)

        client = SmitheryAPIClient(
            api_key="test-key",
            token_store=token_store,
        )

        # Mock Smithery API response for self-hosted server
        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client_class.return_value.__aenter__.return_value = mock_client

            server_response = Mock()
            server_response.status_code = 200
            server_response.json.return_value = {
                "qualified_name": "@company/custom-server",
                "connections": [
                    {
                        "deploymentUrl": "https://custom.company.com/mcp",  # NOT server.smithery.ai
                        "type": "http",
                    }
                ],
            }
            server_response.raise_for_status = Mock()

            mock_client.get = AsyncMock(return_value=server_response)

            # Get server (should NOT raise OAuthRequired)
            spec = await client.get_server("@company/custom-server")

            # Verify spec was created successfully
            assert isinstance(spec, HTTPServerSpec)
            assert spec.url == "https://custom.company.com/mcp"
            assert spec.headers == {}  # No OAuth headers for self-hosted


@pytest.mark.asyncio
async def test_token_rotation_on_refresh() -> None:
    """Test that OAuth 2.1 refresh token rotation works correctly."""
    oauth_server = MockOAuthServer()

    # Get initial tokens
    from oneshotmcp.oauth import PKCEAuthenticator
    verifier, challenge = PKCEAuthenticator.generate_pkce_pair()
    code = oauth_server.create_authorization_code(
        "test-client", "http://localhost/cb", challenge
    )
    tokens1 = oauth_server.exchange_code_for_token(
        code, "test-client", "http://localhost/cb", verifier
    )

    # First refresh
    tokens2 = oauth_server.refresh_access_token(tokens1["refresh_token"])

    # Verify tokens changed
    assert tokens2["access_token"] != tokens1["access_token"]
    assert tokens2["refresh_token"] != tokens1["refresh_token"]

    # Old refresh token should be invalid now (OAuth 2.1 rotation)
    with pytest.raises(ValueError, match="Invalid refresh token"):
        oauth_server.refresh_access_token(tokens1["refresh_token"])

    # New refresh token should work
    tokens3 = oauth_server.refresh_access_token(tokens2["refresh_token"])
    assert tokens3["access_token"] != tokens2["access_token"]


@pytest.mark.asyncio
async def test_pkce_code_verifier_validation() -> None:
    """Test that PKCE code verifier is properly validated during token exchange."""
    oauth_server = MockOAuthServer()

    from oneshotmcp.oauth import PKCEAuthenticator
    verifier, challenge = PKCEAuthenticator.generate_pkce_pair()
    code = oauth_server.create_authorization_code(
        "test-client", "http://localhost/cb", challenge
    )

    # Try to exchange with WRONG verifier
    wrong_verifier = "wrong-verifier-12345"

    with pytest.raises(ValueError, match="Code verifier validation failed"):
        oauth_server.exchange_code_for_token(
            code, "test-client", "http://localhost/cb", wrong_verifier
        )

    # Correct verifier should still work (code not consumed yet)
    tokens = oauth_server.exchange_code_for_token(
        code, "test-client", "http://localhost/cb", verifier
    )

    assert "access_token" in tokens
