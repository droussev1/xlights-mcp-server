"""Apply a show's safety rules to an existing .xsq tree (used by remap_sequence).

Generated sequences get safety rules inside the generator; imported sequences are
written straight from their XML, so the same rules are applied to the tree here:

- excluded models (and groups containing them) lose their effects
- strobes outside the finale are removed; strobes inside it become half-time hits.
  Without the song's audio there is no finale to find, so every strobe is removed
- whites are tinted and capped, white-only looks are limited, and full white is kept
  only in strobes
"""

from __future__ import annotations

import copy
import re

from lxml import etree

from xlights_mcp.audio.analyzer import SongAnalysis
from xlights_mcp.sequencer.show_rules import ShowRules, finale_range, white_plan
from xlights_mcp.sequencer.song_settings import expand_models
from xlights_mcp.xlights.models import ShowConfig
from xlights_mcp.xlights.palettes import ColorPalette
from xlights_mcp.xlights.xsq_writer import EffectPlacement

HEX = re.compile(r"#[0-9A-Fa-f]{6}")


def unsafe_targets(rules: ShowRules, show_config: ShowConfig) -> set[str]:
    """Excluded models plus every group that (directly or through subgroups) contains one."""
    excluded = set(expand_models(rules.safety.exclude, show_config))
    groups = {g.name for g in show_config.model_groups
              if excluded & set(expand_models([g.name], show_config))}
    return excluded | groups


def _model_elements(root: etree._Element):
    effects = root.find("ElementEffects")
    return [] if effects is None else [el for el in effects if el.get("type") == "model"]


def _parse_palette(text: str) -> list[list[str]]:
    return [part.split("=", 1) for part in (text or "").split(",") if "=" in part]


def _active_buttons(parts: list[list[str]]) -> list[int]:
    """Indexes into ``parts`` of the active palette buttons, in palette order."""
    active = {k.removeprefix("C_CHECKBOX_Palette") for k, v in parts
              if k.startswith("C_CHECKBOX_Palette") and v == "1"}
    return [i for i, (k, _) in enumerate(parts)
            if k.startswith("C_BUTTON_Palette") and k.removeprefix("C_BUTTON_Palette") in active]


def apply_safety(
    root: etree._Element,
    rules: ShowRules,
    show_config: ShowConfig,
    analysis: SongAnalysis | None = None,
    tiers: list[str] | None = None,
) -> list[str]:
    """Enforce the safety rules on the tree in place. Returns human-readable warnings."""
    warnings: list[str] = []

    # Exclusions
    unsafe = unsafe_targets(rules, show_config)
    for container in (root.find("ElementEffects"), root.find("DisplayElements")):
        if container is None:
            continue
        for el in [el for el in container if el.get("type") == "model" and el.get("name") in unsafe]:
            if container.tag == "ElementEffects" and el.find("EffectLayer/Effect") is not None:
                warnings.append(f"Safety rules: dropped effects on '{el.get('name')}' (excluded)")
            container.remove(el)

    if rules.safety.strobes:
        warnings += _limit_strobes(root, rules, analysis, tiers)
    if rules.safety.white:
        warnings += _limit_white(root, rules)
    return warnings


def _limit_strobes(root, rules: ShowRules, analysis, tiers) -> list[str]:
    finale = finale_range(analysis, tiers, None) if analysis and tiers else None
    every = rules.safety.strobes.every_beats
    beats = analysis.beats.beat_times if analysis else []
    downbeats = set(analysis.beats.downbeat_times) if analysis else set()
    removed = rebuilt = 0
    for el in _model_elements(root):
        for layer in el.findall("EffectLayer"):
            for eff in [e for e in layer.findall("Effect") if e.get("name") == "Strobe"]:
                start, end = int(eff.get("startTime")) / 1000, int(eff.get("endTime")) / 1000
                lo, hi = (max(start, finale[0]), min(end, finale[1])) if finale else (0, 0)
                hits = [b for b in beats if lo <= b < hi]
                if hits:
                    first = next((i for i, b in enumerate(hits) if b in downbeats), 0)
                    hits = hits[first % every::every]
                pos = list(layer).index(eff)
                layer.remove(eff)
                if not hits:
                    removed += 1
                    continue
                beat = 60 / max(analysis.beats.tempo, 1)
                for j, b in enumerate(hits):
                    new = copy.deepcopy(eff)
                    new.set("startTime", str(int(b * 1000)))
                    new.set("endTime", str(int(min(b + beat, hi) * 1000)))
                    layer.insert(pos + j, new)
                rebuilt += 1
    warnings = []
    if removed:
        where = "outside the finale" if finale else "(no audio to find the finale)"
        warnings.append(f"Safety rules: removed {removed} strobe effect(s) {where}")
    if rebuilt:
        warnings.append(f"Safety rules: re-timed {rebuilt} finale strobe(s) to every {every} beats")
    return warnings


