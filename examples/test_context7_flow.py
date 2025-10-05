"""Demonstration of automatic Context7 MCP installation.

This example shows the new npm-first installation flow with interactive prompts.

Expected behavior:
1. User requests Context7 capability
2. System searches Smithery registry
3. Finds @upstash/context7-mcp
4. FIRST tries npm installation (stdio transport)
5. Detects missing CONTEXT7_API_KEY
6. Prompts user interactively for API key
7. Creates stdio server spec with npx
8. Adds to orchestrator and rebuilds agent
9. ONLY falls back to OAuth if npm package doesn't exist

Environment setup:
    export SMITHERY_API_KEY="sk_..."
    export OPENAI_API_KEY="sk-..."
    export CONTEXT7_API_KEY="..."  # Optional - will prompt if missing

Example output:
    [SEARCH] Searching Smithery for capability: context7
    [SEARCH] Found 5 server(s) matching 'context7'
    [ATTEMPT] Trying 'context7' (rank 1/5)
    [ATTEMPT] Trying local npm installation first...

    🔑 Configuration required for @upstash/context7-mcp
       Field: apiKey
       Description: Context7 API key for authentication
       Environment variable: CONTEXT7_API_KEY
       (You can set CONTEXT7_API_KEY to avoid this prompt)

    Enter value for apiKey: ***************

    [ATTEMPT] ✓ Successfully installed @upstash/context7-mcp locally
    [BUILD] Rebuilding agent with 1 server(s)...
    [BUILD] Agent ready with 8 tool(s)
"""

from __future__ import annotations

import asyncio
import os

from oneshotmcp.orchestrator import DynamicOrchestrator


async def test_context7_automatic_installation() -> None:
    """Test automatic Context7 installation with npm-first strategy."""
    # Check environment
    required_keys = ["SMITHERY_API_KEY", "OPENAI_API_KEY"]
    missing = [k for k in required_keys if not os.getenv(k)]

    if missing:
        print(f"❌ Missing required environment variables: {', '.join(missing)}")
        print("\nPlease set:")
        for key in missing:
            print(f"  export {key}='...'")
        return

    # Create orchestrator with no initial servers
    orchestrator = DynamicOrchestrator(
        model="openai:gpt-4.1-nano",
        initial_servers={},  # Start empty
        smithery_key=os.getenv("SMITHERY_API_KEY"),
        verbose=True,
    )

    print("=" * 70)
    print("TESTING: Automatic Context7 MCP Installation")
    print("=" * 70)
    print()
    print("Query: 'Search for Anthropic documentation using Context7'")
    print()
    print("Expected flow:")
    print("  1. Detect missing 'context7' capability")
    print("  2. Search Smithery registry")
    print("  3. Try npm installation FIRST")
    print("  4. Prompt for CONTEXT7_API_KEY if missing")
    print("  5. Install via stdio (npx @upstash/context7-mcp)")
    print("  6. Rebuild agent with new tools")
    print()
    print("=" * 70)
    print()

    # This would trigger automatic discovery and installation
    # Commenting out to avoid real API calls, but this is the actual usage:
    #
    # response = await orchestrator.chat(
    #     "Search for Anthropic documentation using Context7"
    # )
    # print(response)

    # For demonstration, let's manually trigger the discovery flow
    print("[DEMO] In production, this would:")
    print("  1. Call orchestrator.chat() with the query")
    print("  2. Agent attempts to use Context7 tools")
    print("  3. Pattern detection finds missing capability")
    print("  4. _try_candidates() runs with npm-first strategy")
    print("  5. LocalMCPInstaller prompts for missing config")
    print("  6. Server added, agent rebuilt, query retried")
    print()

    # Show what the server config would look like
    print("[DEMO] Expected server configuration:")
    print("  {")
    print("    'context7': StdioServerSpec(")
    print("      command='npx',")
    print("      args=['-y', '@upstash/context7-mcp'],")
    print("      env={'CONTEXT7_API_KEY': '<from-user-input>'},")
    print("      keep_alive=True")
    print("    )")
    print("  }")
    print()
    print("✓ Demonstration complete!")
    print()
    print("To test for real:")
    print("  1. Set CONTEXT7_API_KEY environment variable")
    print("  2. Run: oneshot")
    print("  3. Ask: 'Search for Anthropic documentation using Context7'")
    print("  4. Watch automatic installation happen!")


if __name__ == "__main__":
    asyncio.run(test_context7_automatic_installation())
