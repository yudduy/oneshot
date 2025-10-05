# OneShotMCP CLI Usage Guide

## Quick Start

### 1. Setup Environment
```bash
# Required
export SMITHERY_API_KEY="sk_..."
export OPENAI_API_KEY="sk-..."

# Optional (pre-configure servers to avoid prompts)
export TAVILY_API_KEY="tvly_..."
export CONTEXT7_API_KEY="..."
export VERCEL_TOKEN="..."
```

### 2. Run Interactive CLI
```bash
oneshot

# Or with verbose mode (see LLM reasoning)
oneshot --verbose
```

## Automatic MCP Installation

### Example 1: Install Vercel MCP
```bash
> install vercel mcp

# What happens:
[DISCOVERY] Detected explicit request for 'vercel' MCP server
[SEARCH] Generated 3 search queries: ['vercel', 'vercel mcp', 'vercel server']
[SEARCH] Found 15 result(s) for 'vercel'
[RANKING] Ranked 15 candidates (3 relevant):
[RANKING]   ✓ 100 pts: @vercel/deployment-mcp - Vercel deployment tools...
[RANKING]   ✓  80 pts: @vercel/edge-config-mcp - Edge config management...
[RANKING]   ✓  60 pts: @smithery/vercel - Vercel integration...
[RANKING]   ✗   0 pts: @cloudflare/playwright-mcp - Browser automation...
[ATTEMPT] Trying @vercel/deployment-mcp
[LOCAL] Trying npm installation first...
[LOCAL] ✓ Successfully installed '@vercel/deployment-mcp' locally
[BUILD] Rebuilding agent with 2 server(s)...
[BUILD] Agent ready with 15 tool(s)

✅ Vercel MCP installed!
```

### Example 2: Install with Missing API Key
```bash
> install context7 mcp

[DISCOVERY] Detected explicit request for 'context7' MCP server
[SEARCH] Found 5 result(s)
[RANKING] Ranked 5 candidates (2 relevant):
[RANKING]   ✓ 100 pts: @upstash/context7-mcp - Documentation search...
[ATTEMPT] Trying @upstash/context7-mcp
[LOCAL] Trying npm installation first...

🔑 Configuration required for @upstash/context7-mcp
   Field: apiKey
   Description: Context7 API key for authentication
   Environment variable: CONTEXT7_API_KEY
   (You can set CONTEXT7_API_KEY to avoid this prompt)

Enter value for apiKey: █

[LOCAL] ✓ Successfully installed '@upstash/context7-mcp' locally
[BUILD] Agent ready with 8 tool(s)

✅ Context7 MCP installed!
```

### Example 3: OAuth Flow (Automatic)
```bash
> install private-mcp-server

[DISCOVERY] Detected explicit request for 'private-mcp-server'
[SEARCH] Found 1 result(s)
[RANKING] Ranked 1 candidates (1 relevant):
[RANKING]   ✓ 100 pts: @company/private-mcp - Internal tools...
[ATTEMPT] Trying @company/private-mcp
[LOCAL] Trying npm installation first...
[LOCAL] Package not found in npm registry
[ATTEMPT] Trying hosted server...
[OAUTH] OAuth authentication required

🔐 Opening browser for authorization...

   Please authorize OneShotMCP to access:
   - @company/private-mcp

[Browser opens automatically]

[OAUTH] ✓ Authorization successful
[OAUTH] Tokens saved to ~/.config/oneshotmcp/tokens.json
[BUILD] Agent ready with 12 tool(s)

✅ Private MCP installed!
```

## Natural Language Requests

The agent automatically detects when you need MCP servers:

### Example 1: Implicit Detection
```bash
> Search GitHub for MCP servers

[AGENT] I don't have access to GitHub search functionality.
[DISCOVERY] Detected missing capability: github
[SEARCH] Searching Smithery for 'github'...
[RANKING] Ranked 8 candidates (2 relevant):
[RANKING]   ✓ 100 pts: @modelcontextprotocol/server-github
[ATTEMPT] Installing @modelcontextprotocol/server-github...
[LOCAL] ✓ Successfully installed locally
[BUILD] Agent ready with 25 tool(s)
[RETRY] Retrying original request with new tools...

Here are 10 MCP servers I found on GitHub:
1. @modelcontextprotocol/server-filesystem
2. @modelcontextprotocol/server-github
[...]
```

### Example 2: Multi-Step Discovery
```bash
> Search for Anthropic documentation and summarize the Claude API

[DISCOVERY] Detected missing capability: documentation search
[SEARCH] Searching Smithery for 'documentation'...
[RANKING] Found @upstash/context7-mcp
[ATTEMPT] Installing Context7...

🔑 Enter CONTEXT7_API_KEY: █

[LOCAL] ✓ Installed
[BUILD] Agent ready with 8 tool(s)
[RETRY] Searching documentation...

📚 Summary of Claude API documentation:
- Claude API provides programmatic access to Anthropic's language models
- Supports streaming responses, function calling, and vision capabilities
[...]
```

