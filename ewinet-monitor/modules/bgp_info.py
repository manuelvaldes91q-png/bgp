"""BGP information module using bgpview.io API."""

from __future__ import annotations

import json
import logging
import urllib.request
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class BGPPrefix:
    prefix: str
    name: str = ""
    description: str = ""
    country: str = ""
    rpki_status: str = ""


@dataclass
class BGPPeer:
    asn: int
    name: str
    description: str = ""
    country: str = ""
    ipv4_prefixes: int = 0
    ipv6_prefixes: int = 0


@dataclass
class ASNInfo:
    asn: int
    name: str = ""
    description: str = ""
    country: str = ""
    email: str = ""
    prefixes_v4: int = 0
    prefixes_v6: int = 0
    prefixes_announced_v4: int = 0
    prefixes_announced_v6: int = 0
    peers_count: int = 0
    upstreams_count: int = 0
    downstreams_count: int = 0
    prefixes: list[BGPPrefix] = None
    peers: list[BGPPeer] = None
    upstreams: list[BGPPeer] = None
    downstreams: list[BGPPeer] = None

    def __post_init__(self):
        if self.prefixes is None:
            self.prefixes = []
        if self.peers is None:
            self.peers = []
        if self.upstreams is None:
            self.upstreams = []
        if self.downstreams is None:
            self.downstreams = []


class BGPInfo:
    """Fetches BGP information from bgpview.io API."""

    # Cache to avoid repeated lookups
    _cache: dict[int, ASNInfo] = {}

    @classmethod
    def get_asn_info(cls, asn: int) -> ASNInfo | None:
        """Get full ASN information including prefixes, peers, upstreams."""
        if asn in cls._cache:
            return cls._cache[asn]

        info = cls._fetch_asn_overview(asn)
        if not info:
            return None

        # Fetch additional details
        cls._fetch_asn_prefixes(info)
        cls._fetch_asn_peers(info)
        cls._fetch_asn_upstreams(info)

        cls._cache[asn] = info
        return info

    @classmethod
    def _api_request(cls, endpoint: str) -> dict[str, Any] | None:
        """Make a request to bgpview.io API."""
        try:
            url = f"https://api.bgpview.io{endpoint}"
            req = urllib.request.Request(url, headers={
                "User-Agent": "ewinet-monitor/1.0",
                "Accept": "application/json",
            })
            with urllib.request.urlopen(req, timeout=10) as response:
                data = json.loads(response.read().decode("utf-8"))
                if data.get("status") == "ok":
                    return data.get("data")
        except Exception as e:
            logger.debug("BGP API request failed for %s: %s", endpoint, e)
        return None

    @classmethod
    def _fetch_asn_overview(cls, asn: int) -> ASNInfo | None:
        """Fetch basic ASN info."""
        data = cls._api_request(f"/asn/{asn}")
        if not data:
            return None

        info = ASNInfo(
            asn=asn,
            name=data.get("name", ""),
            description=data.get("description_short", "") or data.get("description", ""),
            country=data.get("country_code", ""),
            email=data.get("email_contacts", [""])[0] if data.get("email_contacts") else "",
            prefixes_v4=data.get("ipv4_prefixes_count", 0),
            prefixes_v6=data.get("ipv6_prefixes_count", 0),
            prefixes_announced_v4=data.get("ipv4_prefixes_announced_count", 0) or data.get("ipv4_prefixes_count", 0),
            prefixes_announced_v6=data.get("ipv6_prefixes_announced_count", 0) or data.get("ipv6_prefixes_count", 0),
            peers_count=data.get("peers_count", 0),
            upstreams_count=data.get("upstreams_count", 0),
            downstreams_count=data.get("downstreams_count", 0),
        )

        # Also get prefixes from overview
        for p in data.get("ipv4_prefixes", []):
            info.prefixes.append(BGPPrefix(
                prefix=p.get("prefix", ""),
                name=p.get("name", ""),
                country=p.get("country_code", ""),
                description=p.get("description", ""),
            ))

        return info

    @classmethod
    def _fetch_asn_prefixes(cls, info: ASNInfo) -> None:
        """Fetch prefixes announced by ASN."""
        data = cls._api_request(f"/asn/{info.asn}/prefixes")
        if not data:
            return

        for p in data.get("ipv4_prefixes", []):
            existing = {bp.prefix for bp in info.prefixes}
            prefix = p.get("prefix", "")
            if prefix and prefix not in existing:
                info.prefixes.append(BGPPrefix(
                    prefix=prefix,
                    name=p.get("name", ""),
                    country=p.get("country_code", ""),
                    description=p.get("description", ""),
                ))

    @classmethod
    def _fetch_asn_peers(cls, info: ASNInfo) -> None:
        """Fetch BGP peers."""
        data = cls._api_request(f"/asn/{info.asn}/peers")
        if not data:
            return

        for p in data.get("ipv4_peers", []):
            info.peers.append(BGPPeer(
                asn=p.get("asn", 0),
                name=p.get("name", ""),
                description=p.get("description", ""),
                country=p.get("country_code", ""),
                ipv4_prefixes=p.get("ipv4_prefixes_count", 0),
                ipv6_prefixes=p.get("ipv6_prefixes_count", 0),
            ))

    @classmethod
    def _fetch_asn_upstreams(cls, info: ASNInfo) -> None:
        """Fetch upstream providers."""
        data = cls._api_request(f"/asn/{info.asn}/upstreams")
        if not data:
            return

        for p in data.get("ipv4_upstreams", []):
            info.upstreams.append(BGPPeer(
                asn=p.get("asn", 0),
                name=p.get("name", ""),
                description=p.get("description", ""),
                country=p.get("country_code", ""),
                ipv4_prefixes=p.get("ipv4_prefixes_count", 0),
            ))

    @classmethod
    def get_prefix_info(cls, prefix: str) -> dict[str, Any] | None:
        """Get information about a specific prefix."""
        return cls._api_request(f"/prefix/{prefix}")

    @classmethod
    def lookup_ip(cls, ip: str) -> dict[str, Any] | None:
        """Lookup which ASN/prefix an IP belongs to."""
        data = cls._api_request(f"/ip/{ip}")
        if not data:
            return None

        result = {
            "ip": ip,
            "prefixes": [],
        }

        for p in data.get("prefixes", []):
            result["prefixes"].append({
                "prefix": p.get("prefix", ""),
                "name": p.get("name", ""),
                "description": p.get("description", ""),
                "country_code": p.get("country_code", ""),
                "asn": p.get("asn", {}).get("asn", 0),
                "asn_name": p.get("asn", {}).get("name", ""),
                "asn_description": p.get("asn", {}).get("description", ""),
            })

        return result

    @classmethod
    def clear_cache(cls) -> None:
        cls._cache.clear()
