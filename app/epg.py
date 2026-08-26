import copy
import json
import logging
import os
import re
import tempfile
import unicodedata
import xml.etree.ElementTree as ET
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from common import CONFIG_DIR, GENERATED_DIR, atomic_write, download_to_file
from playlist import parse_template

log = logging.getLogger("epg")
OUTPUT = GENERATED_DIR / "guide.xml"
REPORT_OUTPUT = GENERATED_DIR / "epg-report.json"
ALIASES_FILE = CONFIG_DIR / "epg_aliases.json"


# Sources are processed in strict priority order. The first source that
# supplies programmes for a target channel wins, preventing overlapping grids.
DEFAULT_SOURCES = [
    # Best priority for Argentine channels.
    {"name": "iptv-epg-ar", "url": "https://iptv-epg.org/files/epg-ar.xml.gz"},

    # EPGshare remains a valuable fallback because it includes regional cable
    # feeds that may not be available in the first source.
    {
        "name": "epgshare-ar",
        "url": "https://epgshare01.online/epgshare01/epg_ripper_AR1.xml.gz",
    },

    # Regional guides used as fallbacks for international channels.
    {"name": "iptv-epg-us", "url": "https://iptv-epg.org/files/epg-us.xml.gz"},
    {"name": "iptv-epg-mx", "url": "https://iptv-epg.org/files/epg-mx.xml.gz"},
    {"name": "iptv-epg-cl", "url": "https://iptv-epg.org/files/epg-cl.xml.gz"},
    {"name": "iptv-epg-co", "url": "https://iptv-epg.org/files/epg-co.xml.gz"},
    {"name": "iptv-epg-es", "url": "https://iptv-epg.org/files/epg-es.xml.gz"},
    {"name": "iptv-epg-it", "url": "https://iptv-epg.org/files/epg-it.xml.gz"},
    {"name": "iptv-epg-fr", "url": "https://iptv-epg.org/files/epg-fr.xml.gz"},
    {"name": "iptv-epg-br", "url": "https://iptv-epg.org/files/epg-br.xml.gz"},
    {"name": "iptv-epg-uy", "url": "https://iptv-epg.org/files/epg-uy.xml.gz"},
    {"name": "iptv-epg-pe", "url": "https://iptv-epg.org/files/epg-pe.xml.gz"},

    {
        "name": "epgshare-us",
        "url": "https://epgshare01.online/epgshare01/epg_ripper_US2.xml.gz",
    },
    {
        "name": "epgshare-mx",
        "url": "https://epgshare01.online/epgshare01/epg_ripper_MX1.xml.gz",
    },
    {
        "name": "epgshare-cl",
        "url": "https://epgshare01.online/epgshare01/epg_ripper_CL1.xml.gz",
    },
    {
        "name": "epgshare-co",
        "url": "https://epgshare01.online/epgshare01/epg_ripper_CO1.xml.gz",
    },
    {
        "name": "epgshare-es",
        "url": "https://epgshare01.online/epgshare01/epg_ripper_ES1.xml.gz",
    },
    {
        "name": "epgshare-it",
        "url": "https://epgshare01.online/epgshare01/epg_ripper_IT1.xml.gz",
    },
    {
        "name": "epgshare-fr",
        "url": "https://epgshare01.online/epgshare01/epg_ripper_FR1.xml.gz",
    },
    {
        "name": "epgshare-br",
        "url": "https://epgshare01.online/epgshare01/epg_ripper_BR1.xml.gz",
    },
    {
        "name": "epgshare-uy",
        "url": "https://epgshare01.online/epgshare01/epg_ripper_UY1.xml.gz",
    },
    {
        "name": "epgshare-pe",
        "url": "https://epgshare01.online/epgshare01/epg_ripper_PE1.xml.gz",
    },
    {
        "name": "epgshare-pa",
        "url": "https://epgshare01.online/epgshare01/epg_ripper_PA1.xml.gz",
    },
    {
        "name": "epgshare-cr",
        "url": "https://epgshare01.online/epgshare01/epg_ripper_CR1.xml.gz",
    },
    {
        "name": "epgshare-do",
        "url": "https://epgshare01.online/epgshare01/epg_ripper_DO1.xml.gz",
    },
]


