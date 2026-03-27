"""Telegram alerting module for route anomaly notifications."""

from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.request
from typing import Any

from config.settings import TelegramConfig
from models.data_models import Alert, AlertSeverity, PerformanceMetrics, RouteAnomaly

logger = logging.getLogger(__name__)

TELEGRAM_API_URL = "https://api.telegram.org/bot{token}/{method}"


class TelegramAlerter:
    """Sends alerts via Telegram Bot API."""

    def __init__(self, config: TelegramConfig):
        self.config = config
        self._last_alert_time: float = 0
        self._min_interval: float = 5  # Min seconds between API calls

    def _is_configured(self) -> bool:
        return bool(self.config.bot_token and self.config.chat_id)

    def _api_call(self, method: str, data: dict[str, Any]) -> dict[str, Any] | None:
        """Make a call to the Telegram Bot API."""
        if not self._is_configured():
            logger.warning("Telegram not configured, skipping alert")
            return None

        url = TELEGRAM_API_URL.format(token=self.config.bot_token, method=method)
        payload = json.dumps(data).encode("utf-8")

        # Rate limiting
        elapsed = time.time() - self._last_alert_time
        if elapsed < self._min_interval:
            time.sleep(self._min_interval - elapsed)

        req = urllib.request.Request(
            url,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=15) as response:
                result = json.loads(response.read().decode("utf-8"))
                self._last_alert_time = time.time()
                if not result.get("ok"):
                    logger.error("Telegram API error: %s", result)
                return result
        except urllib.error.URLError as e:
            logger.error("Telegram API request failed: %s", e)
            return None
        except Exception as e:
            logger.error("Unexpected error sending Telegram alert: %s", e)
            return None

    def send_alert(self, alert: Alert) -> bool:
        """Send an alert message via Telegram."""
        message = alert.format_telegram_message()

        result = self._api_call("sendMessage", {
            "chat_id": self.config.chat_id,
            "text": message,
            "parse_mode": "Markdown",
            "disable_web_page_preview": True,
        })

        if result and result.get("ok"):
            alert.sent = True
            alert.sent_at = time.time()
            logger.info("Alert sent via Telegram: %s", alert.anomaly.description[:80])
            return True

        logger.error("Failed to send Telegram alert")
        return False

    def send_anomaly_alerts(self, anomalies: list[RouteAnomaly]) -> int:
        """Send alerts for a list of anomalies. Returns count of sent alerts."""
        sent_count = 0

        for anomaly in anomalies:
            alert = Alert(anomaly=anomaly)
            if self.send_alert(alert):
                sent_count += 1

        return sent_count

    def send_performance_summary(self, metrics: list[PerformanceMetrics]) -> bool:
        """Send a periodic performance summary."""
        if not metrics:
            return True

        lines = ["📊 *Ewinet Performance Summary*", ""]

        by_provider: dict[str, list[PerformanceMetrics]] = {}
        for m in metrics:
            by_provider.setdefault(m.provider_name, []).append(m)

        for provider, provider_metrics in sorted(by_provider.items()):
            lines.append(f"*{provider}:*")
            for m in sorted(provider_metrics, key=lambda x: x.destination):
                status = "🟢" if m.packet_loss < 5 else "🟡" if m.packet_loss < 20 else "🔴"
                lines.append(
                    f"  {status} {m.destination}: "
                    f"RTT={m.rtt_avg:.1f}ms, Loss={m.packet_loss:.1f}%"
                )
            lines.append("")

        lines.append(f"⏰ {time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())}")

        message = "\n".join(lines)

        result = self._api_call("sendMessage", {
            "chat_id": self.config.chat_id,
            "text": message,
            "parse_mode": "Markdown",
            "disable_web_page_preview": True,
        })

        return bool(result and result.get("ok"))

    def send_status_message(self, message: str) -> bool:
        """Send a simple status/info message."""
        result = self._api_call("sendMessage", {
            "chat_id": self.config.chat_id,
            "text": f"ℹ️ *Ewinet Monitor*\n{message}",
            "parse_mode": "Markdown",
        })
        return bool(result and result.get("ok"))

    def test_connection(self) -> bool:
        """Test Telegram bot connectivity."""
        if not self._is_configured():
            logger.error("Telegram bot token or chat ID not configured")
            return False

        result = self._api_call("getMe", {})
        if result and result.get("ok"):
            bot_info = result.get("result", {})
            logger.info(
                "Telegram bot connected: @%s (%s)",
                bot_info.get("username", "unknown"),
                bot_info.get("first_name", ""),
            )
            return True

        logger.error("Telegram bot connection test failed")
        return False
