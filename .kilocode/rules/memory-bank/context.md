# Active Context: Ewinet Route Monitor

## Current State

**Status**: ✅ Python monitoring application built and ready for deployment

The `ewinet-monitor/` directory contains a complete ISP route monitoring application for Ewinet. The Next.js base template remains untouched in the project root.

## Recently Completed

- [x] Complete Python application architecture (data models, modules, orchestrator)
- [x] Route monitoring module using MTR + whois ASN lookups (`modules/route_monitor.py`)
- [x] Route change detection with baseline comparison (`modules/route_analyzer.py`)
- [x] Performance metrics collection (RTT, packet loss) (`modules/performance.py`)
- [x] MikroTik RouterOS API/SSH integration (`modules/mikrotik.py`)
- [x] Telegram Bot API alerting with formatted alerts (`modules/alerter.py`)
- [x] YAML-based provider configuration (`config/providers.yaml`)
- [x] Main orchestrator with daemon and single-cycle modes (`main.py`)
- [x] PM2 deployment configuration (`ecosystem.config.js`)
- [x] Automated deployment script (`deploy.sh`)
- [x] Complete README with architecture and deployment guide

## Project Structure

| File/Directory | Purpose |
|----------------|---------|
| `ewinet-monitor/main.py` | Orchestrator entry point |
| `ewinet-monitor/config/providers.yaml` | Provider definitions (Inter, Digitel, Besser) & baselines |
| `ewinet-monitor/config/settings.py` | YAML + env config loader |
| `ewinet-monitor/modules/route_monitor.py` | MTR/traceroute + ASN whois enrichment |
| `ewinet-monitor/modules/route_analyzer.py` | Baseline comparison, anomaly detection |
| `ewinet-monitor/modules/performance.py` | RTT & packet loss metrics |
| `ewinet-monitor/modules/mikrotik.py` | MikroTik RouterOS API/SSH |
| `ewinet-monitor/modules/alerter.py` | Telegram notifications |
| `ewinet-monitor/models/data_models.py` | Data classes (RouteSnapshot, ASN, RouteAnomaly, etc.) |
| `ewinet-monitor/deploy.sh` | Automated deployment script |
| `ewinet-monitor/ecosystem.config.js` | PM2 process manager config |

## Key Design Decisions

- **MTR + whois** approach: Uses `mtr --json` for route probing + `whois -h whois.cymru.com` for ASN lookups per hop
- **Baseline learning**: First run learns routes as baseline; subsequent runs compare against stored baselines
- **Alert cooldown**: 10-minute default cooldown between duplicate alerts for the same provider/destination
- **Optional MikroTik**: `routeros_api` is optional; falls back to SSH if API unavailable
- **No external Python deps beyond pyyaml**: Telegram uses stdlib `urllib.request`, MikroTik SSH uses `subprocess`

## Provider Configuration

```yaml
providers:
  Inter:   AS10929, gateway 10.x.x.x, expected transit via Level3(3356)
  Digitel: AS15135, gateway 172.x.x.x, expected transit via Arelion(1299)
  Besser:  AS263220, gateway 192.x.x.x, expected transit via Cogent(174)
```

## Deployment Notes

- Python 3.10+ required (uses `X | Y` union type syntax, `match` statements avoided for compat)
- System deps: `mtr-tiny`, `traceroute`, `whois`
- PM2 handles process management and auto-restart
- `.env` holds secrets, `providers.yaml` holds network config

## Session History

| Date | Changes |
|------|---------|
| 2026-03-27 | Built complete ewinet-monitor Python application with all 6 modules |
