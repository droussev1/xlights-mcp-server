"""Every xLights effect and its settings, read from the installed app's effect metadata.

xLights ships ``Contents/Resources/effectmetadata/<Effect>.json`` describing each effect's
properties (id, control type, type, default, range, choices). This turns a brief's
``look`` — an effect name plus settings by property id — into the setting keys an .xsq
file stores, checking names, ranges and choices on the way:

- whole-number slider → ``E_SLIDER_<id>``; decimal slider (``divisor`` > 1) → both
  ``E_TEXTCTRL_<id>`` (the value) and ``E_SLIDER_<id>`` (value × divisor), since the
  slider and its text box both carry it
- checkbox → ``E_CHECKBOX_<id>`` ("1"/"0"); choice → ``E_CHOICE_<id>``;
  spin → ``E_SPINCTRL_<id>``; text → ``E_TEXTCTRL_<id>``

Keys already in xsq form (``E_…``, ``B_…``, ``T_…``, ``C_…``) pass through unchecked, for
buffer, timing and color settings.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

DEFAULT_APP = Path("/Applications/xLights.app")
METADATA_SUBDIR = Path("Contents/Resources/effectmetadata")
RAW_PREFIXES = ("E_", "B_", "T_", "C_")


@dataclass
class EffectInfo:
    name: str
    properties: dict[str, dict] = field(default_factory=dict)


def metadata_dir() -> Path:
    """The installed xLights' effect metadata (``XLIGHTS_APP`` overrides the app path)."""
    return Path(os.environ.get("XLIGHTS_APP", DEFAULT_APP)) / METADATA_SUBDIR


@lru_cache(maxsize=4)
def load_catalog(directory: str | None = None) -> dict[str, EffectInfo]:
    """Effect name → its properties. Empty if xLights isn't installed where expected."""
    path = Path(directory) if directory else metadata_dir()
    catalog: dict[str, EffectInfo] = {}
    if not path.is_dir():
        return catalog
    for f in sorted(path.glob("*.json")):
        if f.name.startswith("_"):
            continue
        try:
            data = json.loads(f.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        name = data.get("effectName")
        if name:
            catalog[name] = EffectInfo(name, {p["id"]: p for p in data.get("properties", []) if "id" in p})
    return catalog


def _setting(effect: str, prop: dict, value) -> dict[str, str]:
    pid, control, kind = prop["id"], prop.get("controlType"), prop.get("type")
    where = f"{effect} '{pid}'"
    if kind == "bool" or control == "checkbox":
        if not isinstance(value, bool) and value not in (0, 1, "0", "1"):
            raise ValueError(f"{where} is a checkbox: use true or false")
        return {f"E_CHECKBOX_{pid}": "1" if value in (True, 1, "1") else "0"}
    if kind == "enum" or control == "choice":
        options = [o["value"] if isinstance(o, dict) else o for o in prop.get("options", [])]
        if options and value not in options:
            raise ValueError(f"{where} must be one of {options}, not {value!r}")
        return {f"E_CHOICE_{pid}": str(value)}
    if kind in ("int", "float"):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"{where} needs a number, not {value!r}")
        divisor = prop.get("divisor", 1) or 1
        lo, hi = prop.get("min"), prop.get("max")
        lo = lo / divisor if lo is not None else None
        hi = hi / divisor if hi is not None else None
        if (lo is not None and value < lo) or (hi is not None and value > hi):
            raise ValueError(f"{where} must be between {lo:g} and {hi:g}, not {value}")
        if divisor > 1:
            return {f"E_TEXTCTRL_{pid}": f"{float(value):g}", f"E_SLIDER_{pid}": str(round(value * divisor))}
        if control == "spin":
            return {f"E_SPINCTRL_{pid}": str(int(value))}
        return {f"E_SLIDER_{pid}": str(int(round(value)))}
    if control == "text" or kind == "string":
        return {f"E_TEXTCTRL_{pid}": str(value)}
    raise ValueError(f"{where} ({control}/{kind}) can't be set from a brief")


def effect_settings(effect: str, settings: dict, catalog: dict[str, EffectInfo] | None = None) -> dict[str, str]:
    """A look's settings (by property id) as xsq setting keys. ValueError on unknown
    effects or properties, values out of range, or invalid choices."""
    catalog = load_catalog(str(metadata_dir())) if catalog is None else catalog
    if not catalog:
        raise ValueError(f"xLights effect metadata not found at {metadata_dir()}; set XLIGHTS_APP")
    info = catalog.get(effect)
    if info is None:
        raise ValueError(f"Unknown xLights effect '{effect}'")
    out: dict[str, str] = {}
    for key, value in settings.items():
        if key.startswith(RAW_PREFIXES):
            out[key] = str(value)
            continue
        prop = info.properties.get(key)
        if prop is None:
            raise ValueError(f"{effect} has no setting '{key}' (has: {', '.join(info.properties)})")
        out.update(_setting(effect, prop, value))
    return out
