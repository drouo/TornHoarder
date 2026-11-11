"""Items you need for crimes, so the sell plan never sells your last ones.

Organized Crimes are authoritative: `GET /torn/organizedcrimes` lists every
OC, each role, and the item that role requires, with `is_used` telling
whether the OC consumes it (ID Badge, Zip Ties, C4) or you keep it
(Lockpicks, Jemmy, Wire Cutters). Cached in data/oc_items.json for a day.

Regular crimes have no such list; the only signal is an item's own
description and effect ("Required for the disposal of firearms in Disposal",
"Improves Disposal crime exp", "Unlocks additional outcomes ... in Burglary",
"Ignites a fire in Arson"). `crime_hint()` flags those, ignoring references
to the retired Crimes 1.0.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Optional

import requests

from .http_client import BASE_URL, get_json_with_retry

MAX_AGE_S = 24 * 3600

# Reusable crime tools Torn's text doesn't mark as such (no crime named in the
# description or effect), so neither check above finds them. One is enough.
MANUAL_CRIME_TOOLS = {
    853: "Cemetery Key: unlocks searching the cemetery",
}

_CRIME_WORDS = (
    r"crimes?|disposal|forgery|burglary|scamming|cracking|bootlegging|graffiti|shoplifting|"
    r"pickpocketing|card skimming|skimming|hustling|arson|counterfeiting|search for cash"
)
_HINT = re.compile(
    rf"\b(required|used|needed|useful|unlocks?|enables?|allows?|increases?|boosts?|grants?)\b[^.]{{0,80}}"
    rf"\b({_CRIME_WORDS})\b|\bimproves\b[^.]{{0,60}}\bcrime",
    re.IGNORECASE,
)
_RETIRED = re.compile(r"crimes 1\.0", re.IGNORECASE)
# An item's effect line naming a crime ("... in Arson.") is always about using it there.
_EFFECT_IN_CRIME = re.compile(rf"\bin ({_CRIME_WORDS})\b", re.IGNORECASE)


def crime_hint(description: Optional[str], effect: Optional[str] = None) -> bool:
    """True when an item's description or effect says it's used for a (current) crime."""
    if effect and _EFFECT_IN_CRIME.search(_RETIRED.sub("", effect)):
        return True
    text = " ".join(filter(None, [description, effect]))
    return bool(_HINT.search(_RETIRED.sub("", text))) and not (
        _RETIRED.search(text) and not re.search(r"crimes 2|organi[sz]ed", text, re.IGNORECASE)
    )


def parse_oc_items(data: dict) -> dict[str, dict]:
    """{item_id: {"name", "consumed", "crimes": [...], "min_level", "max_level"}} from torn/organizedcrimes."""
    crimes = data.get("organizedcrimes") or []
    if isinstance(crimes, dict):
        crimes = list(crimes.values())
    out: dict[str, dict] = {}
    for crime in crimes:
        level = crime.get("difficulty")
        for slot in crime.get("slots") or []:
            item = slot.get("required_item")
            if not item or item.get("id") is None:
                continue
            entry = out.setdefault(
                str(item["id"]), {"name": item.get("name"), "consumed": False, "crimes": [], "min_level": level, "max_level": level}
            )
            entry["consumed"] = entry["consumed"] or bool(item.get("is_used"))
            if crime.get("name") not in entry["crimes"]:
                entry["crimes"].append(crime.get("name"))
            if level is not None:
                entry["min_level"] = min(entry["min_level"] or level, level)
                entry["max_level"] = max(entry["max_level"] or level, level)
    return out


def load_oc_items(path: Path) -> dict[str, dict]:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data.get("items", {}) if isinstance(data, dict) else {}


def refresh_oc_items_if_stale(
    session: requests.Session, api_key: str, path: Path, max_age_s: float = MAX_AGE_S
) -> tuple[bool, Optional[str]]:
    """Re-download the OC item list when missing or older than a day. Keeps the old copy on error."""
    try:
        if time.time() - Path(path).stat().st_mtime < max_age_s:
            return False, None
    except OSError:
        pass
    data, err = get_json_with_retry(session, None, f"{BASE_URL}/torn/organizedcrimes", api_key, timeout=20, retries=2)
    if data is None:
        return False, err
    items = parse_oc_items(data)
    if not items:
        return False, "torn/organizedcrimes returned no required items"
    Path(path).write_text(json.dumps({"fetched": int(time.time()), "items": items}, indent=2), encoding="utf-8")
    return True, None
