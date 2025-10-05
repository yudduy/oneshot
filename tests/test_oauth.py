"""Unit tests for OAuth 2.1 PKCE authentication."""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

import pytest

from oneshotmcp.oauth import (
    OAuthConfig,
    OAuthError,
    PKCEAuthenticator,
    TokenStore,
)
from tests.fixtures.mock_oauth import MockOAuthServer


class TestPKCEAuthenticator:
    """Tests for PKCE code generation and OAuth flow."""

    def test_generate_pkce_pair(self) -> None:
        """Test PKCE verifier and challenge generation."""
        verifier, challenge = PKCEAuthenticator.generate_pkce_pair()

        # Verify verifier length (should be 64 chars)
        assert len(verifier) == 64
        assert verifier.replace("-", "").replace("_", "").isalnum()

        # Verify challenge is base64url SHA256 (always 43 chars)
        assert len(challenge) == 43
        assert challenge.replace("-", "").replace("_", "").isalnum()

        # Verify deterministic: same verifier → same challenge
        import hashlib
        import base64

        expected_challenge_bytes = hashlib.sha256(verifier.encode()).digest()
        expected_challenge = base64.urlsafe_b64encode(expected_challenge_bytes).decode().rstrip("=")
        assert challenge == expected_challenge

    def test_build_authorization_url(self) -> None:
        """Test authorization URL construction."""
        auth = PKCEAuthenticator(
            authorization_endpoint="https://auth.example.com/authorize",
            token_endpoint="https://auth.example.com/token",
            client_id="test-client",
            scopes=["read", "write"],
        )

        _, challenge = auth.generate_pkce_pair()
        url = auth.build_authorization_url(
            redirect_uri="http://localhost:8765/callback",
            code_challenge=challenge,
            state="random-state-123",
        )

        # Verify URL components
        assert url.startswith("https://auth.example.com/authorize?")
        assert "response_type=code" in url
        assert "client_id=test-client" in url
        assert "redirect_uri=http" in url
        assert f"code_challenge={challenge}" in url
        assert "code_challenge_method=S256" in url
        assert "scope=read+write" in url or "scope=read%20write" in url
        assert "state=random-state-123" in url

    @pytest.mark.asyncio
    async def test_exchange_code_for_token_success(self) -> None:
        """Test successful token exchange with mock server."""
        server = MockOAuthServer()
        auth = PKCEAuthenticator(
            authorization_endpoint=server.base_url + "/authorize",
            token_endpoint=server.base_url + "/token",
            client_id="test-client",
        )

        verifier, challenge = auth.generate_pkce_pair()
        redirect_uri = "http://localhost:8765/callback"

        # Create authorization code
        code = server.create_authorization_code(
            client_id="test-client",
            redirect_uri=redirect_uri,
            code_challenge=challenge,
        )

        # Mock httpx.AsyncClient
        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client_class.return_value.__aenter__.return_value = mock_client

            # Mock the token response
            mock_response = Mock()
            mock_response.status_code = 200
            mock_response.json.return_value = server.exchange_code_for_token(
                code, "test-client", redirect_uri, verifier
            )
            mock_response.raise_for_status = Mock()

            mock_client.post = AsyncMock(return_value=mock_response)

            # Exchange code
            tokens = await auth.exchange_code_for_token(code, verifier, redirect_uri)

            # Verify tokens
            assert "access_token" in tokens
            assert "refresh_token" in tokens
            assert tokens["token_type"] == "Bearer"
            assert tokens["expires_in"] == 3600

    @pytest.mark.asyncio
    async def test_exchange_code_for_token_invalid_code(self) -> None:
        """Test token exchange with invalid code."""
        auth = PKCEAuthenticator(
            authorization_endpoint="https://auth.example.com/authorize",
            token_endpoint="https://auth.example.com/token",
            client_id="test-client",
        )

        verifier, _ = auth.generate_pkce_pair()

        # Mock httpx.AsyncClient with error response
        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client_class.return_value.__aenter__.return_value = mock_client

            # Mock 400 response
            import httpx
            mock_response = Mock()
            mock_response.status_code = 400
            mock_response.text = "invalid_grant"
            mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
                "Bad Request", request=Mock(), response=mock_response
            )

            mock_client.post = AsyncMock(return_value=mock_response)

            # Should raise OAuthError
            with pytest.raises(OAuthError, match="Token exchange failed"):
                await auth.exchange_code_for_token(
                    "invalid-code", verifier, "http://localhost/callback"
                )

    @pytest.mark.asyncio
    async def test_refresh_access_token_success(self) -> None:
        """Test successful token refresh."""
        server = MockOAuthServer()
        auth = PKCEAuthenticator(
            authorization_endpoint=server.base_url + "/authorize",
            token_endpoint=server.base_url + "/token",
            client_id="test-client",
        )

        # Get initial tokens
        verifier, challenge = auth.generate_pkce_pair()
        code = server.create_authorization_code("test-client", "http://localhost/cb", challenge)
        initial_tokens = server.exchange_code_for_token(code, "test-client", "http://localhost/cb", verifier)
        refresh_token = initial_tokens["refresh_token"]

        # Mock httpx.AsyncClient
        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client_class.return_value.__aenter__.return_value = mock_client

            # Mock refresh response
            mock_response = Mock()
            mock_response.status_code = 200
            mock_response.json.return_value = server.refresh_access_token(refresh_token)
            mock_response.raise_for_status = Mock()

            mock_client.post = AsyncMock(return_value=mock_response)

            # Refresh token
            new_tokens = await auth.refresh_access_token(refresh_token)

            # Verify new tokens
            assert "access_token" in new_tokens
            assert new_tokens["access_token"] != initial_tokens["access_token"]
            assert "refresh_token" in new_tokens  # OAuth 2.1: token rotation

    @pytest.mark.asyncio
    async def test_refresh_access_token_invalid(self) -> None:
        """Test token refresh with invalid refresh_token."""
        auth = PKCEAuthenticator(
            authorization_endpoint="https://auth.example.com/authorize",
            token_endpoint="https://auth.example.com/token",
            client_id="test-client",
        )

        # Mock httpx.AsyncClient with error
        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client_class.return_value.__aenter__.return_value = mock_client

            import httpx
            mock_response = Mock()
            mock_response.status_code = 400
            mock_response.text = "invalid_grant"
            mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
                "Bad Request", request=Mock(), response=mock_response
            )

            mock_client.post = AsyncMock(return_value=mock_response)

            # Should raise OAuthError
            with pytest.raises(OAuthError, match="Token refresh failed"):
                await auth.refresh_access_token("invalid-refresh-token")


