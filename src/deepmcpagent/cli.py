"""
CLI for deepmcpagent: list tools and run an interactive agent session.

Notes:
    - The CLI path uses provider id strings for models (e.g., "openai:gpt-4.1"),
      which `init_chat_model` handles. In code, you can pass a model instance.
    - Model is REQUIRED (no fallback).
    - Usage for repeated server specs:
        --stdio "name=echo command=python args='-m mypkg.server --port 3333' env.API_KEY=xyz keep_alive=false"
        --stdio "name=tool2 command=/usr/local/bin/tool2"
        --http  "name=remote url=http://127.0.0.1:8000/mcp transport=http"

      (Repeat --stdio/--http for multiple servers.)
"""

from __future__ import annotations

import asyncio
import json
import shlex
from importlib.metadata import version as get_version
from typing import Annotated, Any, Literal, cast

import typer
from dotenv import load_dotenv
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from .agent import build_deep_agent
from .config import HTTPServerSpec, ServerSpec, StdioServerSpec
from .orchestrator import DynamicOrchestrator

load_dotenv()

app = typer.Typer(no_args_is_help=True, add_completion=False)
console = Console()


@app.callback(invoke_without_command=True)
def _version_callback(
    version: Annotated[
        bool | None,
        typer.Option("--version", help="Show version and exit", is_eager=True),
    ] = None,
) -> None:
    """Global callback to support --version printing."""
    if version:
        console.print(get_version("deepmcpagent"))
        raise typer.Exit()


def _parse_kv(opts: list[str]) -> dict[str, str]:
    """Parse ['k=v', 'x=y', ...] into a dict. Values may contain spaces."""
    out: dict[str, str] = {}
    for it in opts:
        if "=" not in it:
            raise typer.BadParameter(f"Expected key=value, got: {it}")
        k, v = it.split("=", 1)
        out[k.strip()] = v.strip()
    return out


def _get_default_servers() -> dict[str, ServerSpec]:
    """Get default pre-configured servers from environment variables.

    This function checks for environment variables to auto-configure
    common MCP servers that don't require OAuth authentication.

    Returns:
        Dictionary of pre-configured server specifications.

    Environment Variables:
        TAVILY_API_KEY: If set, configures Tavily web search server.

    Example:
        >>> import os
        >>> os.environ["TAVILY_API_KEY"] = "tvly-..."
        >>> servers = _get_default_servers()
        >>> "tavily" in servers
        True
    """
    import os

    servers: dict[str, ServerSpec] = {}

    # Tavily web search (if API key is available)
    tavily_key = os.getenv("TAVILY_API_KEY")
    if tavily_key:
        servers["tavily"] = HTTPServerSpec(
            url=f"https://mcp.tavily.com/mcp/?tavilyApiKey={tavily_key}",
            transport="http",
        )
        console.print("[dim]✓ Auto-configured Tavily web search from TAVILY_API_KEY[/dim]")

    return servers


def _merge_servers(stdios: list[str], https: list[str]) -> dict[str, ServerSpec]:
    """
    Convert flat lists of block strings into server specs.

    Each entry in `stdios` / `https` is a single quoted string like:
      "name=echo command=python args='-m mymod --port 3333' env.API_KEY=xyz cwd=/tmp keep_alive=false"
      "name=remote url=http://127.0.0.1:8000/mcp transport=http"

    We first shlex-split the string into key=value tokens, then parse.
    """
    servers: dict[str, ServerSpec] = {}

    # stdio (kept for completeness)
    for block_str in stdios:
        tokens = shlex.split(block_str)
        kv = _parse_kv(tokens)

        name = kv.pop("name", None)
        if not name:
            raise typer.BadParameter("Missing required key: name (in --stdio block)")

        command = kv.pop("command", None)
        if not command:
            raise typer.BadParameter("Missing required key: command (in --stdio block)")

        args_value = kv.pop("args", "")
        args_list = shlex.split(args_value) if args_value else []

        env = {k.split(".", 1)[1]: v for k, v in list(kv.items()) if k.startswith("env.")}
        cwd = kv.get("cwd")
        keep_alive = kv.get("keep_alive", "true").lower() != "false"

        stdio_spec: ServerSpec = StdioServerSpec(
            command=command,
            args=args_list,
            env=env,
            cwd=cwd,
            keep_alive=keep_alive,
        )
        servers[name] = stdio_spec

    # http
    for block_str in https:
        tokens = shlex.split(block_str)
        kv = _parse_kv(tokens)

        name = kv.pop("name", None)
        if not name:
            raise typer.BadParameter("Missing required key: name (in --http block)")

        url = kv.pop("url", None)
        if not url:
            raise typer.BadParameter("Missing required key: url (in --http block)")

        transport_str = kv.pop("transport", "http")  # "http", "streamable-http", or "sse"
        transport = cast(Literal["http", "streamable-http", "sse"], transport_str)

        headers = {k.split(".", 1)[1]: v for k, v in list(kv.items()) if k.startswith("header.")}
        auth = kv.get("auth")

        http_spec: ServerSpec = HTTPServerSpec(
            url=url,
            transport=transport,
            headers=headers,
            auth=auth,
        )
        servers[name] = http_spec

    return servers


