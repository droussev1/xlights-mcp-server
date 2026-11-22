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
      "singing": {"exposed_fraction": 0.4, "max_share": 0.3},  # singing-moment overrides
      "brief": {                       # the song's design brief (see DesignBrief)
        "mood": "warm, bouncy",
        "motion": "punchy",            # "punchy" or "flowing"
        "effects": {"tree": ["Pinwheel_sweep", "Spirals_fast"]},  # motion per prop type
        "hero_effects": ["Pinwheel_sweep"],  # hero props' motion
        "stems": {"other": ["SNOWFLAKES"], "bass": ["MEGA-TREES"]},  # accents follow stems
        "arch_pattern": "bounce",      # chase / bounce / center_out / fill
        "signature_moment": {"name": "snowfall", "section": "climax",
                             "models": ["MEGA-TREES"], "layer": 1, "effect": "Meteors_rain"}
      },
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
Motion = Literal["flowing", "punchy"]
ArchPattern = Literal["chase", "bounce", "center_out", "fill"]
StemName = Literal["drums", "bass", "other", "vocals"]


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


class SignatureMoment(CustomEffect):
    """A named moment: a custom effect, optionally dimming the rest of the yard
    (``others``: a preset or look on everyone else, or "off" for darkness) and
    staggering its props' starts (``stagger``)."""

    name: str
    description: str = ""
    others: str | None = None
    stagger: Literal["center_out", "left_to_right"] | None = None
    stagger_share: float = Field(default=0.75, gt=0, le=1)  # of the moment the starts spread over
    exclusive: bool = False  # own its props on every layer (nothing shows through under it)


class LyricCue(BaseModel):
    """An effect that answers a lyric, starting on a transcribed word (which must have
    passed the stem/mix cross-check)."""

    model_config = ConfigDict(extra="forbid")

    word: str
    time: float  # the word's start in the transcription (s)
    models: list[str] = Field(min_length=1)
    effect: str  # an engine preset or a brief look
    layer: int = Field(default=2, ge=0, le=2)
    length_beats: float | None = Field(default=None, gt=0)  # default: the word's own length
    settings: dict[str, str] = Field(default_factory=dict)
    stagger: Literal["center_out", "left_to_right"] | None = None
    note: str = ""


class ArchWalk(BaseModel):
    """A run of featured notes (e.g. a piano walk) the arches step on, one step per note,
    instead of one per beat, for the length of the run."""

    model_config = ConfigDict(extra="forbid")

    name: str = ""
    times: list[float] = Field(min_length=2)  # each note's start (s)
    note: str = ""

    @model_validator(mode="after")
    def _ordered(self):
        if any(b <= a for a, b in zip(self.times, self.times[1:])):
            raise ValueError(f"arch walk '{self.name}': times must increase")
        return self


class NegativeSpace(BaseModel):
    """Keep the yard dark: props light only in peaks, in sections where ``fill_stem`` swells
    above ``fill_above`` × its usual level, in moments and cues, and as clicks."""

    model_config = ConfigDict(extra="forbid")

    fill_stem: StemName | None = None
    fill_above: float = Field(default=1.5, gt=0)


class Clicks(BaseModel):
    """A short hit for every onset of ``stem`` (e.g. a snap), with dark space between —
    wherever the stem plays, except while a face sings. ``together``: every model clicks
    at once. ``travel``: each click lights one prop, moving through the left/right pairs
    from the middle of the yard out and back, alternating left and right, so every bar is
    balanced to within one click. Clicks count toward the show's hit budget (whole bars
    are thinned evenly if they don't fit)."""

    model_config = ConfigDict(extra="forbid")

    stem: StemName
    models: list[str] = Field(min_length=1)
    mode: Literal["together", "travel"] = "together"
    effect: str = "On_solid"
    length_seconds: float = Field(default=0.12, gt=0)
    min_gap_seconds: float = Field(default=0.2, gt=0)


