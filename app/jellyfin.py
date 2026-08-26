import logging
import os

import requests

log = logging.getLogger("jellyfin")


def refresh_jellyfin() -> bool:
    base_url = os.getenv("JELLYFIN_URL", "").rstrip("/")
    api_key = os.getenv("JELLYFIN_API_KEY", "").strip()
    if not base_url or not api_key:
        log.info("Jellyfin no configurado; se omite refresco automático")
        return False

    headers = {"X-Emby-Token": api_key}
    try:
        response = requests.get(f"{base_url}/ScheduledTasks", headers=headers, timeout=20)
        response.raise_for_status()
        tasks = response.json()
        matches = []
        for task in tasks:
            name = str(task.get("Name", "")).lower()
            description = str(task.get("Description", "")).lower()
            text = f"{name} {description}"
            if "guide" in text or "live tv" in text or "tv en vivo" in text or "guía" in text:
                matches.append(task)
        for task in matches:
            task_id = task.get("Id") or task.get("Key")
            if not task_id:
                continue
            run = requests.post(
                f"{base_url}/ScheduledTasks/Running/{task_id}",
                headers=headers,
                timeout=20,
            )
            run.raise_for_status()
            log.info("Tarea de Jellyfin iniciada: %s", task.get("Name", task_id))
        if not matches:
            log.warning("No se encontraron tareas de guía/TV en vivo en Jellyfin")
        return bool(matches)
    except requests.RequestException as error:
        log.warning("No se pudo refrescar Jellyfin: %s", error)
        return False