class TestTokenStore:
    """Tests for secure token storage."""

    def test_save_and_get_tokens(self) -> None:
        """Test saving and retrieving tokens."""
        with tempfile.TemporaryDirectory() as tmpdir:
            token_file = Path(tmpdir) / "tokens.json"
            store = TokenStore(token_file=token_file)

            tokens = {
                "access_token": "abc123",
                "refresh_token": "xyz789",
                "token_type": "Bearer",
                "expires_in": 3600,
            }

            # Save tokens
            store.save_tokens("test-server", tokens)

            # Verify file was created
            assert token_file.exists()

            # Retrieve tokens
            retrieved = store.get_tokens("test-server")
            assert retrieved is not None
            assert retrieved["access_token"] == "abc123"
            assert retrieved["refresh_token"] == "xyz789"
            assert "created_at" in retrieved  # Auto-added timestamp

    def test_get_nonexistent_tokens(self) -> None:
        """Test retrieving tokens for nonexistent server."""
        with tempfile.TemporaryDirectory() as tmpdir:
            token_file = Path(tmpdir) / "tokens.json"
            store = TokenStore(token_file=token_file)

            tokens = store.get_tokens("nonexistent-server")
            assert tokens is None

    def test_delete_tokens(self) -> None:
        """Test deleting tokens."""
        with tempfile.TemporaryDirectory() as tmpdir:
            token_file = Path(tmpdir) / "tokens.json"
            store = TokenStore(token_file=token_file)

            # Save tokens
            tokens = {"access_token": "abc123"}
            store.save_tokens("test-server", tokens)

            # Delete tokens
            store.delete_tokens("test-server")

            # Verify deleted
            retrieved = store.get_tokens("test-server")
            assert retrieved is None

    def test_list_servers(self) -> None:
        """Test listing all authenticated servers."""
        with tempfile.TemporaryDirectory() as tmpdir:
            token_file = Path(tmpdir) / "tokens.json"
            store = TokenStore(token_file=token_file)

            # Add multiple servers
            store.save_tokens("server1", {"access_token": "token1"})
            store.save_tokens("server2", {"access_token": "token2"})
            store.save_tokens("server3", {"access_token": "token3"})

            # List servers
            servers = store.list_servers()
            assert set(servers) == {"server1", "server2", "server3"}

    def test_encryption_and_persistence(self) -> None:
        """Test that tokens are encrypted and persist across instances."""
        with tempfile.TemporaryDirectory() as tmpdir:
            token_file = Path(tmpdir) / "tokens.json"

            # Save tokens with first instance
            store1 = TokenStore(token_file=token_file)
            tokens = {"access_token": "secret123"}
            store1.save_tokens("test-server", tokens)

            # Verify file is not plaintext JSON
            encrypted_content = token_file.read_bytes()
            assert b"secret123" not in encrypted_content
            assert b"test-server" not in encrypted_content

            # Load tokens with second instance
            store2 = TokenStore(token_file=token_file)
            retrieved = store2.get_tokens("test-server")
            assert retrieved is not None
            assert retrieved["access_token"] == "secret123"

    def test_empty_token_file(self) -> None:
        """Test handling of nonexistent token file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            token_file = Path(tmpdir) / "tokens.json"
            store = TokenStore(token_file=token_file)

            # Should return empty list for list_servers()
            servers = store.list_servers()
            assert servers == []

            # Should return None for get_tokens()
            tokens = store.get_tokens("any-server")
            assert tokens is None


class TestOAuthConfig:
    """Tests for OAuth configuration model."""

    def test_oauth_config_validation(self) -> None:
        """Test OAuthConfig pydantic model validation."""
        config = OAuthConfig(
            authorization_endpoint="https://auth.example.com/authorize",
            token_endpoint="https://auth.example.com/token",
            resource="https://mcp.example.com/server",
        )

        assert config.authorization_endpoint == "https://auth.example.com/authorize"
        assert config.token_endpoint == "https://auth.example.com/token"
        assert config.resource == "https://mcp.example.com/server"
        assert config.scopes == []  # Default
        assert config.token_types_supported == ["Bearer"]  # Default

    def test_oauth_config_with_scopes(self) -> None:
        """Test OAuthConfig with custom scopes."""
        config = OAuthConfig(
            authorization_endpoint="https://auth.example.com/authorize",
            token_endpoint="https://auth.example.com/token",
            resource="https://mcp.example.com/server",
            scopes=["read", "write", "admin"],
            token_types_supported=["Bearer", "DPoP"],
        )

        assert config.scopes == ["read", "write", "admin"]
        assert config.token_types_supported == ["Bearer", "DPoP"]


@pytest.mark.asyncio
async def test_discover_oauth_metadata() -> None:
    """Test RFC 9728 OAuth metadata discovery."""
    from oneshotmcp.oauth import discover_oauth_metadata

    resource_url = "https://mcp.example.com/server"

    # Mock httpx.AsyncClient
    with patch("httpx.AsyncClient") as mock_client_class:
        mock_client = AsyncMock()
        mock_client_class.return_value.__aenter__.return_value = mock_client

        # Mock discovery response
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "resource": resource_url,
            "authorization_endpoint": "https://auth.example.com/authorize",
            "token_endpoint": "https://auth.example.com/token",
            "scopes_supported": ["read", "write"],
            "token_types_supported": ["Bearer"],
        }
        mock_response.raise_for_status = Mock()

        mock_client.get = AsyncMock(return_value=mock_response)

        # Discover metadata
        config = await discover_oauth_metadata(resource_url)

        # Verify config
        assert isinstance(config, OAuthConfig)
        assert config.authorization_endpoint == "https://auth.example.com/authorize"
        assert config.token_endpoint == "https://auth.example.com/token"
        assert config.scopes == ["read", "write"]

        # Verify correct discovery URL was called (RFC 8414)
        mock_client.get.assert_called_once()
        call_args = mock_client.get.call_args[0][0]
        assert call_args == "https://mcp.example.com/.well-known/oauth-authorization-server"
