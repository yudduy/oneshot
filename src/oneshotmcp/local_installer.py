"""Local MCP server installation and management.

Provides automatic fallback to local npm installation when Smithery-hosted
servers are unavailable or require OAuth authentication.
"""

from __future__ import annotations

import os
import re
import subprocess
from typing import Any

from .config import StdioServerSpec


class LocalMCPInstaller:
    """Handles local installation of MCP servers from npm packages."""

    def extract_npm_package(self, qualified_name: str) -> str:
        """Extract npm package name from Smithery qualified name.

        Args:
            qualified_name: Smithery qualified name (e.g., "@upstash/context7-mcp")

        Returns:
            npm package name (same as qualified name for npm packages)
        """
        return qualified_name

    def is_npm_installable(self, metadata: dict[str, Any]) -> bool:
        """Check if Smithery server can be installed as npm package.

        Args:
            metadata: Smithery server metadata

        Returns:
            True if qualifiedName looks like valid npm package name
        """
        qualified_name = metadata.get("qualifiedName", "")

        # Valid npm package names:
        # - Can be scoped (@scope/package) or unscoped (package)
        # - Only alphanumeric, hyphens, underscores, dots
        # - No spaces or special chars
        npm_pattern = r"^(@[a-z0-9-_.]+\/)?[a-z0-9-_.]+$"
        return bool(re.match(npm_pattern, qualified_name, re.IGNORECASE))

    def extract_config_requirements(self, metadata: dict[str, Any]) -> dict[str, Any]:
        """Extract configuration requirements from Smithery metadata.

        Args:
            metadata: Smithery server metadata with connections array

        Returns:
            Config schema with required fields and properties
        """
        connections = metadata.get("connections", [])
        if not connections:
            return {"required": [], "properties": {}}

        config_schema = connections[0].get("configSchema", {})
        return {
            "required": config_schema.get("required", []),
            "properties": config_schema.get("properties", {}),
        }

    def build_npx_command(
        self,
        package_name: str,
        config_requirements: dict[str, Any],
        user_config: dict[str, Any],
    ) -> list[str]:
        """Build npx command to run MCP server.

        Args:
            package_name: npm package name
            config_requirements: Required config from Smithery metadata
            user_config: User-provided configuration

        Returns:
            Command array for subprocess

        Raises:
            ValueError: If required config is missing
        """
        cmd = ["npx", "-y", package_name]

        required = config_requirements.get("required", [])
        properties = config_requirements.get("properties", {})

        for field in required:
            if field not in user_config:
                # Check if env var is specified
                field_props = properties.get(field, {})
                env_var = field_props.get("envVar")

                if env_var and os.getenv(env_var):
                    # Will be passed via env, not CLI arg
                    continue

                raise ValueError(f"Missing required configuration: {field}")

        # Add config as CLI arguments
        for field, value in user_config.items():
            if field in properties:
                # Convert camelCase to --kebab-case
                cli_flag = "--" + re.sub(r"([A-Z])", r"-\1", field).lower()
                cmd.extend([cli_flag, str(value)])

        return cmd

    def create_stdio_server_spec(
        self,
        package_name: str,
        config_requirements: dict[str, Any],
        user_config: dict[str, Any],
    ) -> StdioServerSpec:
        """Create StdioServerSpec for local MCP server.

        Args:
            package_name: npm package name
            config_requirements: Required config from Smithery metadata
            user_config: User-provided configuration

        Returns:
            StdioServerSpec ready to use
        """
        cmd = self.build_npx_command(package_name, config_requirements, user_config)

        # Extract env vars from config
        env_vars: dict[str, str] = {}
        properties = config_requirements.get("properties", {})

        for field, value in user_config.items():
            field_props = properties.get(field, {})
            env_var = field_props.get("envVar")

            if env_var:
                env_vars[env_var] = str(value)

        return StdioServerSpec(
            command=cmd[0],  # "npx"
            args=cmd[1:],  # ["-y", "@package/name", ...]
            env=env_vars if env_vars else {},
            keep_alive=True,
        )

    async def is_npm_available(self) -> bool:
        """Check if npm/npx is available on system.

        Returns:
            True if npx is available
        """
        try:
            result = subprocess.run(
                ["npx", "--version"],
                capture_output=True,
                timeout=5,
            )
            return result.returncode == 0
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return False

    async def verify_package_exists(self, package_name: str) -> bool:
        """Verify npm package exists in registry.

        Args:
            package_name: npm package name to verify

        Returns:
            True if package exists
        """
        try:
            result = subprocess.run(
                ["npm", "view", package_name, "name"],
                capture_output=True,
                timeout=10,
            )
            return result.returncode == 0
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return False

    async def attempt_local_installation(
        self,
        smithery_metadata: dict[str, Any],
        user_config: dict[str, Any],
        interactive: bool = True,
    ) -> StdioServerSpec | None:
        """Attempt to install MCP server locally.

        Args:
            smithery_metadata: Full Smithery server metadata
            user_config: User-provided configuration
            interactive: Whether to prompt user for missing config (default: True)

        Returns:
            StdioServerSpec if installation possible, None otherwise
        """
        # Check if npm installable
        if not self.is_npm_installable(smithery_metadata):
            return None

        # Check if npm available
        if not await self.is_npm_available():
            return None

        # Extract package name
        package_name = self.extract_npm_package(smithery_metadata["qualifiedName"])

        # Verify package exists
        if not await self.verify_package_exists(package_name):
            return None

        # Extract config requirements
        config_requirements = self.extract_config_requirements(smithery_metadata)
        required_fields = config_requirements.get("required", [])
        properties = config_requirements.get("properties", {})

        # Auto-populate config from environment variables
        enriched_config = dict(user_config)

        for field, field_props in properties.items():
            if field not in enriched_config:
                env_var = field_props.get("envVar")
                if env_var:
                    env_value = os.getenv(env_var)
                    if env_value:
                        enriched_config[field] = env_value

        # Interactive prompts for missing required config
        if interactive:
            for field in required_fields:
                if field not in enriched_config:
                    # Prompt user
                    field_props = properties.get(field, {})
                    description = field_props.get("description", field)
                    env_var = field_props.get("envVar", "")

                    print(f"\n🔑 Configuration required for {package_name}")
                    print(f"   Field: {field}")
                    print(f"   Description: {description}")
                    if env_var:
                        print(f"   Environment variable: {env_var}")
                        print(f"   (You can set {env_var} to avoid this prompt)")

                    # Get user input
                    try:
                        value = input(f"\nEnter value for {field}: ").strip()
                        if value:
                            enriched_config[field] = value
                    except (EOFError, KeyboardInterrupt):
                        print("\n\nInstallation cancelled by user")
                        return None

        # Create stdio spec
        try:
            return self.create_stdio_server_spec(
                package_name=package_name,
                config_requirements=config_requirements,
                user_config=enriched_config,
            )
        except ValueError as exc:
            # Missing required config even after prompts
            print(f"\n❌ Cannot install {package_name}: {exc}")
            return None
