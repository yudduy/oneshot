"""Public API for deepmcpagent."""

# Suppress third-party deprecation warnings early
from ._warnings import suppress_known_warnings

suppress_known_warnings()

from .agent import build_deep_agent
from .clients import FastMCPMulti
from .config import (
    HTTPServerSpec,
    ServerSpec,
    StdioServerSpec,
    servers_to_mcp_config,
)
from .oauth import validate_oauth_url
from .orchestrator import DynamicOrchestrator
from .registry import OAuthRequired, RegistryError, SmitheryAPIClient
from .tools import MCPToolLoader, ToolInfo

__all__ = [
    "HTTPServerSpec",
    "ServerSpec",
    "StdioServerSpec",
    "servers_to_mcp_config",
    "FastMCPMulti",
    "MCPToolLoader",
    "ToolInfo",
    "build_deep_agent",
    "SmitheryAPIClient",
    "RegistryError",
    "OAuthRequired",
    "DynamicOrchestrator",
    "validate_oauth_url",
]
