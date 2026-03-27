"""Main orchestrator for Ewinet Route Monitor - Diagnostico Local."""

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
from modules.web_server import run_web_server, update_state

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

    def __init__(self, settings: Settings, web_port: int = 8080):
        self.settings = settings
        self.route_monitor = RouteMonitor(settings)
        self.analyzer = RouteAnalyzer(settings)
        self.perf_collector = PerformanceCollector(settings)
        self.alerter = TelegramAlerter(settings.telegram)
        self.mikrotik: MikroTikClient | None = None
        self.web_port = web_port
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

        # Update web dashboard state
        update_state(
            snapshots=snapshots,
            anomalies=anomalies,
            performance=perf_metrics,
            cycle_count=self._cycle_count,
            status="running",
        )

        elapsed = time.time() - cycle_start
        logger.info("Cycle #%d completed in %.1fs", self._cycle_count, elapsed)

    def run_daemon(self) -> None:
        """Run the monitor as a continuous daemon."""
        interval = self.settings.general.poll_interval_seconds

        logger.info("Starting Ewinet Route Monitor daemon")
        logger.info("Poll interval: %d seconds", interval)
        logger.info("Monitoring %d providers", len(self.settings.providers))

        # Start web dashboard
        run_web_server(self.settings, port=self.web_port)
        logger.info("Dashboard available at http://0.0.0.0:%d", self.web_port)
        update_state(status="running")

        if self.settings.general.enable_telegram:
            if self.alerter.test_connection():
                self.alerter.send_status_message(
                    "Monitor started. Watching %d providers."
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
        update_state(status="stopped")

        if self.settings.general.enable_telegram:
            self.alerter.send_status_message("Monitor stopped.")

    def run_once(self) -> None:
        """Run a single diagnostic cycle with detailed console output."""
        from models.data_models import RouteStatus

        print("")
        print("=" * 70)
        print("  EWINET ROUTE DIAGNOSTIC - Diagnostico de Rutas ISP")
        print("=" * 70)
        print(f"  Proveedores configurados: {len(self.settings.providers)}")
        print(f"  Destinos de prueba: {', '.join(self.settings.providers[0].test_destinations[:2])}")
        print(f"  MikroTik: {'ON' if self.settings.general.enable_mikrotik else 'OFF (tu gestionas las rutas)'}")
        print(f"  Telegram: {'ON' if self.settings.general.enable_telegram else 'OFF (diagnostico local)'}")
        print("=" * 70)
        print("")

        cycle_start = time.time()

        # Step 1: Probe all routes
        print("[1/3] Probando rutas de todos los proveedores...")
        snapshots = self.route_monitor.probe_all_providers()

        if not snapshots:
            print("  ERROR: No se recolectaron rutas. Verifica conectividad de red.")
            return

        print(f"  Recolectados {len(snapshots)} snapshots de ruta\n")

        # Step 2: Collect performance metrics
        print("[2/3] Midiendo performance (RTT, packet loss)...")
        perf_metrics = self.perf_collector.measure_all()

        for m in perf_metrics:
            for p in self.settings.providers:
                if m.destination in p.test_destinations:
                    if not m.provider_name:
                        m.provider_name = p.name

        # Step 3: Analyze routes
        print("[3/3] Analizando rutas contra baselines...\n")
        anomalies = self.analyzer.analyze_all(snapshots)

        # ===== DETAILED DIAGNOSTIC OUTPUT =====
        print("=" * 70)
        print("  RESULTADOS DEL DIAGNOSTICO")
        print("=" * 70)

        # Group snapshots by provider
        by_provider: dict[str, list] = {}
        for s in snapshots:
            by_provider.setdefault(s.provider_name, []).append(s)

        for provider_name, provider_snapshots in sorted(by_provider.items()):
            provider_cfg = self.settings.get_provider(provider_name)
            print(f"\n{'─' * 70}")
            print(f"  PROVEEDOR: {provider_name}")
            if provider_cfg:
                print(f"  ASN: AS{provider_cfg.asn}")
                print(f"  Descripcion: {provider_cfg.description}")
                print(f"  Interfaz: {provider_cfg.local_interface} (VLAN {provider_cfg.vlan_id})")
            print(f"{'─' * 70}")

            for snapshot in provider_snapshots:
                status_icon = {
                    RouteStatus.NORMAL: "[OK]",
                    RouteStatus.DEGRADED: "[!!]",
                    RouteStatus.ANOMALY: "[XX]",
                    RouteStatus.UNKNOWN: "[??]",
                }.get(snapshot.status, "[??]")

                print(f"\n  {status_icon} -> {snapshot.destination}")
                print(f"    Estado: {snapshot.status.value.upper()}")
                print(f"    Saltos: {len(snapshot.hops)}")

                # Show AS path
                if snapshot.as_path.hops:
                    unique_asns = snapshot.as_path.unique_asns()
                    as_path_str = " -> ".join(
                        f"AS{asn.number}" + (f" ({asn.name})" if asn.name else "")
                        for asn in unique_asns
                    )
                    print(f"    AS Path: {as_path_str}")

                    # Compare with baseline
                    baseline = self.analyzer.get_baseline(provider_name, snapshot.destination)
                    if baseline:
                        baseline_numbers = baseline.expected_as_path.as_numbers()
                        current_numbers = snapshot.as_path.as_numbers()

                        if baseline_numbers == current_numbers:
                            print(f"    Baseline: COINCIDE con ruta esperada")
                        else:
                            print(f"    Baseline: NO COINCIDE")
                            expected_str = " -> ".join(f"AS{n}" for n in baseline_numbers)
                            current_str = " -> ".join(f"AS{n}" for n in current_numbers)
                            print(f"      Esperada: {expected_str}")
                            print(f"      Actual:   {current_str}")
                else:
                    print(f"    AS Path: No se pudo determinar")

                # Show key hops
                print(f"    Hops relevantes:")
                for hop in snapshot.hops[:8]:
                    asn_info = f"AS{hop.asn.number}" if hop.asn else "?"
                    loss_str = f"{hop.loss_percent:.0f}% loss" if hop.loss_percent > 0 else "0% loss"
                    print(f"      #{hop.hop_number:2d} {hop.ip_address:18s} {asn_info:12s} {hop.rtt_avg:7.1f}ms  {loss_str}")

                if len(snapshot.hops) > 8:
                    print(f"      ... ({len(snapshot.hops) - 8} hops mas)")

        # Performance summary
        print(f"\n{'=' * 70}")
        print("  PERFORMANCE")
        print(f"{'=' * 70}")

        for m in perf_metrics:
            if m.rtt_avg > 0:
                status = "OK" if m.packet_loss < 5 else "DEGRADED" if m.packet_loss < 20 else "DOWN"
                print(f"  {m.provider_name:10s} -> {m.destination:15s}  RTT={m.rtt_avg:7.1f}ms  Loss={m.packet_loss:5.1f}%  [{status}]")
            else:
                print(f"  {m.provider_name:10s} -> {m.destination:15s}  NO RESPONDE")

        # Anomalies summary
        print(f"\n{'=' * 70}")
        if anomalies:
            print(f"  ALERTAS: {len(anomalies)} anomalias detectadas")
            print(f"{'=' * 70}")
            for i, a in enumerate(anomalies, 1):
                print(f"\n  [{i}] {a.severity.value.upper()} - {a.provider_name} -> {a.destination}")
                print(f"      {a.description}")
                print(f"      Esperada: {a.baseline_as_path}")
                print(f"      Actual:   {a.current_as_path}")
        else:
            print("  ESTADO: Todas las rutas dentro de los baselines esperados")
            print(f"{'=' * 70}")

        # Update web dashboard state
        update_state(
            snapshots=snapshots,
            anomalies=anomalies,
            performance=perf_metrics,
            cycle_count=1,
            status="completed",
        )

        elapsed = time.time() - cycle_start
        print(f"\n  Diagnostico completado en {elapsed:.1f}s")
        print(f"  Logs: {LOG_DIR / 'ewinet_monitor.log'}")
        print(f"  Dashboard: http://localhost:8080 (si se ejecuta en modo daemon)")
        print("")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Ewinet Route Monitor - Diagnostico de rutas ISP desde tu PC",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Ejemplos:
  python main.py --once                         # Diagnostico rapido (recomendado)
  python main.py --once --log-level DEBUG       # Diagnostico con mas detalle
  python main.py                                # Modo daemon con dashboard web
  python main.py --port 9090                    # Cambiar puerto del dashboard
  python main.py --config mi_config.yaml        # Usar config personalizada
  python main.py --test-telegram                # Probar conexion Telegram
        """,
    )
    parser.add_argument(
        "--once", action="store_true",
        help="Ejecutar un solo diagnostico y salir (recomendado para PC)",
    )
    parser.add_argument(
        "--config", type=str, default=None,
        help="Ruta al archivo de configuracion providers.yaml",
    )
    parser.add_argument(
        "--log-level", type=str, default=None,
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Sobreescribir nivel de log",
    )
    parser.add_argument(
        "--test-telegram", action="store_true",
        help="Probar conexion Telegram y salir",
    )
    parser.add_argument(
        "--port", type=int, default=8080,
        help="Puerto del dashboard web (default: 8080)",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    # Load settings
    settings = Settings.load(args.config)

    # Override log level if specified
    log_level = args.log_level or settings.general.log_level
    setup_logging(log_level)

    logger.info("Ewinet Route Monitor v1.0.0 - Diagnostico Local")
    logger.info("Config loaded: %d providers", len(settings.providers))

    if args.test_telegram:
        alerter = TelegramAlerter(settings.telegram)
        if alerter.test_connection():
            print("Telegram connection OK")
            alerter.send_status_message("Test message from Ewinet Route Monitor")
            sys.exit(0)
        else:
            print("Telegram connection FAILED")
            sys.exit(1)

    monitor = EwinetMonitor(settings, web_port=args.port)

    if args.once:
        monitor.run_once()
    else:
        monitor.run_daemon()


if __name__ == "__main__":
    main()
