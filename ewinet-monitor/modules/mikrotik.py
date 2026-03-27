"""MikroTik RouterOS API and SSH integration."""

from __future__ import annotations

import logging
import re
import subprocess
from typing import Any, Optional

from config.settings import MikroTikConfig, Settings
from models.data_models import MikroTikInterface

logger = logging.getLogger(__name__)

# Try to import routeros_api (optional dependency)
try:
    import routeros_api
    HAS_ROUTEROS_API = True
except ImportError:
    HAS_ROUTEROS_API = False
    logger.debug("routeros_api not installed, MikroTik API mode unavailable")


class MikroTikClient:
    """Client for MikroTik RouterOS via API or SSH."""

    def __init__(self, config: MikroTikConfig):
        self.config = config
        self._api_connection: Any = None

    def connect_api(self) -> bool:
        """Connect via RouterOS API."""
        if not HAS_ROUTEROS_API:
            logger.warning("routeros_api module not available")
            return False

        try:
            connection = routeros_api.RouterOsApiPool(
                self.config.host,
                username=self.config.username,
                password=self.config.password,
                port=self.config.port,
                plaintext_login=True,
            )
            self._api_connection = connection.get_api()
            logger.info("Connected to MikroTik %s via API", self.config.host)
            return True
        except Exception as e:
            logger.error("MikroTik API connection failed: %s", e)
            return False

    def disconnect_api(self) -> None:
        """Disconnect API session."""
        if self._api_connection:
            try:
                self._api_connection.disconnect()
            except Exception:
                pass
            self._api_connection = None

    def _ssh_command(self, command: str) -> str:
        """Execute a command via SSH on the MikroTik router."""
        cmd = [
            "ssh",
            "-o", "StrictHostKeyChecking=no",
            "-o", "ConnectTimeout=10",
            "-p", str(self.config.ssh_port),
            f"{self.config.username}@{self.config.host}",
            command,
        ]

        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=30,
                input=f"{self.config.password}\n" if not self._has_ssh_key() else None,
            )
            if result.returncode != 0:
                logger.error("SSH command failed: %s", result.stderr)
                return ""
            return result.stdout
        except subprocess.TimeoutExpired:
            logger.error("SSH command timed out: %s", command)
            return ""
        except FileNotFoundError:
            logger.error("ssh command not found")
            return ""

    def _has_ssh_key(self) -> bool:
        """Check if SSH key auth is configured."""
        import os
        key_path = os.path.expanduser("~/.ssh/id_rsa")
        return os.path.exists(key_path)

    def get_interfaces(self) -> list[MikroTikInterface]:
        """Get all interfaces from MikroTik."""
        interfaces: list[MikroTikInterface] = []

        # Try API first
        if self._api_connection or self.connect_api():
            try:
                resource = self._api_connection.get_resource("/interface")
                data = resource.get()
                for iface in data:
                    interfaces.append(MikroTikInterface(
                        name=iface.get("name", ""),
                        type=iface.get("type", ""),
                        comment=iface.get("comment", ""),
                        running=iface.get("running", "false") == "true",
                        mac_address=iface.get("mac-address", ""),
                    ))
                return interfaces
            except Exception as e:
                logger.error("Failed to get interfaces via API: %s", e)

        # Fallback to SSH
        output = self._ssh_command("/interface print detail without-paging")
        if output:
            interfaces = self._parse_interface_print(output)

        return interfaces

    def _parse_interface_print(self, output: str) -> list[MikroTikInterface]:
        """Parse '/interface print detail' output."""
        interfaces: list[MikroTikInterface] = []
        current: dict[str, str] = {}

        for line in output.split("\n"):
            stripped = line.strip()
            if not stripped:
                if current:
                    interfaces.append(MikroTikInterface(
                        name=current.get("name", ""),
                        type=current.get("type", ""),
                        comment=current.get("comment", ""),
                        running=current.get("running", "false") == "true",
                        mac_address=current.get("mac-address", ""),
                    ))
                    current = {}
                continue

            # Parse key=value pairs
            match = re.match(r"(\S+)=(?:\"([^\"]*)\"|(\S+))", stripped)
            if match:
                key = match.group(1)
                value = match.group(2) if match.group(2) is not None else match.group(3)
                current[key] = value

        # Don't forget last entry
        if current:
            interfaces.append(MikroTikInterface(
                name=current.get("name", ""),
                type=current.get("type", ""),
                comment=current.get("comment", ""),
                running=current.get("running", "false") == "true",
                mac_address=current.get("mac-address", ""),
            ))

        return interfaces

    def get_routing_table(self, dst_address: str = "") -> list[dict[str, str]]:
        """Get routing table entries, optionally filtered by destination."""
        routes: list[dict[str, str]] = []

        filter_cmd = ""
        if dst_address:
            filter_cmd = f" where dst-address={dst_address}"

        # Try API
        if self._api_connection or self.connect_api():
            try:
                resource = self._api_connection.get_resource("/ip/route")
                params: dict[str, str] = {}
                if dst_address:
                    params["dst-address"] = dst_address
                data = resource.get(**params)
                for route in data:
                    routes.append(dict(route))
                return routes
            except Exception as e:
                logger.error("Failed to get routes via API: %s", e)

        # Fallback to SSH
        output = self._ssh_command(f"/ip route print detail{filter_cmd}")
        if output:
            for line in output.split("\n"):
                route: dict[str, str] = {}
                for part in re.findall(r'(\S+)=(?:"([^"]*)"|(\S+))', line):
                    key = part[0]
                    value = part[1] if part[1] else part[2]
                    route[key] = value
                if route:
                    routes.append(route)

        return routes

    def get_route_for_destination(self, destination: str) -> dict[str, str] | None:
        """Get the active route for a specific destination IP."""
        routes = self.get_routing_table(dst_address=f"{destination}/32")
        if not routes:
            # Try without /32
            routes = self.get_routing_table(dst_address=destination)

        # Filter for active routes
        for route in routes:
            if route.get("active", "true") == "true":
                return route

        return routes[0] if routes else None

    def get_interface_for_gateway(self, gateway_ip: str) -> MikroTikInterface | None:
        """Find which interface is used for a given gateway IP."""
        route = self.get_route_for_destination(gateway_ip)
        if not route:
            return None

        iface_name = route.get("gateway-interface", "")
        if not iface_name:
            return None

        interfaces = self.get_interfaces()
        for iface in interfaces:
            if iface.name == iface_name:
                return iface

        return MikroTikInterface(name=iface_name, type="unknown")

    def get_bgp_sessions(self) -> list[dict[str, str]]:
        """Get active BGP session information."""
        sessions: list[dict[str, str]] = []

        # Try API
        if self._api_connection or self.connect_api():
            try:
                resource = self._api_connection.get_resource("/routing/bgp/session")
                data = resource.get()
                for session in data:
                    sessions.append(dict(session))
                return sessions
            except Exception as e:
                logger.error("Failed to get BGP sessions via API: %s", e)

        # Fallback to SSH
        output = self._ssh_command("/routing bgp session print detail without-paging")
        if output:
            for line in output.split("\n"):
                session: dict[str, str] = {}
                for part in re.findall(r'(\S+)=(?:"([^"]*)"|(\S+))', line):
                    key = part[0]
                    value = part[1] if part[1] else part[2]
                    session[key] = value
                if session:
                    sessions.append(session)

        return sessions

    def enrich_snapshot(self, snapshot: Any) -> None:
        """Enrich a route snapshot with MikroTik interface data."""
        if not self.config.host:
            return

        try:
            iface = self.get_interface_for_gateway(snapshot.gateway)
            if iface:
                snapshot.physical_interface = iface.name
                if iface.comment:
                    snapshot.physical_interface += f" ({iface.comment})"
                logger.info(
                    "MikroTik: %s uses interface %s",
                    snapshot.provider_name,
                    iface.name,
                )
        except Exception as e:
            logger.warning("Failed to enrich snapshot from MikroTik: %s", e)

    def __del__(self) -> None:
        self.disconnect_api()