class Look(BaseModel):
    """Any xLights effect with its settings by property id (see docs/effect_inventory.md),
    usable by name wherever a brief names an effect."""

    model_config = ConfigDict(extra="forbid")

    effect: str
    settings: dict[str, str | int | float | bool] = Field(default_factory=dict)
    description: str = ""


class DesignBrief(BaseModel):
    """A song's design concept, followed by the generator within the show rules."""

    model_config = ConfigDict(extra="forbid")

    concept: str = ""
    mood: str
    colors: str = ""  # color direction, in words
    looks: dict[str, Look] = Field(default_factory=dict)  # named looks, usable like presets
    faces: bool = False  # faces sing only when the brief says so
    allow_models: list[str] = Field(default_factory=list)  # hands-off models the owner asked to use here
    lyric_cues: list[LyricCue] = Field(default_factory=list)
    secondary_moments: list[SignatureMoment] = Field(default_factory=list)
    negative_space: NegativeSpace | None = None
    clicks: list[Clicks] = Field(default_factory=list)
    arch_walks: list[ArchWalk] = Field(default_factory=list)  # the arches step on these notes
    differences: list[str] = Field(default_factory=list)  # what makes this song different
    motion: Motion = "punchy"  # flowing: longer motion phrases, sparser and softer accents
    effects: dict[str, list[str]] = Field(default_factory=dict)  # model category → motion presets
    hero_effects: list[str] = Field(default_factory=list)  # motion presets for hero props
    stems: dict[StemName, list[str]] = Field(default_factory=dict)  # stem → models whose accents follow it
    arch_pattern: ArchPattern | None = None
    signature_moment: SignatureMoment | None = None

    @model_validator(mode="after")
    def _one_stem_per_model(self):
        seen: dict[str, str] = {}
        for stem, names in self.stems.items():
            for n in names:
                if n in seen:
                    raise ValueError(f"'{n}' is mapped to both the {seen[n]} and {stem} stems")
                seen[n] = stem
        return self


class SongSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    bar_anchor: float | None = None
    unmarked_tier: Tier | None = None
    sections: list[SectionMark] = Field(default_factory=list)
    effects: list[CustomEffect] = Field(default_factory=list)
    signature_colors: list[str] | None = Field(default=None, min_length=1, max_length=2)
    final_hit: float | None = None  # seconds; overrides the detected final hit
    singing: dict | None = None  # overrides for the show's singing-moment settings
    brief: DesignBrief | None = None

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
        for e in self.all_effects:
            if e.section is not None and e.section not in names:
                raise ValueError(f"effect '{e.effect}' refers to unknown section '{e.section}'")
        return self

    @property
    def all_effects(self) -> list[CustomEffect]:
        """Custom effects, then the brief's signature moment, then its secondary moments
        (each placed over what came before, so a secondary moment can add to the signature)."""
        if not self.brief:
            return list(self.effects)
        moment = self.brief.signature_moment
        return [*self.effects, *([moment] if moment else []), *self.brief.secondary_moments]

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
    """Raise ValueError if a custom effect or the brief names a model or group the show lacks."""
    for custom in settings.all_effects:
        expand_models(custom.models, show_config)
    if settings.brief:
        for names in settings.brief.stems.values():
            expand_models(names, show_config)


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
    extra: list[CustomEffect] | None = None,
    protected: set[str] | None = None,
    pairs: list[tuple[str, str]] | None = None,
    busy: dict[str, list[tuple[int, int]]] | None = None,
    hands_off: set[str] | None = None,
) -> list[EffectPlacement]:
    """Place custom effects (then ``extra``, e.g. lyric cues), clipping generated effects
    out of their way.

    A moment with ``others`` also clears everyone else in its range (except ``protected``
    models: floods, the final-hit props, excluded models) and gives them that look, or
    darkness for "off"; such a moment owns its own props on every layer. With
    ``stagger`` its props start one after another, from the middle of the layout out or
    from the audience's left; symmetry ``pairs`` start together. ``protected`` models are
    never taken over, and ``busy`` models (singers) not while they're busy.
    """
    from xlights_mcp.sequencer import engine  # engine imports this module

    beats = analysis.beats.beat_times
    downbeats = set(analysis.beats.downbeat_times)
    beat_ms = 60000 / max(analysis.beats.tempo, 1)
    xs = {m.name: m.x for m in show_config.models if m.x is not None}
    partner = {a: b for a, b in (pairs or [])} | {b: a for a, b in (pairs or [])}

    def resolve(key: str, overrides: dict[str, str]) -> tuple[str, dict[str, str]]:
        if engine.is_effect_key(key):
            return engine._effect_name_from_key(key), {**engine._get_settings(key), **overrides}
        return key, dict(overrides)

    for custom in [*settings.all_effects, *(extra or [])]:
        start, end = custom.start, custom.end
        if start is None or end is None:
            start, end = settings.effect_range(custom)
        lo, hi = int(start * 1000), int(end * 1000)
        taken = set(protected or set()) | {n for n, spans in (busy or {}).items()
                                           if any(a < hi and b > lo for a, b in spans)}
        if isinstance(custom, SignatureMoment):  # the brief's moments and cues; the owner's
            taken |= hands_off or set()           # own custom effects may use hands-off models
        names = [n for n in expand_models(custom.models, show_config) if n not in taken]
        others = getattr(custom, "others", None)
        owns = bool(others) or getattr(custom, "exclusive", False)
        effects = clip_out(effects, names, None if owns else custom.layer, lo, hi)

        if others:
            rest = [m.name for m in show_config.models if m.name not in names and m.name not in taken]
            effects = clip_out(effects, rest, None, lo, hi)
            if others != "off":
                o_name, o_settings = resolve(others, {})
                effects += [EffectPlacement(model_name=n, layer=0, effect_name=o_name, start_time_ms=lo,
                                            end_time_ms=hi, settings=o_settings,
                                            palette=palettes[0] if palettes else None) for n in rest]

        effect_name, fx_settings = resolve(custom.effect, custom.settings)

        if custom.every_beats is None:
            spans = [(lo, hi)]
        else:
            in_range = [b for b in beats if start <= b < end]
            first_bar = next((i for i, b in enumerate(in_range) if b in downbeats), 0)
            hits = in_range[first_bar % custom.every_beats::custom.every_beats]
            length = int((custom.length_beats or custom.every_beats) * beat_ms)
            spans = [(int(b * 1000), min(int(b * 1000) + length, hi)) for b in hits]

        delay = _stagger(custom, names, xs, partner, hi - lo)
        for j, (a, b) in enumerate(spans):
            for name in names:
                a2 = max(a, lo + delay.get(name, 0))
                if a2 >= b:
                    continue
                effects.append(EffectPlacement(
                    model_name=name, layer=custom.layer, effect_name=effect_name,
                    start_time_ms=a2, end_time_ms=b, settings=fx_settings,
                    palette=palettes[j % len(palettes)] if palettes else None,
                ))

    return effects


def _stagger(custom, names: list[str], xs: dict[str, float], partner: dict[str, str], span_ms: int) -> dict[str, int]:
    """Start delay (ms) per model for a staggered moment; partners share the earlier one."""
    mode = getattr(custom, "stagger", None)
    placed = [n for n in names if n in xs]
    if not mode or not placed:
        return {}
    lo_x, hi_x = min(xs.values()), max(xs.values())
    mid = (lo_x + hi_x) / 2
    key = (lambda n: abs(xs[n] - mid)) if mode == "center_out" else (lambda n: xs[n] - lo_x)
    order = {n: key(n) for n in placed}
    for n in placed:  # a symmetry pair starts together
        if partner.get(n) in order and mode == "center_out":
            order[n] = order[partner[n]] = min(order[n], order[partner[n]])
    steps = sorted(set(order.values()))
    window = span_ms * getattr(custom, "stagger_share", 0.75)
    return {n: int(steps.index(order[n]) * window / max(len(steps), 1)) for n in placed}