## Command Reference

### Interactive Commands

```bash
# Explicit installation
> install <mcp-name>
> fetch <mcp-name> and use it
> add <mcp-name> mcp server

# Natural language (automatic detection)
> <any request that requires missing capability>

# List active servers
> what servers are active?
> list mcp servers

# Exit
> exit
> quit
> Ctrl+C
```

### CLI Flags

```bash
# Run with initial servers
oneshot --http "name=math url=http://localhost:8000/mcp"

# Verbose mode (show LLM reasoning)
oneshot --verbose

# Disable interactive prompts (use env vars only)
oneshot --no-interactive  # TODO: Not implemented yet

# Prefer hosted over npm (reverse priority)
oneshot --prefer-hosted   # TODO: Not implemented yet
```

## Troubleshooting

### Issue: "Package not found in npm"
```bash
[LOCAL] Package '@example/mcp' not found in npm registry
[ATTEMPT] Trying hosted server...
```

**Solution:** The package doesn't exist in npm. System automatically tries hosted version with OAuth.

### Issue: "Missing required configuration"
```bash
❌ Cannot install @example/mcp: Missing required configuration: apiKey
```

**Solution:** Set environment variable or provide value interactively:
```bash
export EXAMPLE_API_KEY="..."
oneshot
```

### Issue: "No relevant servers found"
```bash
[RANKING] Ranked 15 candidates (0 relevant)
[DISCOVERY] No relevant MCP servers found for 'xyz'
```

**Solution:** Try different search terms or check Smithery registry manually:
```bash
# Visit https://smithery.ai/search?q=xyz
# Or try broader search:
> install mcp server for xyz
```

### Issue: OAuth flow cancelled
```bash
[OAUTH] Opening browser...
[OAUTH] User cancelled authorization
[DISCOVERY] Failed to add server
```

**Solution:** Complete OAuth in browser or use npm package if available.

## Advanced Usage

### Pre-configure Multiple Servers
```bash
# .env file
SMITHERY_API_KEY=sk_...
OPENAI_API_KEY=sk-...
TAVILY_API_KEY=tvly_...
CONTEXT7_API_KEY=...
GITHUB_TOKEN=ghp_...
```

```bash
# CLI will use all available servers
oneshot

> active servers?
📊 Active servers: 3
  - tavily (web search)
  - context7 (documentation)
  - github (code search)
```

### Custom Model
```bash
export ONESHOT_MODEL="anthropic:claude-sonnet-3.5-v2"
oneshot

# Or inline
ONESHOT_MODEL="openai:gpt-4.1-nano" oneshot
```

### Python API
```python
from oneshotmcp import DynamicOrchestrator

orchestrator = DynamicOrchestrator(
    model="openai:gpt-4.1-nano",
    initial_servers={},
    smithery_key="sk_...",
    verbose=True,
)

# Automatic discovery
response = await orchestrator.chat(
    "Search for Vercel documentation"
)
# → Auto-installs Vercel MCP
# → Returns documentation results
```

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `SMITHERY_API_KEY` | ✅ | Smithery registry access |
| `OPENAI_API_KEY` | ✅ | Default LLM provider |
| `ONESHOT_MODEL` | ❌ | Override default model |
| `TAVILY_API_KEY` | ❌ | Pre-configure web search |
| `CONTEXT7_API_KEY` | ❌ | Pre-configure docs search |
| `GITHUB_TOKEN` | ❌ | Pre-configure GitHub |
| `VERCEL_TOKEN` | ❌ | Pre-configure Vercel |

## Success Indicators

### Installation Success
```
✅ <server-name> MCP installed!
[BUILD] Agent ready with X tool(s)
```

### OAuth Success
```
[OAUTH] ✓ Authorization successful
[OAUTH] Tokens saved to ~/.config/oneshotmcp/tokens.json
```

### Discovery Success
```
[DISCOVERY] ✓ Successfully discovered and added '<capability>'
```

## Tips

1. **Set environment variables** to avoid repeated prompts
2. **Use verbose mode** (`--verbose`) to see LLM reasoning
3. **Check relevance indicators** (✓ = relevant, ✗ = filtered)
4. **Install explicitly** when you know the exact MCP name
5. **Let agent detect** for natural language workflows

---

**Ready to try?**
```bash
export SMITHERY_API_KEY="sk_..."
export OPENAI_API_KEY="sk-..."
oneshot

> install vercel mcp
```

🎉 **Enjoy automatic MCP installation!**
