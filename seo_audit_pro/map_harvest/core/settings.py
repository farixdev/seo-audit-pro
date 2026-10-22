import json
import os
from copy import deepcopy

DEFAULT_SETTINGS = {
    "headless": False,
    "max_limit_cap": 100,
    "default_max_results": 50,
    "export_dir": "",
    "saved_searches": [],
}

MAX_SAVED_SEARCHES = 12
SETTINGS_DIR = os.path.join(os.path.expanduser("~"), ".mapharvest")
SETTINGS_PATH = os.path.join(SETTINGS_DIR, "settings.json")


def _ensure_dir() -> None:
    os.makedirs(SETTINGS_DIR, exist_ok=True)


def load_settings() -> dict:
    _ensure_dir()
    if not os.path.isfile(SETTINGS_PATH):
        return deepcopy(DEFAULT_SETTINGS)
    try:
        with open(SETTINGS_PATH, encoding="utf-8") as f:
            data = json.load(f)
        merged = deepcopy(DEFAULT_SETTINGS)
        merged.update({k: v for k, v in data.items() if k in merged})
        if isinstance(merged.get("saved_searches"), list):
            merged["saved_searches"] = merged["saved_searches"][:MAX_SAVED_SEARCHES]
        else:
            merged["saved_searches"] = []
        return merged
    except Exception:
        return deepcopy(DEFAULT_SETTINGS)


def save_settings(settings: dict) -> None:
    _ensure_dir()
    payload = deepcopy(DEFAULT_SETTINGS)
    payload.update({k: settings.get(k, payload[k]) for k in payload})
    payload["saved_searches"] = list(payload.get("saved_searches") or [])[:MAX_SAVED_SEARCHES]
    with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)


def add_saved_search(settings: dict, domains: list[str], area: str, max_results: int) -> dict:
    entry = {
        "domains": list(domains),
        "area": area.strip(),
        "max_results": max_results,
    }
    saved = [s for s in settings.get("saved_searches", []) if s != entry]
    saved.insert(0, entry)
    settings["saved_searches"] = saved[:MAX_SAVED_SEARCHES]
    save_settings(settings)
    return settings
