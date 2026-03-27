# Ewinet Route Monitor

ISP upstream route monitoring system for detecting BGP path changes, latency degradation, and unexpected ASN routing.

## Architecture

```
ewinet-monitor/
├── main.py                      # Orchestrator entry point (daemon + web)
├── config/
│   ├── settings.py              # Configuration loader (YAML + .env)
│   └── providers.yaml           # Provider definitions & baselines
├── modules/
│   ├── route_monitor.py         # MTR/traceroute + ASN whois enrichment
│   ├── route_analyzer.py        # Baseline comparison & anomaly detection
│   ├── performance.py           # RTT & packet loss metrics
│   ├── mikrotik.py              # MikroTik RouterOS API/SSH integration
│   ├── alerter.py               # Telegram Bot API notifications
│   └── web_server.py            # Flask web dashboard + REST API
├── web/
│   ├── templates/dashboard.html # Dashboard UI template
│   └── static/
│       ├── css/dashboard.css    # Dark theme styles
│       └── js/dashboard.js      # Auto-refresh client
├── models/
│   └── data_models.py           # Data classes
├── data/                        # Persisted baselines & history
├── logs/                        # Application logs
├── deploy.sh                    # Automated deployment script
├── ecosystem.config.js          # PM2 process manager config
├── requirements.txt
└── .env.example
```

## How It Works

1. **Route Probing** — Runs `mtr --json` + whois ASN lookups per provider/destination
2. **Baseline Comparison** — Compares live AS-PATH against stored expected paths
3. **Anomaly Detection** — Flags: unexpected ASNs, missing transit ASNs, first-hop changes, latency spikes, packet loss
4. **MikroTik Enrichment** — Maps gateway IPs to physical interfaces/VLANs via RouterOS API or SSH
5. **Telegram Alerts** — Immediate notification with old route vs. new route and latency delta

## Route Comparison Logic

```
Baseline:   Ewinet -> Inter(10929) -> Level3(3356) -> Google(15169)
Detected:   Ewinet -> Inter(10929) -> Cogent(174)  -> Google(15169)
                                                    └── Unexpected: Cogent (not in baseline)
Action:     ALERT — "First hop ASN changed from Level3 to Cogent"
```

## Quick Start

### Prerequisites

- Linux server (Debian/Ubuntu recommended)
- Python 3.10+
- Root or sudo access
- Network access to upstream providers for probing

### System Dependencies

```bash
sudo apt-get install -y python3 python3-pip mtr-tiny traceroute whois
```

### Installation

```bash
# Clone and deploy
git clone <repo-url> /opt/ewinet-monitor
cd /opt/ewinet-monitor
sudo bash deploy.sh
```

### Manual Setup

```bash
# Create virtual environment
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Configure
cp .env.example .env
nano .env                           # Set Telegram & MikroTik credentials
nano config/providers.yaml          # Adjust provider IPs, ASNs, baselines

# Test Telegram
python main.py --test-telegram

# Run single cycle
python main.py --once

# Run as daemon
python main.py
```

## PM2 Deployment

PM2 ensures the monitor stays running and restarts on failure.

```bash
# Start
pm2 start ecosystem.config.js

# Monitor
pm2 logs ewinet-monitor
pm2 monit

# Restart after config changes
pm2 restart ewinet-monitor

# Auto-start on boot
pm2 startup
pm2 save
```

## Dashboard Web

El monitor incluye un dashboard web en tiempo real accesible en `http://<servidor-ip>:8080`.

**Características del Dashboard:**
- **Tarjetas de resumen**: Total proveedores, rutas OK, anomalías activas, latencia promedio
- **Visualización de AS-PATH**: Ruta visual por cada proveedor/destino con nodos ASN coloreados
- **Detección visual de anomalías**: ASNs inesperados resaltados en rojo con animación de pulso
- **Métricas de rendimiento**: RTT (min/avg/max), pérdida de paquetes con barras de progreso
- **Alertas activas**: Lista de anomalías con ruta esperada vs. detectada
- **Historial de rutas**: Tabla filtrable por proveedor
- **Líneas base configuradas**: Resumen de la configuración por proveedor
- **Auto-refresh**: Actualización cada 15 segundos

