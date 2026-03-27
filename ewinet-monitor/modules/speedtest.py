"""Speedtest module using Ookla Speedtest CLI or speedtest-cli."""

from __future__ import annotations

import json
import logging
import re
import subprocess
import threading
import time
from dataclasses import dataclass
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


def find_speedtest_cmd() -> tuple[str, str]:
    """Find available speedtest CLI binary. Returns (command, version_type)."""
    # Try Ookla speedtest first
    for cmd in ["speedtest", "/usr/bin/speedtest", "/usr/local/bin/speedtest"]:
        try:
            result = subprocess.run([cmd, "--version"], capture_output=True, text=True, timeout=5)
            if result.returncode == 0:
                return cmd, "ookla"
        except (FileNotFoundError, subprocess.TimeoutExpired):
            continue

    # Try speedtest-cli (python version)
    for cmd in ["speedtest-cli", "/usr/bin/speedtest-cli"]:
        try:
            result = subprocess.run([cmd, "--version"], capture_output=True, text=True, timeout=5)
            if result.returncode == 0:
                return cmd, "cli"
        except (FileNotFoundError, subprocess.TimeoutExpired):
            continue

    return "", ""


class SpeedtestRunner:
    """Runs speedtest CLI and parses results."""

    def __init__(self):
        self._running = False
        self._current_result: SpeedtestResult | None = None
        self._history: list[SpeedtestResult] = []
        self._lock = threading.Lock()
        self._cmd, self._cmd_type = find_speedtest_cmd()

    def check_available(self) -> tuple[bool, str]:
        """Check if speedtest CLI is available."""
        if not self._cmd:
            return False, "speedtest no encontrado. Instala: sudo apt install speedtest-cli"

        try:
            result = subprocess.run(
                [self._cmd, "--version"],
                capture_output=True, text=True, timeout=5,
            )
            version = (result.stdout.strip() or result.stderr.strip()).split("\n")[0]
            return True, f"{version} ({self._cmd_type})"
        except Exception as e:
            return False, str(e)

    def list_servers(self) -> list[SpeedtestServer]:
        """List available speedtest servers."""
        if not self._cmd:
            return []

        if self._cmd_type == "ookla":
            return self._list_servers_ookla()
        else:
            return self._list_servers_cli()

    def _list_servers_ookla(self) -> list[SpeedtestServer]:
        """List servers using Ookla speedtest."""
        servers: list[SpeedtestServer] = []
        try:
            result = subprocess.run(
                [self._cmd, "--servers", "--format=json"],
                capture_output=True, text=True, timeout=30,
            )
            if result.returncode == 0:
                data = json.loads(result.stdout)
                for s in data.get("servers", []):
                    servers.append(SpeedtestServer(
                        server_id=str(s.get("id", "")),
                        name=s.get("name", ""),
                        sponsor=s.get("sponsor", ""),
                        country=s.get("country", ""),
                        city=s.get("city", ""),
                        distance_km=round(s.get("distance", 0.0), 1),
                        latency_ms=round(s.get("latency", 0.0), 1),
                    ))
        except Exception as e:
            logger.error("Ookla server list failed: %s", e)
            return self._list_servers_cli()

        servers.sort(key=lambda s: s.distance_km)
        return servers[:100]

    def _list_servers_cli(self) -> list[SpeedtestServer]:
        """List servers using speedtest-cli (text parsing)."""
        servers: list[SpeedtestServer] = []
        try:
            result = subprocess.run(
                [self._cmd, "--list"],
                capture_output=True, text=True, timeout=30,
            )
            # Format: "id) name (city, country)"
            for line in result.stdout.split("\n"):
                match = re.match(r"\s*(\d+)\)\s+(.+?)\s+\((.+?),\s*(.+?)\)", line)
                if match:
                    servers.append(SpeedtestServer(
                        server_id=match.group(1),
                        sponsor=match.group(2).strip(),
                        city=match.group(3).strip(),
                        country=match.group(4).strip(),
                        name=match.group(2).strip(),
                    ))
        except Exception as e:
            logger.error("speedtest-cli --list failed: %s", e)

        return servers[:100]

    def run_test(self, server_id: str = "", callback: Any = None) -> SpeedtestResult:
        """Run a speedtest."""
        with self._lock:
            if self._running:
                return SpeedtestResult(error="Ya hay un test en ejecucion")
            self._running = True

        if not self._cmd:
            with self._lock:
                self._running = False
            return SpeedtestResult(error="speedtest no encontrado. Instala: sudo apt install speedtest-cli")

        start_time = time.time()
        result = SpeedtestResult(timestamp=start_time)

        try:
            if self._cmd_type == "ookla":
                result = self._run_ookla(server_id, start_time)
            else:
                result = self._run_cli(server_id, start_time)
        except subprocess.TimeoutExpired:
            result.error = "Speedtest timeout (5 min)"
        except FileNotFoundError:
            result.error = f"Comando {self._cmd} no encontrado"
        except Exception as e:
            result.error = str(e)

        result.duration_seconds = round(time.time() - start_time, 1)

        with self._lock:
            self._running = False
            self._current_result = result
            self._history.append(result)

        logger.info(
            "Speedtest: Down=%.1f Mbps, Up=%.1f Mbps, Ping=%.1fms",
            result.download_mbps, result.upload_mbps, result.latency_ms,
        )

        return result

    def _run_ookla(self, server_id: str, start_time: float) -> SpeedtestResult:
        """Run Ookla speedtest with JSON output."""
        cmd = [self._cmd, "--format=json", "--progress=no"]
        if server_id:
            cmd.extend(["--server-id", server_id])

        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=300)

        result = SpeedtestResult(timestamp=start_time)

        if proc.returncode != 0:
            # Try with accept-license flag
            cmd_with_license = [self._cmd, "--format=json", "--progress=no", "--accept-license", "--accept-gdpr"]
            if server_id:
                cmd_with_license.extend(["--server-id", server_id])
            proc = subprocess.run(cmd_with_license, capture_output=True, text=True, timeout=300)

        if proc.returncode != 0:
            result.error = proc.stderr.strip() or "speedtest fallo"
            return result

        try:
            data = json.loads(proc.stdout)
        except json.JSONDecodeError:
            result.error = "No se pudo parsear la salida JSON"
            return result

        # Ookla JSON format
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

        result.isp = data.get("isp", "")
        result.external_ip = data.get("interface", {}).get("externalIp", "")

        return result

    def _run_cli(self, server_id: str, start_time: float) -> SpeedtestResult:
        """Run speedtest-cli with JSON output."""
        cmd = [self._cmd, "--json"]
        if server_id:
            cmd.extend(["--server", server_id])

        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=300)

        result = SpeedtestResult(timestamp=start_time)

        if proc.returncode != 0:
            result.error = proc.stderr.strip() or "speedtest-cli fallo"
            return result

        try:
            data = json.loads(proc.stdout)
        except json.JSONDecodeError:
            # Try parsing text output
            return self._parse_text_output(proc.stdout, start_time)

        # speedtest-cli JSON format
        result.download_mbps = round(data.get("download", 0) / 1_000_000, 2)
        result.upload_mbps = round(data.get("upload", 0) / 1_000_000, 2)
        result.latency_ms = round(data.get("ping", 0), 1)

        server = data.get("server", {})
        result.server_id = str(server.get("id", ""))
        result.server_name = server.get("name", "")
        result.server_sponsor = server.get("sponsor", server.get("name", ""))
        result.server_country = server.get("country", "")
        result.server_city = server.get("name", "")

        result.isp = data.get("client", {}).get("isp", "")
        result.external_ip = data.get("client", {}).get("ip", "")

        return result

    def _parse_text_output(self, output: str, start_time: float) -> SpeedtestResult:
        """Parse text output as last resort."""
        result = SpeedtestResult(timestamp=start_time)
        for line in output.split("\n"):
            line = line.strip()
            if "Download:" in line:
                match = re.search(r"([\d.]+)\s*Mbit/s", line)
                if match:
                    result.download_mbps = float(match.group(1))
            elif "Upload:" in line:
                match = re.search(r"([\d.]+)\s*Mbit/s", line)
                if match:
                    result.upload_mbps = float(match.group(1))
            elif "Ping:" in line or "Hosted" in line:
                match = re.search(r"([\d.]+)\s*ms", line)
                if match:
                    result.latency_ms = float(match.group(1))
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