@dataclass(frozen=True)
class TargetChannel:
    tvg_id: str
    name: str


def fix_mojibake(value: str) -> str:
    """Try to repair UTF-8 text decoded as Latin-1, e.g. TelefÃ© -> Telefé."""
    if not value or "Ã" not in value:
        return value or ""

    try:
        return value.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return value


def normalize(value: str) -> str:
    value = fix_mojibake(value or "")
    value = unicodedata.normalize("NFKD", value)
    value = "".join(
        char for char in value if not unicodedata.combining(char)
    )
    value = value.casefold()

    value = re.sub(
        r"\.(ar|us|mx|cl|co|es|it|fr|br|uy|pe|pa|cr|do)$",
        "",
        value,
    )

    value = value.replace("+", " plus ").replace("&", " and ")

    value = re.sub(
        r"\b(canal|tv|hd|fhd|uhd|4k|latinoamerica|latin america|argentina)\b",
        " ",
        value,
    )

    return re.sub(r"[^a-z0-9]+", "", value)


def extract_name(extinf: str, tvg_id: str) -> str:
    match = re.search(r'tvg-name="([^"]+)"', extinf)

    if match:
        return match.group(1).strip()

    if "," in extinf:
        return extinf.rsplit(",", 1)[-1].strip()

    return tvg_id


def load_targets() -> dict[str, TargetChannel]:
    _, entries = parse_template()

    result = {}

    for entry in entries:
        if entry.tvg_id:
            result[entry.tvg_id] = TargetChannel(
                entry.tvg_id,
                extract_name(entry.extinf, entry.tvg_id),
            )

    return result


def load_aliases() -> dict[str, list[str]]:
    if not ALIASES_FILE.exists():
        return {}

    raw = json.loads(
        ALIASES_FILE.read_text(encoding="utf-8")
    )

    aliases = {}

    for target, values in raw.items():
        if isinstance(values, str):
            values = [values]

        aliases[target] = [
            str(value)
            for value in values
            if str(value).strip()
        ]

    return aliases


def source_definitions() -> list[dict[str, str]]:
    configured = os.getenv("EPG_SOURCES", "").strip()

    if not configured:
        return DEFAULT_SOURCES

    result = []

    for index, value in enumerate(
        configured.split(","),
        start=1,
    ):
        url = value.strip()

        if url:
            result.append({
                "name": f"custom-{index}",
                "url": url,
            })

    return result


def source_channel_names(
    root: ET.Element,
) -> dict[str, list[str]]:
    names = {}

    for channel in root.findall("channel"):
        channel_id = channel.get("id", "").strip()

        if not channel_id:
            continue

        display_names = [
            fix_mojibake((element.text or "").strip())
            for element in channel.findall("display-name")
            if (element.text or "").strip()
        ]

        names[channel_id] = display_names

    return names


def build_matcher(
    targets: dict[str, TargetChannel],
    aliases: dict[str, list[str]],
    source_names: dict[str, list[str]],
) -> dict[str, str]:
    """Return source channel id -> local target tvg-id."""

    matches: dict[str, str] = {}

    for source_id in source_names:
        if source_id in targets:
            matches[source_id] = source_id

    explicit: dict[str, str] = {}

    for target_id, values in aliases.items():
        if target_id not in targets:
            continue

        for value in values:
            repaired = fix_mojibake(value)

            explicit[value.casefold()] = target_id
            explicit[repaired.casefold()] = target_id
            explicit[normalize(value)] = target_id
            explicit[normalize(repaired)] = target_id

    for source_id, display_names in source_names.items():
        if source_id in matches:
            continue

        for candidate in (
            source_id,
            fix_mojibake(source_id),
            *display_names,
        ):
            found = (
                explicit.get(candidate.casefold())
                or explicit.get(normalize(candidate))
            )

            if found:
                matches[source_id] = found
                break

    normalized_targets: dict[str, set[str]] = defaultdict(set)

    for target in targets.values():
        for value in (
            target.tvg_id,
            target.name,
        ):
            key = normalize(value)

            if key:
                normalized_targets[key].add(target.tvg_id)

    for source_id, display_names in source_names.items():
        if source_id in matches:
            continue

        possible: set[str] = set()

        for value in (
            source_id,
            fix_mojibake(source_id),
            *display_names,
        ):
            key = normalize(value)

            if key:
                possible.update(
                    normalized_targets.get(key, set())
                )

        if len(possible) == 1:
            matches[source_id] = next(iter(possible))

    return matches


