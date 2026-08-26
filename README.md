# IPTV Manager v3.1

Esta versión corrige el procedimiento de instalación y actualización.

El ZIP contiene una carpeta llamada `iptv-manager-v3.1`, distinta de la
instalación activa `/opt/iptv-manager`. Por eso puede descomprimirse dentro de
`/opt` sin sobrescribir archivos antes de crear el respaldo.

## Actualizar una instalación existente

Copia el ZIP al servidor y ejecuta:

```bash
cd /opt
sudo unzip /ruta/iptv-manager-v3.1.zip
sudo /opt/iptv-manager-v3.1/upgrade.sh /opt/iptv-manager
```

El actualizador:

1. Detiene la versión actual.
2. Crea un respaldo completo con fecha.
3. Prepara la nueva versión en una carpeta temporal.
4. Conserva `dgo.template.m3u`, `docker-compose.yml`, `data/` y `generated/`.
5. Fusiona `channels.json` y `epg_aliases.json`.
6. Valida JSON y Docker Compose.
7. Reemplaza la instalación solo después de validar.
8. Reconstruye y levanta el contenedor.

No ejecutes `upgrade.sh` desde dentro de `/opt/iptv-manager`.

## Instalación nueva

```bash
cd /opt
sudo unzip /ruta/iptv-manager-v3.1.zip
sudo /opt/iptv-manager-v3.1/install.sh /opt/iptv-manager
```

## Fuentes EPG

Cada canal toma programación de una sola fuente, en orden:

1. IPTV-EPG Argentina.
2. EPGshare Argentina.
3. IPTV-EPG regional.
4. EPGshare regional.

La primera fuente que entrega programas para un canal gana. Esto evita
programaciones superpuestas.

## Verificación

```bash
cd /opt/iptv-manager
docker compose ps
docker compose logs -f
```

Forzar actualización:

```bash
curl -X POST http://127.0.0.1:8090/update
```

Contar programas:

```bash
grep -c '<programme ' /opt/iptv-manager/generated/guide.xml
```

Informe por canal:

```bash
curl -s http://127.0.0.1:8090/epg-report.json |
python3 -m json.tool
```

Canales sin guía:

```bash
curl -s http://127.0.0.1:8090/epg-report.json |
python3 -c '
import json, sys
report = json.load(sys.stdin)
for channel_id, channel in report["channels"].items():
    if channel["status"] == "missing":
        print(channel_id, "-", channel["name"])
'
```

## Jellyfin

Sintonizador:

```text
http://192.168.1.2:8090/dgo.m3u
```

Proveedor XMLTV:

```text
http://192.168.1.2:8090/guide.xml
```

Después de la actualización ejecuta en Jellyfin:

```text
Panel → Tareas programadas → Actualizar datos de la guía
```
