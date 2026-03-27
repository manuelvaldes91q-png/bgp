"""Route change detection and anomaly analysis."""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

from config.settings import Settings
from models.data_models import (
    AlertSeverity,
    ASN,
    RouteAnomaly,
    RouteBaseline,
    RouteSnapshot,
    RouteStatus,
)

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).parent.parent / "data"
BASELINES_FILE = DATA_DIR / "baselines.json"
HISTORY_FILE = DATA_DIR / "route_history.jsonl"


class RouteAnalyzer:
    """Analyzes route snapshots against baselines to detect anomalies."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self._ensure_data_dir()
        self._baselines: dict[str, RouteBaseline] = {}
        self._alert_cooldowns: dict[str, float] = {}
        self._load_baselines()

    def _ensure_data_dir(self) -> None:
        DATA_DIR.mkdir(parents=True, exist_ok=True)

    def _baseline_key(self, provider: str, destination: str) -> str:
        return f"{provider}:{destination}"

    def _load_baselines(self) -> None:
        """Load saved baselines from disk, or initialize from config."""
        if BASELINES_FILE.exists():
            try:
                with open(BASELINES_FILE, "r") as f:
                    data = json.load(f)
                for key, bdata in data.items():
                    as_path = ASN(
                        number=0,
                        name="",
                    )  # placeholder
                    from models.data_models import ASPath

                    hops = [
                        ASN(number=n, name=self.settings.resolve_asn_name(n))
                        for n in bdata.get("expected_as_path_numbers", [])
                    ]

                    self._baselines[key] = RouteBaseline(
                        provider_name=bdata["provider_name"],
                        provider_asn=bdata["provider_asn"],
                        gateway=bdata["gateway"],
                        destination=bdata["destination"],
                        expected_as_path=ASPath(hops=hops),
                        expected_intermediate_asns=bdata.get(
                            "expected_intermediate_asns", []
                        ),
                        expected_first_hop_asn=bdata.get("expected_first_hop_asn", 0),
                        known_transit_asns=bdata.get("known_transit_asns", []),
                        baseline_rtt_avg=bdata.get("baseline_rtt_avg", 0.0),
                    )
                logger.info("Loaded %d baselines from disk", len(self._baselines))
                return
            except (json.JSONDecodeError, KeyError) as e:
                logger.warning("Failed to load baselines: %s, reinitializing", e)

        # Initialize from config
        for provider in self.settings.providers:
            key = self._baseline_key(provider.name, provider.baseline.destination)
            self._baselines[key] = provider.baseline

            # Also create baselines for each destination
            for dest in provider.test_destinations:
                k = self._baseline_key(provider.name, dest)
                if k != key:
                    import copy
                    baseline_copy = copy.deepcopy(provider.baseline)
                    baseline_copy.destination = dest
                    self._baselines[k] = baseline_copy

        self._save_baselines()

    def _save_baselines(self) -> None:
        """Persist baselines to disk."""
        data: dict[str, Any] = {}
        for key, b in self._baselines.items():
            data[key] = {
                "provider_name": b.provider_name,
                "provider_asn": b.provider_asn,
                "gateway": b.gateway,
                "destination": b.destination,
                "expected_as_path_numbers": b.expected_as_path.as_numbers(),
                "expected_intermediate_asns": b.expected_intermediate_asns,
                "expected_first_hop_asn": b.expected_first_hop_asn,
                "known_transit_asns": b.known_transit_asns,
                "baseline_rtt_avg": b.baseline_rtt_avg,
            }

        with open(BASELINES_FILE, "w") as f:
            json.dump(data, f, indent=2)

    def get_baseline(self, provider: str, destination: str) -> RouteBaseline | None:
        key = self._baseline_key(provider, destination)
        return self._baselines.get(key)

    def update_baseline(self, snapshot: RouteSnapshot) -> None:
        """Update baseline with a confirmed-good route snapshot."""
        key = self._baseline_key(snapshot.provider_name, snapshot.destination)
        if key in self._baselines:
            baseline = self._baselines[key]
            baseline.expected_as_path = snapshot.as_path
            baseline.expected_intermediate_asns = [
                asn.number for asn in snapshot.intermediate_asns
            ]
            # Calculate average RTT from non-lost hops
            valid_rtts = [h.rtt_avg for h in snapshot.hops if h.rtt_avg > 0]
            if valid_rtts:
                baseline.baseline_rtt_avg = sum(valid_rtts) / len(valid_rtts)
        else:
            valid_rtts = [h.rtt_avg for h in snapshot.hops if h.rtt_avg > 0]
            baseline = RouteBaseline(
                provider_name=snapshot.provider_name,
                provider_asn=snapshot.provider_asn,
                gateway=snapshot.gateway,
                destination=snapshot.destination,
                expected_as_path=snapshot.as_path,
                expected_intermediate_asns=[
                    asn.number for asn in snapshot.intermediate_asns
                ],
                baseline_rtt_avg=sum(valid_rtts) / len(valid_rtts)
                if valid_rtts
                else 0.0,
            )
            self._baselines[key] = baseline

        self._save_baselines()

    def _check_alert_cooldown(self, provider: str, destination: str) -> bool:
        """Check if alert is within cooldown period."""
        key = self._baseline_key(provider, destination)
        last_alert = self._alert_cooldowns.get(key, 0)
        cooldown = self.settings.general.alert_cooldown_seconds
        now = time.time()
        if now - last_alert < cooldown:
            return False
        self._alert_cooldowns[key] = now
        return True

    def analyze_route(self, snapshot: RouteSnapshot) -> list[RouteAnomaly]:
        """Compare a route snapshot against its baseline. Returns list of anomalies."""
        anomalies: list[RouteAnomaly] = []
        baseline = self.get_baseline(snapshot.provider_name, snapshot.destination)

        if not baseline:
            logger.warning(
                "No baseline for %s -> %s, learning current route",
                snapshot.provider_name,
                snapshot.destination,
            )
            self.update_baseline(snapshot)
            return anomalies

        if snapshot.status == RouteStatus.UNKNOWN or not snapshot.as_path.hops:
            anomalies.append(RouteAnomaly(
                provider_name=snapshot.provider_name,
                destination=snapshot.destination,
                severity=AlertSeverity.CRITICAL,
                description=f"No route to {snapshot.destination} via {snapshot.provider_name}. "
                "The link may be down or routes are not being received.",
                baseline_as_path=str(baseline.expected_as_path),
                current_as_path="NO ROUTE",
            ))
            return anomalies

        if not self._check_alert_cooldown(snapshot.provider_name, snapshot.destination):
            logger.debug(
                "Alert cooldown active for %s -> %s",
                snapshot.provider_name,
                snapshot.destination,
            )
            return anomalies

        current_asns = snapshot.as_path.unique_asns()
        current_asn_numbers = [a.number for a in current_asns]
        baseline_asn_numbers = baseline.expected_as_path.as_numbers()
        known_transit = set(baseline.known_transit_asns)

        # Check 1: Unexpected ASNs in the path
        unexpected: list[ASN] = []
        for asn in current_asns:
            if (
                asn.number not in baseline_asn_numbers
                and asn.number != baseline.provider_asn
                and asn.number != 0
            ):
                # Check if it's a known transit
                if asn.number not in known_transit:
                    unexpected.append(asn)
                # Also check if it's in the suspicious list
                elif asn.number in self.settings.suspicious_transit_asns:
                    unexpected.append(asn)

        if unexpected:
            severity = AlertSeverity.CRITICAL
            # Check if any suspicious ASN is present
            if any(a.number in self.settings.suspicious_transit_asns for a in unexpected):
                severity = AlertSeverity.CRITICAL
            else:
                severity = AlertSeverity.WARNING

            unexpected_str = ", ".join(str(a) for a in unexpected)
            anomalies.append(RouteAnomaly(
                provider_name=snapshot.provider_name,
                destination=snapshot.destination,
                severity=severity,
                description=f"Unexpected ASNs detected in path: {unexpected_str}. "
                f"Traffic may be taking an unintended route.",
                baseline_as_path=str(baseline.expected_as_path),
                current_as_path=str(snapshot.as_path),
                unexpected_asns=unexpected,
            ))

        # Check 2: Missing expected ASNs
        expected_set = set(baseline_asn_numbers) - {baseline.provider_asn}
        current_set = set(a.number for a in current_asns) - {baseline.provider_asn}
        missing_numbers = expected_set - current_set

        if missing_numbers and len(baseline_asn_numbers) > 2:
            missing_asns = [
                ASN(number=n, name=self.settings.resolve_asn_name(n))
                for n in missing_numbers
            ]
            anomalies.append(RouteAnomaly(
                provider_name=snapshot.provider_name,
                destination=snapshot.destination,
                severity=AlertSeverity.WARNING,
                description=f"Expected ASNs missing from path: "
                f"{', '.join(str(a) for a in missing_asns)}.",
                baseline_as_path=str(baseline.expected_as_path),
                current_as_path=str(snapshot.as_path),
                missing_asns=missing_asns,
            ))

        # Check 3: First-hop ASN change
        if (
            len(current_asns) > 1
            and baseline.expected_first_hop_asn > 0
            and current_asns[1].number != baseline.expected_first_hop_asn
        ):
            first_hop_expected = self.settings.resolve_asn_name(
                baseline.expected_first_hop_asn
            )
            first_hop_actual = current_asns[1]
            anomalies.append(RouteAnomaly(
                provider_name=snapshot.provider_name,
                destination=snapshot.destination,
                severity=AlertSeverity.CRITICAL,
                description=f"First hop ASN changed from AS{baseline.expected_first_hop_asn} "
                f"({first_hop_expected}) to {first_hop_actual}. "
                f"Upstream peering may have changed.",
                baseline_as_path=str(baseline.expected_as_path),
                current_as_path=str(snapshot.as_path),
                unexpected_asns=[first_hop_actual]
                if first_hop_actual.number != baseline.expected_first_hop_asn
                else [],
            ))

        # Check 4: Latency increase
        valid_rtts = [h.rtt_avg for h in snapshot.hops if h.rtt_avg > 0]
        if valid_rtts and baseline.baseline_rtt_avg > 0:
            current_avg = sum(valid_rtts) / len(valid_rtts)
            increase_pct = (
                (current_avg - baseline.baseline_rtt_avg) / baseline.baseline_rtt_avg
            ) * 100

            if increase_pct > 50:
                anomalies.append(RouteAnomaly(
                    provider_name=snapshot.provider_name,
                    destination=snapshot.destination,
                    severity=AlertSeverity.WARNING
                    if increase_pct < 100
                    else AlertSeverity.CRITICAL,
                    description=f"Latency increased by {increase_pct:.1f}% "
                    f"(baseline: {baseline.baseline_rtt_avg:.1f}ms, "
                    f"current: {current_avg:.1f}ms).",
                    baseline_as_path=str(baseline.expected_as_path),
                    current_as_path=str(snapshot.as_path),
                    latency_increase_percent=increase_pct,
                ))

        # Check 5: High packet loss in intermediate hops
        high_loss_hops = [h for h in snapshot.hops if h.loss_percent > 20]
        if high_loss_hops:
            loss_info = ", ".join(
                f"Hop {h.hop_number} ({h.ip_address}): {h.loss_percent:.0f}%"
                for h in high_loss_hops[:3]
            )
            anomalies.append(RouteAnomaly(
                provider_name=snapshot.provider_name,
                destination=snapshot.destination,
                severity=AlertSeverity.WARNING,
                description=f"High packet loss detected: {loss_info}",
                baseline_as_path=str(baseline.expected_as_path),
                current_as_path=str(snapshot.as_path),
            ))

        if anomalies:
            logger.warning(
                "Detected %d anomalies for %s -> %s",
                len(anomalies),
                snapshot.provider_name,
                snapshot.destination,
            )

        return anomalies

    def save_history(self, snapshot: RouteSnapshot) -> None:
        """Append route snapshot to history file."""
        record = {
            "timestamp": snapshot.timestamp,
            "provider": snapshot.provider_name,
            "provider_asn": snapshot.provider_asn,
            "destination": snapshot.destination,
            "as_path": str(snapshot.as_path),
            "as_path_numbers": snapshot.as_path.as_numbers(),
            "hop_count": len(snapshot.hops),
            "status": snapshot.status.value,
            "interface": snapshot.physical_interface,
            "vlan": snapshot.vlan_id,
            "hops": [
                {
                    "hop": h.hop_number,
                    "ip": h.ip_address,
                    "asn": h.asn.number if h.asn else 0,
                    "rtt_avg": h.rtt_avg,
                    "loss": h.loss_percent,
                }
                for h in snapshot.hops
            ],
        }

        with open(HISTORY_FILE, "a") as f:
            f.write(json.dumps(record) + "\n")

    def analyze_all(
        self, snapshots: list[RouteSnapshot]
    ) -> list[RouteAnomaly]:
        """Analyze all route snapshots and return all detected anomalies."""
        all_anomalies: list[RouteAnomaly] = []

        for snapshot in snapshots:
            anomalies = self.analyze_route(snapshot)
            all_anomalies.extend(anomalies)

            # Save to history
            self.save_history(snapshot)

            # Update baseline if route is normal
            if not anomalies and snapshot.status == RouteStatus.NORMAL:
                self.update_baseline(snapshot)

        return all_anomalies
