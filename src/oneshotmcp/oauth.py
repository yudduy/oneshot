"""OAuth 2.1 PKCE authentication for MCP servers.

This module implements OAuth 2.1 Authorization Code Flow with PKCE (RFC 7636)
for authenticating with Smithery-hosted MCP servers. It supports:

- RFC 9728 Protected Resource Metadata discovery
- S256 code challenge method (SHA-256)
- Automatic token refresh
- Secure token storage with encryption
- Browser-based authorization flow

Example:
    >>> authenticator = PKCEAuthenticator(
    ...     authorization_endpoint="https://auth.example.com/oauth/authorize",
    ...     token_endpoint="https://auth.example.com/oauth/token",
    ...     client_id="my-client"
    ... )
    >>> auth_url = authenticator.build_authorization_url("http://localhost:8765/callback")
    >>> # User visits auth_url in browser, gets redirected back with code
    >>> tokens = await authenticator.exchange_code_for_token(code, verifier)
    >>> print(tokens["access_token"])
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import secrets
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse

import httpx
from cryptography.fernet import Fernet
from pydantic import BaseModel, Field

# Constants
PKCE_VERIFIER_LENGTH = 64  # Between 43-128 chars, using 64 for balance
CALLBACK_PORT_RANGE = (8765, 8865)  # Try ports in this range
TOKEN_DIR = Path.home() / ".config" / "oneshotmcp"
TOKEN_FILE = TOKEN_DIR / "tokens.json"


class OAuthError(Exception):
    """Raised when OAuth operations fail."""


class OAuthConfig(BaseModel):
    """OAuth 2.1 server configuration from RFC 9728 discovery.

    Represents the metadata returned from .well-known/oauth-protected-resource
    endpoint. This metadata tells clients where to send authorization and
    token requests.

    Attributes:
        authorization_endpoint: URL where user authorizes the app.
        token_endpoint: URL where app exchanges code for tokens.
        resource: The resource identifier (MCP server URL).
        scopes: List of OAuth scopes supported.
        token_types_supported: Token types (e.g., ["Bearer"]).
    """

    authorization_endpoint: str
    token_endpoint: str
    resource: str
    scopes: list[str] = Field(default_factory=list)
    token_types_supported: list[str] = Field(default_factory=lambda: ["Bearer"])


class PKCEAuthenticator:
    """OAuth 2.1 PKCE (Proof Key for Code Exchange) authenticator.

    Implements the Authorization Code Flow with PKCE as specified in:
    - OAuth 2.1 (draft)
    - RFC 7636 (PKCE)
    - RFC 6749 (OAuth 2.0)

    The PKCE flow prevents authorization code interception attacks by
    using a cryptographically random code verifier and its SHA-256 hash
    (code challenge).

    Args:
        authorization_endpoint: OAuth authorization URL.
        token_endpoint: OAuth token exchange URL.
        client_id: OAuth client identifier.
        redirect_uri: Callback URL (e.g., http://localhost:8765/callback).
        scopes: Optional list of scopes to request.

    Example:
        >>> auth = PKCEAuthenticator(
        ...     authorization_endpoint="https://auth.example.com/authorize",
        ...     token_endpoint="https://auth.example.com/token",
        ...     client_id="my-app"
        ... )
        >>> verifier, challenge = auth.generate_pkce_pair()
        >>> auth_url = auth.build_authorization_url(redirect_uri, challenge)
    """

    def __init__(
        self,
        authorization_endpoint: str,
        token_endpoint: str,
        client_id: str,
        redirect_uri: str | None = None,
        scopes: list[str] | None = None,
    ) -> None:
        self.authorization_endpoint = authorization_endpoint
        self.token_endpoint = token_endpoint
        self.client_id = client_id
        self.redirect_uri = redirect_uri
        self.scopes = scopes or []

    @staticmethod
    def generate_pkce_pair() -> tuple[str, str]:
        """Generate PKCE code verifier and challenge (S256 method).

        The verifier is a cryptographically random string (43-128 chars).
        The challenge is BASE64URL(SHA256(verifier)).

        Returns:
            Tuple of (code_verifier, code_challenge).

        Example:
            >>> verifier, challenge = PKCEAuthenticator.generate_pkce_pair()
            >>> len(verifier) == 64
            True
            >>> len(challenge) == 43  # base64url(sha256) is always 43 chars
            True
        """
        # Generate cryptographically secure random verifier
        # Use URL-safe characters: [A-Z][a-z][0-9]-._~
        verifier = base64.urlsafe_b64encode(secrets.token_bytes(48)).decode("utf-8")
        verifier = verifier.rstrip("=")  # Remove padding

        # Calculate S256 challenge: BASE64URL(SHA256(verifier))
        challenge_bytes = hashlib.sha256(verifier.encode("utf-8")).digest()
        challenge = base64.urlsafe_b64encode(challenge_bytes).decode("utf-8")
        challenge = challenge.rstrip("=")  # Remove padding

        return verifier, challenge

    def build_authorization_url(
        self,
        redirect_uri: str,
        code_challenge: str,
        state: str | None = None,
    ) -> str:
        """Build the authorization URL for the user to visit.

        Constructs the OAuth authorization URL with PKCE parameters.
        User visits this URL, authorizes the app, and gets redirected back
        with an authorization code.

        Args:
            redirect_uri: Where to redirect after authorization.
            code_challenge: PKCE code challenge (from generate_pkce_pair).
            state: Optional CSRF protection token.

        Returns:
            Complete authorization URL.

        Example:
            >>> auth = PKCEAuthenticator(
            ...     authorization_endpoint="https://auth.example.com/authorize",
            ...     token_endpoint="https://auth.example.com/token",
            ...     client_id="my-app"
            ... )
            >>> _, challenge = auth.generate_pkce_pair()
            >>> url = auth.build_authorization_url("http://localhost:8765/callback", challenge)
            >>> "code_challenge=" in url
            True
        """
        params = {
            "response_type": "code",
            "client_id": self.client_id,
            "redirect_uri": redirect_uri,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
        }

        if self.scopes:
            params["scope"] = " ".join(self.scopes)

        if state:
            params["state"] = state

        return f"{self.authorization_endpoint}?{urlencode(params)}"

    async def exchange_code_for_token(
        self,
        code: str,
        code_verifier: str,
        redirect_uri: str,
    ) -> dict[str, Any]:
        """Exchange authorization code for access token.

        After user authorization, exchange the code + verifier for tokens.
        The authorization server verifies the code_verifier matches the
        original code_challenge.

        Args:
            code: Authorization code from callback.
            code_verifier: Original PKCE verifier (from generate_pkce_pair).
            redirect_uri: Same redirect_uri used in authorization.

        Returns:
            Token response dict with keys:
                - access_token: Bearer token for API calls
                - token_type: Usually "Bearer"
                - expires_in: Token lifetime in seconds
                - refresh_token: Token for refreshing access (optional)

        Raises:
            OAuthError: If token exchange fails.

        Example:
            >>> auth = PKCEAuthenticator(...)
            >>> verifier, challenge = auth.generate_pkce_pair()
            >>> # ... user authorizes, gets code ...
            >>> tokens = await auth.exchange_code_for_token(code, verifier, redirect_uri)
            >>> print(tokens["access_token"])
        """
        data = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "client_id": self.client_id,
            "code_verifier": code_verifier,
        }

        async with httpx.AsyncClient() as client:
            try:
                response = await client.post(
                    self.token_endpoint,
                    data=data,
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                )
                response.raise_for_status()
                return response.json()

            except httpx.HTTPStatusError as exc:
                error_detail = exc.response.text
                raise OAuthError(
                    f"Token exchange failed ({exc.response.status_code}): {error_detail}"
                ) from exc
            except Exception as exc:
                raise OAuthError(f"Token exchange failed: {exc}") from exc

    async def refresh_access_token(self, refresh_token: str) -> dict[str, Any]:
        """Refresh an expired access token using a refresh token.

        OAuth 2.1 requires refresh tokens for public clients to be either
        sender-constrained or one-time use (rotated). This method handles
        token refresh, and the returned dict may contain a new refresh_token.

        Args:
            refresh_token: Refresh token from initial authorization.

        Returns:
            New token response dict (may include new refresh_token).

        Raises:
            OAuthError: If refresh fails (e.g., refresh token expired).

        Example:
            >>> tokens = await auth.exchange_code_for_token(...)
            >>> # Wait for access token to expire...
            >>> new_tokens = await auth.refresh_access_token(tokens["refresh_token"])
        """
        data = {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": self.client_id,
        }

        async with httpx.AsyncClient() as client:
            try:
                response = await client.post(
                    self.token_endpoint,
                    data=data,
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                )
                response.raise_for_status()
                return response.json()

            except httpx.HTTPStatusError as exc:
                error_detail = exc.response.text
                raise OAuthError(
                    f"Token refresh failed ({exc.response.status_code}): {error_detail}"
                ) from exc
            except Exception as exc:
                raise OAuthError(f"Token refresh failed: {exc}") from exc


class BrowserAuthHandler:
    """HTTP server for handling OAuth callback in browser-based flow.

    Starts a local HTTP server to receive the OAuth authorization callback.
    Opens the user's browser to the authorization URL, then waits for the
    callback with the authorization code.

    The server automatically shuts down after receiving the callback.

    Args:
        redirect_uri: Base callback URL (e.g., http://localhost:8765/callback).
        timeout: How long to wait for callback (seconds).

    Example:
        >>> handler = BrowserAuthHandler("http://localhost:8765/callback")
        >>> auth_url = "https://auth.example.com/authorize?..."
        >>> code = await handler.authorize(auth_url)
        >>> print(f"Got authorization code: {code}")
    """

    def __init__(self, redirect_uri: str, timeout: float = 120.0) -> None:
        self.redirect_uri = redirect_uri
        self.timeout = timeout
        self._code: str | None = None
        self._error: str | None = None
        self._server: HTTPServer | None = None
        self._received = asyncio.Event()

    def _create_handler(self) -> type[BaseHTTPRequestHandler]:
        """Create HTTP handler class with access to instance state."""
        # Need to close over self to access instance variables
        handler_instance = self

        class CallbackHandler(BaseHTTPRequestHandler):
            """Handles OAuth callback requests."""

            def do_GET(self) -> None:
                """Handle GET request with authorization code."""
                parsed = urlparse(self.path)

                # Check if this is the callback path
                if not parsed.path.endswith("/callback"):
                    self.send_response(404)
                    self.end_headers()
                    return

                # Parse query parameters
                params = parse_qs(parsed.query)

                # Check for error
                if "error" in params:
                    handler_instance._error = params["error"][0]
                    error_desc = params.get("error_description", ["Unknown error"])[0]

                    self.send_response(400)
                    self.send_header("Content-Type", "text/html")
                    self.end_headers()
                    html = f"""
                    <html>
                    <body style="font-family: sans-serif; padding: 40px; text-align: center;">
                        <h1 style="color: #d32f2f;">❌ Authorization Failed</h1>
                        <p style="color: #666;">{error_desc}</p>
                        <p style="color: #999; font-size: 14px;">You can close this window.</p>
                    </body>
                    </html>
                    """
                    self.wfile.write(html.encode("utf-8"))
                    handler_instance._received.set()
                    return

                # Extract authorization code
                if "code" in params:
                    handler_instance._code = params["code"][0]

                    self.send_response(200)
                    self.send_header("Content-Type", "text/html")
                    self.end_headers()
                    html = """
                    <html>
                    <body style="font-family: sans-serif; padding: 40px; text-align: center;">
                        <h1 style="color: #4caf50;">✓ Authorization Successful!</h1>
                        <p style="color: #666;">You can close this window and return to the terminal.</p>
                        <script>setTimeout(() => window.close(), 2000);</script>
                    </body>
                    </html>
                    """
                    self.wfile.write(html.encode("utf-8"))
                    handler_instance._received.set()
                else:
                    self.send_response(400)
                    self.send_header("Content-Type", "text/html")
                    self.end_headers()
                    html = """
                    <html>
                    <body style="font-family: sans-serif; padding: 40px; text-align: center;">
                        <h1 style="color: #d32f2f;">❌ Missing Authorization Code</h1>
                        <p style="color: #999; font-size: 14px;">You can close this window.</p>
                    </body>
                    </html>
                    """
                    self.wfile.write(html.encode("utf-8"))
                    handler_instance._received.set()

            def log_message(self, format: str, *args: Any) -> None:
                """Suppress server logs."""
                pass

        return CallbackHandler

    async def authorize(self, auth_url: str) -> str:
        """Open browser for authorization and wait for callback.

        Args:
            auth_url: Complete authorization URL to visit.

        Returns:
            Authorization code from callback.

        Raises:
            OAuthError: If authorization fails or times out.

        Example:
            >>> handler = BrowserAuthHandler("http://localhost:8765/callback")
            >>> code = await handler.authorize("https://auth.example.com/authorize?...")
        """
        # Parse redirect URI to get port
        parsed = urlparse(self.redirect_uri)
        port = parsed.port or 80

        # Start local HTTP server
        try:
            self._server = HTTPServer(("localhost", port), self._create_handler())
        except OSError as exc:
            raise OAuthError(f"Failed to start callback server on port {port}: {exc}") from exc

        # Start server in background thread
        def serve() -> None:
            if self._server:
                self._server.serve_forever()

        import threading

        server_thread = threading.Thread(target=serve, daemon=True)
        server_thread.start()

        try:
            # Open browser
            print(f"Opening browser for authorization: {auth_url}")
            print(f"Listening for callback on {self.redirect_uri}")
            webbrowser.open(auth_url)

            # Wait for callback with timeout
            try:
                await asyncio.wait_for(self._received.wait(), timeout=self.timeout)
            except asyncio.TimeoutError:
                raise OAuthError(
                    f"Authorization timed out after {self.timeout} seconds. "
                    "Please try again."
                )

            # Check for errors
            if self._error:
                raise OAuthError(f"Authorization failed: {self._error}")

            if not self._code:
                raise OAuthError("No authorization code received")

            return self._code

        finally:
            # Shutdown server
            if self._server:
                self._server.shutdown()


class TokenStore:
    """Secure storage for OAuth tokens with encryption.

    Stores tokens in an encrypted JSON file at ~/.config/oneshotmcp/tokens.json.
    Uses Fernet symmetric encryption with a key derived from system information.

    Token format per server:
    {
        "server_name": {
            "access_token": "...",
            "refresh_token": "...",
            "token_type": "Bearer",
            "expires_in": 3600,
            "created_at": 1234567890
        }
    }

    Example:
        >>> store = TokenStore()
        >>> tokens = {"access_token": "abc123", "refresh_token": "xyz789"}
        >>> store.save_tokens("github", tokens)
        >>> retrieved = store.get_tokens("github")
        >>> print(retrieved["access_token"])
    """

    def __init__(self, token_file: Path | None = None) -> None:
        self.token_file = token_file or TOKEN_FILE
        self._key: bytes | None = None

    def _get_encryption_key(self) -> bytes:
        """Get or generate encryption key for token storage.

        The key is derived from system info and stored in ~/.config/oneshotmcp/key.
        If the key file doesn't exist, generates a new Fernet key.

        Returns:
            Fernet encryption key (bytes).
        """
        if self._key:
            return self._key

        key_file = self.token_file.parent / "key"

        if key_file.exists():
            self._key = key_file.read_bytes()
        else:
            # Generate new key
            self._key = Fernet.generate_key()
            key_file.parent.mkdir(parents=True, exist_ok=True)
            key_file.write_bytes(self._key)
            key_file.chmod(0o600)  # Owner read/write only

        return self._key

    def _encrypt(self, data: dict[str, Any]) -> bytes:
        """Encrypt token data.

        Args:
            data: Token dict to encrypt.

        Returns:
            Encrypted bytes.
        """
        key = self._get_encryption_key()
        fernet = Fernet(key)
        json_bytes = json.dumps(data).encode("utf-8")
        return fernet.encrypt(json_bytes)

    def _decrypt(self, encrypted: bytes) -> dict[str, Any]:
        """Decrypt token data.

        Args:
            encrypted: Encrypted token bytes.

        Returns:
            Decrypted token dict.

        Raises:
            OAuthError: If decryption fails.
        """
        try:
            key = self._get_encryption_key()
            fernet = Fernet(key)
            json_bytes = fernet.decrypt(encrypted)
            return json.loads(json_bytes.decode("utf-8"))
        except Exception as exc:
            raise OAuthError(f"Failed to decrypt tokens: {exc}") from exc

    def save_tokens(self, server_name: str, tokens: dict[str, Any]) -> None:
        """Save tokens for a server.

        Args:
            server_name: MCP server identifier (e.g., "@upstash/context7-mcp").
            tokens: Token dict from OAuth exchange.

        Example:
            >>> store = TokenStore()
            >>> tokens = {"access_token": "...", "refresh_token": "..."}
            >>> store.save_tokens("github", tokens)
        """
        # Load existing tokens
        all_tokens = self._load_all()

        # Add created_at timestamp if not present
        if "created_at" not in tokens:
            import time

            tokens["created_at"] = int(time.time())

        # Update with new tokens
        all_tokens[server_name] = tokens

        # Encrypt and save
        encrypted = self._encrypt(all_tokens)
        self.token_file.parent.mkdir(parents=True, exist_ok=True)
        self.token_file.write_bytes(encrypted)
        self.token_file.chmod(0o600)  # Owner read/write only

    def get_tokens(self, server_name: str) -> dict[str, Any] | None:
        """Retrieve tokens for a server.

        Args:
            server_name: MCP server identifier.

        Returns:
            Token dict or None if not found.

        Example:
            >>> store = TokenStore()
            >>> tokens = store.get_tokens("github")
            >>> if tokens:
            ...     print(tokens["access_token"])
        """
        all_tokens = self._load_all()
        return all_tokens.get(server_name)

    def delete_tokens(self, server_name: str) -> None:
        """Delete tokens for a server.

        Args:
            server_name: MCP server identifier.

        Example:
            >>> store = TokenStore()
            >>> store.delete_tokens("github")
        """
        all_tokens = self._load_all()
        if server_name in all_tokens:
            del all_tokens[server_name]
            encrypted = self._encrypt(all_tokens)
            self.token_file.write_bytes(encrypted)

    def list_servers(self) -> list[str]:
        """List all servers with stored tokens.

        Returns:
            List of server names.

        Example:
            >>> store = TokenStore()
            >>> servers = store.list_servers()
            >>> print(f"Authenticated servers: {servers}")
        """
        all_tokens = self._load_all()
        return list(all_tokens.keys())

    def _load_all(self) -> dict[str, Any]:
        """Load all tokens from encrypted storage.

        Returns:
            Dict mapping server names to token dicts.
        """
        if not self.token_file.exists():
            return {}

        try:
            encrypted = self.token_file.read_bytes()
            return self._decrypt(encrypted)
        except Exception:
            # If decryption fails (corrupt file, etc.), return empty dict
            return {}


async def discover_oauth_metadata(resource_url: str) -> OAuthConfig:
    """Discover OAuth metadata via RFC 9728 Protected Resource Metadata.

    Fetches the .well-known/oauth-protected-resource endpoint to discover
    the authorization server and token endpoints for a resource.

    Args:
        resource_url: The protected resource URL (MCP server URL).

    Returns:
        OAuth configuration with discovered endpoints.

    Raises:
        OAuthError: If discovery fails.

    Example:
        >>> config = await discover_oauth_metadata("https://mcp.smithery.ai/servers/github/mcp")
        >>> print(config.authorization_endpoint)
        https://auth.smithery.ai/oauth/authorize
    """
    # Parse resource URL to get base
    parsed = urlparse(resource_url)
    base_url = f"{parsed.scheme}://{parsed.netloc}"

    # Check if this is a Smithery-hosted server (use known endpoints)
    if "server.smithery.ai" in resource_url:
        # Smithery uses centralized auth server
        return OAuthConfig(
            authorization_endpoint="https://auth.smithery.ai/oauth/authorize",
            token_endpoint="https://auth.smithery.ai/oauth/token",
            resource=resource_url,
            scopes=["read", "write"],
            token_types_supported=["Bearer"],
        )

    # RFC 8414 Authorization Server Metadata (MCP specification)
    # Try primary endpoint first
    discovery_url = f"{base_url}/.well-known/oauth-authorization-server"

    async with httpx.AsyncClient() as client:
        try:
            response = await client.get(discovery_url)
            response.raise_for_status()
            metadata = response.json()

            # Extract required fields from RFC 8414 format
            return OAuthConfig(
                authorization_endpoint=metadata["authorization_endpoint"],
                token_endpoint=metadata["token_endpoint"],
                resource=resource_url,  # RFC 8414 doesn't include resource
                scopes=metadata.get("scopes_supported", []),
                token_types_supported=metadata.get("token_types_supported", ["Bearer"]),
            )

        except httpx.HTTPStatusError:
            # Fallback: Try RFC 9728 Protected Resource Metadata
            fallback_url = f"{base_url}/.well-known/oauth-protected-resource"
            try:
                response = await client.get(fallback_url)
                response.raise_for_status()
                metadata = response.json()

                return OAuthConfig(
                    authorization_endpoint=metadata["authorization_endpoint"],
                    token_endpoint=metadata["token_endpoint"],
                    resource=metadata.get("resource", resource_url),
                    scopes=metadata.get("scopes_supported", []),
                    token_types_supported=metadata.get("token_types_supported", ["Bearer"]),
                )
            except Exception as exc:
                raise OAuthError(
                    f"OAuth discovery failed at both RFC 8414 and RFC 9728 endpoints: {exc}"
                ) from exc

        except KeyError as exc:
            raise OAuthError(f"Invalid OAuth metadata (missing {exc})") from exc
        except Exception as exc:
            raise OAuthError(f"OAuth discovery failed: {exc}") from exc
