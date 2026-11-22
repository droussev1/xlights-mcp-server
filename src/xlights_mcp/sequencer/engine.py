"""Sequence generation engine — orchestrates audio analysis → effect placement → .xsq output.

Architecture: 3-layer approach matching professional sequencing patterns:
  Layer 0 (background bed): Sustained effects that run the full section — twinkle, color wash, plasma
  Layer 1 (mid-layer motion): Rhythmic/directional effects synced to downbeats — chase, spirals
  Layer 2 (accent hits): Punchy effects on select beats — shockwave, morph, strobe

Models are assigned ROLES based on their physical type:
  - House outline/rooflines → bass pulse, slow morphs
  - Arches / candy canes → rhythmic chases, mirrored directions
  - Tree → spirals, pinwheels, meteors
  - Custom props → shockwaves, circles, warp
  - Windows / doors → marquee, color wash
  - Flakes / spinners → twinkle, shimmer, pinwheel

Palettes cycle per phrase (every 2-4 bars) for variety, not just per section.
"""

from __future__ import annotations

import bisect
import hashlib
import json
import logging
import random
from pathlib import Path

from xlights_mcp.audio.analyzer import SongAnalysis, StemAnalysis, full_analysis
from xlights_mcp.audio.structure import SongSection
from xlights_mcp.config import AudioConfig
from xlights_mcp.sequencer import show_rules as show_rules_mod
from xlights_mcp.sequencer import song_settings as song_settings_mod
from xlights_mcp.sequencer.show_rules import ShowRules
from xlights_mcp.sequencer.song_settings import SongSettings
from xlights_mcp.xlights.models import LightModel, ShowConfig
from xlights_mcp.xlights.palettes import ColorPalette, get_theme_palettes
from xlights_mcp.xlights.show import load_show_config
from xlights_mcp.xlights.xsq_writer import (
    EffectPlacement, SequenceSpec, TimingTrack, TimingTrackLabel, write_xsq,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Professional effect settings — extracted from real human-made sequences
# Multiple variants per effect type for variety
# ---------------------------------------------------------------------------

EFFECT_VARIANTS: dict[str, list[dict[str, str]]] = {
    "Shockwave_hit": [
        # Big expanding shockwave for impact moments
        {"E_CHECKBOX_Shockwave_Blend_Edges": "1", "E_CHECKBOX_Shockwave_Scale": "0",
         "E_SLIDER_Shockwave_Accel": "3", "E_SLIDER_Shockwave_CenterX": "50",
         "E_SLIDER_Shockwave_CenterY": "50", "E_SLIDER_Shockwave_End_Radius": "100",
         "E_SLIDER_Shockwave_End_Width": "5", "E_SLIDER_Shockwave_Start_Radius": "1",
         "E_SLIDER_Shockwave_Start_Width": "25"},
        # Tight imploding shockwave
        {"E_CHECKBOX_Shockwave_Blend_Edges": "1", "E_CHECKBOX_Shockwave_Scale": "0",
         "E_SLIDER_Shockwave_Accel": "0", "E_SLIDER_Shockwave_CenterX": "50",
         "E_SLIDER_Shockwave_CenterY": "50", "E_SLIDER_Shockwave_End_Radius": "0",
         "E_SLIDER_Shockwave_End_Width": "25", "E_SLIDER_Shockwave_Start_Radius": "100",
         "E_SLIDER_Shockwave_Start_Width": "5"},
        # Wide dramatic shockwave
        {"E_CHECKBOX_Shockwave_Blend_Edges": "1", "E_CHECKBOX_Shockwave_Scale": "0",
         "E_SLIDER_Shockwave_Accel": "0", "E_SLIDER_Shockwave_CenterX": "50",
         "E_SLIDER_Shockwave_CenterY": "50", "E_SLIDER_Shockwave_End_Radius": "245",
         "E_SLIDER_Shockwave_End_Width": "50", "E_SLIDER_Shockwave_Start_Radius": "1",
         "E_SLIDER_Shockwave_Start_Width": "10"},
    ],
    "Chase_left": [
        {"B_CHOICE_BufferStyle": "Per Model Single Line", "E_CHECKBOX_Chase_Group_All": "0",
         "E_CHOICE_Chase_Type1": "Left-Right", "E_CHOICE_Fade_Type": "None",
         "E_CHOICE_SingleStrand_Colors": "Palette", "E_NOTEBOOK_SSEFFECT_TYPE": "Chase",
         "E_SLIDER_Color_Mix1": "36", "E_SLIDER_Number_Chases": "1",
         "E_SLIDER_Chase_Rotations": "1"},
    ],
    "Chase_right": [
        {"B_CHOICE_BufferStyle": "Per Model Single Line", "E_CHECKBOX_Chase_Group_All": "0",
         "E_CHOICE_Chase_Type1": "Right-Left", "E_CHOICE_Fade_Type": "None",
         "E_CHOICE_SingleStrand_Colors": "Palette", "E_NOTEBOOK_SSEFFECT_TYPE": "Chase",
         "E_SLIDER_Color_Mix1": "36", "E_SLIDER_Number_Chases": "1",
         "E_SLIDER_Chase_Rotations": "1"},
    ],
    "Chase_from_middle": [
        {"B_CHOICE_BufferStyle": "Per Model Single Line", "E_CHECKBOX_Chase_Group_All": "0",
         "E_CHOICE_Chase_Type1": "From Middle", "E_CHOICE_Fade_Type": "None",
         "E_CHOICE_SingleStrand_Colors": "Palette", "E_NOTEBOOK_SSEFFECT_TYPE": "Chase",
         "E_SLIDER_Color_Mix1": "36", "E_SLIDER_Number_Chases": "1",
         "E_SLIDER_Chase_Rotations": "1"},
    ],
    "Chase_bounce": [
        {"B_CHOICE_BufferStyle": "Per Model Single Line", "E_CHECKBOX_Chase_Group_All": "0",
         "E_CHOICE_Chase_Type1": "Bounce from Left", "E_CHOICE_Fade_Type": "None",
         "E_CHOICE_SingleStrand_Colors": "Palette", "E_NOTEBOOK_SSEFFECT_TYPE": "Chase",
         "E_SLIDER_Color_Mix1": "36", "E_SLIDER_Number_Chases": "1",
         "E_SLIDER_Chase_Rotations": "1"},
    ],
    "Morph_quick": [
        {"E_CHECKBOX_Morph_End_Link": "0", "E_CHECKBOX_Morph_Start_Link": "0",
         "E_CHECKBOX_ShowHeadAtStart": "0", "E_SLIDER_MorphAccel": "0",
         "E_SLIDER_MorphDuration": "20", "E_SLIDER_MorphEndLength": "1",
         "E_SLIDER_MorphStartLength": "1", "E_SLIDER_Morph_End_X1": "0",
         "E_SLIDER_Morph_End_Y1": "0", "E_SLIDER_Morph_Start_X1": "100",
         "E_SLIDER_Morph_Start_Y1": "100"},
        {"E_CHECKBOX_Morph_End_Link": "0", "E_CHECKBOX_Morph_Start_Link": "0",
         "E_CHECKBOX_ShowHeadAtStart": "0", "E_SLIDER_MorphAccel": "0",
         "E_SLIDER_MorphDuration": "20", "E_SLIDER_MorphEndLength": "1",
         "E_SLIDER_MorphStartLength": "1", "E_SLIDER_Morph_End_X1": "100",
         "E_SLIDER_Morph_End_Y1": "100", "E_SLIDER_Morph_Start_X1": "0",
         "E_SLIDER_Morph_Start_Y1": "0"},
    ],
    "Twinkle_ambient": [
        {"E_CHECKBOX_Twinkle_ReRandom": "0", "E_CHECKBOX_Twinkle_Strobe": "0",
         "E_CHOICE_Twinkle_Style": "New Render Method",
         "E_SLIDER_Twinkle_Count": "3", "E_SLIDER_Twinkle_Steps": "14",
         "T_TEXTCTRL_Fadein": "0.5", "T_TEXTCTRL_Fadeout": "1.0"},
    ],
    "Twinkle_dense": [
        {"E_CHECKBOX_Twinkle_ReRandom": "0", "E_CHECKBOX_Twinkle_Strobe": "0",
         "E_CHOICE_Twinkle_Style": "New Render Method",
         "E_SLIDER_Twinkle_Count": "36", "E_SLIDER_Twinkle_Steps": "10",
         "T_TEXTCTRL_Fadein": "0.2", "T_TEXTCTRL_Fadeout": "0.5"},
    ],
    "ColorWash_slow": [
        {"E_CHECKBOX_ColorWash_CircularPalette": "1", "E_TEXTCTRL_ColorWash_Cycles": "1",
         "T_TEXTCTRL_Fadein": "1.0", "T_TEXTCTRL_Fadeout": "1.0"},
    ],
    "ColorWash_fast": [
        {"E_CHECKBOX_ColorWash_CircularPalette": "1", "E_TEXTCTRL_ColorWash_Cycles": "3"},
    ],
    "ColorWash_cycling": [
        {"E_CHECKBOX_ColorWash_CircularPalette": "1", "E_TEXTCTRL_ColorWash_Cycles": "20.0",
         "T_TEXTCTRL_Fadein": "2.5"},
    ],
    "Plasma_slow": [
        {"E_CHOICE_Plasma_Color": "Normal", "E_SLIDER_Plasma_Line_Density": "1",
         "E_SLIDER_Plasma_Speed": "10", "E_SLIDER_Plasma_Style": "10",
         "T_TEXTCTRL_Fadein": "0.5", "T_TEXTCTRL_Fadeout": "0.5"},
    ],
    "Plasma_fast": [
        {"E_CHOICE_Plasma_Color": "Normal", "E_SLIDER_Plasma_Line_Density": "1",
         "E_SLIDER_Plasma_Speed": "80", "E_SLIDER_Plasma_Style": "10",
         "T_TEXTCTRL_Fadein": "0.25", "T_TEXTCTRL_Fadeout": "0.25"},
    ],
    "Spirals_slow": [
        {"E_CHECKBOX_Spirals_3D": "1", "E_CHECKBOX_Spirals_Blend": "0",
         "E_CHECKBOX_Spirals_Grow": "0", "E_CHECKBOX_Spirals_Shrink": "0",
         "E_SLIDER_Spirals_Count": "1", "E_SLIDER_Spirals_Rotation": "20",
         "E_SLIDER_Spirals_Thickness": "50", "E_TEXTCTRL_Spirals_Movement": "1.0",
         "T_TEXTCTRL_Fadein": "0.5", "T_TEXTCTRL_Fadeout": "0.5"},
    ],
    "Spirals_fast": [
        {"E_CHECKBOX_Spirals_3D": "1", "E_CHECKBOX_Spirals_Blend": "0",
         "E_CHECKBOX_Spirals_Grow": "0", "E_CHECKBOX_Spirals_Shrink": "0",
         "E_SLIDER_Spirals_Count": "5", "E_SLIDER_Spirals_Rotation": "20",
         "E_SLIDER_Spirals_Thickness": "18", "E_TEXTCTRL_Spirals_Movement": "3.0"},
    ],
    "Spirals_reverse": [
        {"E_CHECKBOX_Spirals_3D": "1", "E_CHECKBOX_Spirals_Blend": "0",
         "E_CHECKBOX_Spirals_Grow": "0", "E_CHECKBOX_Spirals_Shrink": "0",
         "E_SLIDER_Spirals_Count": "1", "E_SLIDER_Spirals_Rotation": "-20",
         "E_SLIDER_Spirals_Thickness": "50", "E_TEXTCTRL_Spirals_Movement": "-1.0"},
    ],
    "Meteors_explode": [
        {"E_CHECKBOX_Meteors_UseMusic": "0", "E_CHOICE_Meteors_Effect": "Explode",
         "E_CHOICE_Meteors_Type": "Palette", "E_SLIDER_Meteors_Count": "5",
         "E_SLIDER_Meteors_Length": "25", "E_SLIDER_Meteors_Speed": "10",
         "E_SLIDER_Meteors_Swirl_Intensity": "0"},
    ],
    "Meteors_rain": [
        {"E_CHECKBOX_Meteors_UseMusic": "0", "E_CHOICE_Meteors_Effect": "Down",
         "E_CHOICE_Meteors_Type": "Palette", "E_SLIDER_Meteors_Count": "10",
         "E_SLIDER_Meteors_Length": "25", "E_SLIDER_Meteors_Speed": "15",
         "E_SLIDER_Meteors_Swirl_Intensity": "0"},
    ],
    "Pinwheel_sweep": [
        {"E_CHECKBOX_Pinwheel_Rotation": "1", "E_CHOICE_Pinwheel_3D": "Sweep",
         "E_CHOICE_Pinwheel_Style": "New Render Method",
         "E_SLIDER_Pinwheel_ArmSize": "150", "E_SLIDER_Pinwheel_Arms": "10",
         "E_SLIDER_Pinwheel_Speed": "5", "E_SLIDER_Pinwheel_Twist": "60"},
    ],
    "Butterfly_gentle": [
        {"E_CHOICE_Butterfly_Colors": "Palette", "E_CHOICE_Butterfly_Direction": "Normal",
         "E_SLIDER_Butterfly_Chunks": "1", "E_SLIDER_Butterfly_Skip": "2",
         "E_SLIDER_Butterfly_Speed": "10", "E_SLIDER_Butterfly_Style": "1",
         "T_TEXTCTRL_Fadein": "0.25", "T_TEXTCTRL_Fadeout": "0.25"},
    ],
    "Warp_mirror": [
        {"E_CHOICE_Warp_Treatment_APPLYLAST": "constant", "E_CHOICE_Warp_Type": "mirror",
         "E_SLIDER_Warp_X": "50", "E_SLIDER_Warp_Y": "50"},
    ],
    "Warp_wavy": [
        {"E_CHOICE_Warp_Treatment_APPLYLAST": "constant", "E_CHOICE_Warp_Type": "wavy",
         "E_SLIDER_Warp_Speed": "40"},
    ],
    "Marquee_default": [
        {"E_SLIDER_Marquee_Band_Size": "3", "E_SLIDER_Marquee_Skip_Size": "3",
         "E_SLIDER_Marquee_Speed": "3", "E_SLIDER_Marquee_Stagger": "0"},
    ],
    "Strobe_flash": [
        # Dense full-prop strobe; place it on beats for a rhythmic flash
        {"E_SLIDER_Number_Strobes": "50", "E_SLIDER_Strobe_Duration": "3",
         "E_SLIDER_Strobe_Type": "1", "E_CHECKBOX_Strobe_Music": "0"},
    ],
    "On_solid": [{}],
}


# ---------------------------------------------------------------------------
# Model role assignments — what role each model type plays in the show
# ---------------------------------------------------------------------------

# Background bed effects per model category (Layer 0)
BED_EFFECTS: dict[str, list[str]] = {
    "arch": ["ColorWash_slow", "Twinkle_ambient"],
    "tree": ["Spirals_slow", "Plasma_slow", "ColorWash_cycling"],
    "single_line": ["ColorWash_slow", "Twinkle_ambient"],
    "poly_line": ["ColorWash_slow", "Twinkle_ambient"],
    "window": ["ColorWash_slow", "On_solid"],
    "custom": ["Twinkle_ambient", "ColorWash_slow", "Butterfly_gentle"],
    "other": ["ColorWash_slow", "Twinkle_ambient"],
}

# Mid-layer motion effects per model category (Layer 1) — synced to bars/downbeats
MOTION_EFFECTS: dict[str, list[str]] = {
    "arch": ["Chase_from_middle", "Chase_left", "Chase_right", "Chase_bounce"],
    "tree": ["Spirals_fast", "Spirals_reverse", "Pinwheel_sweep", "Meteors_rain"],
    "single_line": ["Chase_left", "Chase_right", "Chase_bounce"],
    "poly_line": ["Chase_left", "Chase_right", "Chase_from_middle"],
    "window": ["Marquee_default"],
    "custom": ["Plasma_fast", "Warp_mirror", "Warp_wavy"],
    "other": ["Chase_from_middle", "ColorWash_fast"],
}

# Accent effects per model category (Layer 2) — on select beats only
ACCENT_EFFECTS: dict[str, list[str]] = {
    "arch": ["Morph_quick", "Shockwave_hit"],
    "tree": ["Shockwave_hit", "Meteors_explode"],
    "single_line": ["Morph_quick"],
    "poly_line": ["Morph_quick"],
    "window": ["Shockwave_hit"],
    "custom": ["Shockwave_hit", "Morph_quick"],
    "other": ["Shockwave_hit"],
}

# Section energy is ranked within each song rather than against fixed thresholds,
# since mastering levels vary so much that a whole song can sit above any cutoff
PEAK_FRACTION = 0.2       # loudest sections → "peak"
BREATHER_FRACTION = 0.25  # quietest sections → "breather"
_UNRANKED_LABELS = ("intro", "outro", "transition")  # always "breather"


def _rank_section_energy(
    sections: list[SongSection], forced: list[str | None] | None = None,
) -> list[str]:
    """Tier each section as "peak", "normal" or "breather" relative to the rest of the song.

    ``forced`` (from a song settings file) fixes the tier of any section where it isn't None.
    """
    ranked = sorted(
        (i for i, s in enumerate(sections) if s.label not in _UNRANKED_LABELS),
        key=lambda i: sections[i].energy_level,
        reverse=True,
    )
    n_peak = max(1, round(len(ranked) * PEAK_FRACTION)) if ranked else 0
    n_breather = round(len(ranked) * BREATHER_FRACTION)
    tiers = ["breather"] * len(sections)
    for rank, i in enumerate(ranked):
        if rank < n_peak:
            tiers[i] = "peak"
        elif rank < len(ranked) - n_breather:
            tiers[i] = "normal"
    if forced:
        tiers = [f or t for f, t in zip(forced, tiers)]
    return tiers


# ---------------------------------------------------------------------------
# Stem-driven effect overrides — when a stem dominates, shift effect choices
# ---------------------------------------------------------------------------

# Motion effects to prefer when a specific stem dominates the section
STEM_MOTION_OVERRIDES: dict[str, dict[str, list[str]]] = {
    "drums": {
        "arch": ["Chase_left", "Chase_right", "Chase_bounce"],
        "tree": ["Meteors_rain", "Meteors_explode"],
        "single_line": ["Chase_left", "Chase_right", "Chase_bounce"],
        "poly_line": ["Chase_left", "Chase_right"],
        "window": ["Marquee_default"],
        "custom": ["Plasma_fast", "Warp_wavy"],
        "other": ["Chase_from_middle", "ColorWash_fast"],
    },
    "bass": {
        "arch": ["ColorWash_fast", "Chase_from_middle"],
        "tree": ["Spirals_slow", "Pinwheel_sweep"],
        "single_line": ["ColorWash_fast", "Chase_from_middle"],
        "poly_line": ["ColorWash_fast"],
        "window": ["ColorWash_fast"],
        "custom": ["Plasma_slow", "Warp_mirror"],
        "other": ["ColorWash_fast"],
    },
    "vocals": {
        "arch": ["ColorWash_slow", "Twinkle_ambient"],
        "tree": ["Spirals_slow", "Plasma_slow"],
        "single_line": ["ColorWash_slow"],
        "poly_line": ["ColorWash_slow"],
        "window": ["ColorWash_slow"],
        "custom": ["Butterfly_gentle", "Twinkle_ambient"],
        "other": ["ColorWash_slow"],
    },
    "other": {
        "arch": ["Chase_left", "Chase_right", "Chase_from_middle"],
        "tree": ["Spirals_fast", "Spirals_reverse", "Pinwheel_sweep"],
        "single_line": ["Chase_left", "Chase_right"],
        "poly_line": ["Chase_left", "Chase_right"],
        "window": ["Marquee_default"],
        "custom": ["Plasma_fast", "Warp_wavy", "Warp_mirror"],
        "other": ["Chase_from_middle"],
    },
}

# Accent effects to prefer when a specific stem dominates
STEM_ACCENT_OVERRIDES: dict[str, list[str]] = {
    "drums": ["Shockwave_hit", "Morph_quick"],
    "bass": ["Morph_quick"],
    "vocals": [],  # no stem preference — fall back to the model category's accents
    "other": ["Shockwave_hit"],
}

# Section-type → effect palette guidance
SECTION_TYPE_CONFIG: dict[str, dict] = {
    "intro": {"use_motion": False, "use_accents": False, "bed_override": "ColorWash_slow"},
    "outro": {"use_motion": False, "use_accents": False, "bed_override": "Twinkle_ambient"},
    "verse": {"use_motion": True, "use_accents": False},
    "chorus": {"use_motion": True, "use_accents": True},
    "bridge": {"use_motion": True, "use_accents": True,
               "motion_override": {"tree": ["Pinwheel_sweep", "Spirals_reverse"],
                                   "custom": ["Warp_mirror", "Warp_wavy"],
                                   "arch": ["Chase_bounce"],
                                   "single_line": ["Chase_bounce"],
                                   "poly_line": ["Chase_bounce"]}},
    "transition": {"use_motion": False, "use_accents": False, "bed_override": "ColorWash_slow"},
    "unknown": {"use_motion": True, "use_accents": False},
    "instrumental": {"use_motion": True, "use_accents": True},
}


# ---------------------------------------------------------------------------
# Model grouping — auto-detect groups from model names
# ---------------------------------------------------------------------------

# Patterns to detect model groups: (group_name, name_patterns, model_category_override)
# DEPRECATED — kept only as fallback when show has no xLights-defined model groups
_LEGACY_GROUP_PATTERNS: list[tuple[str, list[str], str | None]] = [
    ("canes", ["Left Cane", "Right Cane"], "arch"),
    ("arches", ["Arches-"], "arch"),
    ("flakes", ["Flake "], "custom"),
    ("spinners", ["Spinner "], "custom"),
    ("rooflines", ["Roofline", "Roof Peak", "Garage Roofline", "Porch Roofline"], "single_line"),
    ("pillars", ["Pillar"], "single_line"),
    ("windows_doors", ["Window", "Door"], "window"),
    ("bulbs", ["Bulb "], "custom"),
]


def _detect_model_groups(
    models: list[LightModel],
    show_config: ShowConfig,
) -> tuple[
    dict[str, list[LightModel]],  # group_name → member models
    list[LightModel],              # ungrouped models
    dict[str, str],                # group_name → model_category
]:
    """Auto-detect model groups, preferring xLights-defined groups.

    Checks show_config.model_groups first (parsed from xlights_rgbeffects.xml).
    Falls back to common-prefix detection if no xLights groups exist.

    Returns grouped models (effects applied identically to all members),
    ungrouped models (effects applied individually), and category overrides.
    """
    model_by_name: dict[str, LightModel] = {m.name: m for m in models}

    # --- Strategy 1: Use xLights-defined model groups ---
    if show_config.model_groups:
        groups: dict[str, list[LightModel]] = {}
        group_categories: dict[str, str] = {}
        grouped_names: set[str] = set()

        # Smallest groups first, skipping any group that overlaps one already taken, so
        # umbrella groups like "ALL" don't stack a second set of effects on every model
        candidates = [
            (mg.name, [model_by_name[n] for n in mg.members if n in model_by_name])
            for mg in show_config.model_groups
        ]
        candidates.sort(key=lambda c: len(c[1]))
        for name, members in candidates:
            if len(members) >= 2 and not any(m.name in grouped_names for m in members):
                groups[name] = members
                grouped_names.update(m.name for m in members)
                # Derive category from majority of member model categories
                cats = [m.model_category for m in members]
                group_categories[name] = max(sorted(set(cats)), key=cats.count)

        if groups:
            ungrouped = [m for m in models if m.name not in grouped_names]
            logger.info(f"Using {len(groups)} xLights-defined model groups")
            return groups, ungrouped, group_categories

    # --- Strategy 2: Automatic common-prefix grouping ---
    # Group models that share a common prefix (e.g. "Left Cane 1", "Left Cane 2" → "left_cane")
    groups = {}
    group_categories = {}
    grouped_names: set[str] = set()

    prefix_buckets: dict[str, list[LightModel]] = {}
    for model in models:
        # Strip trailing digits/spaces to find the base prefix
        name = model.name.rstrip("0123456789 -")
        if name and name != model.name:
            prefix_buckets.setdefault(name, []).append(model)

    for prefix, members in prefix_buckets.items():
        if len(members) >= 2:
            group_key = prefix.strip().lower().replace(" ", "_")
            groups[group_key] = members
            grouped_names.update(m.name for m in members)
            cats = [m.model_category for m in members]
            group_categories[group_key] = max(sorted(set(cats)), key=cats.count)

    ungrouped = [m for m in models if m.name not in grouped_names]
    logger.info(f"Auto-detected {len(groups)} model groups by common prefix")
    return groups, ungrouped, group_categories


PRIORITIES = ("hero", "key", "normal", "optional")


def _resolve_model_priorities(
    priorities: dict[str, str] | None, show_config: ShowConfig,
) -> dict[str, str]:
    """Expand model/group priority settings to a per-model map.

    Group entries apply to their members; an entry naming a model directly wins.
    """
    if not priorities:
        return {}
    resolved: dict[str, str] = {}
    for mg in show_config.model_groups:
        if mg.name in priorities:
            for member in mg.members:
                resolved[member] = priorities[mg.name]
    model_names = {m.name for m in show_config.models}
    resolved.update({n: p for n, p in priorities.items() if n in model_names})
    return resolved


def _load_inputs(mp3_path: Path, show_path: Path | None):
    """Show config, per-song settings and show rules, validated. Returns a dict on error."""
    if not show_path or not show_path.exists():
        return {"error": f"Show path not found: {show_path}"}
    show_config = load_show_config(show_path)
    if not show_config.models:
        return {"error": "No models found in show configuration"}
    try:
        settings, settings_path = song_settings_mod.load_song_settings(mp3_path)
        if settings:
            song_settings_mod.validate_models(settings, show_config)
        rules, rules_path = show_rules_mod.load_show_rules(show_path)
        if rules:
            show_rules_mod.validate_models(rules, show_config)
        if settings and settings.singing:
            singing = rules.style.singing if rules else None
            if not (singing and singing.moments):
                raise ValueError("Song settings override 'singing', but the show rules define no singing.moments")
            singing.moments_for(settings.singing)
    except ValueError as exc:
        return {"error": str(exc)}
    return show_config, settings, settings_path, rules, rules_path


def generate_sequence(
    mp3_path: Path,
    show_path: Path | None,
    mode: str = "auto",
    palette_hint: str | None = None,
    theme: str | None = None,
    audio_config: AudioConfig | None = None,
    vocal_assignments: dict[str, str] | None = None,
    model_priorities: dict[str, str] | None = None,
) -> dict:
    """Generate a complete xLights sequence from a music file.

    Args:
        vocal_assignments: Optional mapping of model_name → vocal track name.
            Use "all" as key to assign a track to all singing models.
            If None, every singer uses the vocal-stem track.
        model_priorities: Optional mapping of model or group name → priority
            ("hero", "key", "normal", "optional").
    """
    loaded = _load_inputs(mp3_path, show_path)
    if isinstance(loaded, dict):
        return loaded
    show_config, settings, settings_path, rules, rules_path = loaded

    if mode not in ("auto", "guided", "template"):
        return {"error": f"Invalid mode: {mode}"}
    if mode == "template":
        return {"error": "Template mode not yet implemented. Use 'auto' or 'guided'."}

    analysis = full_analysis(mp3_path, audio_config)
    if mode == "guided":
        return _generate_guided_preview(analysis, show_config, rules)

    try:
        result = _generate_auto(
            analysis, show_config, mp3_path, palette_hint, theme,
            vocal_assignments=vocal_assignments,
            model_priorities=model_priorities,
            song_settings=settings,
            show_rules=rules,
        )
    except ValueError as exc:  # a per-song setting breaks a show rule
        return {"error": str(exc)}
    if result.get("success"):
        if settings_path:
            result["song_settings"] = str(settings_path)
        if rules_path:
            result["show_rules"] = str(rules_path)
    return result


def preview_sequence_plan(
    mp3_path: Path,
    show_path: Path | None,
    mode: str = "auto",
    audio_config: AudioConfig | None = None,
    vocal_assignments: dict[str, str] | None = None,
    model_priorities: dict[str, str] | None = None,
    palette_hint: str | None = None,
    theme: str | None = None,
) -> dict:
    """Preview exactly what create_sequence would produce, without writing anything.

    Runs the same generation (song settings, show rules, singing, palettes) and
    summarizes it; ``plan_id`` matches the one create_sequence returns for the same inputs.
    """
    loaded = _load_inputs(mp3_path, show_path)
    if isinstance(loaded, dict):
        return loaded
    show_config, settings, settings_path, rules, rules_path = loaded
    if mode not in ("auto", "guided", "template"):
        return {"error": f"Invalid mode: {mode}"}
    if mode == "template":
        return {"error": "Template mode not yet implemented. Use 'auto' or 'guided'."}

    analysis = full_analysis(mp3_path, audio_config)
    if mode == "guided":
        return _generate_guided_preview(analysis, show_config, rules)
    try:
        plan = _generate_auto(
            analysis, show_config, mp3_path, palette_hint, theme,
            vocal_assignments=vocal_assignments, model_priorities=model_priorities,
            song_settings=settings, show_rules=rules, write=False,
        )
    except ValueError as exc:
        return {"error": str(exc)}
    if "spec" not in plan:
        return plan
    summary = _summarize_plan(plan, show_config, rules)
    return {
        "song": mp3_path.stem,
        "plan_id": plan["plan_id"],
        "song_settings": str(settings_path) if settings_path else None,
        "show_rules": str(rules_path) if rules_path else None,
        "duration": f"{plan['analysis'].duration_seconds:.1f}s",
        "tempo": f"{plan['analysis'].beats.tempo:.0f} BPM",
        "beat_count": len(plan["analysis"].beats.beat_times),
        **summary,
    }


def _summarize_plan(plan: dict, show_config: ShowConfig, rules: ShowRules | None) -> dict:
    """Condense a dry-run plan into per-section and per-rule outcomes."""
    spec, analysis, tiers = plan["spec"], plan["analysis"], plan["tiers"]
    effects = spec.effects
    sig = set(plan["signatures"])
    excluded = sorted(show_rules_mod.excluded_models(rules, show_config)) if rules else []
    style = rules.style if rules else None
    arch_names = {a.model for a in style.arch_chase.arches} if style and style.arch_chase else set()
    floods = set(song_settings_mod.expand_models(style.floods, show_config)) if style else set()
    white = rules.safety.white if rules else None
    hit_models = (set(song_settings_mod.expand_models(style.final_hit.models, show_config))
                  if style and style.final_hit else set())

    def overlaps(e, s):  # by at least 50 ms, so boundary slivers don't count
        return min(e.end_time_ms, s.end_time_ms) - max(e.start_time_ms, s.start_time_ms) >= 50

    sections = []
    all_models = [m.name for m in show_config.models]
    for i, s in enumerate(analysis.sections):
        here = [e for e in effects if overlaps(e, s)]
        lit = {e.model_name for e in here}
        # The flood color showing as the section starts (it may be held from a singing moment)
        flood = next((e.palette.colors[0] for e in effects
                      if e.model_name in floods and e.layer == 0 and e.palette
                      and e.start_time_ms <= s.start_time_ms < e.end_time_ms), None)
        sections.append({
            "label": s.label, "tier": tiers[i],
            "start": f"{s.start_time:.1f}s", "end": f"{s.end_time:.1f}s", "energy": f"{s.energy_level:.2f}",
            "symmetry": (style.symmetry.by_tier.get(tiers[i], style.symmetry.default)
                         if style and style.symmetry else None),
            "arch_chase": any(e.model_name in arch_names and e.layer == 1 for e in here),
            "flood_color": flood,
            "props_lit": len(lit),
            "props_dark": [m for m in all_models if m not in lit and m not in excluded],
            "accent_props": sorted({e.model_name for e in here if e.palette and sig & set(e.palette.colors)}),
        })

    strobes = sorted((e for e in effects if e.effect_name == "Strobe"), key=lambda e: e.start_time_ms)
    starts = sorted({e.start_time_ms for e in strobes})
    beat = 60 / max(analysis.beats.tempo, 1)
    gaps = [(b - a) / 1000 / beat for a, b in zip(starts, starts[1:])]
    full_moments = [e for e in effects if white and e.palette and white.full_white in e.palette.colors]
    white_only: dict[str, float] = {}
    if white:
        for e in effects:
            if e.model_name not in hit_models and e.effect_name not in white.full_white_effects \
                    and show_rules_mod.shows_only_white(e, white.is_white):
                white_only[e.model_name] = white_only.get(e.model_name, 0) + (e.end_time_ms - e.start_time_ms) / 1000

    props = {}
    for e in effects:
        p = props.setdefault(e.model_name, {"effects": 0, "sections_lit": set()})
        p["effects"] += 1
        p["sections_lit"].update(i for i, s in enumerate(analysis.sections) if overlaps(e, s))
    return {
        "total_effects": len(effects),
        "models_with_effects": len(props),
        "sections": sections,
        "rules": {
            "excluded": excluded,
            "final_hit": ({"time": f"{plan['final_hit']:.2f}s", "models": sorted(hit_models)}
                          if plan["final_hit"] is not None and hit_models else None),
            "strobes": ({"models": sorted({e.model_name for e in strobes}),
                         "start": f"{starts[0] / 1000:.2f}s", "end": f"{starts[-1] / 1000:.2f}s",
                         "hits": len(starts),
                         "spacing_beats": round(sorted(gaps)[len(gaps) // 2], 2) if gaps else None}
                        if strobes else None),
            "signature_colors": plan["signatures"],
            "accent_share": plan["share"],
            "full_white_moments": len(full_moments),
            "white_only_seconds": {k: round(v, 2) for k, v in white_only.items()},
            "lyrics": plan["lyrics"],
            "vocal_assignments": plan["vocal_assignments"],
            "singing": _moments_summary(plan["singing_moments"], analysis, plan["trimmed_phrases"]),
        },
        "props": {k: {"effects": v["effects"], "sections_lit": len(v["sections_lit"])}
                  for k, v in sorted(props.items())},
    }


# ---------------------------------------------------------------------------
# Auto-generation engine
# ---------------------------------------------------------------------------


def _generate_auto(
    analysis: SongAnalysis,
    show_config: ShowConfig,
    mp3_path: Path,
    palette_hint: str | None,
    theme: str | None,
    vocal_assignments: dict[str, str] | None = None,
    model_priorities: dict[str, str] | None = None,
    song_settings: SongSettings | None = None,
    show_rules: ShowRules | None = None,
    write: bool = True,
) -> dict:
    """Professional-quality automatic sequence generation.

    With ``write=False`` nothing is written; the plan (spec, tiers, rule outcomes and a
    fingerprint matching the one create_sequence reports) is returned for preview_plan.

    Uses a 3-layer approach with model group synchronization:
      Layer 0: Background bed (twinkle, wash) — runs full section
      Layer 1: Motion (chase, spirals) — synced to bars
      Layer 2: Accent hits (shockwave, morph) — on drum hits, or the beat grid without stems

    Grouped models (canes, arches, etc.) get identical effects for synchronization.

    Sections are tiered by energy relative to the rest of the song (peak / normal /
    breather). Accents land on every beat in peaks; elsewhere hero models accent every
    other beat and other models once per bar; breathers get no accents.

    Model priorities scale how busy a model is: "hero" models get motion in every
    section but the intro/outro and faster variation; "key" models keep motion through
    breathers; "optional" models only light up in peak sections. Prioritized models
    never run singing faces.

    A song settings file can re-anchor bars, force section tiers and add custom effects.
    Show rules (see show_rules.py) then own exclusions, singers, floods, the final hit,
    symmetry, the arch chase, strobe placement and palettes.
    """
    forced_tiers = None
    if song_settings:
        analysis, forced_tiers = song_settings_mod.apply_to_analysis(song_settings, analysis)

    rng = random.Random(_stable_hash(mp3_path.stem))  # deterministic per song

    # Build palette pool
    theme_palettes = get_theme_palettes(theme)
    palette_pool = list(theme_palettes.values())
    if not palette_pool:
        palette_pool = [ColorPalette(colors=["#FFFFFF"], active_colors=[1])]

    all_palettes: list[ColorPalette] = list(palette_pool)
    all_effects: list[EffectPlacement] = []

    # Pre-compute beats within each section
    section_beats = _precompute_section_beats(analysis)
    section_downbeats = _precompute_section_downbeats(analysis)
    section_tiers = _rank_section_energy(analysis.sections, forced_tiers)

    # Models the rules own (excluded, floods, final-hit props) skip generic generation
    skip = show_rules_mod.generic_skip(show_rules, show_config)
    generic_models = [m for m in show_config.models if m.name not in skip]

    # Detect model groups (uses xLights-defined groups, falls back to prefix detection)
    groups, ungrouped, group_categories = _detect_model_groups(generic_models, show_config)
    logger.info(f"Detected {len(groups)} model groups, {len(ungrouped)} ungrouped models")
    for gname, members in groups.items():
        logger.info(f"  Group '{gname}': {[m.name for m in members]}")

    priority_of = _resolve_model_priorities(model_priorities, show_config)
    if show_rules and show_rules.style.priorities:  # show rules win over saved priorities
        priority_of.update(_resolve_model_priorities(show_rules.style.priorities, show_config))

    # Identify singing models — the rules' singers, else normal-priority face models
    singers = set(show_rules.style.singing.singers) if show_rules and show_rules.style.singing else None
    singing_models: dict[str, str] = {}  # model_name → face_definition_name
    for m in generic_models:
        if m.face_definitions and (
            m.name in singers if singers is not None else priority_of.get(m.name, "normal") == "normal"
        ):
            singing_models[m.name] = m.face_definitions[0]

    # Singers default to the vocal-stem track. Without usable lyrics — none found, or a
    # low-confidence transcription — they run regular effects with the rules' fallbacks.
    vocal_tracks = _try_extract_vocal_tracks(mp3_path) if singing_models else []
    min_conf = show_rules.style.singing.min_confidence if show_rules and show_rules.style.singing \
        else DEFAULT_MIN_LYRIC_CONFIDENCE
    lyrics_status = "no singers" if not singing_models else "no lyrics found"
    if vocal_tracks:
        default_track = next((t for t in vocal_tracks if t.source == "vocals_stem"), vocal_tracks[0])
        lyrics_status = f"'{default_track.track_name}' confidence {default_track.confidence:.2f}"
        if default_track.confidence < min_conf:
            lyrics_status += f" < {min_conf:.2f}: treated as no faces"
            vocal_tracks = []
        elif vocal_assignments is None:
            vocal_assignments = {"all": default_track.track_name}
    if singing_models and not vocal_tracks:
        logger.info(f"Singing models treated as regular models ({lyrics_status})")
        singing_models = {}
        if show_rules and show_rules.style.singing:
            priority_of.update(_resolve_model_priorities(show_rules.style.singing.without_lyrics, show_config))
    singing_model_names = set(singing_models)

    # Singing moments: faces sing only in chosen vocal feature moments; in between they stay
    # in the regular pipeline at the rules' between-moments priority (peaks only)
    moments_cfg = (show_rules.style.singing.moments_for(song_settings.singing if song_settings else None)
                   if show_rules and show_rules.style.singing else None)
    if moments_cfg and singing_models:
        priority_of.update({n: moments_cfg.between_moments for n in singing_models})
        singing_model_names = set()

    # Remove singing models from groups and ungrouped lists
    for group_name in list(groups.keys()):
        groups[group_name] = [m for m in groups[group_name] if m.name not in singing_model_names]
        if not groups[group_name]:
            del groups[group_name]
            group_categories.pop(group_name, None)
    ungrouped = [m for m in ungrouped if m.name not in singing_model_names]

    if singing_models:
        logger.info(f"Singing models excluded from regular pipeline: {list(singing_models.keys())}")

    # Members with different priorities can't share one effect plan — split them
    for group_name in list(groups):
        by_priority: dict[str, list[LightModel]] = {}
        for m in groups[group_name]:
            by_priority.setdefault(priority_of.get(m.name, "normal"), []).append(m)
        if len(by_priority) > 1:
            cat = group_categories.pop(group_name)
            del groups[group_name]
            for p, members in by_priority.items():
                groups[f"{group_name} ({p})"] = members
                group_categories[f"{group_name} ({p})"] = cat

    # Track last motion effect key to avoid consecutive repeats
    last_motion_key_per_group: dict[str, str] = {}

    # Helper to generate effects for a model category and apply to a list of model names
    def generate_for_models(model_names: list[str], cat: str, group_seed: str):
        group_rng = random.Random(_stable_hash(group_seed + mp3_path.stem))
        stem_data: StemAnalysis = analysis.stem_analysis
        priority = priority_of.get(model_names[0], "normal")  # groups are split by priority

        for sec_idx, section in enumerate(analysis.sections):
            is_peak = section_tiers[sec_idx] == "peak"
            is_breather = section_tiers[sec_idx] == "breather"
            if priority == "optional" and not is_peak:
                continue

            pal_idx = (sec_idx // 2 + _stable_hash(group_seed)) % len(palette_pool)
            palette = palette_pool[pal_idx]

            downbeats_in_section = section_downbeats.get(sec_idx, [])

            # Determine section type config
            sec_config = SECTION_TYPE_CONFIG.get(section.label, SECTION_TYPE_CONFIG["unknown"])
            use_motion = sec_config.get("use_motion", True) and not is_breather
            use_accents = is_peak or (sec_config.get("use_accents", False) and not is_breather)
            if priority == "hero":
                use_motion = section.label not in ("intro", "outro")
            elif priority == "key":
                use_motion = sec_config.get("use_motion", True)

            # Determine dominant stem for this section
            dom_stem = "other"
            if stem_data.available:
                dom_stem = stem_data.dominant_stem(section.start_time, section.end_time)

            # --- LAYER 0: Background bed ---
            bed_override = sec_config.get("bed_override")
            if bed_override:
                bed_key = bed_override
            elif is_peak:
                bed_choices = BED_EFFECTS.get(cat, BED_EFFECTS["other"])
                bed_key = "Twinkle_dense" if "Twinkle_ambient" in bed_choices else bed_choices[0]
            else:
                bed_choices = BED_EFFECTS.get(cat, BED_EFFECTS["other"])
                bed_key = group_rng.choice(bed_choices)

            bed_settings = _get_settings(bed_key)
            for name in model_names:
                all_effects.append(EffectPlacement(
                    model_name=name, layer=0,
                    effect_name=_effect_name_from_key(bed_key),
                    start_time_ms=section.start_time_ms,
                    end_time_ms=section.end_time_ms,
                    settings=bed_settings, palette=palette,
                ))

            # --- LAYER 1: Motion (stem-reactive, section-varied) ---
            if use_motion:
                # Pick motion choices based on stem dominance and section type
                motion_override = sec_config.get("motion_override", {}).get(cat)
                if motion_override:
                    motion_choices = motion_override
                elif stem_data.available and dom_stem in STEM_MOTION_OVERRIDES:
                    stem_motions = STEM_MOTION_OVERRIDES[dom_stem].get(cat)
                    motion_choices = stem_motions if stem_motions else MOTION_EFFECTS.get(cat, MOTION_EFFECTS["other"])
                else:
                    motion_choices = MOTION_EFFECTS.get(cat, MOTION_EFFECTS["other"])

                # Avoid repeating the same motion as the previous section
                last_key = last_motion_key_per_group.get(group_seed)
                available = [k for k in motion_choices if k != last_key]
                if not available:
                    available = motion_choices

                if len(downbeats_in_section) >= 2:
                    # Vary effect every 2-4 bars within the section
                    bars_per_variant = 2 if is_peak else 4
                    if priority == "hero":
                        bars_per_variant //= 2
                    variant_idx = 0
                    for j in range(len(downbeats_in_section) - 1):
                        if j > 0 and j % bars_per_variant == 0:
                            variant_idx += 1

                        motion_key = available[variant_idx % len(available)]
                        motion_settings = _get_settings(motion_key)

                        start_ms = int(downbeats_in_section[j] * 1000)
                        end_ms = int(downbeats_in_section[j + 1] * 1000)
                        motion_pal_idx = (pal_idx + j // 2) % len(palette_pool)
                        for name in model_names:
                            all_effects.append(EffectPlacement(
                                model_name=name, layer=1,
                                effect_name=_effect_name_from_key(motion_key),
                                start_time_ms=start_ms, end_time_ms=end_ms,
                                settings=motion_settings,
                                palette=palette_pool[motion_pal_idx],
                            ))
                    last_motion_key_per_group[group_seed] = motion_key
                else:
                    motion_key = group_rng.choice(available)
                    motion_settings = _get_settings(motion_key)
                    for name in model_names:
                        all_effects.append(EffectPlacement(
                            model_name=name, layer=1,
                            effect_name=_effect_name_from_key(motion_key),
                            start_time_ms=section.start_time_ms,
                            end_time_ms=section.end_time_ms,
                            settings=motion_settings, palette=palette,
                        ))
                    last_motion_key_per_group[group_seed] = motion_key

            # --- LAYER 2: Accent hits (stem-reactive timing) ---
            if use_accents:
                # Choose accent effects based on dominant stem (vocal-led sections have no
                # stem preference and use the category's accents)
                accent_choices = STEM_ACCENT_OVERRIDES.get(dom_stem) or ACCENT_EFFECTS.get(cat, ACCENT_EFFECTS["other"])

                accent_key = group_rng.choice(accent_choices)
                accent_settings = _get_settings(accent_key)

                # Every beat in peaks, every other beat for heroes, once per bar otherwise
                if is_peak:
                    beat_step = 1
                elif priority == "hero":
                    beat_step = 2
                else:
                    beat_step = 4

                if beat_step == 4:
                    accent_times = downbeats_in_section
                else:
                    beats_here = section_beats.get(sec_idx, [])
                    # Keep every-other-beat hits on beats 1 and 3 of the bar
                    if beat_step == 2 and downbeats_in_section and downbeats_in_section[0] in beats_here:
                        beats_here = beats_here[beats_here.index(downbeats_in_section[0]) % 2:]
                    accent_times = beats_here[::beat_step]
                if stem_data.available and "drums" in stem_data.stems:
                    accent_times = _snap_to_onsets(accent_times, stem_data.get_onsets_in_range(
                        "drums", section.start_time - DRUM_SNAP_SECONDS, section.end_time
                    ))

                beat_dur_ms = int(60000 / max(analysis.beats.tempo, 60))
                accent_dur = min(beat_dur_ms, 500)
                accent_ms = [int(t * 1000) for t in accent_times]

                for j, t_ms in enumerate(accent_ms):
                    # Never run into the next hit or past the section
                    next_ms = accent_ms[j + 1] if j + 1 < len(accent_ms) else section.end_time_ms
                    end_ms = min(t_ms + accent_dur, next_ms)
                    if end_ms <= t_ms:
                        continue
                    accent_pal_idx = (pal_idx + j) % len(palette_pool)
                    for name in model_names:
                        all_effects.append(EffectPlacement(
                            model_name=name, layer=2,
                            effect_name=_effect_name_from_key(accent_key),
                            start_time_ms=t_ms,
                            end_time_ms=end_ms,
                            settings=accent_settings,
                            palette=palette_pool[accent_pal_idx],
                        ))

    # Process grouped models — all members get identical effects
    for group_name, members in groups.items():
        cat = group_categories[group_name]
        member_names = [m.name for m in members]
        generate_for_models(member_names, cat, group_name)

    # Process ungrouped models individually
    for model in ungrouped:
        generate_for_models([model.name], model.model_category, model.name)

    # --- Instrument timing tracks from stem onsets ---
    timing_tracks: list[TimingTrack] = []

    if analysis.stem_analysis.available:
        stem_track_map = {
            "drums": "Drums",
            "bass": "Bass",
            "other": "Instruments",
        }
        beat_dur_ms = int(60000 / max(analysis.beats.tempo, 60))
        onset_label_dur = min(beat_dur_ms // 2, 200)  # label duration for onset markers

        for stem_name, track_name in stem_track_map.items():
            if stem_name not in analysis.stem_analysis.stems:
                continue
            stem = analysis.stem_analysis.stems[stem_name]
            if not stem.onset_times:
                continue

            labels = []
            onset_ms = [int(t * 1000) for t in stem.onset_times]
            for j, t_ms in enumerate(onset_ms):
                # Clip to the next onset so marks never overlap
                end_ms = min(t_ms + onset_label_dur, onset_ms[j + 1]) if j + 1 < len(onset_ms) else t_ms + onset_label_dur
                if end_ms <= t_ms:
                    continue
                labels.append(TimingTrackLabel(
                    label="x",
                    start_time_ms=t_ms,
                    end_time_ms=end_ms,
                ))

            timing_tracks.append(TimingTrack(
                name=track_name,
                labels=[labels],
            ))
            logger.info(f"Added '{track_name}' timing track: {len(labels)} onsets")

    # --- Singing Faces: multi-track extraction + vocal assignment ---
    has_lyrics = False
    resolved_assignments: dict[str, str] = {}

    if singing_models:
        if vocal_tracks:
            has_lyrics = True

            # Resolve "all" shorthand → expand to every singing model
            resolved_assignments: dict[str, str] = {}
            if "all" in vocal_assignments:
                track_name = vocal_assignments["all"]
                for mname in singing_models:
                    resolved_assignments[mname] = track_name
            else:
                resolved_assignments = dict(vocal_assignments)

            # Build a timing track name → LyricTrack lookup
            track_lookup: dict[str, "LyricTrack"] = {t.track_name: t for t in vocal_tracks}

            # Add ALL vocal timing tracks to the sequence (not just assigned ones)
            for lyric_track in vocal_tracks:
                word_labels = []
                for w in lyric_track.words:
                    word_labels.append(TimingTrackLabel(
                        label=w.word,
                        start_time_ms=int(w.start_time * 1000),
                        end_time_ms=int(w.end_time * 1000),
                    ))
                phoneme_labels = []
                for p in lyric_track.phonemes:
                    phoneme_labels.append(TimingTrackLabel(
                        label=p.phoneme,
                        start_time_ms=p.start_time_ms,
                        end_time_ms=p.end_time_ms,
                    ))
                word_labels = _clean_labels(word_labels)
                timing_tracks.append(TimingTrack(
                    name=lyric_track.track_name,
                    labels=[word_labels, word_labels, _clean_labels(phoneme_labels)],
                ))

            logger.info(f"Added {len(timing_tracks)} timing tracks to sequence")

            # Each singer's timing track: its assignment, else the first (vocal-stem) track
            face_tracks = {
                n: resolved_assignments[n] if resolved_assignments.get(n) in track_lookup else vocal_tracks[0].track_name
                for n in singing_models
            }

            # Whole-song faces, unless the rules limit singing to moments (placed later)
            for model_name, face_def in (singing_models.items() if not moments_cfg else []):
                assigned_track_name = resolved_assignments.get(model_name)
                assigned_track = track_lookup.get(assigned_track_name) if assigned_track_name else None

                if not assigned_track:
                    # No assignment for this model — use the first available track
                    assigned_track = vocal_tracks[0]
                    assigned_track_name = assigned_track.track_name
                    logger.info(f"No assignment for '{model_name}', defaulting to '{assigned_track_name}'")

                singing_rng = random.Random(_stable_hash(model_name + mp3_path.stem))

                # Layer 0: Section-aware background that illuminates the whole model
                for sec_idx, section in enumerate(analysis.sections):
                    pal_idx = sec_idx % len(palette_pool)
                    palette = palette_pool[pal_idx]

                    if section_tiers[sec_idx] == "peak":
                        bed_key = "Twinkle_dense"
                    elif section_tiers[sec_idx] == "breather":
                        bed_key = "ColorWash_slow"
                    else:
                        bed_key = singing_rng.choice(["Twinkle_ambient", "ColorWash_cycling", "Butterfly_gentle"])

                    bed_settings = _get_settings(bed_key)
                    all_effects.append(EffectPlacement(
                        model_name=model_name, layer=0,
                        effect_name=_effect_name_from_key(bed_key),
                        start_time_ms=section.start_time_ms,
                        end_time_ms=section.end_time_ms,
                        settings=bed_settings, palette=palette,
                    ))

                # Layer 1: Faces effect with the assigned vocal track
                faces_settings = {
                    "E_CHECKBOX_Faces_Outline": "1",
                    "E_CHOICE_Faces_EyeBlinkDuration": "Normal",
                    "E_CHOICE_Faces_EyeBlinkFrequency": "Normal",
                    "E_CHOICE_Faces_Eyes": "Auto",
                    "E_CHOICE_Faces_FaceDefinition": face_def,
                    "E_CHOICE_Faces_TimingTrack": assigned_track_name,
                    "T_TEXTCTRL_Fadein": "0.5",
                    "T_TEXTCTRL_Fadeout": "0.5",
                }
                all_effects.append(EffectPlacement(
                    model_name=model_name,
                    layer=1,
                    effect_name="Faces",
                    start_time_ms=0,
                    end_time_ms=analysis.duration_ms,
                    settings=faces_settings,
                    palette=all_palettes[0] if all_palettes else ColorPalette(),
                ))

            logger.info(
                f"Added singing pipeline for {list(singing_models.keys())} "
                f"with assignments: {resolved_assignments}"
            )

    signatures: list[str] = []
    share = finale = final_hit = None
    singing_moments: list = []
    trimmed_phrases: list = []
    if show_rules:
        finale = show_rules_mod.finale_range(analysis, section_tiers, song_settings)
        final_hit = show_rules_mod.final_hit_time(analysis, section_tiers, song_settings)
        # Style: roles, symmetry, arch chase
        all_effects = show_rules_mod.apply_structure(
            show_rules, all_effects, analysis, section_tiers, show_config, priority_of, final_hit,
            preset=lambda key: (_effect_name_from_key(key), _get_settings(key)),
        )
        # Style: faces sing only in vocal feature moments; other props calm down meanwhile
        if moments_cfg and has_lyrics and singing_models:
            stem_track = next((t for t in vocal_tracks if t.source == "vocals_stem"), None)
            singing_moments = show_rules_mod.pick_singing_moments(
                stem_track.words if stem_track else [], analysis, section_tiers, moments_cfg,
                trimmed=trimmed_phrases,
            )
            all_effects = show_rules_mod.apply_singing(
                all_effects, singing_moments, singing_models, face_tracks, moments_cfg, show_config,
                show_rules, preset=lambda key: (_effect_name_from_key(key), _get_settings(key)),
            )
        # Safety: strobes only in the finale, at the required spacing
        strobe_presets = {k for k in EFFECT_VARIANTS if _effect_name_from_key(k) == "Strobe"}
        show_rules_mod.check_strobes(show_rules, song_settings, finale, strobe_presets)

    if song_settings and song_settings.effects:
        all_effects = song_settings_mod.apply_custom_effects(
            song_settings, all_effects, analysis, show_config, palette_pool,
        )

    if show_rules:
        # Safety: excluded models never get effects
        excluded = show_rules_mod.excluded_models(show_rules, show_config)
        all_effects = [e for e in all_effects if e.model_name not in excluded]
        # Style: weighted palettes and signature accents
        if show_rules.style.palettes:
            signatures = show_rules_mod.pick_signatures(
                show_rules, Path(show_config.show_path), mp3_path.stem,
                song_settings.signature_colors if song_settings else None,
            )
            all_effects, share = show_rules_mod.apply_palettes(
                show_rules, all_effects, analysis, section_tiers, show_config, priority_of, signatures,
            )
        if singing_moments:
            all_effects = show_rules_mod.hold_floods(all_effects, singing_moments, show_rules, show_config)
        # Safety: tinted, capped whites; full white only at full-white moments
        if show_rules.safety.white:
            all_effects = show_rules_mod.limit_white(
                show_rules, all_effects, analysis.duration_ms, show_config,
            )

    # Build sequence spec
    spec = SequenceSpec(
        song_title=mp3_path.stem, artist="", album="",
        media_file=str(mp3_path),
        duration_ms=analysis.duration_ms, timing_ms=25,
        palettes=all_palettes, effects=all_effects,
        timing_tracks=timing_tracks,
    )
    plan_id = _plan_fingerprint(spec)

    if not write:  # preview: hand back the plan create_sequence would write
        return {
            "spec": spec, "plan_id": plan_id, "analysis": analysis, "tiers": section_tiers,
            "finale": finale, "final_hit": final_hit, "signatures": signatures, "share": share,
            "lyrics": lyrics_status, "priority_of": priority_of, "singing_moments": singing_moments,
            "trimmed_phrases": trimmed_phrases,
            "vocal_assignments": resolved_assignments if has_lyrics and singing_models else {},
        }

    # Write (never overwrite existing)
    base_name = mp3_path.stem
    output_path = Path(show_config.show_path) / f"{base_name}.xsq"
    if output_path.exists():
        counter = 1
        while output_path.exists():
            output_path = Path(show_config.show_path) / f"{base_name} (generated {counter}).xsq"
            counter += 1

    write_xsq(spec, show_config, output_path)
    if signatures:
        show_rules_mod.record_signatures(Path(show_config.show_path), mp3_path.stem, signatures)

    layers_used = set(e.layer for e in all_effects)

    return {
        "success": True,
        "output_path": str(output_path),
        "song": mp3_path.stem,
        "duration": f"{analysis.duration_seconds:.1f}s",
        "tempo": f"{analysis.beats.tempo:.0f} BPM",
        "sections": len(analysis.sections),
        "models_with_effects": len({e.model_name for e in all_effects}),
        "total_effects": len(all_effects),
        "layers_used": len(layers_used),
        "unique_palettes": len(all_palettes),
        "model_groups": len(groups),
        "has_lyrics": has_lyrics,
        "timing_tracks": [t.name for t in timing_tracks],
        "singing_models": list(singing_models.keys()) if has_lyrics else [],
        "vocal_assignments": resolved_assignments if has_lyrics and singing_models else {},
        "lyrics": lyrics_status,
        "singing_moments": _moments_summary(singing_moments, analysis, trimmed_phrases),
        "signature_colors": signatures,
        "accent_share": share,
        "plan_id": plan_id,
        "message": f"Sequence created: {output_path.name}. Open in xLights to preview and render.",
    }


# ---------------------------------------------------------------------------
# Guided mode
# ---------------------------------------------------------------------------


def _generate_guided_preview(
    analysis: SongAnalysis, show_config: ShowConfig, rules: ShowRules | None = None,
) -> dict:
    """Return analysis for guided/interactive mode.

    Excluded models are left out, and the show's safety rules are included so any
    effects planned from this respect them.
    """
    sections = []
    for i, s in enumerate(analysis.sections):
        sections.append({
            "index": i,
            "label": s.label,
            "start_time": f"{s.start_time:.1f}s",
            "end_time": f"{s.end_time:.1f}s",
            "energy": f"{s.energy_level:.2f}",
        })

    excluded = show_rules_mod.excluded_models(rules, show_config)
    models_by_category = {}
    for m in show_config.models:
        if m.name in excluded:
            continue
        cat = m.model_category
        models_by_category.setdefault(cat, []).append(m.name)

    return {
        "mode": "guided",
        "message": "Here's the song analysis. Tell me which effects you want for each section.",
        "song_info": {
            "tempo": f"{analysis.beats.tempo:.0f} BPM",
            "duration": f"{analysis.duration_seconds:.1f}s",
            "beat_count": len(analysis.beats.beat_times),
        },
        "sections": sections,
        "models_by_category": models_by_category,
        "safety_rules": rules.safety.model_dump(exclude_none=True) if rules else None,
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_settings(variant_key: str) -> dict[str, str]:
    """Get effect settings for a named variant."""
    variants = EFFECT_VARIANTS.get(variant_key, [{}])
    return variants[0].copy() if variants else {}


def _effect_name_from_key(variant_key: str) -> str:
    """Extract the xLights effect name from a variant key.

    e.g. 'Chase_left' → 'SingleStrand', 'Shockwave_hit' → 'Shockwave'
    """
    key_to_effect = {
        "Shockwave_hit": "Shockwave",
        "Chase_left": "SingleStrand",
        "Chase_right": "SingleStrand",
        "Chase_from_middle": "SingleStrand",
        "Chase_bounce": "SingleStrand",
        "Morph_quick": "Morph",
        "Twinkle_ambient": "Twinkle",
        "Twinkle_dense": "Twinkle",
        "ColorWash_slow": "Color Wash",
        "ColorWash_fast": "Color Wash",
        "ColorWash_cycling": "Color Wash",
        "Plasma_slow": "Plasma",
        "Plasma_fast": "Plasma",
        "Spirals_slow": "Spirals",
        "Spirals_fast": "Spirals",
        "Spirals_reverse": "Spirals",
        "Meteors_explode": "Meteors",
        "Meteors_rain": "Meteors",
        "Pinwheel_sweep": "Pinwheel",
        "Butterfly_gentle": "Butterfly",
        "Warp_mirror": "Warp",
        "Warp_wavy": "Warp",
        "Marquee_default": "Marquee",
        "On_solid": "On",
    }
    return key_to_effect.get(variant_key, variant_key.split("_")[0])


def _moments_summary(moments: list, analysis: SongAnalysis, trimmed: list | None = None) -> dict:
    total = sum(m.end - m.start for m in moments)
    return {
        "trimmed_phrases": [
            {"start": f"{t.start:.2f}s", "end": f"{t.end:.2f}s", "reason": t.reason, "lyrics": t.lyrics}
            for t in trimmed or []
        ],
        "share": round(total / max(analysis.duration_seconds, 1e-6), 3),
        "moments": [
            {"start": f"{m.start:.2f}s", "end": f"{m.end:.2f}s", "seconds": round(m.end - m.start, 2),
             "vocal_drum_ratio": m.vocal_drum_ratio, "lyrics": m.lyrics}
            for m in moments
        ],
    }


def _plan_fingerprint(spec: SequenceSpec) -> str:
    """Order-independent fingerprint of a sequence's effects and timing tracks."""
    rows = sorted(
        (e.model_name, e.layer, e.effect_name, e.start_time_ms, e.end_time_ms,
         sorted(e.settings.items()), e.palette.to_xlights_string() if e.palette else "")
        for e in spec.effects
    )
    rows.append(sorted(t.name for t in spec.timing_tracks))
    return hashlib.sha1(json.dumps(rows, default=str).encode()).hexdigest()[:12]


def _stable_hash(text: str) -> int:
    """A hash that's the same in every process (built-in hash() is salted per process)."""
    return int(hashlib.md5(text.encode()).hexdigest()[:12], 16)


DRUM_SNAP_SECONDS = 0.06
DEFAULT_MIN_LYRIC_CONFIDENCE = 0.5


def _clean_labels(labels: list[TimingTrackLabel]) -> list[TimingTrackLabel]:
    """Sort timing labels, drop empty or inverted ones and clip each to the next start."""
    labels = sorted((lb for lb in labels if lb.end_time_ms > lb.start_time_ms), key=lambda lb: lb.start_time_ms)
    out: list[TimingTrackLabel] = []
    for i, lb in enumerate(labels):
        end = min(lb.end_time_ms, labels[i + 1].start_time_ms) if i + 1 < len(labels) else lb.end_time_ms
        if end > lb.start_time_ms:
            out.append(lb.model_copy(update={"end_time_ms": end}))
    return out


def _snap_to_onsets(times: list[float], onsets: list[float]) -> list[float]:
    """Move each time onto the nearest drum onset within DRUM_SNAP_SECONDS."""
    if not onsets:
        return times
    snapped = []
    for t in times:
        i = bisect.bisect_left(onsets, t)
        near = min(onsets[max(i - 1, 0):i + 1], key=lambda o: abs(o - t))
        snapped.append(near if abs(near - t) <= DRUM_SNAP_SECONDS else t)
    return snapped


def _precompute_section_beats(analysis: SongAnalysis) -> dict[int, list[float]]:
    """Pre-compute which beats fall in each section."""
    result: dict[int, list[float]] = {}
    for i, section in enumerate(analysis.sections):
        result[i] = [
            b for b in analysis.beats.beat_times
            if section.start_time <= b < section.end_time
        ]
    return result


def _precompute_section_downbeats(analysis: SongAnalysis) -> dict[int, list[float]]:
    """Pre-compute which downbeats fall in each section."""
    result: dict[int, list[float]] = {}
    for i, section in enumerate(analysis.sections):
        result[i] = [
            b for b in analysis.beats.downbeat_times
            if section.start_time <= b < section.end_time
        ]
    return result


def _try_extract_lyrics(mp3_path: Path):
    """Try to extract lyrics using Whisper. Returns None if unavailable."""
    try:
        from xlights_mcp.audio.lyrics import extract_lyrics
        return extract_lyrics(mp3_path, whisper_model="base")
    except ImportError:
        logger.info("Whisper not installed — skipping lyric extraction")
        return None
    except Exception as e:
        logger.warning(f"Lyric extraction failed: {e}")
        return None


LYRICS_CACHE_VERSION = 1


def _try_extract_vocal_tracks(mp3_path: Path) -> list:
    """Extract all vocal timing tracks (cached per audio file). Empty list if unavailable.

    Transcriptions are cached so a preview and the sequence it previews see the same
    lyrics — Whisper can transcribe the same audio differently from run to run.
    """
    from xlights_mcp.audio.lyrics import LyricTrack
    from xlights_mcp.config import AUDIO_CACHE_DIR

    try:
        st = mp3_path.stat()
        key = hashlib.sha1(f"{mp3_path.resolve()}|{st.st_size}|{st.st_mtime_ns}".encode()).hexdigest()
        cache = AUDIO_CACHE_DIR / f"lyrics-v{LYRICS_CACHE_VERSION}-{key}.json"
    except OSError:
        cache = None
    if cache and cache.exists():
        try:
            return [LyricTrack.model_validate(t) for t in json.loads(cache.read_text())]
        except (ValueError, OSError):
            pass

    try:
        from xlights_mcp.audio.lyrics import extract_vocal_tracks
        tracks = [t for t in extract_vocal_tracks(mp3_path, whisper_model="base") if t.available and t.phonemes]
    except Exception as e:
        logger.warning(f"Vocal track extraction failed: {e}")
        track = _try_extract_lyrics(mp3_path)
        tracks = [track] if track and track.available and track.phonemes else []
    if cache and tracks:
        try:
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_text(json.dumps([t.model_dump(mode="json") for t in tracks]))
        except OSError:
            pass
    return tracks