def _extract_final_answer(result: Any) -> str:
    """Best-effort extraction of the final text from various executors."""
    try:
        # LangGraph prebuilt returns {"messages": [ ... ]}
        if isinstance(result, dict) and "messages" in result and result["messages"]:
            last = result["messages"][-1]
            content = getattr(last, "content", None)
            if isinstance(content, str) and content:
                return content
            if isinstance(content, list) and content and isinstance(content[0], dict):
                return content[0].get("text") or str(content)
            return str(last)
        return str(result)
    except Exception:
        return str(result)


@app.command(name="list-tools")
def list_tools(
    model_id: Annotated[
        str,
        typer.Option("--model-id", help="REQUIRED model provider id (e.g., 'openai:gpt-4.1')."),
    ],
    stdio: Annotated[
        list[str] | None,
        typer.Option(
            "--stdio",
            help=(
                "Block string: \"name=... command=... args='...' "
                '[env.X=Y] [cwd=...] [keep_alive=true|false]". Repeatable.'
            ),
        ),
    ] = None,
    http: Annotated[
        list[str] | None,
        typer.Option(
            "--http",
            help=(
                'Block string: "name=... url=... [transport=http|streamable-http|sse] '
                '[header.X=Y] [auth=...]". Repeatable.'
            ),
        ),
    ] = None,
    instructions: Annotated[
        str,
        typer.Option("--instructions", help="Optional system prompt override."),
    ] = "",
) -> None:
    """List all MCP tools discovered using the provided server specs."""
    servers = _merge_servers(stdio or [], http or [])

    async def _run() -> None:
        _, loader = await build_deep_agent(
            servers=servers,
            model=model_id,
            instructions=instructions or None,
        )
        infos = await loader.list_tool_info()
        infos = list(infos or [])

        table = Table(title="MCP Tools", show_lines=True)
        table.add_column("Tool", style="cyan", no_wrap=True)
        table.add_column("Description", style="green")
        table.add_column("Input Schema", style="white")
        for i in infos:
            schema_str = json.dumps(i.input_schema, ensure_ascii=False)
            if len(schema_str) > 120:
                schema_str = schema_str[:117] + "..."
            table.add_row(i.name, i.description or "-", schema_str)
        console.print(table)

    asyncio.run(_run())