def merge_source(
    xml_path: Path,
    source_name: str,
    targets: dict[str, TargetChannel],
    aliases: dict[str, list[str]],
    merged_channels: dict[str, ET.Element],
    merged_programmes: dict[str, list[ET.Element]],
    claimed_programme_channels: set[str],
    source_by_target: dict[str, str],
) -> tuple[int, int]:
    source_names: dict[str, list[str]] = {}

    # Primera pasada:
    # obtenemos los canales de la fuente sin cargar el XML entero
    # en memoria.
    context = ET.iterparse(
        xml_path,
        events=("start", "end"),
    )

    _, root = next(context)

    for event, element in context:
        if event != "end":
            continue

        if element.tag == "channel":
            channel_id = element.get("id", "").strip()

            if channel_id:
                display_names = [
                    fix_mojibake((node.text or "").strip())
                    for node in element.findall("display-name")
                    if (node.text or "").strip()
                ]

                source_names[channel_id] = display_names

            element.clear()
            root.clear()

        elif element.tag == "programme":
            # No necesitamos los programas en esta primera pasada.
            element.clear()
            root.clear()

    matcher = build_matcher(
        targets,
        aliases,
        source_names,
    )

    channel_count = 0
    programme_count = 0

    # Creamos los canales locales que fueron encontrados.
    for source_id, target_id in matcher.items():
        if target_id in merged_channels:
            continue

        channel = ET.Element(
            "channel",
            {"id": target_id},
        )

        display_name = ET.SubElement(
            channel,
            "display-name",
        )

        display_name.text = targets[target_id].name

        merged_channels[target_id] = channel
        channel_count += 1

    eligible_targets = (
        set(matcher.values())
        - claimed_programme_channels
    )

    populated_targets: set[str] = set()

    # Segunda pasada:
    # procesamos cada <programme> completo.
    #
    # Es importante NO limpiar title/desc/category/icon/etc.
    # individualmente antes de que cierre <programme>, porque
    # eso dejaría los programas vacíos.
    context = ET.iterparse(
        xml_path,
        events=("start", "end"),
    )

    _, root = next(context)

    for event, element in context:
        if event != "end":
            continue

        if element.tag == "programme":
            source_id = element.get(
                "channel",
                "",
            )

            target_id = matcher.get(source_id)

            if (
                target_id
                and target_id in eligible_targets
            ):
                node = copy.deepcopy(element)

                node.set(
                    "channel",
                    target_id,
                )

                merged_programmes[target_id].append(
                    node
                )

                populated_targets.add(target_id)
                programme_count += 1

            # Solamente limpiamos después de haber procesado
            # el programme completo.
            element.clear()
            root.clear()

        elif element.tag == "channel":
            element.clear()
            root.clear()

    for target_id in populated_targets:
        source_by_target[target_id] = source_name

    claimed_programme_channels.update(
        populated_targets
    )

    return channel_count, programme_count


def build_xml(
    targets: dict[str, TargetChannel],
    channels: dict[str, ET.Element],
    programmes: dict[str, list[ET.Element]],
) -> tuple[bytes, int, int]:
    tv = ET.Element(
        "tv",
        {
            "generator-info-name": "IPTV Manager v3",
            "generator-info-url": "https://iptv-epg.org/guides",
        },
    )

    channels_with_programmes = {
        channel_id
        for channel_id, items in programmes.items()
        if items
    }

    for target_id, target in targets.items():
        channel = channels.get(target_id)

        if channel is None:
            channel = ET.Element(
                "channel",
                {"id": target_id},
            )

            display = ET.SubElement(
                channel,
                "display-name",
            )

            display.text = target.name

        tv.append(channel)

    seen = set()
    programme_count = 0

    for channel_id in sorted(programmes):
        for programme in sorted(
            programmes[channel_id],
            key=lambda node: node.get("start", ""),
        ):
            key = (
                channel_id,
                programme.get("start", ""),
                programme.get("stop", ""),
                (
                    programme.findtext("title")
                    or ""
                ).strip(),
            )

            if key in seen:
                continue

            seen.add(key)
            tv.append(programme)
            programme_count += 1

    ET.indent(
        tv,
        space="  ",
    )

    return (
        ET.tostring(
            tv,
            encoding="utf-8",
            xml_declaration=True,
        ),
        len(channels_with_programmes),
        programme_count,
    )


