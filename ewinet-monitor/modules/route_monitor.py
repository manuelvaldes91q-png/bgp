"""Route monitoring module using MTR, traceroute, and BGP queries."""

from __future__ import annotations

import json
import logging
import re
import socket
import subprocess
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Optional

from config.settings import Settings
from models.data_models import ASN, ASPath, RouteHop, RouteSnapshot, RouteStatus

logger = logging.getLogger(__name__)

# Public IP detection services
IP_DETECT_SERVICES = [
    "https://api.ipify.org",
    "https://ifconfig.me/ip",
    "https://icanhazip.com",
    "https://checkip.amazonaws.com",
]


def detect_public_ip() -> str:
    """Detect current public IP address."""
    for service in IP_DETECT_SERVICES:
        try:
            req = urllib.request.Request(service, headers={"User-Agent": "ewinet-monitor/1.0"})
            with urllib.request.urlopen(req, timeout=10) as response:
                ip = response.read().decode("utf-8").strip()
                # Validate it's an IP
                socket.inet_aton(ip)
                return ip
        except Exception:
            continue
    return ""


def resolve_hostname(ip: str) -> str:
    """Reverse DNS lookup with timeout."""
    try:
        hostname, _, _ = socket.gethostbyaddr(ip)
        return hostname
    except (socket.herror, socket.gaierror, OSError):
        return ""


def infer_asn_from_hostname(hostname: str) -> tuple[int, str]:
    """Try to extract ASN information from hostname patterns."""
    patterns = [
        r"as(\d+)",
        r"^(\d{5,6})\.",
    ]
    for pattern in patterns:
        match = re.search(pattern, hostname, re.IGNORECASE)
        if match:
            return int(match.group(1)), hostname
    return 0, ""


