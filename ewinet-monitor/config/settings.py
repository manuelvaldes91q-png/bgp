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
    poll_interval_seconds: int = 300
    mtr_cycles: int = 10
    mtr_max_hops: int = 30
    alert_cooldown_seconds: int = 600
    log_level: str = "INFO"
    data_retention_days: int = 30
    enable_mikrotik: bool = True
    enable_telegram: bool = True


@dataclass
class ProviderConfig:
    name: str
    asn: int
    gateway: str
    local_interface: str
    vlan_id: str
    test_destinations: list[str]
    baseline: RouteBaseline
    description: str = ""


@dataclass
class Settings:
    general: GeneralConfig
    telegram: TelegramConfig
    mikrotik: MikroTikConfig
    providers: list[ProviderConfig]
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
            poll_interval_seconds=general_raw.get("poll_interval_seconds", 300),
            mtr_cycles=general_raw.get("mtr_cycles", 10),
            mtr_max_hops=general_raw.get("mtr_max_hops", 30),
            alert_cooldown_seconds=general_raw.get("alert_cooldown_seconds", 600),
            log_level=general_raw.get("log_level", "INFO"),
            data_retention_days=general_raw.get("data_retention_days", 30),
            enable_mikrotik=general_raw.get("enable_mikrotik", True),
            enable_telegram=general_raw.get("enable_telegram", True),
        )

        # Parse providers
        providers: list[ProviderConfig] = []
        raw_providers = raw.get("providers", [])
        asn_registry_raw: dict[str, str] = raw.get("asn_registry", {})

        # Convert string keys to int
        asn_registry: dict[int, str] = {
            int(k): v for k, v in asn_registry_raw.items()
        }

        for p in raw_providers:
            baseline_raw = p.get("baseline", {})
            expected_as_path_nums = baseline_raw.get("expected_as_path", [])
            known_transit = baseline_raw.get("known_transit_asns", [])

            expected_as_path = ASPath(
                hops=[
                    ASN(number=n, name=asn_registry.get(n, ""))
                    for n in expected_as_path_nums
                ]
            )

            baseline = RouteBaseline(
                provider_name=p["name"],
                provider_asn=p["asn"],
                gateway=p.get("gateway", ""),
                destination=p.get("test_destinations", [""])[0],
                expected_as_path=expected_as_path,
                expected_intermediate_asns=expected_as_path_nums[1:-1]
                if len(expected_as_path_nums) > 2
                else [],
                expected_first_hop_asn=baseline_raw.get("expected_first_hop_asn", 0),
                known_transit_asns=known_transit,
                baseline_rtt_avg=baseline_raw.get("baseline_rtt_avg", 0.0),
            )

            provider = ProviderConfig(
                name=p["name"],
                asn=p["asn"],
                gateway=p.get("gateway", ""),
                local_interface=p.get("local_interface", ""),
                vlan_id=p.get("vlan_id", ""),
                test_destinations=p.get("test_destinations", []),
                baseline=baseline,
                description=p.get("description", ""),
            )
            providers.append(provider)

        suspicious = raw.get("suspicious_transit_asns", [])

        settings = cls(
            general=general,
            telegram=telegram,
            mikrotik=mikrotik,
            providers=providers,
            asn_registry=asn_registry,
            suspicious_transit_asns=[int(x) for x in suspicious],
        )

        logger.info("Configuration loaded: %d providers configured", len(providers))
        return settings

    def get_provider(self, name: str) -> ProviderConfig | None:
        for p in self.providers:
            if p.name.lower() == name.lower():
                return p
        return None

    def resolve_asn_name(self, asn_number: int) -> str:
        return self.asn_registry.get(asn_number, f"AS{asn_number}")
