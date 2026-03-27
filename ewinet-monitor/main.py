"""Main orchestrator for Ewinet Route Monitor - Diagnostico Local."""

from __future__ import annotations

import argparse
import logging
import signal
import sys
import time
from pathlib import Path

from config.settings import Settings
from modules.route_monitor import RouteMonitor, detect_public_ip
from modules.route_analyzer import RouteAnalyzer
from modules.performance import PerformanceCollector
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

    console = logging.StreamHandler(sys.stdout)
    console.setLevel(log_level)
    console.setFormatter(formatter)

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
        self.web_port = web_port
        self._running = True
        self._cycle_count = 0
        self._public_ip = ""

        signal.signal(signal.SIGINT, self._handle_shutdown)
        signal.signal(signal.SIGTERM, self._handle_shutdown)

    def _handle_shutdown(self, signum: int, frame: object) -> None:
        logger.info("Received signal %d, shutting down...", signum)
        self._running = False

    def _detect_public_ip(self) -> str:
        """Detect and cache public IP."""
        if not self._public_ip:
            self._public_ip = detect_public_ip()
        return self._public_ip

    def run_single_cycle(self) -> None:
        """Execute a single monitoring cycle."""
        self._cycle_count += 1
        cycle_start = time.time()

        logger.info("Starting monitoring cycle #%d", self._cycle_count)

        # Detect public IP
        public_ip = self._detect_public_ip()
        if public_ip:
            logger.info("Public IP: %s", public_ip)

        # Probe routes
        snapshots = self.route_monitor.probe_all_providers()

        if not snapshots:
            logger.error("No route snapshots collected. Check network connectivity.")
            return

        logger.info("Collected %d route snapshots", len(snapshots))

        # Performance metrics
        perf_metrics = []
        for dest_cfg in self.settings.destinations:
            m = self.perf_collector.measure_provider(
                provider_name="",
                destination=dest_cfg.destination,
            )
            perf_metrics.append(m)

        # Analyze routes
        anomalies = self.analyzer.analyze_all(snapshots)

        if anomalies:
            logger.warning("Detected %d anomalies:", len(anomalies))
            for a in anomalies:
                logger.warning(
                    "  [%s] -> %s: %s",
                    a.severity.value.upper(),
                    a.destination,
                    a.description,
                )
        else:
            logger.info("All routes within expected baselines")

        # Update web dashboard
        update_state(
            snapshots=snapshots,
            anomalies=anomalies,
            performance=perf_metrics,
            cycle_count=self._cycle_count,
            status="running",
            public_ip=public_ip,
        )

        elapsed = time.time() - cycle_start
        logger.info("Cycle #%d completed in %.1fs", self._cycle_count, elapsed)

    def run_daemon(self) -> None:
        """Run the monitor as a continuous daemon with web dashboard."""
        interval = self.settings.general.poll_interval_seconds

        logger.info("Starting Ewinet Route Monitor daemon")
        logger.info("Poll interval: %d seconds", interval)
        logger.info("Monitoring %d destinations", len(self.settings.destinations))

        run_web_server(self.settings, port=self.web_port)
        logger.info("Dashboard at http://localhost:%d", self.web_port)
        update_state(status="running")

        while self._running:
            try:
                self.run_single_cycle()
            except Exception:
                logger.exception("Error in monitoring cycle #%d", self._cycle_count)

            if not self._running:
                break

            logger.info("Next cycle in %d seconds...", interval)
            for _ in range(interval):
                if not self._running:
                    break
                time.sleep(1)

        logger.info("Monitor stopped after %d cycles", self._cycle_count)
        update_state(status="stopped")

    def run_once(self) -> None:
        """Run a single diagnostic cycle with detailed console output."""
        from models.data_models import RouteStatus

        print("")
        print("=" * 70)
        print("  EWINET ROUTE DIAGNOSTIC")
        print("=" * 70)

        cycle_start = time.time()

        # Step 1: Detect public IP
        print("\n[1/4] Detectando IP publica...")
        public_ip = self._detect_public_ip()
        if public_ip:
            print(f"  IP Publica detectada: {public_ip}")
        else:
            print("  No se pudo detectar IP publica (sin internet?)")
            print("  Continuando de todos modos...")

        # Step 2: Probe routes
        print(f"\n[2/4] Probando rutas a {len(self.settings.destinations)} destinos...")
        for dest_cfg in self.settings.destinations:
            desc = f" ({dest_cfg.description})" if dest_cfg.description else ""
            print(f"  -> {dest_cfg.destination}{desc}")

        snapshots = self.route_monitor.probe_all_providers()

        if not snapshots:
            print("\n  ERROR: No se recolectaron rutas. Verifica conectividad de red.")
            return

        print(f"\n  Recolectados {len(snapshots)} snapshots de ruta")

        # Step 3: Performance metrics
        print(f"\n[3/4] Midiendo performance...")
        perf_metrics = []
        for dest_cfg in self.settings.destinations:
            m = self.perf_collector.measure_provider(
                provider_name="",
                destination=dest_cfg.destination,
            )
            perf_metrics.append(m)

        # Step 4: Analyze routes
        print(f"\n[4/4] Analizando rutas contra baselines...")
        anomalies = self.analyzer.analyze_all(snapshots)

        # ===== DETAILED OUTPUT =====
        print(f"\n{'=' * 70}")
        print("  RESULTADOS DEL DIAGNOSTICO")
        print(f"{'=' * 70}")

        if public_ip:
            print(f"\n  Tu IP publica: {public_ip}")

        for snapshot in snapshots:
            status_icon = {
                RouteStatus.NORMAL: "[OK]",
                RouteStatus.DEGRADED: "[!!]",
                RouteStatus.ANOMALY: "[XX]",
                RouteStatus.UNKNOWN: "[??]",
            }.get(snapshot.status, "[??]")

            # Find description for this destination
            desc = ""
            for dc in self.settings.destinations:
                if dc.destination == snapshot.destination:
                    desc = dc.description
                    break

            print(f"\n{'─' * 70}")
            title = f"-> {snapshot.destination}"
            if desc:
                title += f" ({desc})"
            print(f"  {status_icon} {title}")
            print(f"  Estado: {snapshot.status.value.upper()}")
            print(f"  Saltos: {len(snapshot.hops)}")

            # Show AS path
            if snapshot.as_path.hops:
                unique_asns = snapshot.as_path.unique_asns()
                as_path_str = " -> ".join(
                    f"AS{asn.number}" + (f" ({asn.name})" if asn.name else "")
                    for asn in unique_asns
                )
                print(f"  AS Path: {as_path_str}")

                # Compare with baseline
                baseline = self.analyzer.get_baseline(snapshot.destination)
                if baseline:
                    baseline_numbers = baseline.expected_as_path.as_numbers()
                    current_numbers = snapshot.as_path.as_numbers()

                    if baseline_numbers == current_numbers:
                        print(f"  Baseline: COINCIDE con ruta esperada")
                    else:
                        print(f"  Baseline: NO COINCIDE")
                        expected_str = " -> ".join(f"AS{n}" for n in baseline_numbers)
                        current_str = " -> ".join(f"AS{n}" for n in current_numbers)
                        print(f"    Esperada: {expected_str}")
                        print(f"    Actual:   {current_str}")
            else:
                print(f"  AS Path: No se pudo determinar")

            # Show key hops
            print(f"  Hops:")
            for hop in snapshot.hops[:10]:
                asn_str = f"AS{hop.asn.number}" if hop.asn else "?"
                name_str = f" ({hop.asn.name})" if hop.asn and hop.asn.name else ""
                loss_str = f" {hop.loss_percent:.0f}% loss" if hop.loss_percent > 0 else ""
                print(
                    f"    #{hop.hop_number:2d} "
                    f"{hop.ip_address:18s} "
                    f"{asn_str}{name_str}"
                    f"  {hop.rtt_avg:7.1f}ms"
                    f"{loss_str}"
                )

            if len(snapshot.hops) > 10:
                print(f"    ... ({len(snapshot.hops) - 10} hops mas)")

        # Performance summary
        print(f"\n{'=' * 70}")
        print("  PERFORMANCE")
        print(f"{'=' * 70}")

        for m in perf_metrics:
            if m.rtt_avg > 0:
                status = "OK" if m.packet_loss < 5 else "!!DEGRADED" if m.packet_loss < 20 else "XX DOWN"
                desc = ""
                for dc in self.settings.destinations:
                    if dc.destination == m.destination:
                        desc = dc.description
                        break
                label = f"{m.destination} ({desc})" if desc else m.destination
                print(f"  {label:30s}  RTT={m.rtt_avg:7.1f}ms  Loss={m.packet_loss:5.1f}%  [{status}]")
            else:
                print(f"  {m.destination:30s}  NO RESPONDE")

        # Anomalies summary
        print(f"\n{'=' * 70}")
        if anomalies:
            print(f"  ALERTAS: {len(anomalies)} anomalias detectadas")
            print(f"{'=' * 70}")
            for i, a in enumerate(anomalies, 1):
                print(f"\n  [{i}] {a.severity.value.upper()} -> {a.destination}")
                print(f"      {a.description}")
                if a.baseline_as_path:
                    print(f"      Esperada: {a.baseline_as_path}")
                if a.current_as_path:
                    print(f"      Actual:   {a.current_as_path}")
        else:
            if any(not s.as_path.hops for s in snapshots):
                print("  ESTADO: Algunas rutas no pudieron ser determinadas")
            else:
                print("  ESTADO: Todas las rutas dentro de los baselines")
            print(f"{'=' * 70}")

        # Update web dashboard
        update_state(
            snapshots=snapshots,
            anomalies=anomalies,
            performance=perf_metrics,
            cycle_count=1,
            status="completed",
            public_ip=public_ip,
        )

        elapsed = time.time() - cycle_start
        print(f"\n  Diagnostico completado en {elapsed:.1f}s")
        print(f"  Logs: {LOG_DIR / 'ewinet_monitor.log'}")
        print(f"  Dashboard: http://localhost:8080 (modo daemon)")
        print("")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Ewinet Route Monitor - Diagnostico de rutas ISP desde tu PC",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Ejemplos:
  python main.py --once                         # Diagnostico rapido
  python main.py --once --log-level DEBUG       # Con mas detalle
  python main.py                                # Modo daemon + dashboard
  python main.py --port 9090                    # Cambiar puerto dashboard
  python main.py --config mi_config.yaml        # Config personalizada
        """,
    )
    parser.add_argument(
        "--once", action="store_true",
        help="Ejecutar un solo diagnostico y salir",
    )
    parser.add_argument(
        "--config", type=str, default=None,
        help="Ruta al archivo de configuracion",
    )
    parser.add_argument(
        "--log-level", type=str, default=None,
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Nivel de log",
    )
    parser.add_argument(
        "--port", type=int, default=8080,
        help="Puerto del dashboard web (default: 8080)",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    settings = Settings.load(args.config)

    log_level = args.log_level or settings.general.log_level
    setup_logging(log_level)

    logger.info("Ewinet Route Monitor - Diagnostico Local")
    logger.info("Config loaded: %d destinations", len(settings.destinations))

    monitor = EwinetMonitor(settings, web_port=args.port)

    if args.once:
        monitor.run_once()
    else:
        monitor.run_daemon()


if __name__ == "__main__":
    main()
