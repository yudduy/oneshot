"""Tests for DynamicOrchestrator state management and tool discovery."""

from __future__ import annotations

from unittest.mock import AsyncMock, Mock, patch

import pytest

from deepmcpagent.config import HTTPServerSpec


@pytest.mark.asyncio
async def test_preserves_messages_across_rebuild() -> None:
    """Test that messages stay intact when agent is rebuilt."""
    from deepmcpagent.orchestrator import DynamicOrchestrator

    # Create orchestrator with minimal setup
    orchestrator = DynamicOrchestrator(
        model="openai:gpt-4",
        initial_servers={},
        smithery_key="test_key",
    )

    # Mock the agent graph
    with patch("deepmcpagent.orchestrator.build_deep_agent") as mock_build:
        mock_graph = AsyncMock()
        mock_loader = Mock()
        mock_build.return_value = (mock_graph, mock_loader)

        # Add some messages
        orchestrator.messages.append({"role": "user", "content": "Hello"})
        orchestrator.messages.append({"role": "assistant", "content": "Hi"})

        # Rebuild agent
        await orchestrator._rebuild_agent()

        # Verify messages preserved
        assert len(orchestrator.messages) == 2
        assert orchestrator.messages[0]["content"] == "Hello"
        assert orchestrator.messages[1]["content"] == "Hi"


@pytest.mark.asyncio
async def test_multiple_rebuilds_dont_lose_context() -> None:
    """Test that we can rebuild 5+ times without losing history."""
    from deepmcpagent.orchestrator import DynamicOrchestrator

    orchestrator = DynamicOrchestrator(
        model="openai:gpt-4",
        initial_servers={},
        smithery_key="test_key",
    )

    with patch("deepmcpagent.orchestrator.build_deep_agent") as mock_build:
        mock_graph = AsyncMock()
        mock_loader = Mock()
        mock_build.return_value = (mock_graph, mock_loader)

        # Add messages and rebuild multiple times
        for i in range(10):
            orchestrator.messages.append({"role": "user", "content": f"Message {i}"})
            await orchestrator._rebuild_agent()

        # Verify all messages preserved
        assert len(orchestrator.messages) == 10
        assert orchestrator.messages[0]["content"] == "Message 0"
        assert orchestrator.messages[9]["content"] == "Message 9"


@pytest.mark.asyncio
async def test_rebuild_updates_graph_and_loader() -> None:
    """Test that rebuild replaces graph and loader references."""
    from deepmcpagent.orchestrator import DynamicOrchestrator

    orchestrator = DynamicOrchestrator(
        model="openai:gpt-4",
        initial_servers={},
        smithery_key="test_key",
    )

    with patch("deepmcpagent.orchestrator.build_deep_agent") as mock_build:
        # First build
        mock_graph1 = AsyncMock()
        mock_loader1 = Mock()
        mock_build.return_value = (mock_graph1, mock_loader1)

        await orchestrator._rebuild_agent()
        assert orchestrator.graph is mock_graph1
        assert orchestrator.loader is mock_loader1

        # Second build with different objects
        mock_graph2 = AsyncMock()
        mock_loader2 = Mock()
        mock_build.return_value = (mock_graph2, mock_loader2)

        await orchestrator._rebuild_agent()
        assert orchestrator.graph is mock_graph2
        assert orchestrator.loader is mock_loader2


@pytest.mark.asyncio
async def test_rebuild_uses_current_servers() -> None:
    """Test that rebuild passes current servers to build_deep_agent."""
    from deepmcpagent.orchestrator import DynamicOrchestrator

    initial_servers = {
        "math": HTTPServerSpec(url="http://localhost:8000/mcp", transport="http")
    }

    orchestrator = DynamicOrchestrator(
        model="openai:gpt-4",
        initial_servers=initial_servers,
        smithery_key="test_key",
    )

    with patch("deepmcpagent.orchestrator.build_deep_agent") as mock_build:
        mock_graph = AsyncMock()
        mock_loader = Mock()
        mock_build.return_value = (mock_graph, mock_loader)

        # Rebuild
        await orchestrator._rebuild_agent()

        # Verify build_deep_agent was called with current servers
        mock_build.assert_called_once()
        call_kwargs = mock_build.call_args.kwargs
        assert call_kwargs["servers"] == initial_servers
        assert call_kwargs["model"] == "openai:gpt-4"


