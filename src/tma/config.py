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


def load_sell_prefs() -> tuple[dict, dict[int, str]]:
    """(sell rules dict, {item_id: plan} overrides) saved from the Sell plan tab."""
    cfg = load_config()
    overrides = {int(k): str(v) for k, v in (cfg.get("sell_overrides") or {}).items()}
    return dict(cfg.get("sell_rules") or {}), overrides


def save_sell_prefs(rules: dict, overrides: dict[int, str]) -> None:
    cfg = load_config()
    cfg["sell_rules"] = rules
    cfg["sell_overrides"] = {str(k): v for k, v in sorted(overrides.items())}
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2), encoding="utf-8")


def load_reserves() -> dict[int, int]:
    """{item_id: how many to keep} set by hand on the Sell plan tab."""
    return {int(k): int(v) for k, v in (load_config().get("reserves") or {}).items()}


def save_reserves(reserves: dict[int, int]) -> None:
    cfg = load_config()
    cfg["reserves"] = {str(k): int(v) for k, v in sorted(reserves.items())}
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
