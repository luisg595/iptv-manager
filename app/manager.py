import logging
import os
import threading
import time
from pathlib import Path

from apscheduler.schedulers.background import BackgroundScheduler
from flask import Flask, Response, abort, jsonify, send_from_directory

from common import GENERATED_DIR, STREAMS_DIR
from epg import update_epg
from jellyfin import refresh_jellyfin
from playlist import update_playlist
from youtube_streams import CHANNELS, STARTUP_TIMEOUT, states, running, touch_and_start

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("manager")
app = Flask(__name__)
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "http://192.168.1.2:8090").rstrip("/")
update_lock = threading.Lock()


def update_all(update_playlist_file=True, update_epg_file=True):
    if not update_lock.acquire(blocking=False):
        log.info("Ya existe una actualización en curso")
        return
    try:
        changed = False
        if update_playlist_file:
            changed = update_playlist(PUBLIC_BASE_URL) or changed
        if update_epg_file:
            changed = update_epg() or changed
        if changed:
            refresh_jellyfin()
    except Exception:
        log.exception("Falló la actualización")
    finally:
        update_lock.release()


def bootstrap():
    update_all(True, True)


threading.Thread(target=bootstrap, daemon=True).start()
scheduler = BackgroundScheduler(timezone=os.getenv("TZ", "America/Argentina/Buenos_Aires"))
scheduler.add_job(lambda: update_all(True, False), "interval", hours=int(os.getenv("IPTV_UPDATE_HOURS", "6")), id="playlist", max_instances=1, coalesce=True)
scheduler.add_job(lambda: update_all(False, True), "interval", hours=int(os.getenv("EPG_UPDATE_HOURS", "12")), id="epg", max_instances=1, coalesce=True)
scheduler.start()


@app.get("/")
def index():
    return jsonify(
        service="iptv-manager",
        playlist=f"{PUBLIC_BASE_URL}/dgo.m3u",
        guide=f"{PUBLIC_BASE_URL}/guide.xml",
        epg_report=f"{PUBLIC_BASE_URL}/epg-report.json",
        status=f"{PUBLIC_BASE_URL}/status",
    )


@app.get("/dgo.m3u")
def playlist_file():
    path = GENERATED_DIR / "dgo.m3u"
    if not path.exists():
        return Response("Lista aún no generada\n", status=503)
    return send_from_directory(GENERATED_DIR, "dgo.m3u", mimetype="audio/x-mpegurl", max_age=0)


@app.get("/guide.xml")
def guide_file():
    path = GENERATED_DIR / "guide.xml"
    if not path.exists():
        return Response("Guía aún no generada\n", status=503)
    return send_from_directory(GENERATED_DIR, "guide.xml", mimetype="application/xml", max_age=0)



@app.get("/epg-report.json")
def epg_report_file():
    path = GENERATED_DIR / "epg-report.json"
    if not path.exists():
        return Response("Informe EPG aún no generado\n", status=503)
    return send_from_directory(
        GENERATED_DIR,
        "epg-report.json",
        mimetype="application/json",
        max_age=0,
    )


@app.post("/update")
def update_now():
    threading.Thread(target=update_all, args=(True, True), daemon=True).start()
    return jsonify(started=True), 202


@app.get("/status")
def status():
    channels = {}
    for slug, state in states.items():
        with state.lock:
            is_running = running(state)
            channels[slug] = {
                "running": is_running,
                "starting": state.starting,
                "idle_seconds": round(time.monotonic() - state.last_access, 1) if state.last_access else None,
                "pid": state.process.pid if is_running else None,
            }
    return jsonify(
        youtube=channels,
        playlist_exists=(GENERATED_DIR / "dgo.m3u").exists(),
        guide_exists=(GENERATED_DIR / "guide.xml").exists(),
    )


@app.get("/youtube/<slug>/index.m3u8")
def youtube_playlist(slug: str):
    if slug not in CHANNELS:
        abort(404)
    touch_and_start(slug)
    deadline = time.monotonic() + STARTUP_TIMEOUT
    path = STREAMS_DIR / slug / "index.m3u8"
    while time.monotonic() < deadline:
        state = states[slug]
        with state.lock:
            state.last_access = time.monotonic()
        if path.exists() and path.stat().st_size > 0:
            response = send_from_directory(path.parent, path.name, mimetype="application/vnd.apple.mpegurl", max_age=0)
            response.headers["Cache-Control"] = "no-store"
            return response
        time.sleep(0.5)
    return Response(f"No se pudo iniciar {slug}\n", status=503)


@app.get("/youtube/<slug>/<path:filename>")
def youtube_segment(slug: str, filename: str):
    if slug not in CHANNELS or "/" in filename:
        abort(404)
    if not any(filename.endswith(extension) for extension in (".ts", ".m4s", ".mp4", ".aac", ".key")):
        abort(404)
    state = states[slug]
    with state.lock:
        state.last_access = time.monotonic()
    return send_from_directory(STREAMS_DIR / slug, filename, max_age=0)
