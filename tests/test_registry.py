"""Tests for Smithery API registry client."""

from __future__ import annotations

from unittest.mock import AsyncMock, Mock, patch

import pytest

from deepmcpagent.config import HTTPServerSpec

# Mock HTTP responses
MOCK_SEARCH_RESPONSE = {
    "servers": [
        {
            "qualified_name": "@smithery/github",
            "name": "github",
            "description": "GitHub API tools",
        },
        {
            "qualified_name": "@smithery/weather",
            "name": "weather",
            "description": "Weather API tools",
        },
    ]
}

MOCK_SERVER_METADATA = {
    "qualified_name": "@smithery/github",
    "name": "github",
    "description": "GitHub API tools",
    "connections": [
        {
            "deploymentUrl": "https://example.com/mcp/github",
            "type": "http",
        }
    ],
}


@pytest.mark.asyncio
async def test_search_returns_server_list() -> None:
    """Test that search returns a list of servers from Smithery."""
    from deepmcpagent.registry import SmitheryAPIClient

    client = SmitheryAPIClient(api_key="test_key")

    with patch("httpx.AsyncClient") as mock_client_class:
        # Mock the async context manager
        mock_client = AsyncMock()
        mock_client_class.return_value.__aenter__.return_value = mock_client

        # Mock the GET response - use Mock for sync methods
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = MOCK_SEARCH_RESPONSE
        mock_response.raise_for_status = Mock()

        # Make the async get() return the mock response
        mock_client.get = AsyncMock(return_value=mock_response)

        results = await client.search("github")

        assert len(results) == 2
        assert results[0]["qualified_name"] == "@smithery/github"
        assert results[1]["qualified_name"] == "@smithery/weather"

        # Verify GET was called with correct params
        mock_client.get.assert_called_once()
        call_args = mock_client.get.call_args
        assert "servers" in call_args[0][0]  # URL contains /servers
        assert call_args[1]["params"] == {"q": "github", "pageSize": 5}
        assert call_args[1]["headers"]["Authorization"] == "Bearer test_key"


@pytest.mark.asyncio
async def test_get_server_returns_http_spec() -> None:
    """Test that get_server returns a valid HTTPServerSpec."""
    from deepmcpagent.registry import SmitheryAPIClient

    client = SmitheryAPIClient(api_key="test_key")

    with patch("httpx.AsyncClient") as mock_client_class:
        # Mock the async context manager
        mock_client = AsyncMock()
        mock_client_class.return_value.__aenter__.return_value = mock_client

        # Mock the GET response - use Mock for sync methods
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = MOCK_SERVER_METADATA
        mock_response.raise_for_status = Mock()

        # Make the async get() return the mock response
        mock_client.get = AsyncMock(return_value=mock_response)

        spec = await client.get_server("@smithery/github")

        assert isinstance(spec, HTTPServerSpec)
        assert spec.url == "https://example.com/mcp/github"
        assert spec.transport == "http"
        mock_client.get.assert_called_once()


@pytest.mark.asyncio
async def test_search_caches_results() -> None:
    """Test that repeated searches use cache and don't make redundant API calls."""
    from deepmcpagent.registry import SmitheryAPIClient

    client = SmitheryAPIClient(api_key="test_key")

    with patch("httpx.AsyncClient") as mock_client_class:
        # Mock the async context manager
        mock_client = AsyncMock()
        mock_client_class.return_value.__aenter__.return_value = mock_client

        # Mock the GET response - use Mock for sync methods
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = MOCK_SEARCH_RESPONSE
        mock_response.raise_for_status = Mock()

        # Make the async get() return the mock response
        mock_client.get = AsyncMock(return_value=mock_response)

        # First call - should hit API
        results1 = await client.search("github")
        assert len(results1) == 2

        # Second call with same query - should use cache
        results2 = await client.search("github")
        assert len(results2) == 2
        assert results1 == results2

        # Should only call API once due to caching
        assert mock_client.get.call_count == 1


