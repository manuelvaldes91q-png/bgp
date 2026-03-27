"""Configuration loader for Ewinet Route Monitor."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from models.data_models import ASN, ASPath, RouteBaseline

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).parent.parent
CONFIG_DIR = Path(__file__).parent
DEFAULT_CONFIG_PATH = CONFIG_DIR / "providers.yaml"


@dataclass
class TelegramConfig:
    bot_token: str = ""
    chat_id: str = ""


@dataclass
class MikroTikConfig:
    host: str = ""
    username: str = "admin"
    password: str = ""
    port: int = 8728
    ssh_port: int = 22
    use_ssl: bool = False


@dataclass
class GeneralConfig:
    poll_interval_seconds: int = 120
    mtr_cycles: int = 10
    mtr_max_hops: int = 30
    alert_cooldown_seconds: int = 300
    log_level: str = "INFO"
    data_retention_days: int = 7
    enable_mikrotik: bool = False
    enable_telegram: bool = False


@dataclass
class DestinationConfig:
    """A test destination for route diagnostics."""
    destination: str
    description: str = ""
    expected_provider: str = ""
    expected_asn: int = 0
    baseline: RouteBaseline | None = None


@dataclass
class Settings:
    general: GeneralConfig
    telegram: TelegramConfig
    mikrotik: MikroTikConfig
    destinations: list[DestinationConfig]
    asn_registry: dict[int, str]
    suspicious_transit_asns: list[int]

    @classmethod
    def load(cls, config_path: str | Path | None = None) -> Settings:
        path = Path(config_path) if config_path else DEFAULT_CONFIG_PATH

        with open(path, "r") as f:
            raw: dict[str, Any] = yaml.safe_load(f)

        # Load env overrides
        env = os.environ

        telegram = TelegramConfig(
            bot_token=env.get("TELEGRAM_BOT_TOKEN", ""),
            chat_id=env.get("TELEGRAM_CHAT_ID", ""),
        )

        mikrotik = MikroTikConfig(
            host=env.get("MIKROTIK_HOST", ""),
            username=env.get("MIKROTIK_USERNAME", "admin"),
            password=env.get("MIKROTIK_PASSWORD", ""),
            port=int(env.get("MIKROTIK_PORT", "8728")),
            ssh_port=int(env.get("MIKROTIK_SSH_PORT", "22")),
            use_ssl=env.get("MIKROTIK_USE_SSL", "false").lower() == "true",
        )

        general_raw = raw.get("general", {})
        general = GeneralConfig(
            poll_interval_seconds=general_raw.get("poll_interval_seconds", 120),
            mtr_cycles=general_raw.get("mtr_cycles", 10),
            mtr_max_hops=general_raw.get("mtr_max_hops", 30),
            alert_cooldown_seconds=general_raw.get("alert_cooldown_seconds", 300),
            log_level=general_raw.get("log_level", "INFO"),
            data_retention_days=general_raw.get("data_retention_days", 7),
            enable_mikrotik=general_raw.get("enable_mikrotik", False),
            enable_telegram=general_raw.get("enable_telegram", False),
        )

        # Parse ASN registry
        asn_registry_raw: dict[str, str] = raw.get("asn_registry", {})
        asn_registry: dict[int, str] = {
            int(k): v for k, v in asn_registry_raw.items()
        }

        # Parse destinations (new format)
        destinations: list[DestinationConfig] = []
        raw_destinations = raw.get("destinations", [])

        for d in raw_destinations:
            dest = cls._parse_destination(d, asn_registry)
            destinations.append(dest)

        # Backward compat: parse old providers format if no destinations
        if not destinations:
            raw_providers = raw.get("providers", [])
            for p in raw_providers:
                for test_dest in p.get("test_destinations", []):
                    baseline_raw = p.get("baseline", {})
                    baseline = cls._parse_baseline(
                        baseline_raw, test_dest, p.get("name", ""), p.get("asn", 0), asn_registry
                    )

                    dest = DestinationConfig(
                        destination=test_dest,
                        description=p.get("description", ""),
                        expected_provider=p.get("name", ""),
                        expected_asn=p.get("asn", 0),
                        baseline=baseline,
                    )
                    destinations.append(dest)

        suspicious = raw.get("suspicious_transit_asns", [])

        settings = cls(
            general=general,
            telegram=telegram,
            mikrotik=mikrotik,
            destinations=destinations,
            asn_registry=asn_registry,
            suspicious_transit_asns=[int(x) for x in suspicious],
        )

        logger.info("Configuration loaded: %d destinations", len(destinations))
        return settings

    @staticmethod
    def _parse_baseline(
        baseline_raw: dict[str, Any],
        destination: str,
        provider_name: str,
        provider_asn: int,
        asn_registry: dict[int, str],
    ) -> RouteBaseline | None:
        if not baseline_raw:
            return None

        expected_as_path_nums = baseline_raw.get("expected_as_path", [])
        known_transit = baseline_raw.get("known_transit_asns", [])

        expected_as_path = ASPath(
            hops=[
                ASN(number=n, name=asn_registry.get(n, ""))
                for n in expected_as_path_nums
            ]
        )

        return RouteBaseline(
            provider_name=provider_name,
            provider_asn=provider_asn,
            gateway="",
            destination=destination,
            expected_as_path=expected_as_path,
            expected_intermediate_asns=expected_as_path_nums[1:-1]
            if len(expected_as_path_nums) > 2
            else [],
            expected_first_hop_asn=baseline_raw.get("expected_first_hop_asn", 0),
            known_transit_asns=known_transit,
            baseline_rtt_avg=baseline_raw.get("baseline_rtt_avg", 0.0),
        )

    @classmethod
    def _parse_destination(
        cls, d: dict[str, Any], asn_registry: dict[int, str]
    ) -> DestinationConfig:
        baseline = cls._parse_baseline(
            d.get("baseline", {}),
            d["destination"],
            d.get("expected_provider", ""),
            d.get("expected_asn", 0),
            asn_registry,
        )

        return DestinationConfig(
            destination=d["destination"],
            description=d.get("description", ""),
            expected_provider=d.get("expected_provider", ""),
            expected_asn=d.get("expected_asn", 0),
            baseline=baseline,
        )

    def resolve_asn_name(self, asn_number: int) -> str:
        return self.asn_registry.get(asn_number, f"AS{asn_number}")
