import json
import logging
import os
import shutil
import signal
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from common import CONFIG_DIR, STREAMS_DIR, env_bool

log = logging.getLogger("youtube")
IDLE_TIMEOUT = int(os.getenv("IDLE_TIMEOUT", "180"))
STARTUP_TIMEOUT = int(os.getenv("STARTUP_TIMEOUT", "60"))

with (CONFIG_DIR / "channels.json").open(encoding="utf-8") as file:
    RAW_CHANNELS = json.load(file)

CHANNELS = {info["slug"]: info["url"] for info in RAW_CHANNELS.values()}


@dataclass
class State:
    process: Optional[subprocess.Popen] = None
    last_access: float = 0.0
    starting: bool = False
    lock: threading.RLock = field(default_factory=threading.RLock)


states = {slug: State() for slug in CHANNELS}
ytdlp_update_lock = threading.Lock()


def output_dir(slug: str) -> Path:
    return STREAMS_DIR / slug


def clean(slug: str):
    path = output_dir(slug)
    shutil.rmtree(path, ignore_errors=True)
    path.mkdir(parents=True, exist_ok=True)


def running(state: State) -> bool:
    return state.process is not None and state.process.poll() is None


def update_ytdlp():
    with ytdlp_update_lock:
        log.warning("Actualizando yt-dlp después de un fallo")
        subprocess.run(
            [
                "python",
                "-m",
                "pip",
                "install",
                "--no-cache-dir",
                "--upgrade",
                "yt-dlp",
            ],
            timeout=180,
            check=True,
        )


def resolve(url: str) -> list[str]:
    command = [
        "yt-dlp",
        "--no-playlist",
        "--no-warnings",
        "--socket-timeout",
        "20",
        "--retries",
        "3",
        "--format",
        "bestvideo[protocol^=m3u8]+bestaudio[protocol^=m3u8]/bestvideo+bestaudio/best",
        "--get-url",
        url,
    ]

    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=60,
    )

    if result.returncode == 0:
        urls = [
            line.strip()
            for line in result.stdout.splitlines()
            if line.strip()
        ]

        if urls:
            return urls

    first_error = result.stderr.strip() or "yt-dlp no devolvió URL"

    if env_bool("UPDATE_YTDLP_ON_FAILURE", True):
        update_ytdlp()

        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=60,
        )

        urls = [
            line.strip()
            for line in result.stdout.splitlines()
            if line.strip()
        ]

        if result.returncode == 0 and urls:
            return urls

    raise RuntimeError(first_error)


def launch(slug: str, stream_urls: list[str]) -> subprocess.Popen:
    directory = output_dir(slug)

    command = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "warning",
        "-nostdin",
    ]

    for stream_url in stream_urls:
        command += [
            "-reconnect",
            "1",
            "-reconnect_streamed",
            "1",
            "-reconnect_delay_max",
            "10",
            "-i",
            stream_url,
        ]

    if len(stream_urls) >= 2:
        command += [
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
        ]
    else:
        command += [
            "-map",
            "0:v:0",
            "-map",
            "0:a:0?",
        ]

    command += [
        "-c",
        "copy",
        "-f",
        "hls",
        "-hls_time",
        "4",
        "-hls_list_size",
        "12",
        "-hls_delete_threshold",
        "6",
        "-hls_flags",
        "delete_segments+append_list+omit_endlist+independent_segments",
        "-hls_segment_filename",
        str(directory / "segment_%09d.ts"),
        str(directory / "index.m3u8"),
    ]

    return subprocess.Popen(
        command,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )


def consume_errors(slug: str, process: subprocess.Popen):
    if process.stderr:
        for line in process.stderr:
            if line.strip():
                log.warning("[%s] %s", slug, line.strip())


def start(slug: str):
    state = states[slug]

    with state.lock:
        state.last_access = time.monotonic()

        if running(state) or state.starting:
            return

        state.starting = True
        clean(slug)

    try:
        stream_urls = resolve(CHANNELS[slug])

        with state.lock:
            process = launch(slug, stream_urls)
            state.process = process

        threading.Thread(
            target=consume_errors,
            args=(slug, process),
            daemon=True,
        ).start()

        log.info("[%s] iniciado PID %s", slug, process.pid)

    except Exception:
        log.exception("[%s] no se pudo iniciar", slug)

    finally:
        with state.lock:
            state.starting = False


def touch_and_start(slug: str):
    state = states[slug]

    with state.lock:
        state.last_access = time.monotonic()
        should_start = not running(state) and not state.starting

    if should_start:
        threading.Thread(
            target=start,
            args=(slug,),
            daemon=True,
        ).start()


def stop(slug: str, reason: str):
    state = states[slug]

    with state.lock:
        process = state.process
        state.process = None

    if process and process.poll() is None:
        log.info("[%s] deteniendo: %s", slug, reason)

        try:
            os.killpg(process.pid, signal.SIGTERM)
            process.wait(timeout=10)

        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)

        except ProcessLookupError:
            pass

    clean(slug)


def monitor():
    while True:
        now = time.monotonic()

        for slug, state in states.items():
            with state.lock:
                is_running = running(state)
                idle = now - state.last_access

            if is_running and idle >= IDLE_TIMEOUT:
                stop(
                    slug,
                    f"{int(idle)} segundos sin solicitudes",
                )

        time.sleep(10)


threading.Thread(
    target=monitor,
    daemon=True,
).start()

