"""Mock OAuth 2.1 server for testing.

Provides RFC 9728 compliant mock OAuth endpoints for testing OneShotMCP's
OAuth integration without requiring real authorization servers.
"""

from __future__ import annotations

import hashlib
import base64
from typing import Any


class MockOAuthServer:
    """RFC 9728 compliant mock OAuth server for testing.

    Simulates an OAuth 2.1 Authorization Server with:
    - Protected Resource Metadata discovery (RFC 9728)
    - Authorization Code Flow with PKCE (S256)
    - Token exchange and refresh

    Example:
        >>> server = MockOAuthServer()
        >>> metadata = server.get_protected_resource_metadata()
        >>> assert "authorization_endpoint" in metadata
    """

    def __init__(
        self,
        base_url: str = "https://oauth.example.com",
        resource_url: str = "https://mcp.example.com/server",
    ) -> None:
        self.base_url = base_url
        self.resource_url = resource_url

        # Storage for codes and tokens
        self._authorization_codes: dict[str, dict[str, Any]] = {}
        self._access_tokens: dict[str, dict[str, Any]] = {}
        self._refresh_tokens: dict[str, str] = {}  # refresh_token -> access_token

    def get_protected_resource_metadata(self) -> dict[str, Any]:
        """Return RFC 9728 Protected Resource Metadata.

        Returns:
            Metadata dict with OAuth endpoints and supported features.

        Example:
            >>> server = MockOAuthServer()
            >>> meta = server.get_protected_resource_metadata()
            >>> meta["authorization_endpoint"]
            'https://oauth.example.com/authorize'
        """
        return {
            "resource": self.resource_url,
            "authorization_endpoint": f"{self.base_url}/authorize",
            "token_endpoint": f"{self.base_url}/token",
            "scopes_supported": ["read", "write"],
            "token_types_supported": ["Bearer"],
            "grant_types_supported": ["authorization_code", "refresh_token"],
            "code_challenge_methods_supported": ["S256"],
        }

    def create_authorization_code(
        self,
        client_id: str,
        redirect_uri: str,
        code_challenge: str,
        code_challenge_method: str = "S256",
    ) -> str:
        """Create an authorization code for testing.

        Args:
            client_id: OAuth client identifier.
            redirect_uri: Callback URL.
            code_challenge: PKCE code challenge.
            code_challenge_method: Should be "S256".

        Returns:
            Authorization code.

        Example:
            >>> server = MockOAuthServer()
            >>> code = server.create_authorization_code(
            ...     "test-client",
            ...     "http://localhost:8765/callback",
            ...     "CHALLENGE123"
            ... )
            >>> len(code) > 0
            True
        """
        # Generate random code
        import secrets

        code = secrets.token_urlsafe(32)

        # Store code with metadata
        self._authorization_codes[code] = {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "code_challenge": code_challenge,
            "code_challenge_method": code_challenge_method,
        }

        return code

    def exchange_code_for_token(
        self,
        code: str,
        client_id: str,
        redirect_uri: str,
        code_verifier: str,
    ) -> dict[str, Any]:
        """Exchange authorization code for access token.

        Validates PKCE code_verifier against stored code_challenge.

        Args:
            code: Authorization code.
            client_id: OAuth client ID.
            redirect_uri: Redirect URI (must match).
            code_verifier: PKCE code verifier.

        Returns:
            Token response dict.

        Raises:
            ValueError: If validation fails.

        Example:
            >>> server = MockOAuthServer()
            >>> verifier = "test-verifier-abc123"
            >>> # Calculate challenge: BASE64URL(SHA256(verifier))
            >>> import hashlib, base64
            >>> challenge_bytes = hashlib.sha256(verifier.encode()).digest()
            >>> challenge = base64.urlsafe_b64encode(challenge_bytes).decode().rstrip("=")
            >>> code = server.create_authorization_code("client", "http://localhost/cb", challenge)
            >>> tokens = server.exchange_code_for_token(code, "client", "http://localhost/cb", verifier)
            >>> "access_token" in tokens
            True
        """
        # Validate code exists
        if code not in self._authorization_codes:
            raise ValueError("invalid_grant: Invalid authorization code")

        code_data = self._authorization_codes[code]

        # Validate client_id and redirect_uri
        if code_data["client_id"] != client_id:
            raise ValueError("invalid_client: Client ID mismatch")

        if code_data["redirect_uri"] != redirect_uri:
            raise ValueError("invalid_grant: Redirect URI mismatch")

        # Validate PKCE code_verifier
        expected_challenge = code_data["code_challenge"]
        actual_challenge = self._calculate_pkce_challenge(code_verifier)

        if actual_challenge != expected_challenge:
            raise ValueError("invalid_grant: Code verifier validation failed")

        # Generate tokens
        import secrets

        access_token = f"access_{secrets.token_urlsafe(32)}"
        refresh_token = f"refresh_{secrets.token_urlsafe(32)}"

        # Store tokens
        self._access_tokens[access_token] = {
            "client_id": client_id,
            "scopes": ["read", "write"],
        }
        self._refresh_tokens[refresh_token] = access_token

        # Delete used authorization code
        del self._authorization_codes[code]

        return {
            "access_token": access_token,
            "token_type": "Bearer",
            "expires_in": 3600,
            "refresh_token": refresh_token,
            "scope": "read write",
        }

    def refresh_access_token(self, refresh_token: str) -> dict[str, Any]:
        """Refresh an access token using refresh_token.

        Args:
            refresh_token: Refresh token from initial authorization.

        Returns:
            New token response dict (may include new refresh_token).

        Raises:
            ValueError: If refresh token is invalid.

        Example:
            >>> server = MockOAuthServer()
            >>> # ... get initial tokens ...
            >>> new_tokens = server.refresh_access_token(refresh_token)
            >>> "access_token" in new_tokens
            True
        """
        if refresh_token not in self._refresh_tokens:
            raise ValueError("invalid_grant: Invalid refresh token")

        # Invalidate old access token
        old_access_token = self._refresh_tokens[refresh_token]
        if old_access_token in self._access_tokens:
            client_data = self._access_tokens[old_access_token]
            del self._access_tokens[old_access_token]
        else:
            client_data = {"client_id": "test-client", "scopes": ["read", "write"]}

        # Generate new tokens (OAuth 2.1: rotate refresh tokens)
        import secrets

        new_access_token = f"access_{secrets.token_urlsafe(32)}"
        new_refresh_token = f"refresh_{secrets.token_urlsafe(32)}"

        # Store new tokens
        self._access_tokens[new_access_token] = client_data
        self._refresh_tokens[new_refresh_token] = new_access_token

        # Delete old refresh token
        del self._refresh_tokens[refresh_token]

        return {
            "access_token": new_access_token,
            "token_type": "Bearer",
            "expires_in": 3600,
            "refresh_token": new_refresh_token,
            "scope": "read write",
        }

    def validate_access_token(self, access_token: str) -> bool:
        """Validate an access token.

        Args:
            access_token: Bearer token to validate.

        Returns:
            True if valid, False otherwise.

        Example:
            >>> server = MockOAuthServer()
            >>> server.validate_access_token("invalid")
            False
        """
        return access_token in self._access_tokens

    @staticmethod
    def _calculate_pkce_challenge(code_verifier: str) -> str:
        """Calculate PKCE code challenge (S256 method).

        Args:
            code_verifier: PKCE code verifier.

        Returns:
            BASE64URL(SHA256(code_verifier)).

        Example:
            >>> challenge = MockOAuthServer._calculate_pkce_challenge("test123")
            >>> len(challenge) == 43  # SHA256 base64url is always 43 chars
            True
        """
        challenge_bytes = hashlib.sha256(code_verifier.encode("utf-8")).digest()
        challenge = base64.urlsafe_b64encode(challenge_bytes).decode("utf-8")
        return challenge.rstrip("=")  # Remove padding
