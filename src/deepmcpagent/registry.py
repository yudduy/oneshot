"""Smithery API registry client for discovering MCP servers."""

from __future__ import annotations

import asyncio
from typing import Any

import httpx

from .config import HTTPServerSpec


class RegistryError(RuntimeError):
    """Raised when registry operations fail."""


class SmitheryAPIClient:
    """Client for the Smithery MCP server registry API.

    Provides search and retrieval of MCP server specifications from
    the Smithery registry (https://smithery.ai).

    Args:
        api_key: Smithery API key for authentication.
        base_url: Base URL for the Smithery API (defaults to production).
        timeout: Request timeout in seconds (default: 30).

    Example:
        >>> client = SmitheryAPIClient(api_key="sk_...")
        >>> results = await client.search("github")
        >>> spec = await client.get_server("@smithery/github")
    """

    def __init__(
        self,
        api_key: str,
        base_url: str = "https://registry.smithery.ai",
        timeout: float = 30.0,
        max_retries: int = 3,
    ) -> None:
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._max_retries = max_retries

        # Simple in-memory caching
        self._search_cache: dict[str, list[dict[str, Any]]] = {}
        self._server_cache: dict[str, HTTPServerSpec] = {}

    async def _retry_with_backoff(
        self,
        operation: str,
        func: Any,
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        """Retry an async function with exponential backoff.

        Args:
            operation: Description of the operation for error messages.
            func: Async function to retry.
            *args, **kwargs: Arguments to pass to func.

        Returns:
            Result from func.

        Raises:
            RegistryError: If all retries fail.
        """
        last_exception: Exception | None = None

        for attempt in range(self._max_retries):
            try:
                return await func(*args, **kwargs)
            except (httpx.TimeoutException, httpx.NetworkError) as exc:
                last_exception = exc
                if attempt < self._max_retries - 1:
                    # Exponential backoff: 1s, 2s, 4s, ...
                    backoff = 2**attempt
                    await asyncio.sleep(backoff)
                    continue
                # Last attempt failed
                raise RegistryError(
                    f"{operation} failed after {self._max_retries} attempts: {exc}"
                ) from exc
            except httpx.HTTPStatusError:
                # Don't retry on HTTP errors (404, 500, etc.)
                raise

        # Should not reach here, but for type safety
        if last_exception:
            raise RegistryError(
                f"{operation} failed after {self._max_retries} attempts"
            ) from last_exception
        raise RegistryError(f"{operation} failed")

    async def search(self, query: str, limit: int = 5) -> list[dict[str, Any]]:
        """Search for MCP servers matching a query.

        Args:
            query: Search query (e.g., "github", "weather", "database").
            limit: Maximum number of results to return (default: 5).

        Returns:
            List of server metadata dictionaries with keys:
                - qualified_name: Full server name (e.g., "@smithery/github")
                - name: Short name
                - description: Server description

        Raises:
            RegistryError: If the search request fails.

        Example:
            >>> results = await client.search("github", limit=3)
            >>> for server in results:
            ...     print(server["qualified_name"])
        """
        # Check cache first
        cache_key = f"{query}:{limit}"
        if cache_key in self._search_cache:
            return self._search_cache[cache_key]

        url = f"{self._base_url}/servers"
        headers = {"Authorization": f"Bearer {self._api_key}"}
        params = {"q": query, "pageSize": limit}

        async def _do_search() -> list[dict[str, Any]]:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.get(url, params=params, headers=headers)
                response.raise_for_status()
                data = response.json()
                # API returns either a list directly or a dict with "servers" key
                if isinstance(data, list):
                    return data
                servers: list[dict[str, Any]] = data.get("servers", [])
                return servers

        try:
            servers: list[dict[str, Any]] = await self._retry_with_backoff(
                operation=f"Search for '{query}'",
                func=_do_search,
            )

            # Cache results
            self._search_cache[cache_key] = servers

            return servers

        except httpx.HTTPStatusError as exc:
            raise RegistryError(
                f"Failed to search for '{query}': {exc.response.status_code} {exc.response.text}"
            ) from exc
        except Exception as exc:
            raise RegistryError(f"Failed to search for '{query}': {exc}") from exc

    async def get_server(self, qualified_name: str) -> HTTPServerSpec:
        """Retrieve server metadata and return as HTTPServerSpec.

        Args:
            qualified_name: Full server identifier (e.g., "@smithery/github").

        Returns:
            HTTPServerSpec configured for the server.

        Raises:
            RegistryError: If the server cannot be retrieved.

        Example:
            >>> spec = await client.get_server("@smithery/github")
            >>> assert spec.url == "https://mcp.smithery.ai/servers/github/mcp"
            >>> assert spec.transport == "http"
        """
        # Check cache first
        if qualified_name in self._server_cache:
            return self._server_cache[qualified_name]

        # URL-encode the qualified name for the GET request
        encoded_name = qualified_name.replace("/", "%2F").replace("@", "%40")
        url = f"{self._base_url}/servers/{encoded_name}"
        headers = {"Authorization": f"Bearer {self._api_key}"}

        async def _do_get_server() -> dict[str, Any]:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.get(url, headers=headers)
                response.raise_for_status()
                data: dict[str, Any] = response.json()
                return data

        try:
            data = await self._retry_with_backoff(
                operation=f"Get server '{qualified_name}'",
                func=_do_get_server,
            )

            # Extract connection information from the connections array
            connections = data.get("connections", [])
            if not connections:
                raise RegistryError(
                    f"Server '{qualified_name}' has no connections defined"
                )

            # Use the first connection (typically the primary one)
            connection = connections[0]
            server_url = connection.get("deploymentUrl")
            transport = connection.get("type", "http")

            if not server_url:
                raise RegistryError(
                    f"Server '{qualified_name}' connection missing 'deploymentUrl' field"
                )

            # Validate transport type
            if transport not in ("http", "streamable-http", "sse"):
                raise RegistryError(
                    f"Server '{qualified_name}' has unsupported transport '{transport}'"
                )

            # Create HTTPServerSpec
            spec = HTTPServerSpec(
                url=server_url,
                transport=transport,  # validated above
                headers={},  # Headers would come from config if needed
                auth=None,  # Auth would come from config if needed
            )

            # Cache the spec
            self._server_cache[qualified_name] = spec

            return spec

        except httpx.HTTPStatusError as exc:
            raise RegistryError(
                f"Failed to get server '{qualified_name}': "
                f"{exc.response.status_code} {exc.response.text}"
            ) from exc
        except Exception as exc:
            raise RegistryError(f"Failed to get server '{qualified_name}': {exc}") from exc
