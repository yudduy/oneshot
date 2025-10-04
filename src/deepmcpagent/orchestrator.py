"""Dynamic orchestrator for MCP tool discovery and agent rebuilding."""

from __future__ import annotations

from typing import Any

from .agent import ModelLike, build_deep_agent
from .config import ServerSpec
from .registry import SmitheryAPIClient
from .tools import MCPToolLoader


class DynamicOrchestrator:
    """Orchestrator that manages agent state and enables dynamic tool discovery.

    This orchestrator maintains conversation state externally (not inside the agent graph),
    allowing the agent to be rebuilt when new MCP servers are added without losing context.

    Key responsibilities:
    - External message storage (survives rebuilds)
    - Server management (add/remove servers dynamically)
    - Agent rebuild coordination
    - Tool discovery via Smithery registry

    Args:
        model: LangChain model instance or provider ID string (e.g., "openai:gpt-4").
        initial_servers: Initial MCP servers to connect to.
        smithery_key: API key for Smithery registry.
        instructions: Optional system prompt override.

    Example:
        >>> orchestrator = DynamicOrchestrator(
        ...     model="openai:gpt-4",
        ...     initial_servers={"math": HTTPServerSpec(...)},
        ...     smithery_key="sk_..."
        ... )
        >>> response = await orchestrator.chat("Calculate 2 + 2")
    """

    def __init__(
        self,
        model: ModelLike,
        initial_servers: dict[str, ServerSpec],
        smithery_key: str,
        instructions: str | None = None,
    ) -> None:
        self.model = model
        self.servers: dict[str, ServerSpec] = dict(initial_servers)
        self.smithery = SmitheryAPIClient(api_key=smithery_key)
        self.instructions = instructions

        # External message storage (persists across rebuilds)
        self.messages: list[dict[str, Any]] = []

        # Agent components (replaced on rebuild)
        self.graph: Any = None
        self.loader: MCPToolLoader | None = None

    async def _rebuild_agent(self) -> None:
        """Rebuild the agent with current servers.

        This creates a new agent graph with the current set of servers.
        Conversation history is preserved in self.messages and passed
        to the new graph on first invocation.

        Raises:
            Exception: If agent building fails.
        """
        self.graph, self.loader = await build_deep_agent(
            servers=self.servers,
            model=self.model,
            instructions=self.instructions,
        )
