"""Flask web dashboard for Ewinet Route Monitor."""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from config.settings import Settings

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).parent.parent / "data"
HISTORY_FILE = DATA_DIR / "route_history.jsonl"
BASELINES_FILE = DATA_DIR / "baselines.json"
ALERTS_FILE = DATA_DIR / "alerts.json"

# Shared state updated by the monitor
_state: dict[str, Any] = {
    "snapshots": [],
    "anomalies": [],
    "performance": [],
    "last_update": 0,
    "cycle_count": 0,
    "status": "starting",
    "public_ip": "",
}


def create_app(settings: Settings) -> Any:
    """Create and configure the Flask app."""
    from flask import Flask, jsonify, render_template, request
    from models.data_models import (
        PerformanceMetrics,
        RouteAnomaly,
        RouteSnapshot,
    )

    project_root = Path(__file__).parent.parent
    template_dir = project_root / "web" / "templates"
    static_dir = project_root / "web" / "static"

    app = Flask(
        __name__,
        template_folder=str(template_dir),
        static_folder=str(static_dir),
    )

    @app.route("/")
    def dashboard() -> str:
        return render_template("dashboard.html", settings=settings)

    @app.route("/api/status")
    def api_status() -> Any:
        return jsonify({
            "status": _state["status"],
            "cycle_count": _state["cycle_count"],
            "last_update": _state["last_update"],
            "last_update_human": datetime.fromtimestamp(
                _state["last_update"]
            ).strftime("%Y-%m-%d %H:%M:%S") if _state["last_update"] else "Never",
            "uptime": time.time() - _state.get("start_time", time.time()),
            "destinations_count": len(settings.destinations),
            "public_ip": _state.get("public_ip", ""),
        })

    @app.route("/api/snapshots")
    def api_snapshots() -> Any:
        snapshots = _state.get("snapshots", [])
        result = []
        for s in snapshots:
            if isinstance(s, RouteSnapshot):
                result.append(_snapshot_to_dict(s))
            elif isinstance(s, dict):
                result.append(s)
        return jsonify(result)

    @app.route("/api/performance")
    def api_performance() -> Any:
        metrics = _state.get("performance", [])
        result = []
        for m in metrics:
            if isinstance(m, PerformanceMetrics):
                result.append({
                    "provider_name": m.provider_name,
                    "destination": m.destination,
                    "timestamp": m.timestamp,
                    "rtt_min": m.rtt_min,
                    "rtt_avg": m.rtt_avg,
                    "rtt_max": m.rtt_max,
                    "rtt_stddev": m.rtt_stddev,
                    "packet_loss": m.packet_loss,
                    "packets_sent": m.packets_sent,
                    "packets_received": m.packets_received,
                })
            elif isinstance(m, dict):
                result.append(m)
        return jsonify(result)

    @app.route("/api/anomalies")
    def api_anomalies() -> Any:
        anomalies = _state.get("anomalies", [])
        result = []
        for a in anomalies:
            if isinstance(a, RouteAnomaly):
                result.append({
                    "provider_name": a.provider_name,
                    "destination": a.destination,
                    "timestamp": a.timestamp,
                    "timestamp_human": datetime.fromtimestamp(a.timestamp).strftime(
                        "%Y-%m-%d %H:%M:%S"
                    ),
                    "severity": a.severity.value,
                    "description": a.description,
                    "baseline_as_path": a.baseline_as_path,
                    "current_as_path": a.current_as_path,
                    "unexpected_asns": [
                        {"number": asn.number, "name": asn.name}
                        for asn in a.unexpected_asns
                    ],
                    "missing_asns": [
                        {"number": asn.number, "name": asn.name}
                        for asn in a.missing_asns
                    ],
                    "latency_increase_percent": a.latency_increase_percent,
                })
            elif isinstance(a, dict):
                result.append(a)
        return jsonify(result)

    @app.route("/api/providers")
    def api_providers() -> Any:
        """Return destinations as provider-like objects for dashboard compatibility."""
        providers = []
        for d in settings.destinations:
            providers.append({
                "name": d.description or d.destination,
                "asn": d.expected_asn or 0,
                "gateway": "",
                "interface": "",
                "vlan_id": "",
                "destinations": [d.destination],
                "description": d.description,
                "baseline": {
                    "expected_as_path": d.baseline.expected_as_path.as_numbers() if d.baseline else [],
                    "expected_as_path_str": str(d.baseline.expected_as_path) if d.baseline else "--",
                    "known_transit_asns": d.baseline.known_transit_asns if d.baseline else [],
                    "expected_first_hop_asn": d.baseline.expected_first_hop_asn if d.baseline else 0,
                    "baseline_rtt_avg": d.baseline.baseline_rtt_avg if d.baseline else 0.0,
                } if d.baseline else {},
            })
        return jsonify(providers)

    @app.route("/api/destinations")
    def api_destinations() -> Any:
        dests = []
        for d in settings.destinations:
            dests.append({
                "destination": d.destination,
                "description": d.description,
                "expected_provider": d.expected_provider,
                "expected_asn": d.expected_asn,
            })
        return jsonify(dests)

    @app.route("/api/history")
    def api_history() -> Any:
        limit = request.args.get("limit", 50, type=int)
        records: list[dict[str, Any]] = []

        if HISTORY_FILE.exists():
            try:
                with open(HISTORY_FILE, "r") as f:
                    lines = f.readlines()
                    for line in lines[-limit:]:
                        try:
                            records.append(json.loads(line.strip()))
                        except json.JSONDecodeError:
                            continue
            except Exception:
                pass

        return jsonify(records)

    @app.route("/api/saved-alerts")
    def api_saved_alerts() -> Any:
        if ALERTS_FILE.exists():
            try:
                with open(ALERTS_FILE, "r") as f:
                    return jsonify(json.load(f))
            except Exception:
                pass
        return jsonify([])

    @app.route("/api/baselines")
    def api_baselines() -> Any:
        if BASELINES_FILE.exists():
            try:
                with open(BASELINES_FILE, "r") as f:
                    return jsonify(json.load(f))
            except Exception:
                pass
        return jsonify({})

    # ── Speedtest Endpoints ──

    from modules.speedtest import SpeedtestRunner

    speedtest_runner = SpeedtestRunner()

    @app.route("/api/speedtest/status")
    def api_speedtest_status() -> Any:
        available, version = speedtest_runner.check_available()
        last = speedtest_runner.last_result
        return jsonify({
            "available": available,
            "version": version,
            "running": speedtest_runner.is_running,
            "last_result": speedtest_runner.to_dict(last) if last else None,
        })

    @app.route("/api/speedtest/servers")
    def api_speedtest_servers() -> Any:
        servers = speedtest_runner.list_servers()
        return jsonify([
            {
                "server_id": s.server_id,
                "name": s.name,
                "sponsor": s.sponsor,
                "country": s.country,
                "city": s.city,
                "distance_km": round(s.distance_km, 1),
                "latency_ms": round(s.latency_ms, 1),
            }
            for s in servers[:100]
        ])

    @app.route("/api/speedtest/run", methods=["POST"])
    def api_speedtest_run() -> Any:
        if speedtest_runner.is_running:
            return jsonify({"error": "A speedtest is already running"}), 409

        data = request.get_json(silent=True) or {}
        server_id = str(data.get("server_id", ""))

        speedtest_runner.run_test_async(server_id)
        return jsonify({"status": "started", "server_id": server_id or "auto"})

    @app.route("/api/speedtest/result")
    def api_speedtest_result() -> Any:
        last = speedtest_runner.last_result
        if not last:
            return jsonify({"error": "No results yet"}), 404
        return jsonify(speedtest_runner.to_dict(last))

    @app.route("/api/speedtest/history")
    def api_speedtest_history() -> Any:
        history = speedtest_runner.history
        return jsonify([speedtest_runner.to_dict(r) for r in history])

    return app