def write_report(
    targets: dict[str, TargetChannel],
    source_by_target: dict[str, str],
    programmes: dict[str, list[ET.Element]],
    source_errors: dict[str, str],
) -> None:
    channels = {}

    for target_id, target in targets.items():
        count = len(
            programmes.get(
                target_id,
                [],
            )
        )

        channels[target_id] = {
            "name": target.name,
            "source": source_by_target.get(target_id),
            "programmes": count,
            "status": "ok" if count else "missing",
        }

    report = {
        "channels_total": len(targets),
        "channels_with_programmes": sum(
            1
            for data in channels.values()
            if data["programmes"] > 0
        ),
        "programmes_total": sum(
            data["programmes"]
            for data in channels.values()
        ),
        "channels": channels,
        "source_errors": source_errors,
    }

    atomic_write(
        REPORT_OUTPUT,
        json.dumps(
            report,
            indent=2,
            ensure_ascii=False,
        ).encode("utf-8"),
    )


def update_epg() -> bool:
    targets = load_targets()
    aliases = load_aliases()

    merged_channels: dict[
        str,
        ET.Element,
    ] = {}

    merged_programmes: dict[
        str,
        list[ET.Element],
    ] = defaultdict(list)

    claimed_programme_channels: set[str] = set()

    source_by_target: dict[
        str,
        str,
    ] = {}

    source_errors: dict[
        str,
        str,
    ] = {}

    successful_sources = 0

    for source in source_definitions():
        name = source["name"]
        url = source["url"]

        try:
            log.info(
                "Descargando EPG %s: %s",
                name,
                url,
            )

            with tempfile.NamedTemporaryFile(
                suffix=".xml",
                delete=False,
            ) as tmp:
                xml_path = Path(tmp.name)

            try:
                download_to_file(
                    url,
                    xml_path,
                    timeout=(20, 300),
                )

                channel_count, programme_count = merge_source(
                    xml_path,
                    name,
                    targets,
                    aliases,
                    merged_channels,
                    merged_programmes,
                    claimed_programme_channels,
                    source_by_target,
                )

            finally:
                xml_path.unlink(
                    missing_ok=True
                )

            successful_sources += 1

            log.info(
                "%s procesada: %d canales asociados, %d programas nuevos",
                name,
                channel_count,
                programme_count,
            )

        except Exception as error:
            source_errors[name] = str(error)

            log.warning(
                "No se pudo procesar %s: %s",
                name,
                error,
            )

    content, channels_with_data, programme_count = build_xml(
        targets,
        merged_channels,
        merged_programmes,
    )

    write_report(
        targets,
        source_by_target,
        merged_programmes,
        source_errors,
    )

    minimum_programmes = int(
        os.getenv(
            "EPG_MIN_PROGRAMMES",
            "10",
        )
    )

    if (
        successful_sources == 0
        or programme_count < minimum_programmes
    ):
        if (
            OUTPUT.exists()
            and b"<programme " in OUTPUT.read_bytes()
        ):
            log.error(
                "La nueva guía solo contiene %d programas; "
                "se conserva la última guía válida",
                programme_count,
            )

            return False

        raise RuntimeError(
            f"No se pudo generar una guía válida: "
            f"{successful_sources} fuentes, "
            f"{programme_count} programas"
        )

    changed = atomic_write(
        OUTPUT,
        content,
    )

    log.info(
        "Guía XMLTV %s: %d/%d canales con programación, %d programas",
        "actualizada" if changed else "sin cambios",
        channels_with_data,
        len(targets),
        programme_count,
    )

    return changed
