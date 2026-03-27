# Active Context: Ewinet Route Monitor - Diagnostico Local

## Current State

**Status**: Herramienta de diagnostico local para identificar cambios de ruta de ISPs

La app se ejecuta desde tu PC detras de MikroTik. Detecta tu IP publica automaticamente, hace traceroute a los destinos configurados, y muestra la ruta AS completa con comparacion contra baselines.

## Uso

```bash
cd ewinet-monitor
pip install -r requirements.txt

# Diagnostico rapido
python main.py --once

# Modo daemon + dashboard web
python main.py
```

## Flujo del Diagnostico (--once)

1. **Detectar IP publica** - Consulta ipify/ifconfig.me para saber tu IP publica actual
2. **Traceroute a destinos** - Hace MTR a cada destino configurado (8.8.8.8, 1.1.1.1, etc.)
3. **Enriquecer con ASN** - Consulta whois.cymru.com para cada hop publico
4. **Comparar contra baselines** - Muestra si la ruta coincide o cambio

## Configuracion (providers.yaml)

```yaml
destinations:
  - destination: "8.8.8.8"
    description: "Google DNS"
  - destination: "1.1.1.1"
    description: "Cloudflare DNS"
```

Los baselines se aprenden automaticamente en la primera ejecucion si no se configuran.

## Arquitectura Actual

- `route_monitor.py` - detect_public_ip() + probe_route() con MTR/whois
- `route_analyzer.py` - Comparacion por destino (no por proveedor)
- `settings.py` - DestinationConfig en vez de ProviderConfig
- `web_server.py` - API con /api/destinations y public_ip

## Session History

| Date | Changes |
|------|---------|
| 2026-03-27 | Auto-detect public IP, destination-based config, auto-learn baselines |
| 2026-03-27 | Simplified for local PC diagnostic (MikroTik/Telegram OFF) |
| 2026-03-27 | Added run scripts (run.sh, run.bat) |
| 2026-03-27 | Improved --once mode with detailed console output |
