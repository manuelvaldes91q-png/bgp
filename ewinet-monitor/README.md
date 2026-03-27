# Ewinet Route Monitor - Diagnostico Local

Herramienta de diagnostico para identificar cambios de ruta de tus proveedores ISP (Inter, Digitel, Besser). Se ejecuta desde tu PC.

## Como Funciona

1. Ejecutas `python main.py --once` desde tu PC
2. Hace MTR + whois a cada destino por cada proveedor
3. Compara las rutas actuales contra baselines configurados
4. Muestra un reporte detallado con AS Paths, anomalias y performance
5. Tu decides si cambiar rutas estaticas en MikroTik

## Quick Start

### Dependencias del Sistema

```bash
# Ubuntu/Debian
sudo apt install python3 python3-pip mtr-tiny traceroute whois

# macOS
brew install python3 mtr traceroute whois

# Windows (recomendado usar WSL)
wsl --install
# Luego dentro de WSL:
sudo apt install python3 python3-pip mtr-tiny traceroute whois
```

### Instalacion y Ejecucion

```bash
cd ewinet-monitor

# Crear entorno virtual
python3 -m venv venv
source venv/bin/activate    # Linux/Mac
# venv\Scripts\activate     # Windows

# Instalar dependencias Python
pip install -r requirements.txt

# Ejecutar diagnostico
python main.py --once
```

### Scripts de Inicio Rapido

```bash
# Linux/Mac
./run.sh --once
./run.sh --once --debug

# Windows
run.bat --once
```

## Ejemplo de Output

```
======================================================================
  EWINET ROUTE DIAGNOSTIC - Diagnostico de Rutas ISP
======================================================================
  Proveedores configurados: 3
  Destinos de prueba: 8.8.8.8, 1.1.1.1
  MikroTik: OFF (tu gestionas las rutas)
  Telegram: OFF (diagnostico local)
======================================================================

[1/3] Probando rutas de todos los proveedores...
[2/3] Midiendo performance (RTT, packet loss)...
[3/3] Analizando rutas contra baselines...

======================================================================
  RESULTADOS DEL DIAGNOSTICO
======================================================================

----------------------------------------------------------------------
  PROVEEDOR: Inter
  ASN: AS10929
  Descripcion: Upstream primario - Inter (AS10929)
  Interfaz: ether2 (VLAN 100)
----------------------------------------------------------------------

  [OK] -> 8.8.8.8
    Estado: NORMAL
    Saltos: 14
    AS Path: AS10929 (Inter) -> AS3356 (Level3/Lumen) -> AS15169 (Google)
    Baseline: COINCIDE con ruta esperada
    Hops relevantes:
       #1 10.0.0.1            AS10929        2.3ms  0% loss
       #2 200.10.20.1         AS3356         15.1ms 0% loss
       #3 72.14.215.85        AS15169        62.4ms 0% loss

======================================================================
  PERFORMANCE
======================================================================
  Inter      -> 8.8.8.8          RTT=  62.4ms  Loss=  0.0%  [OK]
  Inter      -> 1.1.1.1          RTT=  58.2ms  Loss=  0.0%  [OK]
  Digitel    -> 8.8.8.8          RTT=  71.3ms  Loss=  0.0%  [OK]

======================================================================
  ESTADO: Todas las rutas dentro de los baselines esperados
======================================================================
```

## Modo Daemon (Dashboard Web)

```bash
# Iniciar con dashboard web
python main.py

# Abrir en navegador
# http://localhost:8080

# Cambiar puerto
python main.py --port 9090
```

El dashboard muestra:
- AS-PATH visual por proveedor/destino
- Metricas de rendimiento (RTT, packet loss)
- Alertas y anomalias detectadas
- Historial de rutas

## Configuracion

### `config/providers.yaml`

```yaml
general:
  enable_mikrotik: false    # Tu gestionas MikroTik manualmente
  enable_telegram: false    # Diagnostico local
  poll_interval_seconds: 120

providers:
  - name: Inter
    asn: 10929
    gateway: ""             # Vacio = traceroute desde tu PC
    local_interface: "ether2"
    test_destinations:
      - "8.8.8.8"
      - "1.1.1.1"
    baseline:
      expected_as_path: [10929, 3356, 15169]
      known_transit_asns: [3356, 174, 1299]
      expected_first_hop_asn: 3356
      baseline_rtt_avg: 65.0
```

## CLI Options

```bash
python main.py --once              # Diagnostico rapido (recomendado)
python main.py --once --log-level DEBUG   # Con mas detalle
python main.py                     # Modo daemon + dashboard
python main.py --port 9090         # Cambiar puerto dashboard
python main.py --config mi.yaml    # Config personalizada
```

## Troubleshooting

| Problema | Solucion |
|----------|----------|
| MTR not found | `apt install mtr-tiny` |
| whois timeout | Verificar firewall puerto 43 |
| No ASN data | whois.cymru.com puede limitar; usa RADB como fallback |
| Sin conectividad | Verificar que tu PC tiene acceso a internet |

## Estructura

```
ewinet-monitor/
├── main.py                  # Entry point
├── run.sh                   # Script Linux/Mac
├── run.bat                  # Script Windows
├── config/
│   ├── providers.yaml       # Configuracion proveedores
│   └── settings.py          # Loader de configuracion
├── modules/
│   ├── route_monitor.py     # MTR + whois ASN lookup
│   ├── route_analyzer.py    # Comparacion baseline + anomalias
│   ├── performance.py       # RTT y packet loss
│   └── web_server.py        # Dashboard Flask
├── models/
│   └── data_models.py       # Dataclasses
├── data/                    # Baselines e historial
└── logs/                    # Logs de aplicacion
```
