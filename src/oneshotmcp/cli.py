"""
OneShotMCP CLI: One prompt. Zero setup. Infinite MCP tools.

The CLI automatically discovers and configures MCP servers on-demand using
the Smithery registry. Model configuration is via environment variable.

Environment Variables:
    ONESHOT_MODEL: Model to use (default: "openai:gpt-4.1-nano")
    SMITHERY_API_KEY: Required - API key for Smithery registry
    TAVILY_API_KEY: Optional - Auto-configures Tavily web search if set

Usage:
    oneshot  # Start interactive agent with dynamic discovery
    oneshot --http "name=math url=http://localhost:8000/mcp"  # Add custom server
"""

from __future__ import annotations

import asyncio
import os
import shlex
from importlib.metadata import version as get_version
from typing import Annotated, Literal, cast

import typer
from dotenv import load_dotenv
from rich.console import Console

# Suppress known third-party deprecation warnings BEFORE importing agent modules
from ._warnings import suppress_known_warnings

suppress_known_warnings()

from .config import HTTPServerSpec, ServerSpec, StdioServerSpec
from .orchestrator import DynamicOrchestrator

load_dotenv()

app = typer.Typer(add_completion=False)
console = Console()


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


@app.command()
def main(
    version: Annotated[
        bool,
        typer.Option("--version", help="Show version and exit"),
    ] = False,
    smithery_key: Annotated[
        str | None,
        typer.Option(
            "--smithery-key",
            envvar="SMITHERY_API_KEY",
            help="Smithery API key for dynamic tool discovery (or set SMITHERY_API_KEY env var)",
        ),
    ] = None,
    http: Annotated[
        list[str] | None,
        typer.Option(
            "--http",
            help=(
                'Add custom server: "name=... url=... [transport=http|streamable-http|sse] '
                '[header.X=Y] [auth=...]". Repeatable.'
            ),
        ),
    ] = None,
    stdio: Annotated[
        list[str] | None,
        typer.Option(
            "--stdio",
            help=(
                "Add stdio server: \"name=... command=... args='...' "
                '[env.X=Y] [cwd=...] [keep_alive=true|false]". Repeatable.'
            ),
        ),
    ] = None,
    instructions: Annotated[
        str,
        typer.Option("--instructions", help="Custom system prompt"),
    ] = "",
    verbose: Annotated[
        bool,
        typer.Option("--verbose", "-v", help="Show LLM reasoning and tool calls"),
    ] = True,
) -> None:
    """OneShotMCP: Dynamic MCP agent with automatic tool discovery.

    Automatically discovers and configures MCP servers on-demand using the
    Smithery registry. When the agent needs a capability, it:

    1. Detects the missing tool
    2. Searches Smithery for matching servers
    3. Adds the server dynamically
    4. Retries with the new capability

    Example:
        $ export SMITHERY_API_KEY="sk_..."
        $ export TAVILY_API_KEY="tvly_..."
        $ oneshot

        > Search GitHub for MCP servers
        → Auto-discovers GitHub server and searches!
    """
    if version:
        console.print(get_version("oneshotmcp"))
        raise typer.Exit()

    if not smithery_key:
        console.print("[red]Error:[/red] SMITHERY_API_KEY is required")
        console.print("\nSet it via:")
        console.print("  export SMITHERY_API_KEY='your_key_here'")
        console.print("  or use --smithery-key flag")
        raise typer.Exit(code=1)

    # Get model from env var
    model = os.getenv("ONESHOT_MODEL", "openai:gpt-4.1-nano")

    # Get default pre-configured servers (e.g., Tavily from env)
    default_servers = _get_default_servers()

    # Parse user-provided servers (optional)
    user_servers = _merge_servers(stdio or [], http or [])

    # Merge: user servers override defaults
    servers = {**default_servers, **user_servers}

    async def _chat() -> None:
        # Create dynamic orchestrator
        orchestrator = DynamicOrchestrator(
            model=model,
            initial_servers=servers,
            smithery_key=smithery_key,
            instructions=instructions or None,
            verbose=verbose,
        )

        console.print(f"[bold cyan]OneShotMCP ready![/bold cyan] (model: {model})")
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
                if verbose:
                    console.print(f"[dim]→ Thinking...[/dim]")

                # Use orchestrator's chat method
                response = await orchestrator.chat(user)

                # Display response
                console.print(f"\n[bold green]Assistant:[/bold green] {response}\n")

                if verbose:
                    # Show active servers
                    num_servers = len(orchestrator.servers)
                    server_names = list(orchestrator.servers.keys())
                    console.print(
                        f"[dim]📊 Active servers: {num_servers} {server_names}[/dim]\n"
                    )

            except Exception as exc:
                console.print(f"[red]Error:[/red] {exc}\n")
                if verbose:
                    import traceback
                    console.print(f"[dim]{traceback.format_exc()}[/dim]")
                continue

    asyncio.run(_chat())


if __name__ == "__main__":
    app()
