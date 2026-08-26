import gzip
import logging
import os
import shutil
import tempfile
from pathlib import Path
from typing import Iterable

import requests

CONFIG_DIR = Path("/config")
GENERATED_DIR = Path("/generated")
DATA_DIR = Path("/data")
STREAMS_DIR = DATA_DIR / "streams"

for directory in (GENERATED_DIR, DATA_DIR, STREAMS_DIR):
    directory.mkdir(parents=True, exist_ok=True)

USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) IPTV-Manager/1.0"
HTTP = requests.Session()
HTTP.headers.update({"User-Agent": USER_AGENT, "Accept": "*/*"})


def env_bool(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


def atomic_write(path: Path, content: bytes) -> bool:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.read_bytes() == content:
        return False
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as tmp:
        tmp.write(content)
        temp_path = Path(tmp.name)
    shutil.move(temp_path, path)
    return True


def download_bytes(url: str, timeout: tuple[int, int] = (15, 90)) -> bytes:
    response = HTTP.get(url, timeout=timeout)
    response.raise_for_status()
    data = response.content
    if url.lower().endswith(".gz") or data[:2] == b"\x1f\x8b":
        data = gzip.decompress(data)
    return data


def chunks(values: Iterable, size: int):
    current = []
    for value in values:
        current.append(value)
        if len(current) >= size:
            yield current
            current = []
    if current:
        yield current

def download_to_file(url: str, path: Path, timeout: tuple[int, int] = (15, 90)) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)

    with HTTP.get(url, timeout=timeout, stream=True) as response:
        response.raise_for_status()

        is_gzip = url.lower().endswith(".gz")

        if is_gzip:
            with gzip.GzipFile(fileobj=response.raw) as gz:
                with path.open("wb") as output:
                    shutil.copyfileobj(gz, output, length=1024 * 1024)
        else:
            with path.open("wb") as output:
                for chunk in response.iter_content(chunk_size=1024 * 1024):
                    if chunk:
                        output.write(chunk)

    return path
