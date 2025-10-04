# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

DeepMCPAgent is a LangChain/LangGraph agent library that dynamically discovers and uses tools from MCP (Model Context Protocol) servers over HTTP/SSE. The library is model-agnostic and supports any LangChain chat model.

## Key Architecture

### Core Flow
1. **Server Specs** (`config.py`) → typed specifications for MCP servers (HTTP/SSE or stdio)
2. **FastMCP Client** (`clients.py`) → wraps FastMCP to connect to multiple servers
3. **Tool Discovery** (`tools.py`) → fetches tools from MCP servers and converts JSON-Schema → Pydantic → LangChain BaseTool
4. **Agent Builder** (`agent.py`) → creates either DeepAgents loop (if installed) or LangGraph ReAct fallback
5. **CLI** (`cli.py`) → Typer-based interface for listing tools and running interactive sessions

### Tool Conversion Pipeline
MCP Server → FastMCP Client → `list_tools()` → JSON-Schema (inputSchema) → `_jsonschema_to_pydantic()` → Pydantic BaseModel → `_FastMCPTool` (LangChain BaseTool) → Agent

### Agent Runtime
- **Primary**: DeepAgents loop (if `deepagents` extra installed)
- **Fallback**: LangGraph ReAct agent (always available)
- Model is REQUIRED (no fallback) — accepts string provider-id or LangChain model instance

## Dynamic Tool Discovery (NEW)

DeepMCPAgent now supports **automatic MCP server discovery** via the `DynamicOrchestrator`. When the agent needs a tool it doesn't have, it can:

1. **Detect** the missing capability (pattern matching on LLM responses)
2. **Search** the Smithery registry for matching MCP servers
3. **Add** the server dynamically and rebuild the agent
4. **Retry** the task with the newly discovered tools

### Core Components

- **`DynamicOrchestrator`** (`orchestrator.py`) - Manages conversation state, server lifecycle, and dynamic discovery
- **`SmitheryAPIClient`** (`registry.py`) - Client for searching and fetching MCP servers from Smithery
- **`run-dynamic` CLI command** - Interactive mode with auto-discovery enabled

### How It Works

```
User: "Search GitHub for MCP servers"
  ↓
Agent: "I don't have access to GitHub"  [Pattern detected]
  ↓
Orchestrator: Extracts capability ("github")
  ↓
Smithery Search: Finds @smithery/github server
  ↓
Add Server: Updates servers dict, rebuilds agent
  ↓
Retry: Re-invokes with new GitHub tools
  ↓
Agent: "Here are 10 MCP servers on GitHub..."  [Success!]
```

### Usage Example

**CLI:**
```bash
deepmcpagent run-dynamic \
  --model-id "openai:gpt-4" \
  --smithery-key "sk_..." \
  --http name=math url=http://localhost:8000/mcp  # Optional initial servers

# Then ask: "Search GitHub for MCP servers"
# → Auto-discovers and adds GitHub server!
```

**Python API:**
```python
from deepmcpagent import DynamicOrchestrator, HTTPServerSpec

orchestrator = DynamicOrchestrator(
    model="openai:gpt-4",
    initial_servers={},  # Start with no servers
    smithery_key="sk_...",
)

response = await orchestrator.chat("What's the weather in SF?")
# → Auto-discovers weather server, adds it, retries
```

### Pattern Detection

The orchestrator detects missing tools via regex patterns:
- "I don't have access to"
- "I cannot ... without"
- "I'm unable to"
- "no tools available"

Then extracts capabilities via keywords (github, weather, database, etc.)

### State Management

**Key Design:** Messages live OUTSIDE the agent graph, allowing rebuilds without context loss.

```python
orchestrator.messages  # Persistent conversation history
orchestrator.servers   # Active MCP servers (mutable)
orchestrator.graph     # Agent graph (rebuilt when servers change)
```

### Known Limitations

#### Smithery-Hosted Servers Require OAuth

**Problem:** Servers hosted on `server.smithery.ai` require OAuth 2.1 authentication with PKCE flow, which is not currently supported by the Python MCP SDK.

**Impact:**
- Many servers in the Smithery registry cannot be used via dynamic discovery
- Agent will receive a `RegistryError` explaining the OAuth requirement
- Discovery continues - agent may find alternative self-hosted servers

**Example Error:**
```
⚠️  Cannot add '@ref-tools/ref-tools-mcp' for research:
   Server '@ref-tools/ref-tools-mcp' is hosted on Smithery and requires
   OAuth 2.1 authentication (not currently supported in Python MCP SDK).
💡 Tip: You can manually configure this server if you have credentials,
   or try a different query to find alternative servers.
```

