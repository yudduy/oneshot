"""Dynamic orchestrator for MCP tool discovery and agent rebuilding."""

from __future__ import annotations

import re
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

    def _needs_tools(self, response: str) -> bool:
        """Detect if the agent response indicates missing tools.

        Uses pattern matching to identify phrases that suggest the agent
        lacks necessary capabilities.

        Args:
            response: The agent's response text.

        Returns:
            True if missing tools detected, False otherwise.

        Example:
            >>> orchestrator._needs_tools("I don't have access to GitHub")
            True
            >>> orchestrator._needs_tools("The result is 42")
            False
        """
        # Patterns that indicate missing tools
        patterns = [
            r"i don'?t have (access to|tools for)",
            r"i (cannot|can'?t) .* without",
            r"i'?m unable to",
            r"(there are )?no .*(server|tool)s? .*(available|configured)",
            r"i don'?t have",
            r"i cannot",
        ]

        response_lower = response.lower()

        return any(re.search(pattern, response_lower) for pattern in patterns)

    def _extract_capability(self, response: str) -> str | None:
        """Extract the capability name from agent response.

        Uses keyword matching to identify what capability is missing
        (e.g., "github", "weather", "database").

        Args:
            response: The agent's response text.

        Returns:
            Capability name (lowercase) or None if unclear.

        Example:
            >>> orchestrator._extract_capability("I need GitHub access")
            'github'
        """
        response_lower = response.lower()

        # Common capability keywords
        capabilities = {
            "github": ["github", "git hub", "repository", "repositories"],
            "weather": ["weather", "forecast", "temperature", "climate"],
            "database": ["database", "db", "sql", "query", "queries"],
            "search": ["search", "google", "bing"],
            "email": ["email", "mail", "smtp"],
            "slack": ["slack", "messaging"],
            "jira": ["jira", "ticket", "issue tracker"],
            "calendar": ["calendar", "schedule", "appointment"],
        }

        # Find the first matching capability
        for capability, keywords in capabilities.items():
            for keyword in keywords:
                if keyword in response_lower:
                    return capability

        return None
