"""Speedtest module using Ookla Speedtest CLI."""

from __future__ import annotations

import json
import logging
import subprocess
import threading
import time
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class SpeedtestServer:
    server_id: str
    name: str
    sponsor: str
    country: str
    city: str
    distance_km: float = 0.0
    latency_ms: float = 0.0


@dataclass
class SpeedtestResult:
    timestamp: float = 0.0
    server_id: str = ""
    server_name: str = ""
    server_sponsor: str = ""
    server_country: str = ""
    server_city: str = ""
    download_mbps: float = 0.0
    upload_mbps: float = 0.0
    latency_ms: float = 0.0
    jitter_ms: float = 0.0
    packet_loss: float = 0.0
    isp: str = ""
    external_ip: str = ""
    error: str = ""
    duration_seconds: float = 0.0


class SpeedtestRunner:
    """Runs Ookla Speedtest CLI and parses results."""

    def __init__(self):
        self._running = False
        self._current_result: SpeedtestResult | None = None
        self._history: list[SpeedtestResult] = []
        self._lock = threading.Lock()

    def check_available(self) -> tuple[bool, str]:
        """Check if speedtest CLI is available."""
        try:
            result = subprocess.run(
                ["speedtest", "--version"],
                capture_output=True, text=True, timeout=10,
            )
            if result.returncode == 0:
                version = result.stdout.strip() or result.stderr.strip()
                return True, version
            return False, "speedtest command found but returned error"
        except FileNotFoundError:
            return False, "speedtest not found. Install: https://www.speedtest.net/apps/cli"
        except Exception as e:
            return False, str(e)

    def list_servers(self) -> list[SpeedtestServer]:
        """List available speedtest servers."""
        servers: list[SpeedtestServer] = []
        try:
            result = subprocess.run(
                ["speedtest", "--servers", "--format=json"],
                capture_output=True, text=True, timeout=30,
            )
            if result.returncode != 0:
                logger.error("speedtest --servers failed: %s", result.stderr)
                return self._list_servers_fallback()

            data = json.loads(result.stdout)
            for s in data.get("servers", []):
                servers.append(SpeedtestServer(
                    server_id=str(s.get("id", "")),
                    name=s.get("name", ""),
                    sponsor=s.get("sponsor", ""),
                    country=s.get("country", ""),
                    city=s.get("city", ""),
                    distance_km=s.get("distance", 0.0),
                    latency_ms=s.get("latency", 0.0),
                ))
        except (json.JSONDecodeError, Exception) as e:
            logger.error("Failed to parse server list: %s", e)
            return self._list_servers_fallback()

        servers.sort(key=lambda s: s.distance_km)
        return servers

    def _list_servers_fallback(self) -> list[SpeedtestServer]:
        """Fallback: parse text output for server list."""
        servers: list[SpeedtestServer] = []
        try:
            result = subprocess.run(
                ["speedtest", "--servers"],
                capture_output=True, text=True, timeout=30,
            )
            current_server: dict[str, str] = {}
            for line in result.stdout.split("\n"):
                line = line.strip()
                if line.startswith(") ") and current_server:
                    # Server line like: ) Server Name (City, Country)
                    parts = line[2:].split(" (")
                    if parts:
                        current_server["name"] = parts[0]
                    if len(parts) > 1:
                        loc = parts[1].rstrip(")").split(", ")
                        current_server["city"] = loc[0] if loc else ""
                        current_server["country"] = loc[1] if len(loc) > 1 else ""
                    if current_server.get("id"):
                        servers.append(SpeedtestServer(
                            server_id=current_server["id"],
                            name=current_server.get("name", ""),
                            sponsor=current_server.get("sponsor", ""),
                            country=current_server.get("country", ""),
                            city=current_server.get("city", ""),
                        ))
                    current_server = {}
                elif line.startswith("("):
                    # ID line like: (id)
                    current_server["id"] = line.strip("()")
        except Exception:
            pass
        return servers

    def run_test(self, server_id: str = "", callback: Any = None) -> SpeedtestResult:
        """Run a speedtest. Returns result when complete."""
        with self._lock:
            if self._running:
                return SpeedtestResult(error="A test is already running")
            self._running = True

        start_time = time.time()
        result = SpeedtestResult(timestamp=start_time)

        try:
            cmd = ["speedtest", "--format=json", "--progress=no"]
            if server_id:
                cmd.extend(["--server-id", server_id])

            logger.info("Starting speedtest (server: %s)", server_id or "auto")

            proc = subprocess.run(
                cmd,
                capture_output=True, text=True, timeout=300,
            )

            if proc.returncode != 0:
                error_msg = proc.stderr.strip() or "speedtest failed"
                result.error = error_msg
                logger.error("Speedtest failed: %s", error_msg)
            else:
                data = json.loads(proc.stdout)
                result.download_mbps = round(data.get("download", {}).get("bandwidth", 0) * 8 / 1_000_000, 2)
                result.upload_mbps = round(data.get("upload", {}).get("bandwidth", 0) * 8 / 1_000_000, 2)
                result.latency_ms = round(data.get("ping", {}).get("latency", 0), 1)
                result.jitter_ms = round(data.get("ping", {}).get("jitter", 0), 1)
                result.packet_loss = round(data.get("packetLoss", 0), 1)

                server = data.get("server", {})
                result.server_id = str(server.get("id", ""))
                result.server_name = server.get("name", "")
                result.server_sponsor = server.get("sponsor", "")
                result.server_country = server.get("country", "")
                result.server_city = server.get("location", "")

                isp_info = data.get("isp", "")
                result.isp = isp_info

                result.external_ip = data.get("interface", {}).get("externalIp", "")

        except subprocess.TimeoutExpired:
            result.error = "Speedtest timed out (5 min)"
        except json.JSONDecodeError:
            result.error = "Failed to parse speedtest output"
        except FileNotFoundError:
            result.error = "speedtest not found. Install: https://www.speedtest.net/apps/cli"
        except Exception as e:
            result.error = str(e)

        result.duration_seconds = round(time.time() - start_time, 1)

        with self._lock:
            self._running = False
            self._current_result = result
            self._history.append(result)

        logger.info(
            "Speedtest complete: Down=%.1f Mbps, Up=%.1f Mbps, Ping=%.1fms",
            result.download_mbps, result.upload_mbps, result.latency_ms,
        )

        return result

    def run_test_async(self, server_id: str = "") -> None:
        """Run speedtest in a background thread."""
        thread = threading.Thread(
            target=self.run_test,
            args=(server_id,),
            daemon=True,
            name="speedtest",
        )
        thread.start()

    @property
    def is_running(self) -> bool:
        with self._lock:
            return self._running

    @property
    def last_result(self) -> SpeedtestResult | None:
        with self._lock:
            return self._current_result

    @property
    def history(self) -> list[SpeedtestResult]:
        with self._lock:
            return list(self._history[-50:])

    def to_dict(self, result: SpeedtestResult) -> dict[str, Any]:
        return {
            "timestamp": result.timestamp,
            "server_id": result.server_id,
            "server_name": result.server_name,
            "server_sponsor": result.server_sponsor,
            "server_country": result.server_country,
            "server_city": result.server_city,
            "download_mbps": result.download_mbps,
            "upload_mbps": result.upload_mbps,
            "latency_ms": result.latency_ms,
            "jitter_ms": result.jitter_ms,
            "packet_loss": result.packet_loss,
            "isp": result.isp,
            "external_ip": result.external_ip,
            "error": result.error,
            "duration_seconds": result.duration_seconds,
        }