@pytest.mark.asyncio
async def test_get_server_caches_results() -> None:
    """Test that get_server caches HTTPServerSpec objects."""
    from deepmcpagent.registry import SmitheryAPIClient

    client = SmitheryAPIClient(api_key="test_key")

    with patch("httpx.AsyncClient") as mock_client_class:
        # Mock the async context manager
        mock_client = AsyncMock()
        mock_client_class.return_value.__aenter__.return_value = mock_client

        # Mock the GET response - use Mock for sync methods
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = MOCK_SERVER_METADATA
        mock_response.raise_for_status = Mock()

        # Make the async get() return the mock response
        mock_client.get = AsyncMock(return_value=mock_response)

        # First call
        spec1 = await client.get_server("@smithery/github")
        # Second call
        spec2 = await client.get_server("@smithery/github")

        assert spec1.url == spec2.url
        # Should only call API once
        assert mock_client.get.call_count == 1


@pytest.mark.asyncio
async def test_search_handles_404() -> None:
    """Test graceful handling of not found responses."""
    from httpx import HTTPStatusError, Request, Response

    from deepmcpagent.registry import RegistryError, SmitheryAPIClient

    client = SmitheryAPIClient(api_key="test_key")

    with patch("httpx.AsyncClient") as mock_client_class:
        # Mock the async context manager
        mock_client = AsyncMock()
        mock_client_class.return_value.__aenter__.return_value = mock_client

        # Mock a 404 response
        mock_response = AsyncMock()
        mock_response.status_code = 404
        mock_response.text = "Not found"

        # Create proper HTTPStatusError
        request = Request("GET", "https://registry.smithery.ai/servers")
        response = Response(404, text="Not found", request=request)
        mock_response.raise_for_status.side_effect = HTTPStatusError(
            "404 Not Found", request=request, response=response
        )

        mock_client.get.return_value = mock_response

        with pytest.raises(RegistryError, match="Failed to search"):
            await client.search("nonexistent")


@pytest.mark.asyncio
async def test_get_server_handles_404() -> None:
    """Test graceful handling of not found server."""
    from httpx import HTTPStatusError, Request, Response

    from deepmcpagent.registry import RegistryError, SmitheryAPIClient

    client = SmitheryAPIClient(api_key="test_key")

    with patch("httpx.AsyncClient") as mock_client_class:
        # Mock the async context manager
        mock_client = AsyncMock()
        mock_client_class.return_value.__aenter__.return_value = mock_client

        # Mock a 404 response
        mock_response = AsyncMock()
        mock_response.status_code = 404
        mock_response.text = "Not found"

        # Create proper HTTPStatusError
        request = Request("GET", "https://registry.smithery.ai/servers/%40nonexistent%2Fserver")
        response = Response(404, text="Not found", request=request)
        mock_response.raise_for_status.side_effect = HTTPStatusError(
            "404 Not Found", request=request, response=response
        )

        mock_client.get.return_value = mock_response

        with pytest.raises(RegistryError, match="Failed to get server"):
            await client.get_server("@nonexistent/server")


@pytest.mark.asyncio
async def test_search_handles_timeout() -> None:
    """Test timeout handling with appropriate error message."""
    from httpx import TimeoutException

    from deepmcpagent.registry import RegistryError, SmitheryAPIClient

    client = SmitheryAPIClient(api_key="test_key")

    with patch("httpx.AsyncClient") as mock_client_class:
        # Mock the async context manager
        mock_client = AsyncMock()
        mock_client_class.return_value.__aenter__.return_value = mock_client

        # Mock timeout exception
        mock_client.get.side_effect = TimeoutException("Request timeout")

        with pytest.raises(RegistryError, match="failed after 3 attempts"):
            await client.search("github")


