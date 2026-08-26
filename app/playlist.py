import concurrent.futures
import json
import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import requests

from common import CONFIG_DIR, GENERATED_DIR, HTTP, USER_AGENT, atomic_write

log = logging.getLogger("playlist")
STREAMS_API = "https://iptv-org.github.io/api/streams.json"
TEMPLATE = CONFIG_DIR / "dgo.template.m3u"
CHANNELS_CONFIG = CONFIG_DIR / "channels.json"
OUTPUT = GENERATED_DIR / "dgo.m3u"


@dataclass
class Entry:
    extinf: str
    tvg_id: str
    original_url: str


@dataclass
class Candidate:
    url: str
    referrer: Optional[str] = None
    user_agent: Optional[str] = None
    quality: Optional[str] = None


def load_youtube_channels() -> dict:
    with CHANNELS_CONFIG.open(encoding="utf-8") as file:
        return json.load(file)


def parse_template() -> tuple[str, list[Entry]]:
    lines = TEMPLATE.read_text(encoding="utf-8").splitlines()
    header = lines[0] if lines and lines[0].startswith("#EXTM3U") else "#EXTM3U"
    entries: list[Entry] = []
    index = 0
    while index < len(lines):
        line = lines[index].strip()
        if not line.startswith("#EXTINF:"):
            index += 1
            continue
        match = re.search(r'tvg-id="([^"]*)"', line)
        tvg_id = match.group(1) if match else ""
        index += 1
        while index < len(lines) and (not lines[index].strip() or lines[index].lstrip().startswith("#")):
            index += 1
        if index < len(lines):
            entries.append(Entry(line, tvg_id, lines[index].strip()))
        index += 1
    return header, entries


def load_iptv_org_candidates() -> dict[str, list[Candidate]]:
    response = HTTP.get(STREAMS_API, timeout=(15, 90))
    response.raise_for_status()
    result: dict[str, list[Candidate]] = {}
    for item in response.json():
        channel = item.get("channel")
        url = item.get("url")
        if not channel or not url:
            continue
        result.setdefault(channel, []).append(Candidate(
            url=url,
            referrer=item.get("referrer"),
            user_agent=item.get("user_agent"),
            quality=item.get("quality"),
        ))
    return result


def candidate_headers(candidate: Candidate) -> dict[str, str]:
    headers = {"User-Agent": candidate.user_agent or USER_AGENT, "Accept": "*/*"}
    if candidate.referrer:
        headers["Referer"] = candidate.referrer
    return headers


def stream_works(candidate: Candidate) -> bool:
    try:
        with requests.get(
            candidate.url,
            headers=candidate_headers(candidate),
            timeout=(7, 15),
            stream=True,
            allow_redirects=True,
        ) as response:
            if response.status_code >= 400:
                return False
            data = next(response.iter_content(4096), b"")
            return bool(data)
    except (requests.RequestException, StopIteration):
        return False


def quality_score(candidate: Candidate) -> int:
    if not candidate.quality:
        return 0
    match = re.search(r"(\d+)", candidate.quality)
    return int(match.group(1)) if match else 0


def select_candidate(entry: Entry, alternatives: list[Candidate]) -> Candidate:
    original = Candidate(entry.original_url)
    candidates = [original]
    seen = {entry.original_url}
    for candidate in sorted(alternatives, key=quality_score, reverse=True):
        if candidate.url not in seen:
            candidates.append(candidate)
            seen.add(candidate.url)

    # Se conserva la URL original mientras funcione; si cae, se busca reemplazo.
    for candidate in candidates:
        if stream_works(candidate):
            return candidate
    return alternatives[0] if alternatives else original


def render_entry(entry: Entry, selected: Candidate) -> list[str]:
    lines = [entry.extinf]
    if selected.referrer:
        lines.append(f"#EXTVLCOPT:http-referrer={selected.referrer}")
    if selected.user_agent:
        lines.append(f"#EXTVLCOPT:http-user-agent={selected.user_agent}")
    lines.append(selected.url)
    return lines


def update_playlist(public_base_url: str) -> bool:
    header, entries = parse_template()
    youtube = load_youtube_channels()
    alternatives = load_iptv_org_candidates()

    selected: dict[str, Candidate] = {}
    ordinary = [entry for entry in entries if entry.tvg_id not in youtube]

    def choose(entry: Entry):
        return entry.tvg_id, select_candidate(entry, alternatives.get(entry.tvg_id, []))

    workers = max(2, min(8, int(os.getenv("STREAM_CHECK_WORKERS", "6"))))
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
        for tvg_id, candidate in executor.map(choose, ordinary):
            selected[tvg_id] = candidate

    output = [header, ""]
    for entry in entries:
        if entry.tvg_id in youtube:
            info = youtube[entry.tvg_id]
            candidate = Candidate(f"{public_base_url.rstrip('/')}/youtube/{info['slug']}/index.m3u8")
        else:
            candidate = selected[entry.tvg_id]
        output.extend(render_entry(entry, candidate))
        output.append("")

    changed = atomic_write(OUTPUT, ("\n".join(output).rstrip() + "\n").encode("utf-8"))
    log.info("Lista M3U %s (%d canales)", "actualizada" if changed else "sin cambios", len(entries))
    return changed
