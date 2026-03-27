"""Route monitoring module using MTR, traceroute, and BGP queries."""

from __future__ import annotations

import json
import logging
import re
import socket
import subprocess
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from config.settings import Settings
from models.data_models import ASN, ASPath, RouteHop, RouteSnapshot, RouteStatus

logger = logging.getLogger(__name__)

# International transit ASNs (outside Venezuela)
INTERNATIONAL_TRANSIT_ASNS = {
    3356, 1299, 174, 6453, 3257, 2914, 6939, 1273, 9002, 3491,
    5511, 6762, 7018, 3320, 286, 3216, 12956, 4230, 15169, 13335,
    20940, 8075, 16509, 32934, 20473, 14061, 54113, 7922, 22773,
    701, 209, 396982, 19527, 8068, 16265, 49544, 24940, 15133,
    3209, 2119, 2603, 13030, 3333, 5408, 6830,
}

# Venezuelan ASNs
VENEZUELAN_ASNS = {
    10929, 15135, 263220, 269693, 264628, 271910, 263702,
    271886, 267798, 271949, 27984, 273087, 272058, 264681, 272800, 266793,
}

# Public IP detection services
IP_DETECT_SERVICES = [
    "https://api.ipify.org",
    "https://ifconfig.me/ip",
    "https://icanhazip.com",
]


def detect_public_ip() -> str:
    """Detect current public IP address."""
    for service in IP_DETECT_SERVICES:
        try:
            req = urllib.request.Request(service, headers={"User-Agent": "ewinet-monitor/1.0"})
            with urllib.request.urlopen(req, timeout=5) as response:
                ip = response.read().decode("utf-8").strip()
                socket.inet_aton(ip)
                return ip
        except Exception:
            continue
    return ""


def lookup_asn_hackertarget(ip: str) -> dict[str, str] | None:
    """Lookup ASN info via hackertarget.com API."""
    if ip == "*" or ip.startswith("10.") or ip.startswith("192.168.") or ip.startswith("172.16."):
        return None
    try:
        url = f"https://api.hackertarget.com/aslookup/?q={ip}&output=json"
        req = urllib.request.Request(url, headers={"User-Agent": "ewinet-monitor/1.0"})
        with urllib.request.urlopen(req, timeout=6) as response:
            data = json.loads(response.read().decode("utf-8"))
            if "asn" in data:
                return {
                    "asn": data.get("asn", ""),
                    "name": data.get("asn_name", ""),
                    "prefix": data.get("asn_range", ""),
                }
    except Exception:
        pass
    return None


def lookup_asn_batch_cymru(ips: list[str]) -> dict[str, tuple[int, str]]:
    """Batch ASN lookup via whois.cymru.com (much faster than individual queries)."""
    results: dict[str, tuple[int, str]] = {}
    if not ips:
        return results

    # Filter private IPs
    public_ips = [ip for ip in ips if not (
        ip == "*" or ip.startswith("10.") or ip.startswith("192.168.") or ip.startswith("172.16.")
    )]
    if not public_ips:
        return results

    try:
        query = "\n".join(public_ips)
        result = subprocess.run(
            ["whois", "-h", "whois.cymru.com", f" -v {query}"],
            capture_output=True, text=True, timeout=30,
        )
        for line in result.stdout.strip().split("\n"):
            if "|" in line and "BGP" not in line and "AS" not in line[:3]:
                parts = [p.strip() for p in line.split("|")]
                if len(parts) >= 3:
                    try:
                        asn_num = int(parts[0].strip())
                        ip = parts[1].strip()
                        asn_name = parts[2].strip()
                        results[ip] = (asn_num, asn_name)
                    except ValueError:
                        continue
    except Exception:
        pass

    return results


def lookup_bgp_routes(asn: int) -> list[str]:
    """Lookup BGP prefixes for an ASN via whois.radb.net."""
    prefixes: list[str] = []
    try:
        result = subprocess.run(
            ["whois", "-h", "whois.radb.net", f"-i origin AS{asn}"],
            capture_output=True, text=True, timeout=10,
        )
        for line in result.stdout.split("\n"):
            line = line.strip()
            if line.startswith("route:") or line.startswith("route6:"):
                prefix = line.split(":", 1)[1].strip()
                if prefix:
                    prefixes.append(prefix)
    except Exception:
        pass
    return prefixes[:10]


def is_international_hop(asn_number: int) -> bool:
    """Check if ASN is an international transit (not Venezuelan)."""
    if asn_number in VENEZUELAN_ASNS:
        return False
    if asn_number in INTERNATIONAL_TRANSIT_ASNS:
        return True
    # Heuristic: if not in our known lists, check if it's a well-known transit
    return asn_number > 1000 and asn_number not in VENEZUELAN_ASNS