class RouteMonitor:
    """Monitors routes using MTR and whois-based ASN lookup."""

    def __init__(self, settings: Settings):
        self.settings = settings

    def run_mtr_json(self, destination: str, source_ip: str = "",
                     cycles: int = 10, max_hops: int = 30) -> dict[str, Any] | None:
        """Execute MTR in JSON mode and return parsed output."""
        cmd = [
            "mtr", "--json",
            "--report",
            "--report-cycles", str(cycles),
            "--max-ttl", str(max_hops),
            "--no-dns",
            destination,
        ]
        if source_ip:
            cmd.insert(4, "--address")
            cmd.insert(5, source_ip)

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=120,
            )
            if result.returncode != 0:
                logger.error("MTR failed for %s: %s", destination, result.stderr)
                return None
            return json.loads(result.stdout)
        except subprocess.TimeoutExpired:
            logger.error("MTR timed out for %s", destination)
            return None
        except json.JSONDecodeError:
            logger.error("Failed to parse MTR JSON for %s", destination)
            return None
        except FileNotFoundError:
            logger.error("mtr not found. Install with: apt install mtr-tiny")
            return None

    def run_traceroute_asn(self, destination: str, source_ip: str = "",
                           max_hops: int = 30) -> list[RouteHop]:
        """Execute traceroute with ASN info via whois lookups."""
        cmd = ["traceroute", "-n", "-m", str(max_hops), "-w", "2", destination]
        if source_ip:
            cmd.extend(["-s", source_ip])

        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=180,
            )
            if result.returncode != 0:
                logger.error("Traceroute failed: %s", result.stderr)
                return []

            hops: list[RouteHop] = []
            for line in result.stdout.strip().split("\n")[1:]:
                hop = self._parse_traceroute_line(line)
                if hop:
                    hops.append(hop)
            return hops
        except subprocess.TimeoutExpired:
            logger.error("Traceroute timed out for %s", destination)
            return []
        except FileNotFoundError:
            logger.error("traceroute not found. Install with: apt install traceroute")
            return []

    def _parse_traceroute_line(self, line: str) -> RouteHop | None:
        """Parse a single traceroute output line."""
        match = re.match(
            r"\s*(\d+)\s+(\S+)\s+([\d.]+)\s+ms\s+([\d.]+)\s+ms\s+([\d.]+)\s+ms",
            line,
        )
        if not match:
            match_timeout = re.match(r"\s*(\d+)\s+\*", line)
            if match_timeout:
                return RouteHop(
                    hop_number=int(match_timeout.group(1)),
                    ip_address="*",
                    loss_percent=100.0,
                )
            return None

        hop_num = int(match.group(1))
        ip = match.group(2)
        rtt_min = float(match.group(3))
        rtt_avg = float(match.group(4))
        rtt_max = float(match.group(5))

        return RouteHop(
            hop_number=hop_num,
            ip_address=ip,
            rtt_min=rtt_min,
            rtt_avg=rtt_avg,
            rtt_max=rtt_max,
        )

    def _lookup_asn(self, ip: str) -> ASN | None:
        """Lookup ASN for an IP using whois."""
        if ip == "*" or ip.startswith("10.") or ip.startswith("192.168.") or ip.startswith("172.16."):
            return None

        try:
            result = subprocess.run(
                ["whois", "-h", "whois.cymru.com", f" -v {ip}"],
                capture_output=True, text=True, timeout=15,
            )
            if result.returncode != 0:
                return self._lookup_asn_radb(ip)

            lines = result.stdout.strip().split("\n")
            for line in lines:
                if "BGP" in line or "|" in line:
                    parts = [p.strip() for p in line.split("|")]
                    if len(parts) >= 3:
                        try:
                            asn_num = int(parts[0].strip())
                            asn_name = parts[2].strip() if len(parts) > 2 else ""
                            return ASN(number=asn_num, name=asn_name)
                        except ValueError:
                            continue

            return self._lookup_asn_radb(ip)

        except (subprocess.TimeoutExpired, FileNotFoundError):
            return self._lookup_asn_radb(ip)

    def _lookup_asn_radb(self, ip: str) -> ASN | None:
        """Fallback ASN lookup using RADB whois."""
        try:
            result = subprocess.run(
                ["whois", "-h", "whois.radb.net", f"origin {ip}"],
                capture_output=True, text=True, timeout=15,
            )
            for line in result.stdout.split("\n"):
                if line.startswith("origin:"):
                    match = re.search(r"AS(\d+)", line, re.IGNORECASE)
                    if match:
                        return ASN(number=int(match.group(1)))
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass
        return None

    def enrich_hops_with_asn(self, hops: list[RouteHop]) -> list[RouteHop]:
        """Add ASN info to each hop using whois lookups."""
        ip_to_hop: dict[str, RouteHop] = {}
        for hop in hops:
            if hop.ip_address != "*" and hop.ip_address not in ip_to_hop:
                ip_to_hop[hop.ip_address] = hop

        with ThreadPoolExecutor(max_workers=5) as executor:
            futures = {
                executor.submit(self._lookup_asn, ip): ip
                for ip in ip_to_hop
            }
            for future in as_completed(futures, timeout=120):
                ip = futures[future]
                try:
                    asn = future.result()
                    if asn:
                        ip_to_hop[ip].asn = asn
                except Exception:
                    logger.debug("ASN lookup failed for %s", ip)

        return hops

    def build_as_path(self, hops: list[RouteHop]) -> ASPath:
        """Build AS path from enriched hops."""
        asns: list[ASN] = []
        seen: set[int] = set()
        for hop in hops:
            if hop.asn and hop.asn.number not in seen:
                seen.add(hop.asn.number)
                asns.append(hop.asn)
        return ASPath(hops=asns)

    def probe_route(
        self,
        destination: str,
        provider_name: str = "",
        provider_asn: int = 0,
    ) -> RouteSnapshot:
        """Full route probe: MTR + ASN enrichment -> RouteSnapshot."""
        logger.info("Probing route to %s", destination)

        # Run MTR
        mtr_data = self.run_mtr_json(
            destination,
            cycles=self.settings.general.mtr_cycles,
            max_hops=self.settings.general.mtr_max_hops,
        )

        hops: list[RouteHop] = []

        if mtr_data:
            report = mtr_data.get("report", {})
            mtr_hops = report.get("hops", [])
            for h in mtr_hops:
                if isinstance(h, dict):
                    ip = h.get("host", {}).get("ip", "*")
                    loss = h.get("loss", 0.0)
                    rtt_avg = h.get("avg", 0.0)
                    rtt_min = h.get("min", 0.0)
                    rtt_max = h.get("max", 0.0)
                    hop_num = h.get("count", len(hops) + 1)
                else:
                    continue

                hops.append(RouteHop(
                    hop_number=hop_num,
                    ip_address=ip,
                    rtt_avg=rtt_avg,
                    rtt_min=rtt_min,
                    rtt_max=rtt_max,
                    loss_percent=loss,
                ))

        # Fallback to traceroute if MTR failed
        if not hops:
            logger.warning("MTR failed, falling back to traceroute for %s", destination)
            hops = self.run_traceroute_asn(destination)

        # Enrich with ASN data
        hops = self.enrich_hops_with_asn(hops)

        # Build AS path
        as_path = self.build_as_path(hops)

        # Determine status
        status = RouteStatus.NORMAL
        if not as_path.hops:
            status = RouteStatus.UNKNOWN
        elif any(h.loss_percent > 50 for h in hops):
            status = RouteStatus.DEGRADED

        # Auto-detect provider from first non-private ASN if not specified
        if not provider_name and as_path.hops:
            first_public_asn = as_path.hops[0]
            provider_name = first_public_asn.name or f"AS{first_public_asn.number}"
            provider_asn = first_public_asn.number

        snapshot = RouteSnapshot(
            provider_name=provider_name,
            provider_asn=provider_asn,
            gateway="",
            destination=destination,
            as_path=as_path,
            hops=hops,
            status=status,
        )

        logger.info(
            "Route to %s: %s (%d hops, %d ASNs)",
            destination, str(as_path), len(hops), len(as_path.hops),
        )

        return snapshot

    def probe_all_providers(self) -> list[RouteSnapshot]:
        """Probe all configured destinations from local PC."""
        snapshots: list[RouteSnapshot] = []

        for dest_config in self.settings.destinations:
            snapshot = self.probe_route(
                destination=dest_config.destination,
                provider_name=dest_config.expected_provider or "",
                provider_asn=dest_config.expected_asn or 0,
            )
            snapshots.append(snapshot)

        return snapshots
