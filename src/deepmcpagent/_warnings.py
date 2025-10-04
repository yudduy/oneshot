"""Warnings suppression for third-party deprecation warnings.

This module suppresses known deprecation warnings from dependencies that we cannot
control. These warnings are cosmetic and don't affect functionality.

Environment Variables:
    DEEPMCPAGENT_SHOW_WARNINGS: Set to "1" to disable suppression and show all warnings.

Suppressed Warnings:
    - LangGraphDeprecatedSinceV10: config_schema → context_schema deprecation
    - PydanticDeprecatedSince20: class-based config → ConfigDict deprecation

These come from third-party dependencies (LangGraph, Pydantic, LangChain) and will
be resolved when those libraries update their code.
"""

from __future__ import annotations

import os
import warnings


def suppress_known_warnings() -> None:
    """Suppress known third-party deprecation warnings.

    This function should be called early in the startup process, before importing
    modules that trigger these warnings.

    Set DEEPMCPAGENT_SHOW_WARNINGS=1 to disable suppression for debugging.

    Example:
        >>> from deepmcpagent._warnings import suppress_known_warnings
        >>> suppress_known_warnings()
        >>> # Now imports won't show deprecation warnings
    """
    # Allow users to see warnings if needed for debugging
    if os.getenv("DEEPMCPAGENT_SHOW_WARNINGS", "").strip() == "1":
        return

    # Suppress LangGraph deprecation: config_schema → context_schema
    # Source: langgraph/prebuilt/chat_agent_executor.py
    # This occurs in LangGraph's internal code when creating ReAct agents.
    warnings.filterwarnings(
        "ignore",
        message=r"`config_schema` is deprecated.*",
        category=DeprecationWarning,
    )

    # Suppress Pydantic deprecation: class Config → ConfigDict
    # Source: pydantic/_internal/_config.py
    # This occurs when LangChain/FastMCP models are loaded with old Pydantic patterns.
    warnings.filterwarnings(
        "ignore",
        message=r"Support for class-based `config` is deprecated.*",
        category=DeprecationWarning,
    )
