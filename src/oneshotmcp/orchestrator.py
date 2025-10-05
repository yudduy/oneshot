"""Dynamic orchestrator for MCP tool discovery and agent rebuilding."""

from __future__ import annotations

import re
from typing import Any

from .agent import ModelLike, build_deep_agent
from .config import ServerSpec
from .oauth import TokenStore
from .registry import OAuthRequired, SmitheryAPIClient
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
        token_store: TokenStore | None = None,
    ) -> None:
        self.model = model
        self.servers: dict[str, ServerSpec] = dict(initial_servers)
        self.token_store = token_store or TokenStore()
        self.smithery = SmitheryAPIClient(api_key=smithery_key, token_store=self.token_store)
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

    def _extract_response_text(self, result: dict[str, Any]) -> str:
        """Extract text response from agent invocation result.

        Args:
            result: Result from graph.ainvoke()

        Returns:
            Extracted text content or empty string.
        """
        messages = result.get("messages", [])
        if not messages:
            return ""

        final_message = messages[-1]
        content = getattr(final_message, "content", None)

        if isinstance(content, str):
            return content
        elif isinstance(content, list) and content:
            if isinstance(content[0], dict):
                return content[0].get("text", "")

        return str(final_message) if final_message else ""

    def _deduplicate_servers(self, servers: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Remove duplicate servers based on qualifiedName.

        Args:
            servers: List of server info dicts from Smithery API.

        Returns:
            Deduplicated list preserving order.
        """
        seen: set[str] = set()
        unique: list[dict[str, Any]] = []

        for server in servers:
            qualified_name = server.get("qualifiedName") or server.get("qualified_name")
            if qualified_name and qualified_name not in seen:
                seen.add(qualified_name)
                unique.append(server)

        return unique

    def _extract_explicit_mcp_request(self, user_message: str) -> str | None:
        """Extract explicit MCP server request from user message.

        Detects patterns where user explicitly requests a specific MCP server:
        - "fetch context7 mcp"
        - "use github mcp to..."
        - "get weather server"
        - "add slack tools"

        Args:
            user_message: The user's input message.

        Returns:
            Server name if explicit request detected, None otherwise.

        Example:
            >>> orchestrator._extract_explicit_mcp_request("fetch context7 mcp and use it")
            'context7'
            >>> orchestrator._extract_explicit_mcp_request("what is the weather?")
            None
        """
        message_lower = user_message.lower()

        # Patterns for explicit MCP requests
        patterns = [
            r"fetch\s+(\w+)\s+mcp",
            r"use\s+(\w+)\s+mcp",
            r"get\s+(\w+)\s+(?:server|mcp)",
            r"add\s+(\w+)\s+(?:server|tools|mcp)",
            r"install\s+(\w+)",
            r"load\s+(\w+)\s+(?:server|mcp)",
        ]

        for pattern in patterns:
            match = re.search(pattern, message_lower)
            if match:
                return match.group(1)  # Return the server name

        return None

    async def _research_capability(self, capability: str) -> dict[str, Any]:
        """Use web search to understand what the capability/tool is.

        This phase uses Tavily (if available) to research the capability
        before searching Smithery, enabling semantic query generation.

        Args:
            capability: Capability name to research (e.g., "context7").

        Returns:
            Dict with "description" and "keywords" if research succeeded,
            empty dict otherwise.

        Example:
            >>> research = await orchestrator._research_capability("context7")
            >>> research["description"]
            'Context7 is a documentation search tool for libraries and frameworks'
        """
        # Skip research if Tavily not available
        if "tavily" not in self.servers:
            if self.verbose:
                print(f"[RESEARCH] Skipping research (Tavily not configured)")
            return {}

        try:
            if self.verbose:
                print(f"[RESEARCH] Researching '{capability}' using web search...")

            # Build temporary mini-agent with just Tavily
            from .agent import build_deep_agent

            research_graph, _ = await build_deep_agent(
                servers={"tavily": self.servers["tavily"]},
                model=self.model,
                instructions=f"You are a research assistant. Provide concise, factual answers about developer tools and MCP servers.",
                trace_tools=False,  # Silent research
            )

            # Ask research question
            result = await research_graph.ainvoke({
                "messages": [{
                    "role": "user",
                    "content": f"What is {capability} in the context of MCP servers, developer tools, or software? Answer in 1-2 sentences."
                }]
            })

            # Extract response
            description = self._extract_response_text(result)

            if description:
                # Extract keywords from description (simple approach)
                # Future: use LLM to extract structured info
                keywords = [
                    word.lower().strip(".,!?")
                    for word in description.split()
                    if len(word) > 4  # Skip short words
                ][:5]  # Top 5 keywords

                if self.verbose:
                    print(f"[RESEARCH] Found: {description[:80]}...")
                    print(f"[RESEARCH] Keywords: {keywords}")

                return {"description": description, "keywords": keywords}

        except Exception as exc:
            if self.verbose:
                print(f"[RESEARCH] Research failed: {exc}")

        return {}

    def _generate_search_queries(
        self, capability: str, research: dict[str, Any]
    ) -> list[str]:
        """Generate multiple search query variations for Smithery.

        Creates variations to improve discovery success rate:
        - Exact capability name
        - With "mcp" suffix
        - With "server" suffix
        - Semantic description from research (if available)

        Args:
            capability: Capability name (e.g., "context7").
            research: Research results from _research_capability().

        Returns:
            List of search query strings to try.

        Example:
            >>> queries = orchestrator._generate_search_queries(
            ...     "context7",
            ...     {"description": "documentation search tool"}
            ... )
            >>> queries
            ['context7', 'context7 mcp', 'context7 server', 'documentation search tool']
        """
        queries = [
            capability,  # Original
            f"{capability} mcp",  # With MCP suffix
            f"{capability} server",  # With server suffix
        ]

        # Add semantic query from research description
        if research and research.get("description"):
            # Use first sentence of description as query
            desc = research["description"]
            first_sentence = desc.split(".")[0].strip()
            if first_sentence and first_sentence not in queries:
                queries.append(first_sentence)

        # Add keyword-based query
        if research and research.get("keywords"):
            keyword_query = " ".join(research["keywords"][:3])
            if keyword_query not in queries:
                queries.append(keyword_query)

        if self.verbose:
            print(f"[SEARCH] Generated {len(queries)} search queries: {queries}")

        return queries

    async def _search_with_refinement(
        self, queries: list[str]
    ) -> list[dict[str, Any]]:
        """Execute multiple search queries and combine results.

        Tries all query variations, collects results, and deduplicates.

        Args:
            queries: List of search query strings from _generate_search_queries().

        Returns:
            Deduplicated list of server info dicts.

        Example:
            >>> candidates = await orchestrator._search_with_refinement([
            ...     "context7",
            ...     "context7 mcp",
            ...     "documentation search"
            ... ])
        """
        all_results: list[dict[str, Any]] = []

        for query in queries:
            try:
                if self.verbose:
                    print(f"[SEARCH] Trying query: '{query}'...")

                results = await self.smithery.search(query=query, limit=5)

                if results:
                    if self.verbose:
                        print(f"[SEARCH] Found {len(results)} result(s) for '{query}'")
                    all_results.extend(results)
                else:
                    if self.verbose:
                        print(f"[SEARCH] No results for '{query}'")

            except Exception as exc:
                if self.verbose:
                    print(f"[SEARCH] Error searching '{query}': {exc}")
                continue

        # Deduplicate combined results
        unique = self._deduplicate_servers(all_results)

        if self.verbose:
            print(f"[SEARCH] Total unique candidates: {len(unique)}")

        return unique

    def _rank_servers(
        self,
        capability: str,
        servers: list[dict[str, Any]],
        research: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """Rank servers by relevance to the requested capability.

        Scoring criteria (higher is better):
        - 100 points: Exact match in qualified name
        - 80 points: Match in server name
        - 60 points: Match in description
        - 40 points: Keyword overlap with research
        - 0 points: No match

        Args:
            capability: Requested capability (e.g., "context7").
            servers: List of server candidates from search.
            research: Research results with keywords.

        Returns:
            Servers sorted by relevance score (highest first).

        Example:
            >>> ranked = orchestrator._rank_servers(
            ...     "context7",
            ...     [{"qualifiedName": "@context7/mcp", ...}, ...],
            ...     {"keywords": ["documentation", "search"]}
            ... )
        """

        def calculate_score(server: dict[str, Any]) -> int:
            qualified_name = (server.get("qualifiedName") or "").lower()
            name = (server.get("name") or "").lower()
            description = (server.get("description") or "").lower()
            capability_lower = capability.lower()

            # Exact match in qualified name (highest priority)
            if capability_lower in qualified_name:
                return 100

            # Match in server name
            if capability_lower in name:
                return 80

            # Match in description
            if capability_lower in description:
                return 60

            # Keyword overlap from research
            if research and research.get("keywords"):
                keywords = research["keywords"]
                matches = sum(1 for kw in keywords if kw.lower() in description)
                if matches > 0:
                    return 40 + (matches * 5)  # Bonus for multiple keyword matches

            return 0

        # Score and sort
        scored = [(s, calculate_score(s)) for s in servers]
        ranked = sorted(scored, key=lambda x: x[1], reverse=True)

        if self.verbose:
            print(f"[RANKING] Ranked {len(ranked)} candidates:")
            for server, score in ranked[:5]:  # Show top 5
                qn = server.get("qualifiedName", "unknown")
                desc = server.get("description", "")[:40]
                print(f"[RANKING]   {score:3d} pts: {qn} - {desc}...")

        return [s for s, _ in ranked]

    async def _handle_oauth_flow(self, oauth_exc: OAuthRequired, capability: str) -> bool:
        """Handle OAuth authentication flow automatically.

        Opens browser for user authorization, saves tokens, and retries server addition.

        Args:
            oauth_exc: OAuthRequired exception with OAuth config and auth URL.
            capability: Capability name for server naming.

        Returns:
            True if OAuth succeeded and server was added, False otherwise.
        """
        from .oauth import BrowserAuthHandler, PKCEAuthenticator

        try:
            # Inform user about OAuth requirement
            print(f"\n🔐 Server '{oauth_exc.server_name}' requires OAuth 2.1 authentication")
            print(f"This will open your browser to authorize OneShotMCP.")

            # Prompt user for consent
            user_input = input("\nOpen browser for authorization? (yes/no): ").strip().lower()

            # Check if user accepts (accept variations: yes, y, YES, Y)
            if user_input not in ("yes", "y"):
                if self.verbose:
                    print(f"[OAUTH] Authorization declined by user")
                return False

            if self.verbose:
                print(f"\n[OAUTH] Opening browser for authorization...")

            # Initialize authenticator
            client_id = "oneshotmcp-cli"
            redirect_uri = "http://localhost:8765/callback"

            auth = PKCEAuthenticator(
                authorization_endpoint=oauth_exc.oauth_config.authorization_endpoint,
                token_endpoint=oauth_exc.oauth_config.token_endpoint,
                client_id=client_id,
                scopes=oauth_exc.oauth_config.scopes,
            )

            # Generate PKCE pair
            verifier, challenge = auth.generate_pkce_pair()

            # Build authorization URL
            auth_url = auth.build_authorization_url(redirect_uri, challenge)

            # Start browser-based authorization
            handler = BrowserAuthHandler(redirect_uri, timeout=180.0)  # 3 min timeout
            code = await handler.authorize(auth_url)

            if self.verbose:
                print(f"[OAUTH] Authorization successful! Exchanging code for tokens...")

            # Exchange code for tokens
            tokens = await auth.exchange_code_for_token(code, verifier, redirect_uri)

            # Save tokens
            self.token_store.save_tokens(oauth_exc.server_name, tokens)

            if self.verbose:
                print(f"[OAUTH] ✓ Tokens saved for '{oauth_exc.server_name}'")

            # Retry getting the server (should work now with stored tokens)
            spec = await self.smithery.get_server(oauth_exc.server_name)
            self.servers[capability] = spec

            if self.verbose:
                print(f"[OAUTH] ✓ Successfully added '{oauth_exc.server_name}' as '{capability}' server")

            return True

        except Exception as exc:
            if self.verbose:
                print(f"[OAUTH] ✗ OAuth flow failed: {exc}")
            return False

    async def _try_local_installation(
        self, qualified_name: str, capability: str
    ) -> bool:
        """Attempt local installation of MCP server when hosted version fails.

        This is the automatic fallback when OAuth is required but fails/declined.
        It will:
        1. Fetch full Smithery metadata for the server
        2. Check if it's npm-installable
        3. Install locally using npx
        4. Add as stdio server

        Args:
            qualified_name: Smithery qualified name (e.g., "@upstash/context7-mcp")
            capability: Capability name for server naming

        Returns:
            True if local installation succeeded, False otherwise
        """
        from .local_installer import LocalMCPInstaller

        try:
            # Fetch full Smithery metadata (need it for config requirements)
            if self.verbose:
                print(f"[LOCAL] Fetching metadata for local installation...")

            # Get metadata from Smithery API directly
            import httpx

            encoded_name = qualified_name.replace("/", "%2F").replace("@", "%40")
            url = f"{self.smithery._base_url}/servers/{encoded_name}"
            headers = {"Authorization": f"Bearer {self.smithery._api_key}"}

            async with httpx.AsyncClient(timeout=30) as client:
                response = await client.get(url, headers=headers)
                response.raise_for_status()
                metadata = response.json()

            # Attempt local installation
            installer = LocalMCPInstaller()

            spec = await installer.attempt_local_installation(
                smithery_metadata=metadata,
                user_config={},  # TODO: Allow user to provide config
            )

            if spec is None:
                if self.verbose:
                    print(f"[LOCAL] ✗ Local installation not possible for '{qualified_name}'")
                return False

            # Add as stdio server
            self.servers[capability] = spec

            if self.verbose:
                print(f"[LOCAL] ✓ Successfully installed '{qualified_name}' locally")
                print(f"[LOCAL]   Command: {spec.command} {' '.join(spec.args)}")

            return True

        except Exception as exc:
            if self.verbose:
                print(f"[LOCAL] ✗ Local installation failed: {exc}")
            return False

    async def _try_candidates(
        self, ranked_servers: list[dict[str, Any]], capability: str
    ) -> bool:
        """Try adding servers from ranked list, handling failures gracefully.

        Attempts to add servers in ranked order (best match first),
        automatically handling OAuth flows when needed.

        Args:
            ranked_servers: Servers sorted by relevance (from _rank_servers).
            capability: Capability name for server naming.

        Returns:
            True if a server was successfully added, False otherwise.

        Example:
            >>> success = await orchestrator._try_candidates(
            ...     [{"qualifiedName": "@context7/mcp"}, ...],
            ...     "context7"
            ... )
        """
        if not ranked_servers:
            if self.verbose:
                print(f"[ATTEMPT] No candidates to try")
            return False

        # Try top 5 candidates (or all if fewer)
        max_attempts = min(5, len(ranked_servers))

        for i, server_info in enumerate(ranked_servers[:max_attempts], 1):
            qualified_name = server_info.get("qualifiedName") or server_info.get(
                "qualified_name"
            )

            if not qualified_name:
                continue

            try:
                if self.verbose:
                    desc = server_info.get("description", "N/A")[:50]
                    print(
                        f"[ATTEMPT] Attempt {i}/{max_attempts}: Trying '{qualified_name}'"
                    )
                    print(f"[ATTEMPT]   Description: {desc}...")

                # PRIORITY 1: Try local npm installation first (simpler, no OAuth)
                if self.verbose:
                    print(f"[ATTEMPT] Trying local npm installation first...")

                local_success = await self._try_local_installation(
                    qualified_name=qualified_name,
                    capability=capability,
                )
                if local_success:
                    return True

                # PRIORITY 2: Local installation failed, try Smithery-hosted with OAuth
                if self.verbose:
                    print(f"[ATTEMPT] Local installation failed, trying hosted server...")

                try:
                    # Get full server spec from Smithery (may require OAuth)
                    spec = await self.smithery.get_server(qualified_name)

                    # Add to active servers
                    self.servers[capability] = spec

                    if self.verbose:
                        print(
                            f"[ATTEMPT] ✓ Successfully added '{qualified_name}' as '{capability}' server (hosted)"
                        )

                    return True

                except OAuthRequired as oauth_exc:
                    # Handle OAuth automatically
                    if self.verbose:
                        print(f"[ATTEMPT] Hosted server requires OAuth authentication")

                    # Try OAuth flow
                    oauth_success = await self._handle_oauth_flow(oauth_exc, capability)
                    if oauth_success:
                        return True

                    # OAuth failed
                    if self.verbose:
                        print(f"[ATTEMPT] OAuth flow failed")

                except Exception as exc:
                    # Check if it's a RegistryError (config requirement, etc.)
                    from .registry import RegistryError

                    if isinstance(exc, RegistryError):
                        if self.verbose:
                            print(f"[ATTEMPT] Cannot use hosted server: {exc}")
                    else:
                        if self.verbose:
                            print(f"[ATTEMPT] Error with hosted server: {exc}")

                # Both local and hosted failed, try next candidate
                if self.verbose:
                    print(f"[ATTEMPT]   Trying next candidate...")
                continue

            except Exception as exc:
                # Unexpected error in local installation attempt
                if self.verbose:
                    print(f"[ATTEMPT] ✗ Unexpected error: {exc}")
                    print(f"[ATTEMPT]   Trying next candidate...")
                continue

        if self.verbose:
            print(f"[ATTEMPT] All {max_attempts} attempts failed")

        return False

    def _suggest_alternatives(
        self, capability: str, ranked_servers: list[dict[str, Any]]
    ) -> None:
        """Suggest alternatives to the user when all attempts fail.

        Displays helpful information about what was found and why it failed,
        along with suggestions for next steps.

        Args:
            capability: The requested capability.
            ranked_servers: Servers that were attempted (ranked by relevance).

        Example:
            >>> orchestrator._suggest_alternatives("context7", [
            ...     {"qualifiedName": "@mem0ai/mem0", "description": "Memory tool"},
            ... ])
        """
        if not ranked_servers:
            print(f"\n⚠️  Discovery failed: No MCP servers found for '{capability}'")
            print("\n💡 Suggestions:")
            print(f"   • Try being more specific (e.g., 'fetch {capability}-docs mcp')")
            print(f"   • Describe what it does (e.g., 'get documentation search tool')")
            print(f"   • Check spelling or try alternative names")
            return

        print(
            f"\n⚠️  Discovery failed: Found {len(ranked_servers)} potential matches, but all require OAuth or manual config"
        )
        print(f"\n🔍 Top candidates found:")

        for i, server in enumerate(ranked_servers[:3], 1):
            qn = server.get("qualifiedName", "unknown")
            desc = server.get("description", "No description available")[:80]
            print(f"\n   {i}. {qn}")
            print(f"      {desc}...")

        print(f"\n💡 Next steps:")
        print(f"   • Try self-hosting one of these servers")
        print(
            f"   • Use --http flag to manually configure with your credentials:"
        )
        print(
            f'     oneshot --http "name={capability} url=http://localhost:8000/mcp"'
        )
        print(
            f"   • Search for alternative servers: https://smithery.ai/search?q={capability}"
        )

    async def _discover_and_add_server(self, capability: str) -> bool:
        """Intelligent multi-phase MCP server discovery.

        This method orchestrates a 5-phase discovery process:
        1. Research: Use web search to understand the capability
        2. Multi-Query Search: Try multiple search variations
        3. Ranking: Score candidates by relevance
        4. Multi-Attempt: Try top candidates, skip OAuth errors
        5. Fallback: Suggest alternatives if all fail

        Args:
            capability: Capability name to search for (e.g., "github", "context7").

        Returns:
            True if server was found and added, False otherwise.

        Example:
            >>> success = await orchestrator._discover_and_add_server("context7")
            >>> if success:
            ...     print("Context7 server added!")
        """
        if self.verbose:
            print(f"\n[DISCOVERY] Starting intelligent discovery for '{capability}'...")

        # Phase 1: Research the capability (if Tavily available)
        research = await self._research_capability(capability)

        # Phase 2: Generate search queries and execute multi-query search
        queries = self._generate_search_queries(capability, research)
        candidates = await self._search_with_refinement(queries)

        if not candidates:
            if self.verbose:
                print(f"[DISCOVERY] No candidates found across all queries")
            self._suggest_alternatives(capability, [])
            return False

        # Phase 3: Rank candidates by relevance
        ranked = self._rank_servers(capability, candidates, research)

        # Phase 4: Try adding candidates in ranked order
        success = await self._try_candidates(ranked, capability)

        if success:
            if self.verbose:
                print(f"[DISCOVERY] ✓ Successfully discovered and added '{capability}'")
            return True

        # Phase 5: All attempts failed - suggest alternatives
        self._suggest_alternatives(capability, ranked)
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

        # Check for explicit MCP server request (proactive discovery)
        explicit_server = self._extract_explicit_mcp_request(user_message)
        if explicit_server:
            if self.verbose:
                print(f"[DISCOVERY] Detected explicit request for '{explicit_server}' MCP server")

            # Proactively discover and add the server
            success = await self._discover_and_add_server(explicit_server)

            if success:
                # Rebuild agent with new server
                await self._rebuild_agent()
            else:
                if self.verbose:
                    print(f"[DISCOVERY] Could not add '{explicit_server}' server")

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