def _snapshot_to_dict(s: Any) -> dict[str, Any]:
    return {
        "provider_name": s.provider_name,
        "provider_asn": s.provider_asn,
        "gateway": s.gateway,
        "destination": s.destination,
        "timestamp": s.timestamp,
        "timestamp_human": datetime.fromtimestamp(s.timestamp).strftime(
            "%Y-%m-%d %H:%M:%S"
        ),
        "as_path": str(s.as_path),
        "as_path_numbers": s.as_path.as_numbers(),
        "intermediate_asns": [
            {"number": asn.number, "name": asn.name}
            for asn in s.intermediate_asns
        ],
        "physical_interface": s.physical_interface,
        "vlan_id": s.vlan_id,
        "status": s.status.value,
        "hops": [
            {
                "hop_number": h.hop_number,
                "ip_address": h.ip_address,
                "hostname": h.hostname,
                "asn": {"number": h.asn.number, "name": h.asn.name} if h.asn else None,
                "rtt_avg": h.rtt_avg,
                "rtt_min": h.rtt_min,
                "rtt_max": h.rtt_max,
                "loss_percent": h.loss_percent,
            }
            for h in s.hops
        ],
    }


def update_state(
    snapshots: list[Any] | None = None,
    anomalies: list[Any] | None = None,
    performance: list[Any] | None = None,
    cycle_count: int | None = None,
    status: str | None = None,
    public_ip: str | None = None,
) -> None:
    """Update shared state from the monitor thread."""
    if snapshots is not None:
        _state["snapshots"] = snapshots
    if anomalies is not None:
        _state["anomalies"] = anomalies
    if performance is not None:
        _state["performance"] = performance
    if cycle_count is not None:
        _state["cycle_count"] = cycle_count
    if status is not None:
        _state["status"] = status
    if public_ip is not None:
        _state["public_ip"] = public_ip
    _state["last_update"] = time.time()

    if anomalies:
        _save_alerts(anomalies)


def _save_alerts(anomalies: list[Any]) -> None:
    """Append anomalies to alerts history file."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    existing: list[dict[str, Any]] = []
    if ALERTS_FILE.exists():
        try:
            with open(ALERTS_FILE, "r") as f:
                existing = json.load(f)
        except Exception:
            pass

    for a in anomalies:
        existing.append({
            "provider_name": a.provider_name,
            "destination": a.destination,
            "timestamp": a.timestamp,
            "timestamp_human": datetime.fromtimestamp(a.timestamp).strftime(
                "%Y-%m-%d %H:%M:%S"
            ),
            "severity": a.severity.value,
            "description": a.description,
            "baseline_as_path": a.baseline_as_path,
            "current_as_path": a.current_as_path,
            "latency_increase_percent": a.latency_increase_percent,
        })

    existing = existing[-500:]

    with open(ALERTS_FILE, "w") as f:
        json.dump(existing, f, indent=2)


def run_web_server(settings: Settings, host: str = "0.0.0.0", port: int = 8080) -> None:
    """Run the Flask web server in a separate thread."""
    _state["start_time"] = time.time()

    app = create_app(settings)

    def _run() -> None:
        logger.info("Starting web dashboard on http://%s:%d", host, port)
        app.run(host=host, port=port, debug=False, use_reloader=False, threaded=True)

    thread = threading.Thread(target=_run, daemon=True, name="web-dashboard")
    thread.start()
    logger.info("Web dashboard thread started on port %d", port)
