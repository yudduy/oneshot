"""
Example: Using DynamicOrchestrator for automatic MCP server discovery.

This example demonstrates the dynamic tool discovery capability where the agent
can automatically discover and add MCP servers from the Smithery registry when
it needs capabilities it doesn't have.

Prerequisites:
    1. Set SMITHERY_API_KEY environment variable (or pass directly)
    2. Set OPENAI_API_KEY environment variable (or use another provider)
    3. Optionally run a local MCP server (e.g., examples/servers/math_server.py)

Usage:
    python examples/dynamic_agent.py
"""

import asyncio
import os

from dotenv import load_dotenv
from rich.console import Console
from rich.panel import Panel

from deepmcpagent import DynamicOrchestrator, HTTPServerSpec, ServerSpec

# Load environment variables
load_dotenv()

console = Console()


async def main() -> None:
    """Run the dynamic agent example."""
    # Get API keys from environment
    smithery_key = os.getenv("SMITHERY_API_KEY")
    if not smithery_key:
        console.print("[red]Error: SMITHERY_API_KEY not set in environment[/red]")
        console.print("[dim]Set it in .env file or export SMITHERY_API_KEY=your_key[/dim]")
        return

    # Optional: Start with some initial servers
    # In this example, we start with NO servers to demonstrate pure dynamic discovery
    initial_servers: dict[str, ServerSpec] = {}

    # You could also start with some servers:
    # initial_servers = {
    #     "math": HTTPServerSpec(
    #         url="http://127.0.0.1:8000/mcp",
    #         transport="http"
    #     )
    # }

    # Create orchestrator
    console.print("[bold cyan]Creating DynamicOrchestrator...[/bold cyan]")
    orchestrator = DynamicOrchestrator(
        model="openai:gpt-4",  # Or "anthropic:claude-3-5-sonnet-latest", etc.
        initial_servers=initial_servers,
        smithery_key=smithery_key,
        instructions="You are a helpful assistant. When you need tools, clearly state what you need.",
    )

    console.print("[green]✓ Orchestrator created[/green]")
    console.print(f"[dim]Starting servers: {list(orchestrator.servers.keys()) or 'None'}[/dim]\n")

    # Example 1: Ask about GitHub (should trigger discovery)
    console.print(Panel.fit("Example 1: GitHub Discovery", style="bold magenta"))
    console.print("[cyan]User:[/cyan] Search GitHub for MCP servers")

    try:
        response = await orchestrator.chat("Search GitHub for MCP servers")

        console.print(Panel(response, title="Agent Response", style="green"))
        console.print(
            f"[dim]Active servers after request: {list(orchestrator.servers.keys())}[/dim]\n"
        )

    except Exception as exc:
        console.print(f"[red]Error:[/red] {exc}\n")

    # Example 2: Follow-up that uses the newly discovered tool
    console.print(Panel.fit("Example 2: Using Discovered Tool", style="bold magenta"))
    console.print("[cyan]User:[/cyan] How many stars does the first repository have?")

    try:
        response = await orchestrator.chat("How many stars does the first repository have?")

        console.print(Panel(response, title="Agent Response", style="green"))
        console.print(
            f"[dim]Active servers: {list(orchestrator.servers.keys())}[/dim]\n"
        )

    except Exception as exc:
        console.print(f"[red]Error:[/red] {exc}\n")

    # Example 3: Different capability (weather)
    console.print(Panel.fit("Example 3: Weather Discovery", style="bold magenta"))
    console.print("[cyan]User:[/cyan] What's the weather in San Francisco?")

    try:
        response = await orchestrator.chat("What's the weather in San Francisco?")

        console.print(Panel(response, title="Agent Response", style="green"))
        console.print(
            f"[dim]Active servers: {list(orchestrator.servers.keys())}[/dim]\n"
        )

    except Exception as exc:
        console.print(f"[red]Error:[/red] {exc}\n")

    # Show final state
    console.print(Panel.fit("Final State", style="bold yellow"))
    console.print(f"[green]Total servers discovered: {len(orchestrator.servers)}[/green]")
    console.print(f"[green]Conversation turns: {len(orchestrator.messages)}[/green]")

    for name, spec in orchestrator.servers.items():
        if isinstance(spec, HTTPServerSpec):
            console.print(f"  • {name}: {spec.url}")


if __name__ == "__main__":
    asyncio.run(main())
