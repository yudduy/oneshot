"""Tests for package executable validation."""

from __future__ import annotations

from unittest.mock import AsyncMock, Mock, patch

import pytest

from oneshotmcp.local_installer import LocalMCPInstaller


class TestPackageExecutableValidation:
    """Test suite for package executable validation."""

    @pytest.mark.asyncio
    async def test_verify_package_has_bin(self) -> None:
        """Test package with bin field is detected as executable."""
        installer = LocalMCPInstaller()

        with patch("oneshotmcp.local_installer.subprocess.run") as mock_run:
            # Mock bin field exists
            mock_run.return_value = Mock(
                returncode=0,
                stdout='{"my-command": "./bin/cli.js"}',  # Has bin
            )

            has_exec, error = await installer.verify_package_executable("@foo/bar")

            assert has_exec is True
            assert error == ""
            mock_run.assert_called_once()

    @pytest.mark.asyncio
    async def test_verify_package_no_bin_but_has_main(self) -> None:
        """Test package with main but no bin is detected as non-executable."""
        installer = LocalMCPInstaller()

        with patch("oneshotmcp.local_installer.subprocess.run") as mock_run:
            # First call (bin) returns empty, second call (main) returns value
            mock_run.side_effect = [
                Mock(returncode=0, stdout=""),  # No bin
                Mock(returncode=0, stdout="index.js"),  # Has main
            ]

            has_exec, error = await installer.verify_package_executable("@foo/bar")

            assert has_exec is False
            assert "has 'main' (index.js) but no 'bin' entry" in error
            assert mock_run.call_count == 2

    @pytest.mark.asyncio
    async def test_verify_package_no_bin_no_main(self) -> None:
        """Test package with neither bin nor main is detected as non-executable."""
        installer = LocalMCPInstaller()

        with patch("oneshotmcp.local_installer.subprocess.run") as mock_run:
            # Both calls return empty
            mock_run.side_effect = [
                Mock(returncode=0, stdout=""),  # No bin
                Mock(returncode=0, stdout=""),  # No main
            ]

            has_exec, error = await installer.verify_package_executable("@foo/bar")

            assert has_exec is False
            assert "neither 'bin' nor 'main' entry" in error

    @pytest.mark.asyncio
    async def test_verify_package_npm_error(self) -> None:
        """Test handling of npm command errors."""
        installer = LocalMCPInstaller()

        with patch("oneshotmcp.local_installer.subprocess.run") as mock_run:
            # Simulate subprocess error
            mock_run.side_effect = FileNotFoundError("npm not found")

            has_exec, error = await installer.verify_package_executable("@foo/bar")

            assert has_exec is False
            assert "Failed to verify executable" in error

    @pytest.mark.asyncio
    async def test_attempt_local_installation_rejects_non_executable(self) -> None:
        """Test that attempt_local_installation rejects packages without executables."""
        installer = LocalMCPInstaller()

        smithery_metadata = {
            "qualifiedName": "@cloudflare/playwright-mcp",
            "connections": [{"configSchema": {"properties": {}, "required": []}}],
        }

        with patch.object(installer, "is_npm_available", return_value=True):
            with patch.object(installer, "verify_package_exists", return_value=True):
                with patch.object(
                    installer,
                    "verify_package_executable",
                    return_value=(False, "No bin entry"),
                ):
                    # Should return None (reject installation)
                    spec = await installer.attempt_local_installation(
                        smithery_metadata=smithery_metadata,
                        user_config={},
                        interactive=False,
                    )

                    assert spec is None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
