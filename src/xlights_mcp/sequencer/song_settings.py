"""Per-song sequencing settings read from a JSON file next to the music file.

``06 Holly Jolly Christmas.mp3`` is paired with ``06 Holly Jolly Christmas.sequence.json``.
All fields are optional:

    {
      "bar_anchor": 32.5,              # a bar starts on the beat nearest this time (s)
      "unmarked_tier": "normal",       # tier for sections no mark covers; omit to rank
                                       # them by energy as usual
      "sections": [                    # manual marks: split sections and force tiers
        {"name": "climax", "start": 65.5, "end": 72.4, "tier": "peak"}
      ],
      "signature_colors": ["#7B2FBE"], # this song's accent colors (overrides the show's pick)
      "final_hit": 113.0,              # seconds; overrides the detected final hit
      "effects": [                     # custom effects, replacing generated ones on the
        {                              # same models/layer within their time range
          "section": "climax",         # a mark name, or "start"/"end" in seconds
          "models": ["PORCH-COLUMNS"], # model or group names
          "layer": 2,
          "effect": "Strobe_flash",    # an engine preset, or any xLights effect name
          "settings": {},              # merged over the preset's settings
          "every_beats": 2,            # repeat on every Nth beat from the bar line;
          "length_beats": 1            # omit both for one effect over the whole range
        }
      ]
    }
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from xlights_mcp.audio.analyzer import SongAnalysis
from xlights_mcp.audio.structure import SongSection
from xlights_mcp.xlights.models import ShowConfig
from xlights_mcp.xlights.palettes import ColorPalette
from xlights_mcp.xlights.xsq_writer import EffectPlacement

SETTINGS_SUFFIX = ".sequence.json"
SNAP_SECONDS = 0.25  # marks this close to an analyzer boundary reuse it instead of splitting
MIN_CLIP_MS = 50     # drop slivers left over when clipping generated effects
Tier = Literal["peak", "normal", "breather"]


class SectionMark(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    start: float
    end: float
    tier: Tier
    label: str | None = None  # optional section type, e.g. "intro" or "bridge"

    @model_validator(mode="after")
    def _ordered(self):
        if self.end <= self.start:
            raise ValueError(f"section '{self.name}' ends before it starts")
        return self


class CustomEffect(BaseModel):
    model_config = ConfigDict(extra="forbid")

    models: list[str] = Field(min_length=1)
    effect: str
    layer: int = Field(default=0, ge=0, le=2)
    section: str | None = None
    start: float | None = None
    end: float | None = None
    settings: dict[str, str] = Field(default_factory=dict)
    every_beats: int | None = Field(default=None, ge=1)
    length_beats: float | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def _has_range(self):
        if self.section is None and (self.start is None or self.end is None):
            raise ValueError(f"effect '{self.effect}' needs a section or both start and end")
        return self


class SongSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    bar_anchor: float | None = None
    unmarked_tier: Tier | None = None
    sections: list[SectionMark] = Field(default_factory=list)
    effects: list[CustomEffect] = Field(default_factory=list)
    signature_colors: list[str] | None = Field(default=None, min_length=1, max_length=2)
    final_hit: float | None = None  # seconds; overrides the detected final hit

    @field_validator("signature_colors")
    @classmethod
    def _colors(cls, v):
        if v is not None:
            for c in v:
                if len(c) != 7 or c[0] != "#" or any(ch not in "0123456789abcdefABCDEF" for ch in c[1:]):
                    raise ValueError(f"signature color '{c}' is not #RRGGBB")
            v = [c.upper() for c in v]
        return v

    @model_validator(mode="after")
    def _references(self):
        names = {m.name for m in self.sections}
        for e in self.effects:
            if e.section is not None and e.section not in names:
                raise ValueError(f"effect '{e.effect}' refers to unknown section '{e.section}'")
        return self

    def effect_range(self, effect: CustomEffect) -> tuple[float, float]:
        mark = next((m for m in self.sections if m.name == effect.section), None)
        start = effect.start if effect.start is not None else mark.start
        end = effect.end if effect.end is not None else mark.end
        return start, end


def settings_path_for(music_path: Path) -> Path:
    return music_path.with_name(music_path.stem + SETTINGS_SUFFIX)


def load_song_settings(music_path: Path) -> tuple[SongSettings | None, Path | None]:
    """Load the settings file paired with a music file, if there is one.

    Raises ValueError with a readable message when the file is malformed.
    """
    path = settings_path_for(music_path)
    if not path.exists():
        return None, None
    try:
        return SongSettings.model_validate(json.loads(path.read_text())), path
    except (json.JSONDecodeError, ValidationError) as exc:
        raise ValueError(f"Invalid song settings in {path.name}: {exc}") from exc


def apply_to_analysis(
    settings: SongSettings, analysis: SongAnalysis,
) -> tuple[SongAnalysis, list[str | None]]:
    """Re-anchor the bar grid and split sections at manual marks.

    Returns a copy of the analysis and, per section, the tier forced by a mark
    or by ``unmarked_tier`` (None where the engine's ranking should decide).
    """
    beats = analysis.beats
    if settings.bar_anchor is not None and beats.beat_times:
        times = beats.beat_times
        anchor = min(range(len(times)), key=lambda i: abs(times[i] - settings.bar_anchor))
        beats = beats.model_copy(update={"downbeat_times": times[anchor % beats.beats_per_bar::beats.beats_per_bar]})

    # Split at every mark boundary not already (nearly) on an analyzer boundary
    boundaries = sorted({s.start_time for s in analysis.sections} | {s.end_time for s in analysis.sections})
    cuts = sorted({
        t for m in settings.sections for t in (m.start, m.end)
        if all(abs(t - b) > SNAP_SECONDS for b in boundaries)
    })
    sections: list[SongSection] = []
    for s in analysis.sections:
        start = s.start_time
        for t in (c for c in cuts if s.start_time < c < s.end_time):
            sections.append(s.model_copy(update={"start_time": start, "end_time": t}))
            start = t
        sections.append(s.model_copy(update={"start_time": start}))

    tiers: list[str | None] = []
    for i, s in enumerate(sections):
        mid = (s.start_time + s.end_time) / 2
        mark = next((m for m in settings.sections if m.start <= mid < m.end), None)
        if mark:
            if mark.label:
                sections[i] = s.model_copy(update={"label": mark.label})
            tiers.append(mark.tier)
        elif s.label in ("intro", "outro", "transition"):
            tiers.append("breather")
        else:
            tiers.append(settings.unmarked_tier)

    return analysis.model_copy(update={"beats": beats, "sections": sections}), tiers


def validate_models(settings: SongSettings, show_config: ShowConfig) -> None:
    """Raise ValueError if a custom effect names a model or group the show lacks."""
    for custom in settings.effects:
        expand_models(custom.models, show_config)


def expand_models(names: list[str], show_config: ShowConfig) -> list[str]:
    """Resolve model and group names (recursively) to model names.

    Submodel members of groups ("Model/Sub") are skipped; unknown names raise ValueError.
    """
    groups = {g.name: g.members for g in show_config.model_groups}
    models = {m.name for m in show_config.models}
    out: list[str] = []
    seen: set[str] = set()

    def visit(name: str):
        if name in seen or (name not in groups and "/" in name):
            return
        seen.add(name)
        if name in models:
            out.append(name)
        elif name in groups:
            for member in groups[name]:
                visit(member)
        else:
            raise ValueError(f"Unknown model or group: '{name}'")

    for n in names:
        visit(n)
    return out


def clip_out(
    effects: list[EffectPlacement], names: list[str], layer: int | None, lo: int, hi: int,
) -> list[EffectPlacement]:
    """Clip effects on these models (and layer, or all layers if None) out of [lo, hi) ms."""
    names = set(names)
    kept: list[EffectPlacement] = []
    for e in effects:
        if (e.model_name not in names or (layer is not None and e.layer != layer)
                or e.end_time_ms <= lo or e.start_time_ms >= hi):
            kept.append(e)
            continue
        if lo - e.start_time_ms >= MIN_CLIP_MS:
            kept.append(e.model_copy(update={"end_time_ms": lo}))
        if e.end_time_ms - hi >= MIN_CLIP_MS:
            kept.append(e.model_copy(update={"start_time_ms": hi}))
    return kept


def apply_custom_effects(
    settings: SongSettings,
    effects: list[EffectPlacement],
    analysis: SongAnalysis,
    show_config: ShowConfig,
    palettes: list[ColorPalette],
) -> list[EffectPlacement]:
    """Place custom effects, clipping generated effects out of their way."""
    from xlights_mcp.sequencer import engine  # engine imports this module

    beats = analysis.beats.beat_times
    downbeats = set(analysis.beats.downbeat_times)
    beat_ms = 60000 / max(analysis.beats.tempo, 1)

    for custom in settings.effects:
        start, end = settings.effect_range(custom)
        lo, hi = int(start * 1000), int(end * 1000)
        names = expand_models(custom.models, show_config)
        effects = clip_out(effects, names, custom.layer, lo, hi)

        if custom.effect in engine.EFFECT_VARIANTS:
            effect_name = engine._effect_name_from_key(custom.effect)
            fx_settings = {**engine._get_settings(custom.effect), **custom.settings}
        else:
            effect_name, fx_settings = custom.effect, dict(custom.settings)

        if custom.every_beats is None:
            spans = [(lo, hi)]
        else:
            in_range = [b for b in beats if start <= b < end]
            first_bar = next((i for i, b in enumerate(in_range) if b in downbeats), 0)
            hits = in_range[first_bar % custom.every_beats::custom.every_beats]
            length = int((custom.length_beats or custom.every_beats) * beat_ms)
            spans = [(int(b * 1000), min(int(b * 1000) + length, hi)) for b in hits]

        for j, (a, b) in enumerate(spans):
            for name in names:
                effects.append(EffectPlacement(
                    model_name=name, layer=custom.layer, effect_name=effect_name,
                    start_time_ms=a, end_time_ms=b, settings=fx_settings,
                    palette=palettes[j % len(palettes)] if palettes else None,
                ))

    return effects
