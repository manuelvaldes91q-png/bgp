"""Data models for Ewinet Route Monitor."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class AlertSeverity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class RouteStatus(str, Enum):
    NORMAL = "normal"
    DEGRADED = "degraded"
    ANOMALY = "anomaly"
    UNKNOWN = "unknown"


@dataclass
class ASN:
    """Represents an Autonomous System hop."""
    number: int
    name: str = ""
    country: str = ""
    registry: str = ""

    def __str__(self) -> str:
        if self.name:
            return f"AS{self.number} ({self.name})"
        return f"AS{self.number}"

    def __eq__(self, other: object) -> bool:
        if isinstance(other, ASN):
            return self.number == other.number
        return NotImplemented

    def __hash__(self) -> int:
        return hash(self.number)


@dataclass
class RouteHop:
    """Single hop in a traceroute/mtr path."""
    hop_number: int
    ip_address: str
    hostname: str = ""
    asn: Optional[ASN] = None
    rtt_avg: float = 0.0
    rtt_min: float = 0.0
    rtt_max: float = 0.0
    loss_percent: float = 0.0


@dataclass
class ASPath:
    """Complete AS path from source to destination."""
    hops: list[ASN] = field(default_factory=list)

    def as_numbers(self) -> list[int]:
        return [asn.number for asn in self.hops]

    def unique_asns(self) -> list[ASN]:
        seen: set[int] = set()
        result: list[ASN] = []
        for asn in self.hops:
            if asn.number not in seen:
                seen.add(asn.number)
                result.append(asn)
        return result

    def __str__(self) -> str:
        return " -> ".join(str(asn) for asn in self.unique_asns())


@dataclass
class RouteSnapshot:
    """Complete route snapshot for a provider to a destination."""
    provider_name: str
    provider_asn: int
    gateway: str
    destination: str
    timestamp: float = field(default_factory=time.time)
    as_path: ASPath = field(default_factory=ASPath)
    physical_interface: str = ""
    vlan_id: str = ""
    hops: list[RouteHop] = field(default_factory=list)
    status: RouteStatus = RouteStatus.UNKNOWN

    @property
    def intermediate_asns(self) -> list[ASN]:
        """ASNs between provider's ASN and destination (excluding first and last)."""
        unique = self.as_path.unique_asns()
        if len(unique) <= 2:
            return []
        return unique[1:-1]


@dataclass
class PerformanceMetrics:
    """Performance metrics for a route probe."""
    provider_name: str
    destination: str
    timestamp: float = field(default_factory=time.time)
    rtt_min: float = 0.0
    rtt_avg: float = 0.0
    rtt_max: float = 0.0
    rtt_stddev: float = 0.0
    packet_loss: float = 0.0
    packets_sent: int = 0
    packets_received: int = 0


@dataclass
class RouteBaseline:
    """Expected route baseline for a provider."""
    provider_name: str
    provider_asn: int
    gateway: str
    destination: str
    expected_as_path: ASPath = field(default_factory=ASPath)
    expected_intermediate_asns: list[int] = field(default_factory=list)
    expected_first_hop_asn: int = 0
    known_transit_asns: list[int] = field(default_factory=list)
    baseline_rtt_avg: float = 0.0


@dataclass
class RouteAnomaly:
    """Detected route anomaly."""
    provider_name: str
    destination: str
    timestamp: float = field(default_factory=time.time)
    severity: AlertSeverity = AlertSeverity.WARNING
    description: str = ""
    baseline_as_path: str = ""
    current_as_path: str = ""
    unexpected_asns: list[ASN] = field(default_factory=list)
    missing_asns: list[ASN] = field(default_factory=list)
    latency_increase_percent: float = 0.0


@dataclass
class Alert:
    """Alert to be sent via Telegram."""
    anomaly: RouteAnomaly
    message: str = ""
    sent: bool = False
    sent_at: float = 0.0

    def format_telegram_message(self) -> str:
        a = self.anomaly
        severity_icon = {
            AlertSeverity.INFO: "ℹ️",
            AlertSeverity.WARNING: "⚠️",
            AlertSeverity.CRITICAL: "🚨",
        }.get(a.severity, "⚠️")

        lines = [
            f"{severity_icon} *Ewinet Route Alert*",
            f"",
            f"*Proveedor:* {a.provider_name}",
            f"*Destino:* {a.destination}",
            f"*Severidad:* {a.severity.value.upper()}",
            f"",
            f"*Descripción:* {a.description}",
            f"",
            f"*Ruta Esperada:*",
            f"`{a.baseline_as_path}`",
            f"",
            f"*Ruta Actual:*",
            f"`{a.current_as_path}`",
        ]

        if a.unexpected_asns:
            unexpected = ", ".join(str(asn) for asn in a.unexpected_asns)
            lines.append(f"\n*ASNs Inesperados:* `{unexpected}`")

        if a.missing_asns:
            missing = ", ".join(str(asn) for asn in a.missing_asns)
            lines.append(f"*ASNs Faltantes:* `{missing}`")

        if a.latency_increase_percent > 0:
            lines.append(
                f"\n*Aumento de Latencia:* +{a.latency_increase_percent:.1f}%"
            )

        lines.append(f"\n⏰ {time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())}")
        return "\n".join(lines)


@dataclass
class MikroTikInterface:
    """MikroTik interface information."""
    name: str
    type: str
    comment: str = ""
    running: bool = False
    vlan_id: str = ""
    mac_address: str = ""
