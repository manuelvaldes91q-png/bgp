"""Main orchestrator for Ewinet Route Monitor."""

from __future__ import annotations

import argparse
import logging
import signal
import sys
import time
from pathlib import Path

from config.settings import Settings
from modules.alerter import TelegramAlerter
from modules.mikrotik import MikroTikClient
from modules.performance import PerformanceCollector
from modules.route_analyzer import RouteAnalyzer
from modules.route_monitor import RouteMonitor

PROJECT_ROOT = Path(__file__).parent
LOG_DIR = PROJECT_ROOT / "logs"

logger = logging.getLogger("ewinet")


def setup_logging(level: str = "INFO") -> None:
    """Configure logging to file and console."""
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    log_level = getattr(logging, level.upper(), logging.INFO)

    formatter = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # Console handler
    console = logging.StreamHandler(sys.stdout)
    console.setLevel(log_level)
    console.setFormatter(formatter)

    # File handler
    file_handler = logging.FileHandler(
        LOG_DIR / "ewinet_monitor.log", encoding="utf-8"
    )
    file_handler.setLevel(log_level)
    file_handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)
    root_logger.addHandler(console)
    root_logger.addHandler(file_handler)


class EwinetMonitor:
    """Main application orchestrator."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.route_monitor = RouteMonitor(settings)
        self.analyzer = RouteAnalyzer(settings)
        self.perf_collector = PerformanceCollector(settings)
        self.alerter = TelegramAlerter(settings.telegram)
        self.mikrotik: MikroTikClient | None = None
        self._running = True
        self._cycle_count = 0

        if settings.general.enable_mikrotik and settings.mikrotik.host:
            self.mikrotik = MikroTikClient(settings.mikrotik)

        # Register signal handlers
        signal.signal(signal.SIGINT, self._handle_shutdown)
        signal.signal(signal.SIGTERM, self._handle_shutdown)

    def _handle_shutdown(self, signum: int, frame: object) -> None:
        logger.info("Received signal %d, shutting down gracefully...", signum)
        self._running = False

    def run_single_cycle(self) -> None:
        """Execute a single monitoring cycle."""
        self._cycle_count += 1
        cycle_start = time.time()

        logger.info("=" * 60)
        logger.info("Starting monitoring cycle #%d", self._cycle_count)
        logger.info("=" * 60)

        # Step 1: Probe all routes
        logger.info("[1/4] Probing routes for all providers...")
        snapshots = self.route_monitor.probe_all_providers()

        if not snapshots:
            logger.error("No route snapshots collected. Check network connectivity.")
            return

        logger.info("Collected %d route snapshots", len(snapshots))

        # Step 2: Enrich with MikroTik data
        if self.mikrotik:
            logger.info("[2/4] Enriching with MikroTik interface data...")
            for snapshot in snapshots:
                self.mikrotik.enrich_snapshot(snapshot)
        else:
            logger.info("[2/4] MikroTik integration disabled, skipping...")

        # Step 3: Collect performance metrics
        logger.info("[3/4] Collecting performance metrics...")
        perf_metrics = self.perf_collector.measure_all()

        # Enrich performance metrics with provider names
        for m in perf_metrics:
            for p in self.settings.providers:
                if m.destination in p.test_destinations:
                    if not m.provider_name:
                        m.provider_name = p.name

        # Step 4: Analyze routes and detect anomalies
        logger.info("[4/4] Analyzing routes against baselines...")
        anomalies = self.analyzer.analyze_all(snapshots)

        # Report results
        if anomalies:
            logger.warning("Detected %d anomalies:", len(anomalies))
            for a in anomalies:
                logger.warning(
                    "  [%s] %s -> %s: %s",
                    a.severity.value.upper(),
                    a.provider_name,
                    a.destination,
                    a.description,
                )

            # Send Telegram alerts
            if self.settings.general.enable_telegram:
                sent = self.alerter.send_anomaly_alerts(anomalies)
                logger.info("Sent %d/%d alerts via Telegram", sent, len(anomalies))
        else:
            logger.info("All routes are within expected baselines")

        # Log performance summary
        for m in perf_metrics:
            status = "OK" if m.packet_loss < 5 else "DEGRADED" if m.packet_loss < 20 else "DOWN"
            logger.info(
                "  Perf %s -> %s: RTT=%.1fms Loss=%.1f%% [%s]",
                m.provider_name,
                m.destination,
                m.rtt_avg,
                m.packet_loss,
                status,
            )

        elapsed = time.time() - cycle_start
        logger.info("Cycle #%d completed in %.1fs", self._cycle_count, elapsed)

    def run_daemon(self) -> None:
        """Run the monitor as a continuous daemon."""
        interval = self.settings.general.poll_interval_seconds

        logger.info("Starting Ewinet Route Monitor daemon")
        logger.info("Poll interval: %d seconds", interval)
        logger.info("Monitoring %d providers", len(self.settings.providers))

        if self.settings.general.enable_telegram:
            if self.alerter.test_connection():
                self.alerter.send_status_message(
                    "🟢 Monitor started. Watching %d providers."
                    % len(self.settings.providers)
                )
            else:
                logger.warning("Telegram bot connection failed, alerts will be logged only")

        while self._running:
            try:
                self.run_single_cycle()
            except Exception:
                logger.exception("Error in monitoring cycle #%d", self._cycle_count)

            if not self._running:
                break

            logger.info("Next cycle in %d seconds...", interval)

            # Sleep in small increments to allow graceful shutdown
            for _ in range(interval):
                if not self._running:
                    break
                time.sleep(1)

        logger.info("Ewinet Route Monitor stopped after %d cycles", self._cycle_count)

        if self.settings.general.enable_telegram:
            self.alerter.send_status_message("🔴 Monitor stopped.")

    def run_once(self) -> None:
        """Run a single monitoring cycle and exit."""
        logger.info("Running single monitoring cycle...")
        self.run_single_cycle()
        logger.info("Single cycle completed")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Ewinet Route Monitor - ISP upstream route monitoring",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python main.py                    # Run as daemon
  python main.py --once             # Single cycle
  python main.py --config custom.yaml
  python main.py --log-level DEBUG
        """,
    )
    parser.add_argument(
        "--once", action="store_true",
        help="Run a single monitoring cycle and exit",
    )
    parser.add_argument(
        "--config", type=str, default=None,
        help="Path to providers.yaml config file",
    )
    parser.add_argument(
        "--log-level", type=str, default=None,
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Override log level",
    )
    parser.add_argument(
        "--test-telegram", action="store_true",
        help="Test Telegram bot connectivity and exit",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    # Load settings
    settings = Settings.load(args.config)

    # Override log level if specified
    log_level = args.log_level or settings.general.log_level
    setup_logging(log_level)

    logger.info("Ewinet Route Monitor v1.0.0")
    logger.info("Config loaded: %d providers", len(settings.providers))

    if args.test_telegram:
        alerter = TelegramAlerter(settings.telegram)
        if alerter.test_connection():
            print("Telegram connection OK")
            alerter.send_status_message("✅ Test message from Ewinet Route Monitor")
            sys.exit(0)
        else:
            print("Telegram connection FAILED")
            sys.exit(1)

    monitor = EwinetMonitor(settings)

    if args.once:
        monitor.run_once()
    else:
        monitor.run_daemon()


if __name__ == "__main__":
    main()
