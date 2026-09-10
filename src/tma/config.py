from __future__ import annotations

import json
from pathlib import Path


CONFIG_PATH = Path.home() / ".tma_config.json"


def load_config() -> dict:
    try:
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def load_api_key() -> str:
    return str(load_config().get("api_key") or "")


def save_api_key(api_key: str) -> None:
    cfg = load_config()
    cfg["api_key"] = api_key
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2), encoding="utf-8")


def clear_api_key() -> None:
    cfg = load_config()
    cfg.pop("api_key", None)
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2), encoding="utf-8")