def _limit_white(root, rules: ShowRules) -> list[str]:
    limit = rules.safety.white
    palettes_el = root.find("ColorPalettes")
    if palettes_el is None:
        return []
    palette_texts = [p.text or "" for p in palettes_el.findall("ColorPalette")]
    db = [e.text or "" for e in root.findall("EffectDB/Effect")]
    head_duration = root.findtext("head/sequenceDuration") or "0"
    try:
        duration_ms = int(float(head_duration) * 1000)
    except ValueError:
        duration_ms = 0

    # One proxy per effect so the auto-mode white logic can decide what to do
    targets, proxies = [], []
    for el in _model_elements(root):
        for layer in el.findall("EffectLayer"):
            for eff in layer.findall("Effect"):
                p = eff.get("palette")
                if p is None or not p.isdigit() or int(p) >= len(palette_texts):
                    continue
                parts = _parse_palette(palette_texts[int(p)])
                colors = [c.upper() for i in _active_buttons(parts) for c in HEX.findall(parts[i][1])]
                ref = eff.get("ref")
                settings_text = db[int(ref)] if ref and ref.isdigit() and int(ref) < len(db) else ""
                proxies.append(EffectPlacement(
                    model_name=el.get("name"), effect_name=eff.get("name") or "",
                    start_time_ms=int(eff.get("startTime")), end_time_ms=int(eff.get("endTime")),
                    settings={"B_CUSTOM_SubBuffer": "1"} if "B_CUSTOM_SubBuffer" in settings_text else {},
                    palette=ColorPalette(colors=colors or ["#000000"], active_colors=list(range(1, len(colors) + 1))),
                ))
                targets.append((eff, parts))

    actions = white_plan(proxies, limit, duration_ms or 1,
                         lambda e: e.effect_name in limit.full_white_effects)
    index = {t: i for i, t in enumerate(palette_texts)}
    changed = recolored = 0
    for (eff, parts), proxy, action in zip(targets, proxies, actions):
        parts = [list(kv) for kv in parts]
        active = _active_buttons(parts)
        if action == "recolor":
            whites_only = all(limit.is_white(c) for c in proxy.palette.colors)
            if proxy.effect_name == "On" and not whites_only:
                # Lead with the first non-white active color
                k = next(i for i in active if not all(limit.is_white(c.upper()) for c in HEX.findall(parts[i][1])))
                parts[active[0]][1], parts[k][1] = parts[k][1], parts[active[0]][1]
            else:
                fb = iter(limit.fallback_colors * (len(active) + 1))
                for i in active:
                    parts[i][1] = HEX.sub(lambda m: next(fb) if limit.is_white(m.group().upper()) else m.group(),
                                          parts[i][1])
            recolored += 1
        swap = (lambda c: limit.full_white) if action == "full" else limit.soften
        for i in active:
            parts[i][1] = HEX.sub(lambda m: swap(m.group().upper()) if limit.is_white(m.group().upper())
                                  else m.group(), parts[i][1])
        text = ",".join(f"{k}={v}" for k, v in parts)
        if text == palette_texts[int(eff.get("palette"))]:
            continue
        if text not in index:
            new = etree.SubElement(palettes_el, "ColorPalette")
            new.text = text
            index[text] = len(palette_texts)
            palette_texts.append(text)
        eff.set("palette", str(index[text]))
        changed += 1
    warnings = []
    if changed:
        warnings.append(f"Safety rules: softened whites in {changed} effect(s)"
                        + (f", recolored {recolored} long white-only look(s)" if recolored else ""))
    return warnings