**Workarounds:**
1. **Use pre-configured servers**: Set `TAVILY_API_KEY` for web search (doesn't require OAuth)
2. **Self-host servers**: Deploy MCP servers yourself with simple token auth
3. **Manual configuration**: If you have OAuth tokens, manually configure via `--http` flag
4. **Wait for SDK support**: Track [smithery-ai/cli#336](https://github.com/smithery-ai/cli/issues/336)

**What Works:**
- ✅ Self-hosted MCP servers (via HTTP/SSE)
- ✅ Servers with simple auth (API key in URL/header)
- ✅ Public servers without auth
- ✅ Tavily, Brave Search (pre-configured with env vars)

**What Doesn't Work:**
- ❌ Smithery-hosted servers (`server.smithery.ai/*`)
- ❌ OAuth 2.1 protected servers
- ❌ Servers requiring browser-based login

#### Server Configuration Requirements

Some MCP servers (even self-hosted) require configuration like API keys. The Smithery API returns `configSchema` indicating required fields, but the current implementation:
- Detects the requirement (shows in error message)
- Cannot automatically prompt for user input
- Cannot inject credentials at runtime

**Future Enhancement:** Auto-prompt users for required API keys during discovery.

### Recommended Server Setup

For best results with dynamic discovery:

**Option 1: Pre-configure Essential Servers**
```bash
export TAVILY_API_KEY="tvly-..."  # Web search
export BRAVE_API_KEY="..."        # Alternative web search
export OPENAI_API_KEY="..."       # LLM provider
```

**Option 2: Use Hybrid Mode**
```bash
# Start with Tavily + custom server
deepmcpagent run-dynamic \
  --model-id "openai:gpt-4" \
  --smithery-key "sk_..." \
  --http name=custom url=http://localhost:8000/mcp
```

**Option 3: Self-Host Popular Servers**
Deploy your own instances of popular MCP servers without OAuth dependencies.

### Example Files

- `examples/dynamic_agent.py` - Full example with 3 discovery scenarios
- See docstrings in `orchestrator.py` for detailed API docs

## Development Commands

```bash
# Install with dev dependencies
pip install -e ".[dev]"

# Install with DeepAgents support (recommended)
pip install -e ".[deep,dev]"

# Linting and formatting
ruff check .
ruff format .

# Type checking (strict mypy)
mypy

# Run tests
pytest -q

# Build documentation
mkdocs build

# Serve docs locally
mkdocs serve
```

## Testing

### Running Example Server & Agent
```bash
# Terminal 1: Start sample MCP server
python examples/servers/math_server.py
# Serves at http://127.0.0.1:8000/mcp

# Terminal 2: Run example agent
python examples/use_agent.py
```

### Running CLI
```bash
# List tools from HTTP server
deepmcpagent list-tools \
  --http name=math url=http://127.0.0.1:8000/mcp transport=http \
  --model-id "openai:gpt-4.1"

# Interactive agent session
deepmcpagent run \
  --http name=math url=http://127.0.0.1:8000/mcp transport=http \
  --model-id "openai:gpt-4.1"
```

## Code Standards

### Type Safety
- **mypy strict mode** enforced (no untyped defs, complete defs, etc.)
- Use explicit types; avoid `Any` unless absolutely necessary (document why)
- All public APIs must be fully typed

### Linting
- **ruff** with target Python 3.10+
- Line length: 100 characters
- Selected rules: E, F, I, UP, B, C4, SIM, ARG
- E501 (line length) ignored (ruff formatter handles this)

### Testing
- Use pytest with `asyncio_mode = "auto"`
- Integration tests may be skipped if they require live servers/credentials
- Prefer unit tests for schema conversion, config parsing, etc.

### Documentation
- Use Google-style docstrings
- API docs generated via mkdocstrings
- Keep README examples consistent with `examples/` directory

## Important Implementation Details

### Tool Wrapper (_FastMCPTool)
- Uses `PrivateAttr` for `_client`, `_tool_name`, and callbacks
- Implements async `_arun()` for tool execution
- Wraps all MCP tool calls with error handling → raises `MCPClientError`

### Server Specs
- `HTTPServerSpec`: for HTTP/SSE servers (primary use case for FastMCP)
  - Fields: `url`, `transport` (http|streamable-http|sse), `headers`, `auth`
- `StdioServerSpec`: for local stdio servers (requires adapter or HTTP shim)
  - Fields: `command`, `args`, `env`, `cwd`, `keep_alive`

### CLI Parser
- Uses `shlex.split()` to parse block strings with quoted values
- Supports repeated `--http` and `--stdio` flags
- Headers and env vars use dot notation: `header.Authorization="Bearer X"`, `env.API_KEY=Y`

### Model Initialization
- String provider-id → `init_chat_model()` (e.g., "openai:gpt-4.1")
- Direct LangChain model instance → used as-is
- Reads env vars like `OPENAI_API_KEY`, `ANTHROPIC_API_KEY` automatically

## File Structure

```
src/deepmcpagent/
├── __init__.py          # Public API exports
├── __main__.py          # Entry point for python -m deepmcpagent
├── config.py            # Server specs and config conversion
├── clients.py           # FastMCP multi-server client wrapper
├── tools.py             # MCP tool discovery and LangChain conversion
├── agent.py             # build_deep_agent() - main builder function
├── cli.py               # Typer CLI (list-tools, run commands)
└── prompt.py            # DEFAULT_SYSTEM_PROMPT

tests/
├── test_config.py       # Server spec and config tests
├── test_tools_schema.py # JSON-Schema → Pydantic conversion tests
├── test_cli_parse.py    # CLI argument parsing tests
└── test_agent.py        # Integration tests (may be skipped)

examples/
├── use_agent.py         # Demo with fancy Rich console output
└── servers/
    └── math_server.py   # Sample MCP HTTP server

docs/                    # MkDocs documentation
```

## Key Limitations & Extension Opportunities

### Tool Discovery is Static (Not Dynamic)

**Current behavior:**
- MCP servers must be specified **upfront** in the `servers` dict
- Tools are discovered **once at startup** via `client.list_tools()`
- **NO** runtime server addition or MCP registry lookup
- **NO** adaptive tool discovery based on task analysis

**What this means:**
```python
# You MUST specify all servers before starting
servers = {
    "math": HTTPServerSpec(url="http://localhost:8000/mcp"),
    "github": HTTPServerSpec(url="http://localhost:8001/mcp"),
}

graph, _ = await build_deep_agent(servers=servers, ...)

# Agent can ONLY use tools from these two servers
# If user asks "what's the weather?", agent will fail (no weather server)
```

**Extension opportunities:**
- MCP registry integration (search for servers by capability)
- Runtime tool addition (add servers during conversation)
- Reflection pattern (detect missing tools, search registry, retry)
- See `examples/dynamic_discovery.py` and `examples/runtime_tool_addition.py`

### Tool Selection is LLM Reasoning Only

**How the agent picks tools:**
1. Reads tool descriptions (name + description + JSON schema)
2. Uses LLM reasoning to decide which tool(s) to call
3. NO explicit planning, NO efficiency scoring, NO dependency analysis

**What "efficient" means:**
- The LLM guesses based on descriptions and training patterns
- Example: If tool description says "bulk operation", LLM might prefer it
- If multiple approaches work, LLM picks one (not necessarily optimal)

**Example of inefficiency:**
```python
# Task: "Sum numbers 1 to 100"

# If only 'add(a, b)' exists:
# → LLM might call add() 99 times (very inefficient!)

# If 'sum_range(start, end)' exists with good description:
# → LLM will likely use it (efficient!)

# Efficiency depends on tool availability + descriptions
```

### No Built-in Computer Use / Adaptive Discovery

Unlike Claude's "Computer Use" mode which can discover and use new tools dynamically:
- DeepMCPAgent requires pre-configuration of all MCP servers
- Cannot search for or add tools at runtime
- Cannot adapt to new requirements mid-conversation

**To build this yourself:**
- Implement MCP registry client (search by capability)
- Add reflection loop (detect failures → search registry → add server → retry)
- Extend `MCPToolLoader` to support runtime tool addition
- See conceptual examples in `examples/` directory

## Common Patterns

### Creating an Agent Programmatically
```python
from deepmcpagent import HTTPServerSpec, build_deep_agent

servers = {
    "math": HTTPServerSpec(
        url="http://127.0.0.1:8000/mcp",
        transport="http",
    ),
}

graph, loader = await build_deep_agent(
    servers=servers,
    model="openai:gpt-4.1",  # or model instance
    instructions="Custom system prompt",
    trace_tools=True,
)

result = await graph.ainvoke({
    "messages": [{"role": "user", "content": "add 2 and 3"}]
})
```

### Adding HTTP Headers for Auth
```python
HTTPServerSpec(
    url="https://api.example.com/mcp",
    transport="http",
    headers={"Authorization": "Bearer <token>"},
)
```

### Inspecting Discovered Tools
```python
tools = await loader.get_all_tools()        # List[BaseTool]
tool_info = await loader.list_tool_info()   # List[ToolInfo] (metadata)
```

## Environment Variables

- `.env` file loaded via `python-dotenv`
- Common vars: `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `GROQ_API_KEY`, etc.
- LangChain's `init_chat_model()` reads these automatically

## Debugging

- Use `trace_tools=True` in `build_deep_agent()` to print tool invocations
- CLI flag: `--trace` (enabled by default in `run` command)
- Callback hooks: `on_before`, `on_after`, `on_error` in `MCPToolLoader`

## Dependencies

**Runtime:**
- fastmcp (≥2.12.2) — MCP client
- langchain (≥0.3.27) — LLM framework
- langgraph (≥0.6, <0.7) — agent graph
- pydantic (≥2.8) — schema validation
- typer (≥0.15.2) + rich (≥14) — CLI
- anyio, python-dotenv

**Optional:**
- deepagents (≥0.0.5, <1.0) — recommended agent loop
- langchain-openai — for OpenAI models (examples)

**Dev:**
- pytest, pytest-asyncio
- mypy (strict)
- ruff
- mkdocs, mkdocs-material, mkdocstrings