**Endpoints REST API:**
| Endpoint | Descripción |
|----------|-------------|
| `GET /api/status` | Estado del sistema, uptime, ciclo actual |
| `GET /api/snapshots` | Snapshots de rutas actuales |
| `GET /api/performance` | Métricas de rendimiento |
| `GET /api/anomalies` | Anomalías detectadas |
| `GET /api/providers` | Configuración de proveedores |
| `GET /api/history` | Historial de rutas (JSONL) |
| `GET /api/baselines` | Baselines almacenados |

Cambiar el puerto:
```bash
python main.py --port 9090
```

## Configuration

### `.env` — Secrets

```env
TELEGRAM_BOT_TOKEN=your_bot_token
TELEGRAM_CHAT_ID=your_chat_id
MIKROTIK_HOST=192.168.1.1
MIKROTIK_USERNAME=admin
MIKROTIK_PASSWORD=your_password
```

### `config/providers.yaml` — Provider Definitions

```yaml
providers:
  - name: Inter
    asn: 10929
    gateway: "10.0.0.1"               # Next-hop from your router
    local_interface: "ether2"          # MikroTik interface name
    vlan_id: "100"
    test_destinations:
      - "8.8.8.8"
      - "1.1.1.1"
    baseline:
      expected_as_path: [10929, 3356, 15169]
      known_transit_asns: [3356, 174, 1299, 6453]
      expected_first_hop_asn: 3356
      baseline_rtt_avg: 65.0
```

**Key fields:**
- `expected_as_path` — The ideal ASN path (your ASN, transit, destination)
- `known_transit_asns` — ASNs considered acceptable as transits
- `expected_first_hop_asn` — The ASN expected immediately after yours
- `suspicious_transit_asns` — ASNs that should trigger critical alerts

## Alert Format

```
🚨 Ewinet Route Alert

Proveedor: Inter
Destino: 8.8.8.8
Severidad: CRITICAL

Descripción: First hop ASN changed from AS3356 (Level3/Lumen) to AS174 (Cogent).
Upstream peering may have changed.

Ruta Esperada:
Ewinet(AS263220) -> Level3/Lumen(AS3356) -> Google(AS15169)

Ruta Actual:
Ewinet(AS263220) -> Cogent(AS174) -> Google(AS15169)

ASNs Inesperados: Cogent(AS174)

Aumento de Latencia: +45.2%

⏰ 2026-03-27 14:30:00 UTC
```

## CLI Options

```bash
python main.py                      # Run as daemon (default)
python main.py --once               # Single cycle, then exit
python main.py --config path.yaml   # Custom config path
python main.py --log-level DEBUG    # Verbose logging
python main.py --test-telegram      # Verify Telegram bot
```

## Data Files

| File | Purpose |
|------|---------|
| `data/baselines.json` | Stored route baselines (auto-learned) |
| `data/route_history.jsonl` | Historical route snapshots |
| `logs/ewinet_monitor.log` | Application log |

## MikroTik Integration

The monitor can query your MikroTik router to determine which physical interface/VLAN each provider uses.

**Two modes:**
- **API** (recommended): Uses `routeros_api` Python library on port 8728
- **SSH**: Falls back to SSH commands if API is unavailable

Enable API on MikroTik:
```
/ip service set api port=8728
/user add name=monitor group=full password=STRONG_PASSWORD
```

Install API library:
```bash
pip install routeros-api
```

## Troubleshooting

| Issue | Solution |
|-------|----------|
| MTR not found | `apt install mtr-tiny` |
| whois timeout | Check firewall rules for port 43 |
| Telegram 401 | Verify bot token in `.env` |
| MikroTik SSH fails | Check credentials and SSH service on router |
| No ASN data | whois.cymru.com may be rate-limited; the monitor falls back to RADB |

## License

Internal use — Ewinet ISP Operations
