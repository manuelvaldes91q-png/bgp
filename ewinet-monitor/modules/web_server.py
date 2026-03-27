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

from flask import Flask, jsonify, render_template, request

from config.settings import Settings
from models.data_models import (
    AlertSeverity,
    PerformanceMetrics,
    RouteAnomaly,
    RouteSnapshot,
    RouteStatus,
)

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
}


def create_app(settings: Settings) -> Flask:
    """Create and configure the Flask app."""
    template_dir = Path(__file__).parent / "templates"
    static_dir = Path(__file__).parent / "static"

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
            "providers_count": len(settings.providers),
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
        providers = []
        for p in settings.providers:
            providers.append({
                "name": p.name,
                "asn": p.asn,
                "gateway": p.gateway,
                "interface": p.local_interface,
                "vlan_id": p.vlan_id,
                "destinations": p.test_destinations,
                "description": p.description,
                "baseline": {
                    "expected_as_path": p.baseline.expected_as_path.as_numbers(),
                    "expected_as_path_str": str(p.baseline.expected_as_path),
                    "known_transit_asns": p.baseline.known_transit_asns,
                    "expected_first_hop_asn": p.baseline.expected_first_hop_asn,
                    "baseline_rtt_avg": p.baseline.baseline_rtt_avg,
                },
            })
        return jsonify(providers)

    @app.route("/api/history")
    def api_history() -> Any:
        """Read recent history from the JSONL file."""
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

    return app


def _snapshot_to_dict(s: RouteSnapshot) -> dict[str, Any]:
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
    snapshots: list[RouteSnapshot] | None = None,
    anomalies: list[RouteAnomaly] | None = None,
    performance: list[PerformanceMetrics] | None = None,
    cycle_count: int | None = None,
    status: str | None = None,
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
    _state["last_update"] = time.time()

    # Persist alerts
    if anomalies:
        _save_alerts(anomalies)


def _save_alerts(anomalies: list[RouteAnomaly]) -> None:
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

    # Keep last 500 alerts
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