@pytest.mark.asyncio
async def test_rebuild_with_added_server() -> None:
    """Test that rebuild includes newly added servers."""
    from deepmcpagent.orchestrator import DynamicOrchestrator

    orchestrator = DynamicOrchestrator(
        model="openai:gpt-4",
        initial_servers={},
        smithery_key="test_key",
    )

    with patch("deepmcpagent.orchestrator.build_deep_agent") as mock_build:
        mock_graph = AsyncMock()
        mock_loader = Mock()
        mock_build.return_value = (mock_graph, mock_loader)

        # Add a server dynamically
        orchestrator.servers["github"] = HTTPServerSpec(
            url="http://localhost:8001/mcp", transport="http"
        )

        # Rebuild
        await orchestrator._rebuild_agent()

        # Verify new server was included
        call_kwargs = mock_build.call_args.kwargs
        assert "github" in call_kwargs["servers"]
        assert call_kwargs["servers"]["github"].url == "http://localhost:8001/mcp"


@pytest.mark.asyncio
async def test_external_message_storage() -> None:
    """Test that messages are stored externally, not in graph."""
    from deepmcpagent.orchestrator import DynamicOrchestrator

    orchestrator = DynamicOrchestrator(
        model="openai:gpt-4",
        initial_servers={},
        smithery_key="test_key",
    )

    # Messages should be in orchestrator, not graph
    assert isinstance(orchestrator.messages, list)
    assert len(orchestrator.messages) == 0

    # Add messages
    orchestrator.messages.append({"role": "user", "content": "Test"})
    assert len(orchestrator.messages) == 1

    # Graph should be None until built
    assert orchestrator.graph is None


@pytest.mark.asyncio
async def test_initialization_with_servers() -> None:
    """Test that orchestrator initializes with provided servers."""
    from deepmcpagent.orchestrator import DynamicOrchestrator

    initial_servers = {
        "math": HTTPServerSpec(url="http://localhost:8000/mcp", transport="http"),
        "weather": HTTPServerSpec(url="http://localhost:8001/mcp", transport="sse"),
    }

    orchestrator = DynamicOrchestrator(
        model="openai:gpt-4",
        initial_servers=initial_servers,
        smithery_key="test_key",
    )

    # Verify servers were stored
    assert "math" in orchestrator.servers
    assert "weather" in orchestrator.servers
    assert orchestrator.servers["math"].url == "http://localhost:8000/mcp"
    assert orchestrator.servers["weather"].transport == "sse"


@pytest.mark.asyncio
async def test_smithery_client_initialization() -> None:
    """Test that Smithery client is initialized with API key."""
    from deepmcpagent.orchestrator import DynamicOrchestrator

    orchestrator = DynamicOrchestrator(
        model="openai:gpt-4",
        initial_servers={},
        smithery_key="secret_key_123",
    )

    # Verify Smithery client exists
    assert orchestrator.smithery is not None
    assert orchestrator.smithery._api_key == "secret_key_123"


@pytest.mark.asyncio
async def test_model_storage() -> None:
    """Test that model parameter is stored correctly."""
    from deepmcpagent.orchestrator import DynamicOrchestrator

    orchestrator = DynamicOrchestrator(
        model="anthropic:claude-3-5-sonnet-latest",
        initial_servers={},
        smithery_key="test_key",
    )

    assert orchestrator.model == "anthropic:claude-3-5-sonnet-latest"


@pytest.mark.asyncio
async def test_rebuild_handles_build_failure() -> None:
    """Test that rebuild handles build_deep_agent failures gracefully."""
    from deepmcpagent.orchestrator import DynamicOrchestrator

    orchestrator = DynamicOrchestrator(
        model="openai:gpt-4",
        initial_servers={},
        smithery_key="test_key",
    )

    with patch("deepmcpagent.orchestrator.build_deep_agent") as mock_build:
        mock_build.side_effect = Exception("Build failed")

        # Rebuild should raise the exception
        with pytest.raises(Exception, match="Build failed"):
            await orchestrator._rebuild_agent()

        # Graph and loader should remain None
        assert orchestrator.graph is None
        assert orchestrator.loader is None
