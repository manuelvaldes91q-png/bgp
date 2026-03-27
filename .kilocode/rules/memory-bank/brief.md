# Project Brief: Ewinet Route Monitor - Diagnostico Local

## Purpose

Herramienta de diagnostico para identificar cambios de ruta de los proveedores ISP (Inter, Digitel, Besser). Se ejecuta desde tu PC para que puedas decidir que cambios hacer en las rutas estaticas de tu MikroTik.

## Target Users

- Administradores de red con MikroTik que necesitan diagnosticar rutas ISP
- Operadores que necesitan identificar cuando un proveedor cambia su ruta de transito

## How It Works

1. Ejecutas `python main.py --once` desde tu PC
2. La herramienta hace MTR + whois a cada destino por cada proveedor
3. Compara las rutas actuales contra los baselines configurados
4. Te muestra un reporte detallado con AS Paths, anomalias y performance
5. Tu decides si necesitas cambiar rutas estaticas en MikroTik

## Key Requirements

### Must Have
- Diagnostico de rutas via MTR + whois ASN lookup
- Comparacion contra baselines configurados
- Deteccion de anomalias (ASN inesperado, ruta cambiada)
- Output claro en consola
- Dashboard web opcional (Flask)

### Not Needed (tu lo manejas)
- MikroTik API/SSH integration (deshabilitado)
- Telegram alerts (deshabilitado)
- Cambio automatico de rutas

## Tech Stack

- Python 3.10+
- PyYAML (configuracion)
- Flask (dashboard web opcional)
- MTR + traceroute + whois (herramientas del sistema)
