# Active Context: Ewinet Route Monitor - Diagnostico Local

## Current State

**Status**: Herramienta de diagnostico local para identificar cambios de ruta de ISPs

La aplicacion se ejecuta desde tu PC como herramienta de diagnostico. MikroTik y Telegram estan deshabilitados - tu gestionas los cambios de rutas estaticas manualmente en el router.

## Uso Recomendado

```bash
# Diagnostico rapido (un solo ciclo)
python main.py --once

# Diagnostico con mas detalle
python main.py --once --log-level DEBUG

# Modo daemon con dashboard web en http://localhost:8080
python main.py

# Con scripts de inicio
./run.sh --once          # Linux/Mac
run.bat --once           # Windows
```

## Que Hace el Diagnostico

1. **Probing de rutas**: Hace MTR a cada destino de prueba (8.8.8.8, 1.1.1.1, etc.) por cada proveedor
2. **ASN enrichment**: Consulta whois.cymru.com para identificar el ASN de cada hop
3. **Comparacion de baseline**: Compara la ruta actual contra la ruta esperada configurada
4. **Deteccion de anomalias**: Identifica ASN inesperados, ASNs faltantes, cambios de primer hop, latencia alta
5. **Output detallado**: Muestra AS Path, hops relevantes, performance y alertas en consola

## Cambios Recientes (2026-03-27)

- Configuracion simplificada para uso local (MikroTik OFF, Telegram OFF)
- Gateways vacios - el traceroute se hace desde tu PC
- `run_once()` mejorado con output detallado en consola
- Scripts de arranque para Linux/Mac (`run.sh`) y Windows (`run.bat`)
- `route_monitor.py` actualizado para manejar gateways vacios

## Proveedores Configurados

| Proveedor | ASN      | Transito Esperado       |
|-----------|----------|-------------------------|
| Inter     | AS10929  | Level3/Lumen (3356)     |
| Digitel   | AS15135  | Arelion/Telia (1299)    |
| Besser    | AS263220 | Cogent (174)            |

## Dependencias del Sistema

- `mtr-tiny` o `mtr`
- `traceroute`
- `whois`

```bash
# Ubuntu/Debian
sudo apt install mtr-tiny traceroute whois

# macOS
brew install mtr traceroute whois
```

## Session History

| Date | Changes |
|------|---------|
| 2026-03-27 | Simplified for local PC diagnostic use |
| 2026-03-27 | Added run scripts (run.sh, run.bat) |
| 2026-03-27 | Improved --once mode with detailed console output |
| 2026-03-27 | Disabled MikroTik and Telegram integrations |