class RouteMonitor:
    """Monitors routes using MTR and whois-based ASN lookup."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self._asn_name_cache: dict[int, str] = {}
        self._asn_ip_cache: dict[str, ASN] = {}

    def run_mtr_json(self, destination: str, cycles: int = 5, max_hops: int = 20) -> dict[str, Any] | None:
        """Execute MTR in JSON mode - fast mode with fewer cycles."""
        cmd = [
            "mtr", "--json",
            "--report",
            "--report-cycles", str(cycles),
            "--max-ttl", str(max_hops),
            "--no-dns",
            destination,
        ]

        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=60,
            )
            if result.returncode != 0:
                return None
            return json.loads(result.stdout)
        except Exception:
            return None

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

        return RouteHop(
            hop_number=int(match.group(1)),
            ip_address=match.group(2),
            rtt_min=float(match.group(3)),
            rtt_avg=float(match.group(4)),
            rtt_max=float(match.group(5)),
        )

    def _lookup_asn_for_ip(self, ip: str) -> ASN | None:
        """Lookup ASN for a single IP - hackertarget first, then cymru, then whois."""
        if ip == "*" or ip.startswith("10.") or ip.startswith("192.168.") or ip.startswith("172.16."):
            return None

        # Check cache
        if ip in self._asn_ip_cache:
            return self._asn_ip_cache[ip]

        # Try hackertarget first (fastest, best names)
        ht = lookup_asn_hackertarget(ip)
        if ht:
            try:
                asn_num = int(ht["asn"])
                asn_name = ht["name"]
                asn = ASN(number=asn_num, name=asn_name)
                self._asn_ip_cache[ip] = asn
                self._asn_name_cache[asn_num] = asn_name
                return asn
            except (ValueError, KeyError):
                pass

        # Fallback to whois.cymru
        try:
            result = subprocess.run(
                ["whois", "-h", "whois.cymru.com", f" -v {ip}"],
                capture_output=True, text=True, timeout=8,
            )
            for line in result.stdout.strip().split("\n"):
                if "|" in line and "BGP" not in line:
                    parts = [p.strip() for p in line.split("|")]
                    if len(parts) >= 3:
                        try:
                            asn_num = int(parts[0].strip())
                            asn_name = parts[2].strip()
                            asn = ASN(number=asn_num, name=asn_name)
                            self._asn_ip_cache[ip] = asn
                            self._asn_name_cache[asn_num] = asn_name
                            return asn
                        except ValueError:
                            continue
        except Exception:
            pass

        return None

    def enrich_hops_with_asn(self, hops: list[RouteHop]) -> list[RouteHop]:
        """Add ASN info to each hop using parallel lookups."""
        ip_to_hop: dict[str, RouteHop] = {}
        for hop in hops:
            if hop.ip_address != "*" and hop.ip_address not in ip_to_hop:
                ip_to_hop[hop.ip_address] = hop

        # Parallel ASN lookups
        with ThreadPoolExecutor(max_workers=8) as executor:
            futures = {
                executor.submit(self._lookup_asn_for_ip, ip): ip
                for ip in ip_to_hop
            }
            for future in as_completed(futures, timeout=60):
                ip = futures[future]
                try:
                    asn = future.result()
                    if asn:
                        ip_to_hop[ip].asn = asn
                except Exception:
                    pass

        # Resolve missing names from cache
        for hop in hops:
            if hop.asn and not hop.asn.name and hop.asn.number in self._asn_name_cache:
                hop.asn.name = self._asn_name_cache[hop.asn.number]

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

    def probe_route(self, destination: str, provider_name: str = "", provider_asn: int = 0) -> RouteSnapshot:
        """Full route probe - fast mode."""
        logger.info("Probing route to %s", destination)

        # Run MTR with fewer cycles for speed
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
                    hops.append(RouteHop(
                        hop_number=h.get("count", len(hops) + 1),
                        ip_address=h.get("host", {}).get("ip", "*"),
                        rtt_avg=h.get("avg", 0.0),
                        rtt_min=h.get("min", 0.0),
                        rtt_max=h.get("max", 0.0),
                        loss_percent=h.get("loss", 0.0),
                    ))

        if not hops:
            logger.warning("MTR failed for %s", destination)

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

        # Auto-detect provider from first ASN
        if not provider_name and as_path.hops:
            first = as_path.hops[0]
            provider_name = first.name or f"AS{first.number}"
            provider_asn = first.number

        return RouteSnapshot(
            provider_name=provider_name,
            provider_asn=provider_asn,
            gateway="",
            destination=destination,
            as_path=as_path,
            hops=hops,
            status=status,
        )

    def probe_all_providers(self) -> list[RouteSnapshot]:
        """Probe all destinations in parallel for speed."""
        snapshots: list[RouteSnapshot] = []

        with ThreadPoolExecutor(max_workers=len(self.settings.destinations) or 1) as executor:
            futures = {
                executor.submit(
                    self.probe_route,
                    destination=dest_cfg.destination,
                    provider_name=dest_cfg.expected_provider or "",
                    provider_asn=dest_cfg.expected_asn or 0,
                ): dest_cfg.destination
                for dest_cfg in self.settings.destinations
            }
            for future in as_completed(futures, timeout=300):
                try:
                    snapshot = future.result()
                    snapshots.append(snapshot)
                except Exception as e:
                    dest = futures[future]
                    logger.error("Probe failed for %s: %s", dest, e)

        # Sort by destination order from config
        dest_order = {d.destination: i for i, d in enumerate(self.settings.destinations)}
        snapshots.sort(key=lambda s: dest_order.get(s.destination, 999))

        return snapshots