@pytest.mark.asyncio
async def test_search_handles_500_error() -> None:
    """Test handling of server errors."""
    from httpx import HTTPStatusError, Request, Response

    from deepmcpagent.registry import RegistryError, SmitheryAPIClient

    client = SmitheryAPIClient(api_key="test_key")

    with patch("httpx.AsyncClient") as mock_client_class:
        # Mock the async context manager
        mock_client = AsyncMock()
        mock_client_class.return_value.__aenter__.return_value = mock_client

        # Mock a 500 response
        mock_response = AsyncMock()
        mock_response.status_code = 500
        mock_response.text = "Internal server error"

        # Create proper HTTPStatusError
        request = Request("GET", "https://registry.smithery.ai/servers")
        response = Response(500, text="Internal server error", request=request)
        mock_response.raise_for_status.side_effect = HTTPStatusError(
            "500 Internal Server Error", request=request, response=response
        )

        mock_client.get.return_value = mock_response

        with pytest.raises(RegistryError, match="Failed to search"):
            await client.search("github")


@pytest.mark.asyncio
async def test_search_with_limit() -> None:
    """Test that search respects limit parameter."""
    from deepmcpagent.registry import SmitheryAPIClient

    client = SmitheryAPIClient(api_key="test_key")

    with patch("httpx.AsyncClient") as mock_client_class:
        # Mock the async context manager
        mock_client = AsyncMock()
        mock_client_class.return_value.__aenter__.return_value = mock_client

        # Mock the GET response - use Mock for sync methods
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = MOCK_SEARCH_RESPONSE
        mock_response.raise_for_status = Mock()

        # Make the async get() return the mock response
        mock_client.get = AsyncMock(return_value=mock_response)

        await client.search("github", limit=3)

        # Verify limit was passed in request params
        call_args = mock_client.get.call_args
        assert call_args is not None
        params = call_args[1]["params"]
        assert params["pageSize"] == 3


@pytest.mark.asyncio
async def test_empty_search_results() -> None:
    """Test handling of empty search results."""
    from deepmcpagent.registry import SmitheryAPIClient

    client = SmitheryAPIClient(api_key="test_key")

    with patch("httpx.AsyncClient") as mock_client_class:
        # Mock the async context manager
        mock_client = AsyncMock()
        mock_client_class.return_value.__aenter__.return_value = mock_client

        # Mock the GET response with empty results - use Mock for sync methods
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"servers": []}
        mock_response.raise_for_status = Mock()

        # Make the async get() return the mock response
        mock_client.get = AsyncMock(return_value=mock_response)

        results = await client.search("nonexistent_capability")

        assert results == []


@pytest.mark.asyncio
async def test_get_server_with_sse_transport() -> None:
    """Test that get_server correctly handles different transport types."""
    from deepmcpagent.registry import SmitheryAPIClient

    client = SmitheryAPIClient(api_key="test_key")

    # Create metadata with SSE transport in connections
    metadata = {
        "qualified_name": "@smithery/github",
        "name": "github",
        "description": "GitHub API tools",
        "connections": [
            {
                "deploymentUrl": "https://example.com/mcp/github",
                "type": "sse",
            }
        ],
    }

    with patch("httpx.AsyncClient") as mock_client_class:
        # Mock the async context manager
        mock_client = AsyncMock()
        mock_client_class.return_value.__aenter__.return_value = mock_client

        # Mock the GET response - use Mock for sync methods
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = metadata
        mock_response.raise_for_status = Mock()

        # Make the async get() return the mock response
        mock_client.get = AsyncMock(return_value=mock_response)

        spec = await client.get_server("@smithery/github")

        assert isinstance(spec, HTTPServerSpec)
        assert spec.transport == "sse"


@pytest.mark.asyncio
async def test_client_uses_api_key() -> None:
    """Test that API key is included in requests."""
    from deepmcpagent.registry import SmitheryAPIClient

    client = SmitheryAPIClient(api_key="secret_key_123")

    with patch("httpx.AsyncClient") as mock_client_class:
        # Mock the async context manager
        mock_client = AsyncMock()
        mock_client_class.return_value.__aenter__.return_value = mock_client

        # Mock the GET response - use Mock for sync methods
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = MOCK_SEARCH_RESPONSE
        mock_response.raise_for_status = Mock()

        # Make the async get() return the mock response
        mock_client.get = AsyncMock(return_value=mock_response)

        await client.search("github")

        # Verify API key in headers
        call_args = mock_client.get.call_args
        headers = call_args[1]["headers"]
        assert headers is not None
        assert headers["Authorization"] == "Bearer secret_key_123"