@app.command()
def run(
    model_id: Annotated[
        str,
        typer.Option(..., help="REQUIRED model provider id (e.g., 'openai:gpt-4.1')."),
    ],
    stdio: Annotated[
        list[str] | None,
        typer.Option(
            "--stdio",
            help=(
                "Block string: \"name=... command=... args='...' "
                '[env.X=Y] [cwd=...] [keep_alive=true|false]". Repeatable.'
            ),
        ),
    ] = None,
    http: Annotated[
        list[str] | None,
        typer.Option(
            "--http",
            help=(
                'Block string: "name=... url=... [transport=http|streamable-http|sse] '
                '[header.X=Y] [auth=...]". Repeatable.'
            ),
        ),
    ] = None,
    instructions: Annotated[
        str,
        typer.Option("--instructions", help="Optional system prompt override."),
    ] = "",
    # IMPORTANT: don't duplicate defaults in Option() and the parameter!
    trace: Annotated[
        bool,
        typer.Option("--trace/--no-trace", help="Print tool invocations & results."),
    ] = True,
    raw: Annotated[
        bool,
        typer.Option("--raw/--no-raw", help="Also print raw result object."),
    ] = False,
) -> None:
    """Start an interactive agent that uses only MCP tools."""
    servers = _merge_servers(stdio or [], http or [])

    async def _chat() -> None:
        graph, _ = await build_deep_agent(
            servers=servers,
            model=model_id,
            instructions=instructions or None,
            trace_tools=trace,  # <- enable deepmcpagent tool tracing
        )
        console.print("[bold]DeepMCPAgent is ready. Type 'exit' to quit.[/bold]")
        while True:
            try:
                user = input("> ").strip()
            except (EOFError, KeyboardInterrupt):
                console.print("\nExiting.")
                break
            if user.lower() in {"exit", "quit"}:
                break
            if not user:
                continue
            try:
                result = await graph.ainvoke({"messages": [{"role": "user", "content": user}]})
            except Exception as exc:
                console.print(f"[red]Error during run:[/red] {exc}")
                continue

            final_text = _extract_final_answer(result)
            console.print(
                Panel(final_text or "(no content)", title="Final LLM Answer", style="bold green")
            )
            if raw:
                console.print(result)

    asyncio.run(_chat())


@app.command(name="run-dynamic")
def run_dynamic(
    model_id: Annotated[
        str,
        typer.Option(..., help="REQUIRED model provider id (e.g., 'openai:gpt-4')."),
    ],
    smithery_key: Annotated[
        str,
        typer.Option(
            ...,
            "--smithery-key",
            envvar="SMITHERY_API_KEY",
            help="Smithery API key for dynamic tool discovery.",
        ),
    ],
    http: Annotated[
        list[str] | None,
        typer.Option(
            "--http",
            help=(
                'Block string: "name=... url=... [transport=http|streamable-http|sse] '
                '[header.X=Y] [auth=...]". Repeatable. Optional - tools discovered dynamically.'
            ),
        ),
    ] = None,
    instructions: Annotated[
        str,
        typer.Option("--instructions", help="Optional system prompt override."),
    ] = "",
) -> None:
    """Start an interactive agent with DYNAMIC tool discovery via Smithery registry.

    This command enables automatic MCP server discovery. When the agent needs a tool
    that isn't available, it will:
    1. Detect the missing capability
    2. Search the Smithery registry
    3. Add the server dynamically
    4. Retry the task with the new tool

    Example:
        deepmcpagent run-dynamic \\
          --model-id "openai:gpt-4" \\
          --smithery-key "sk_..." \\
          --http name=math url=http://localhost:8000/mcp

    Then ask: "Search GitHub for MCP servers"
    → Agent will auto-discover and add the GitHub MCP server!
    """
    # Get default pre-configured servers (e.g., Tavily from env)
    default_servers = _get_default_servers()

    # Parse user-provided servers (optional)
    user_servers = _merge_servers([], http or [])

    # Merge: user servers override defaults
    servers = {**default_servers, **user_servers}

    async def _chat() -> None:
        # Create dynamic orchestrator
        orchestrator = DynamicOrchestrator(
            model=model_id,
            initial_servers=servers,
            smithery_key=smithery_key,
            instructions=instructions or None,
        )

        console.print("[bold cyan]DynamicOrchestrator is ready![/bold cyan]")
        console.print("[dim]Dynamic tool discovery enabled via Smithery registry.[/dim]")
        console.print("[dim]Type 'exit' to quit.[/dim]\n")

        while True:
            try:
                user = input("> ").strip()
            except (EOFError, KeyboardInterrupt):
                console.print("\nExiting.")
                break

            if user.lower() in {"exit", "quit"}:
                break

            if not user:
                continue

            try:
                # Use orchestrator's chat method
                response = await orchestrator.chat(user)

                # Display response
                console.print(
                    Panel(response or "(no content)", title="Agent Response", style="bold green")
                )

                # Show active servers count
                num_servers = len(orchestrator.servers)
                console.print(
                    f"[dim]Active servers: {num_servers} | "
                    f"Servers: {list(orchestrator.servers.keys())}[/dim]\n"
                )

            except Exception as exc:
                console.print(f"[red]Error:[/red] {exc}\n")
                continue

    asyncio.run(_chat())
