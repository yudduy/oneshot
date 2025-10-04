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
        verbose: bool = False,
    ) -> None:
        self.model = model
        self.servers: dict[str, ServerSpec] = dict(initial_servers)
        self.smithery = SmitheryAPIClient(api_key=smithery_key)
        self.instructions = instructions
        self.verbose = verbose

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
        if self.verbose:
            print(f"[BUILD] Rebuilding agent with {len(self.servers)} server(s)...")

        self.graph, self.loader = await build_deep_agent(
            servers=self.servers,
            model=self.model,
            instructions=self.instructions,
            trace_tools=self.verbose,  # Enable tool tracing in verbose mode
        )

        if self.verbose and self.loader:
            # Get detailed tool statistics
            stats = await self.loader.get_tool_stats()
            total_loaded = sum(s["loaded"] for s in stats.values())
            total_available = sum(s["total"] for s in stats.values())

            # Show per-server breakdown
            for server_name, counts in stats.items():
                if counts["total"] > counts["loaded"]:
                    print(
                        f"[BUILD] {server_name}: loaded {counts['loaded']}/{counts['total']} tools (filtered)"
                    )
                else:
                    print(
                        f"[BUILD] {server_name}: loaded {counts['loaded']} tools"
                    )

            print(f"[BUILD] Agent ready with {total_loaded} tool(s) total")
            if total_available > total_loaded:
                from .config import MAX_TOOLS_PER_SERVER
                print(
                    f"[BUILD] ℹ️  Filtered {total_available - total_loaded} tools to prevent context overflow "
                    f"(MAX_TOOLS_PER_SERVER={MAX_TOOLS_PER_SERVER})"
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

    async def _discover_and_add_server(self, capability: str) -> bool:
        """Discover and add MCP server for the given capability.

        Searches the Smithery registry for a server matching the capability,
        retrieves its specification, and adds it to the active servers.

        Args:
            capability: Capability name to search for (e.g., "github", "weather").

        Returns:
            True if server was found and added, False otherwise.

        Example:
            >>> success = await orchestrator._discover_and_add_server("github")
            >>> if success:
            ...     print("GitHub server added!")
        """
        try:
            if self.verbose:
                print(f"[DISCOVERY] Searching Smithery for '{capability}' servers...")

            # Search Smithery for matching servers
            results = await self.smithery.search(query=capability, limit=1)

            if not results:
                if self.verbose:
                    print(f"[DISCOVERY] No servers found for '{capability}'")
                return False

            # Get the first result
            server_info = results[0]
            # API returns camelCase, try both formats
            qualified_name = server_info.get("qualifiedName") or server_info.get("qualified_name")

            if not qualified_name:
                return False

            if self.verbose:
                print(f"[DISCOVERY] Found server: {qualified_name}")
                print(f"[DISCOVERY] Fetching server specification...")

            # Get full server spec
            spec = await self.smithery.get_server(qualified_name)

            # Add to active servers (use capability as name)
            self.servers[capability] = spec

            if self.verbose:
                print(f"[DISCOVERY] ✓ Added '{qualified_name}' as '{capability}' server")

            return True

        except Exception as exc:
            # Check if it's a RegistryError (e.g., Smithery OAuth server)
            from .registry import RegistryError

            if isinstance(exc, RegistryError):
                # User-friendly error message for incompatible servers
                print(f"⚠️  Cannot add '{qualified_name}' for {capability}:")
                print(f"   {exc}")
                print(
                    f"💡 Tip: You can manually configure this server if you have credentials,"
                )
                print(f"   or try a different query to find alternative servers.")
            else:
                if self.verbose:
                    print(f"[DISCOVERY] Error during discovery: {exc}")
            # Otherwise silently fail for other discovery errors
            return False

    async def chat(self, user_message: str) -> str:
        """Main conversation loop with dynamic tool discovery.

        Sends a message to the agent, detects if tools are missing,
        dynamically discovers and adds them, then retries.

        Args:
            user_message: The user's message/query.

        Returns:
            The agent's final response text.

        Raises:
            Exception: If agent invocation fails.

        Example:
            >>> response = await orchestrator.chat("Search GitHub for MCP servers")
            >>> print(response)
        """
        # Add user message to history
        self.messages.append({"role": "user", "content": user_message})

        # If no servers yet, create a synthetic "no tools" response to trigger discovery
        if not self.servers:
            final_text = "I don't have access to any tools yet to help with this request."
        else:
            # Build agent if not already built
            if self.graph is None:
                await self._rebuild_agent()

            # Invoke agent with full message history
            result = await self.graph.ainvoke({"messages": self.messages})

            # Extract final response
            final_message = result.get("messages", [])[-1] if result.get("messages") else None
            final_text = ""

            if final_message:
                content = getattr(final_message, "content", None)
                if isinstance(content, str):
                    final_text = content
                elif isinstance(content, list) and content and isinstance(content[0], dict):
                    final_text = content[0].get("text", str(content))
                else:
                    final_text = str(final_message)

        # Add assistant response to history
        self.messages.append({"role": "assistant", "content": final_text})

        # Check if agent needs tools
        if self._needs_tools(final_text):
            # Extract what capability is needed from the user's message
            capability = self._extract_capability(user_message)

            if capability:
                # Try to discover and add the server
                success = await self._discover_and_add_server(capability)

                if success:
                    # Rebuild agent with new server
                    await self._rebuild_agent()

                    # Retry the original query (remove the failed response first)
                    self.messages.pop()  # Remove failed assistant response

                    # Invoke again with updated tools
                    result = await self.graph.ainvoke({"messages": self.messages})

                    # Extract final response again
                    final_message = (
                        result.get("messages", [])[-1] if result.get("messages") else None
                    )
                    final_text = ""

                    if final_message:
                        content = getattr(final_message, "content", None)
                        if isinstance(content, str):
                            final_text = content
                        elif isinstance(content, list) and content and isinstance(content[0], dict):
                            final_text = content[0].get("text", str(content))
                        else:
                            final_text = str(final_message)

                    # Update history with successful response
                    self.messages.append({"role": "assistant", "content": final_text})

        return final_text
