"""Performance metrics collection via MTR and ping."""

from __future__ import annotations

import json
import logging
import re
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed

from config.settings import Settings
from models.data_models import PerformanceMetrics

logger = logging.getLogger(__name__)


class PerformanceCollector:
    """Collects RTT and packet loss metrics for each destination."""

    def __init__(self, settings: Settings):
        self.settings = settings

    def _run_ping(self, destination: str, source_ip: str = "",
                  count: int = 20) -> PerformanceMetrics | None:
        """Run ping and extract performance metrics."""
        cmd = ["ping", "-c", str(count), "-W", "2", "-I", source_ip, destination] if source_ip \
            else ["ping", "-c", str(count), "-W", "2", destination]

        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=60,
            )
            if result.returncode != 0 and not result.stdout:
                logger.error("Ping failed for %s: %s", destination, result.stderr)
                return None

            return self._parse_ping_output(result.stdout, destination)

        except subprocess.TimeoutExpired:
            logger.error("Ping timed out for %s", destination)
            return None
        except FileNotFoundError:
            logger.error("ping command not found")
            return None

    def _parse_ping_output(self, output: str, destination: str) -> PerformanceMetrics:
        """Parse ping output for statistics."""
        metrics = PerformanceMetrics(
            provider_name="",
            destination=destination,
        )

        stats_match = re.search(
            r"rtt min/avg/max/mdev = ([\d.]+)/([\d.]+)/([\d.]+)/([\d.]+)", output
        )
        if stats_match:
            metrics.rtt_min = float(stats_match.group(1))
            metrics.rtt_avg = float(stats_match.group(2))
            metrics.rtt_max = float(stats_match.group(3))
            metrics.rtt_stddev = float(stats_match.group(4))

        loss_match = re.search(r"(\d+(?:\.\d+)?)% packet loss", output)
        if loss_match:
            metrics.packet_loss = float(loss_match.group(1))

        tx_match = re.search(r"(\d+) packets transmitted, (\d+) received", output)
        if tx_match:
            metrics.packets_sent = int(tx_match.group(1))
            metrics.packets_received = int(tx_match.group(2))

        return metrics

    def _run_mtr_metrics(
        self, destination: str, cycles: int = 20
    ) -> PerformanceMetrics | None:
        """Run MTR for detailed hop-by-hop metrics."""
        cmd = ["mtr", "--json", "--report", "--report-cycles", str(cycles), destination]

        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=120,
            )
            if result.returncode != 0:
                return None

            data = json.loads(result.stdout)
            report = data.get("report", {})
            hubs = report.get("hubs", [])

            if not hubs:
                return None

            dest_hop = hubs[-1]

            metrics = PerformanceMetrics(
                provider_name="",
                destination=destination,
                rtt_min=dest_hop.get("Min", 0.0),
                rtt_avg=dest_hop.get("Avg", 0.0),
                rtt_max=dest_hop.get("Max", 0.0),
                rtt_stddev=dest_hop.get("StDev", 0.0),
                packet_loss=dest_hop.get("Loss%", 0.0),
                packets_sent=cycles,
                packets_received=int(cycles * (1 - dest_hop.get("Loss%", 0) / 100)),
            )

            return metrics

        except (subprocess.TimeoutExpired, json.JSONDecodeError, FileNotFoundError):
            return None

    def measure_provider(
        self, provider_name: str, destination: str, source_ip: str = "",
    ) -> PerformanceMetrics:
        """Measure performance for a destination."""
        metrics = self._run_mtr_metrics(destination)

        if not metrics:
            metrics = self._run_ping(destination)

        if not metrics:
            metrics = PerformanceMetrics(
                provider_name=provider_name,
                destination=destination,
                packet_loss=100.0,
            )
            logger.warning("Both MTR and ping failed for %s", destination)
            return metrics

        metrics.provider_name = provider_name
        metrics.destination = destination

        logger.info(
            "Performance %s: RTT=%.1fms, Loss=%.1f%%",
            destination,
            metrics.rtt_avg,
            metrics.packet_loss,
        )

        return metrics

    def measure_all(self) -> list[PerformanceMetrics]:
        """Measure performance for all configured destinations."""
        metrics_list: list[PerformanceMetrics] = []

        for dest_cfg in self.settings.destinations:
            m = self.measure_provider(
                provider_name="",
                destination=dest_cfg.destination,
            )
            metrics_list.append(m)

        return metrics_list
