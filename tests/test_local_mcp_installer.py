"""Unit tests for local MCP installation fallback."""

from __future__ import annotations

import subprocess
from typing import Any
from unittest.mock import AsyncMock, Mock, patch

import pytest

from oneshotmcp.config import StdioServerSpec
from oneshotmcp.local_installer import LocalMCPInstaller


class TestLocalMCPInstaller:
    """Test suite for LocalMCPInstaller."""

    def test_extract_npm_package_from_qualified_name(self) -> None:
        """Test extracting npm package name from Smithery qualified name."""
        installer = LocalMCPInstaller()

        # Standard scoped package
        assert installer.extract_npm_package("@upstash/context7-mcp") == "@upstash/context7-mcp"

        # Unscoped package
        assert installer.extract_npm_package("my-mcp-server") == "my-mcp-server"

        # Complex scoped package
        assert installer.extract_npm_package("@smithery/toolbox") == "@smithery/toolbox"

    def test_detect_npm_package_from_smithery_metadata(self) -> None:
        """Test detecting if Smithery server has npm package."""
        installer = LocalMCPInstaller()

        # Has npm package (qualified name looks like npm package)
        metadata = {
            "qualifiedName": "@upstash/context7-mcp",
            "displayName": "Context7",
        }
        assert installer.is_npm_installable(metadata) is True

        # Has npm package (unscoped)
        metadata = {
            "qualifiedName": "filesystem-mcp",
            "displayName": "FileSystem",
        }
        assert installer.is_npm_installable(metadata) is True

        # Not npm package (has spaces or special chars)
        metadata = {
            "qualifiedName": "some server name",
            "displayName": "Some Server",
        }
        assert installer.is_npm_installable(metadata) is False

    def test_extract_config_from_smithery_metadata(self) -> None:
        """Test extracting required config from Smithery metadata."""
        installer = LocalMCPInstaller()

        # Server with API key requirement
        metadata = {
            "qualifiedName": "@upstash/context7-mcp",
            "connections": [
                {
                    "configSchema": {
                        "type": "object",
                        "properties": {
                            "apiKey": {
                                "type": "string",
                                "description": "Context7 API key",
                            }
                        },
                        "required": ["apiKey"],
                    }
                }
            ],
        }

        config = installer.extract_config_requirements(metadata)
        assert "apiKey" in config["required"]
        assert config["properties"]["apiKey"]["type"] == "string"

    def test_build_npx_command_basic(self) -> None:
        """Test building basic npx command without config."""
        installer = LocalMCPInstaller()

        cmd = installer.build_npx_command(
            package_name="@upstash/context7-mcp",
            config_requirements={"required": [], "properties": {}},
            user_config={},
        )

        assert cmd == ["npx", "-y", "@upstash/context7-mcp"]

    def test_build_npx_command_with_api_key(self) -> None:
        """Test building npx command with API key."""
        installer = LocalMCPInstaller()

        cmd = installer.build_npx_command(
            package_name="@upstash/context7-mcp",
            config_requirements={
                "required": ["apiKey"],
                "properties": {
                    "apiKey": {"type": "string", "description": "API key"}
                },
            },
            user_config={"apiKey": "test-key-123"},
        )

        assert cmd == ["npx", "-y", "@upstash/context7-mcp", "--api-key", "test-key-123"]

    def test_build_npx_command_with_env_vars(self) -> None:
        """Test building npx command with environment variables."""
        installer = LocalMCPInstaller()

        with patch("os.getenv", return_value="token-from-env"):
            cmd = installer.build_npx_command(
                package_name="@some/mcp-server",
                config_requirements={
                    "required": ["apiToken"],
                    "properties": {
                        "apiToken": {"type": "string", "envVar": "API_TOKEN"}
                    },
                },
                user_config={},  # Should look for env var
            )

            # Should not include --api-token flag, will use env var
            assert cmd == ["npx", "-y", "@some/mcp-server"]

    def test_build_npx_command_missing_required_config(self) -> None:
        """Test that missing required config raises error."""
        installer = LocalMCPInstaller()

        with pytest.raises(ValueError, match="Missing required configuration: apiKey"):
            installer.build_npx_command(
                package_name="@upstash/context7-mcp",
                config_requirements={
                    "required": ["apiKey"],
                    "properties": {"apiKey": {"type": "string"}},
                },
                user_config={},  # Missing apiKey
            )

    def test_create_stdio_server_spec(self) -> None:
        """Test creating StdioServerSpec for local MCP server."""
        installer = LocalMCPInstaller()

        spec = installer.create_stdio_server_spec(
            package_name="@upstash/context7-mcp",
            config_requirements={"required": [], "properties": {}},
            user_config={},
        )

        assert isinstance(spec, StdioServerSpec)
        assert spec.command == "npx"
        assert spec.args == ["-y", "@upstash/context7-mcp"]
        assert spec.keep_alive is True

    def test_create_stdio_server_spec_with_env(self) -> None:
        """Test creating StdioServerSpec with environment variables."""
        installer = LocalMCPInstaller()

        spec = installer.create_stdio_server_spec(
            package_name="@some/mcp-server",
            config_requirements={
                "required": ["token"],
                "properties": {
                    "token": {"type": "string", "envVar": "MCP_TOKEN"}
                },
            },
            user_config={"token": "secret-123"},
        )

        assert isinstance(spec, StdioServerSpec)
        assert spec.command == "npx"
        assert spec.env == {"MCP_TOKEN": "secret-123"}

    @pytest.mark.asyncio
    async def test_check_npm_available(self) -> None:
        """Test checking if npm/npx is available."""
        installer = LocalMCPInstaller()

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = Mock(returncode=0)
            assert await installer.is_npm_available() is True

        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = FileNotFoundError()
            assert await installer.is_npm_available() is False

    @pytest.mark.asyncio
    async def test_verify_package_exists(self) -> None:
        """Test verifying npm package exists."""
        installer = LocalMCPInstaller()

        with patch("subprocess.run") as mock_run:
            # npm view returns 0 if package exists
            mock_run.return_value = Mock(returncode=0)
            assert await installer.verify_package_exists("@upstash/context7-mcp") is True

        with patch("subprocess.run") as mock_run:
            # npm view returns non-zero if package doesn't exist
            mock_run.return_value = Mock(returncode=1)
            assert await installer.verify_package_exists("@nonexistent/package") is False

    @pytest.mark.asyncio
    async def test_attempt_local_installation_success(self) -> None:
        """Test successful local installation attempt."""
        installer = LocalMCPInstaller()

        smithery_metadata = {
            "qualifiedName": "@upstash/context7-mcp",
            "displayName": "Context7",
            "connections": [
                {
                    "configSchema": {
                        "type": "object",
                        "properties": {},
                        "required": [],
                    }
                }
            ],
        }

        with patch.object(installer, "is_npm_available", return_value=True):
            with patch.object(installer, "verify_package_exists", return_value=True):
                spec = await installer.attempt_local_installation(
                    smithery_metadata=smithery_metadata,
                    user_config={},
                )

                assert spec is not None
                assert isinstance(spec, StdioServerSpec)
                assert spec.command == "npx"
                assert "@upstash/context7-mcp" in spec.args

    @pytest.mark.asyncio
    async def test_attempt_local_installation_npm_unavailable(self) -> None:
        """Test local installation fails when npm unavailable."""
        installer = LocalMCPInstaller()

        smithery_metadata = {
            "qualifiedName": "@upstash/context7-mcp",
            "displayName": "Context7",
        }

        with patch.object(installer, "is_npm_available", return_value=False):
            spec = await installer.attempt_local_installation(
                smithery_metadata=smithery_metadata,
                user_config={},
            )

            assert spec is None

    @pytest.mark.asyncio
    async def test_attempt_local_installation_package_not_found(self) -> None:
        """Test local installation fails when package doesn't exist."""
        installer = LocalMCPInstaller()

        smithery_metadata = {
            "qualifiedName": "@nonexistent/package",
            "displayName": "Non-existent",
        }

        with patch.object(installer, "is_npm_available", return_value=True):
            with patch.object(installer, "verify_package_exists", return_value=False):
                spec = await installer.attempt_local_installation(
                    smithery_metadata=smithery_metadata,
                    user_config={},
                )

                assert spec is None

    @pytest.mark.asyncio
    async def test_attempt_local_installation_with_api_key_from_env(self) -> None:
        """Test local installation retrieves API key from environment."""
        installer = LocalMCPInstaller()

        smithery_metadata = {
            "qualifiedName": "@upstash/context7-mcp",
            "connections": [
                {
                    "configSchema": {
                        "type": "object",
                        "properties": {
                            "apiKey": {
                                "type": "string",
                                "envVar": "CONTEXT7_API_KEY",
                            }
                        },
                        "required": ["apiKey"],
                    }
                }
            ],
        }

        with patch.object(installer, "is_npm_available", return_value=True):
            with patch.object(installer, "verify_package_exists", return_value=True):
                with patch("os.getenv", return_value="env-key-123"):
                    spec = await installer.attempt_local_installation(
                        smithery_metadata=smithery_metadata,
                        user_config={},
                    )

                    assert spec is not None
                    assert isinstance(spec, StdioServerSpec)
                    # Should have env var set
                    assert spec.env.get("CONTEXT7_API_KEY") == "env-key-123"
