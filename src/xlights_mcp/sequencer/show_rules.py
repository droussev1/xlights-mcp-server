"""Show-wide sequencing rules read from ``xlights_mcp_rules.json`` in the show folder.

create_sequence loads the file on every run and the generator enforces it:

- ``exclude``: models that never get effects
- ``priorities``: hero / key / normal / optional per model or group, always applied
  (overrides priorities set with set_model_priority)
- ``symmetry``: left/right prop pairs; per-tier mode ``mirror`` (right side is the left
  side flipped horizontally), ``call_response`` (motion and accents alternate sides bar
  by bar) or ``sync`` (identical)
- ``arch_chase``: arch models listed left to right; their motion becomes one arch per
  beat, starting on the bar line
- ``floods``: one solid color per section — no motion, no accents
- ``twinkle``: props that only twinkle in the given tiers (intro/outro count as breathers)
- ``final_hit``: props kept dark except for the song's final hit
- ``singing``: which face models sing, and fallback priorities while lyrics are missing
- ``strobes``: strobes only in the finale, at a fixed beat spacing
- ``palettes``: weighted core palettes plus per-song signature accent colors

Signature colors are remembered per song in ``xlights_mcp_signatures.json`` next to the
rules file so songs in the same show don't repeat them.
"""

from __future__ import annotations

import bisect
import hashlib
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from xlights_mcp.audio.analyzer import SongAnalysis
from xlights_mcp.sequencer.song_settings import (
    SongSettings, Tier, clip_out, expand_models,
)
from xlights_mcp.xlights.models import ShowConfig
from xlights_mcp.xlights.palettes import ColorPalette
from xlights_mcp.xlights.xsq_writer import EffectPlacement

logger = logging.getLogger(__name__)

RULES_FILENAME = "xlights_mcp_rules.json"
SIGNATURES_FILENAME = "xlights_mcp_signatures.json"
FLIP = {"B_CHOICE_BufferTransform": "Flip Horizontal"}
QUIET_LABELS = ("intro", "outro", "transition")

Priority = Literal["hero", "key", "normal", "optional"]
SymmetryMode = Literal["mirror", "call_response", "sync"]


def _hex(colors: list[str]) -> list[str]:
    for c in colors:
        if len(c) != 7 or c[0] != "#" or any(ch not in "0123456789abcdefABCDEF" for ch in c[1:]):
            raise ValueError(f"'{c}' is not a #RRGGBB color")
    return [c.upper() for c in colors]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Symmetry(_Strict):
    pairs: list[tuple[str, str]]  # (left, right)
    default: SymmetryMode = "mirror"
    by_tier: dict[Tier, SymmetryMode] = Field(default_factory=dict)


class ArchModel(_Strict):
    model: str
    count: int = Field(ge=1)
    reverse: bool = False  # set if the model's arches are wired right to left


class ArchChase(_Strict):
    arches: list[ArchModel] = Field(min_length=1)  # left to right


class Twinkle(_Strict):
    models: list[str]
    tiers: list[Tier] = Field(default_factory=lambda: ["breather"])


class FinalHit(_Strict):
    models: list[str]
    bars: float = Field(default=2, gt=0)  # how long the hit holds before fading out


class Singing(_Strict):
    singers: list[str]
    without_lyrics: dict[str, Priority] = Field(default_factory=dict)


class Strobes(_Strict):
    every_beats: int = Field(default=2, ge=1)


class Palettes(_Strict):
    core: dict[str, list[str]]
    default: str
    by_model: dict[str, str] = Field(default_factory=dict)
    by_section_label: dict[str, str] = Field(default_factory=dict)
    by_tier: dict[Tier, str] = Field(default_factory=dict)
    arch_chase: str | None = None
    accent_pool: list[str] = Field(default_factory=list)
    signatures_per_song: int = Field(default=2, ge=1, le=2)
    max_accent_share: float = Field(default=0.25, ge=0, le=1)

    @field_validator("core")
    @classmethod
    def _core_colors(cls, v):
        return {k: _hex(c) for k, c in v.items()}

    @field_validator("accent_pool")
    @classmethod
    def _pool_colors(cls, v):
        return _hex(v)

    @model_validator(mode="after")
    def _known_palettes(self):
        names = [self.default, self.arch_chase, *self.by_model.values(),
                 *self.by_section_label.values(), *self.by_tier.values()]
        unknown = sorted({n for n in names if n and n not in self.core})
        if unknown:
            raise ValueError(f"unknown core palettes: {unknown}")
        return self


class ShowRules(_Strict):
    notes: list[str] = Field(default_factory=list)  # the rules in plain words
    exclude: list[str] = Field(default_factory=list)
    priorities: dict[str, Priority] = Field(default_factory=dict)
    symmetry: Symmetry | None = None
    arch_chase: ArchChase | None = None
    floods: list[str] = Field(default_factory=list)
    twinkle: Twinkle | None = None
    final_hit: FinalHit | None = None
    singing: Singing | None = None
    strobes: Strobes | None = None
    palettes: Palettes | None = None

    def model_names(self) -> list[str]:
        names = [*self.exclude, *self.floods, *self.priorities]
        if self.symmetry:
            names += [n for pair in self.symmetry.pairs for n in pair]
        if self.arch_chase:
            names += [a.model for a in self.arch_chase.arches]
        for part in (self.twinkle, self.final_hit):
            if part:
                names += part.models
        if self.singing:
            names += [*self.singing.singers, *self.singing.without_lyrics]
        if self.palettes:
            names += list(self.palettes.by_model)
        return names


def load_show_rules(show_path: Path) -> tuple[ShowRules | None, Path | None]:
    """Load the show's rules file, if it has one. Raises ValueError if it's malformed."""
    path = show_path / RULES_FILENAME
    if not path.exists():
        return None, None
    try:
        return ShowRules.model_validate(json.loads(path.read_text())), path
    except (json.JSONDecodeError, ValidationError) as exc:
        raise ValueError(f"Invalid show rules in {path.name}: {exc}") from exc


def validate_models(rules: ShowRules, show_config: ShowConfig) -> None:
    """Raise ValueError if the rules name a model or group the show doesn't have."""
    for name in rules.model_names():
        expand_models([name], show_config)


def excluded_models(rules: ShowRules | None, show_config: ShowConfig) -> set[str]:
    return set(expand_models(rules.exclude, show_config)) if rules else set()


def generic_skip(rules: ShowRules | None, show_config: ShowConfig) -> set[str]:
    """Models the generic generator should leave alone because the rules own them."""
    if not rules:
        return set()
    names = [*rules.exclude, *rules.floods, *(rules.final_hit.models if rules.final_hit else [])]
    return set(expand_models(names, show_config))


# ---------------------------------------------------------------------------
# Song timing helpers
# ---------------------------------------------------------------------------


def finale_range(
    analysis: SongAnalysis, tiers: list[str], song_settings: SongSettings | None,
) -> tuple[float, float] | None:
    """The finale: a section mark named "finale", else the last run of peak sections."""
    if song_settings:
        mark = next((m for m in song_settings.sections if m.name == "finale"), None)
        if mark:
            return mark.start, mark.end
    last = [i for i, t in enumerate(tiers) if t == "peak"]
    if not last:
        return None
    first = last[-1]
    while first > 0 and tiers[first - 1] == "peak":
        first -= 1
    return analysis.sections[first].start_time, analysis.sections[last[-1]].end_time


def final_hit_time(
    analysis: SongAnalysis, tiers: list[str], song_settings: SongSettings | None,
) -> float | None:
    """The final hit: per-song override, else the beat nearest the end of the last peak."""
    if song_settings and song_settings.final_hit is not None:
        return song_settings.final_hit
    peaks = [i for i, t in enumerate(tiers) if t == "peak"]
    if not peaks:
        return None
    t = analysis.sections[peaks[-1]].end_time
    beats = analysis.beats.beat_times
    return min(beats, key=lambda b: abs(b - t)) if beats else t


def check_strobes(
    rules: ShowRules | None, song_settings: SongSettings | None,
    finale: tuple[float, float] | None, strobe_presets: set[str],
) -> None:
    """Strobes may only run in the finale at the configured spacing (ValueError if not)."""
    if not (rules and rules.strobes and song_settings):
        return
    for e in song_settings.effects:
        if e.effect != "Strobe" and e.effect not in strobe_presets:
            continue
        start, end = song_settings.effect_range(e)
        if finale is None or start < finale[0] - 0.25 or end > finale[1] + 0.25:
            where = f"{finale[0]:.1f}-{finale[1]:.1f}s" if finale else "none found"
            raise ValueError(
                f"Show rules allow strobes only in the finale (finale: {where}); "
                f"'{e.effect}' runs {start:.1f}-{end:.1f}s"
            )
        if e.every_beats != rules.strobes.every_beats:
            raise ValueError(
                f"Show rules require strobes every {rules.strobes.every_beats} beats "
                f"(half time); '{e.effect}' has every_beats={e.every_beats}"
            )


@dataclass
class _Clock:
    analysis: SongAnalysis
    tiers: list[str]

    def __post_init__(self):
        self.starts = [s.start_time for s in self.analysis.sections]
        self.beats = self.analysis.beats.beat_times
        self.downbeats = self.analysis.beats.downbeat_times

    def section(self, t: float) -> int:
        return max(bisect.bisect_right(self.starts, t + 1e-3) - 1, 0)

    def bar(self, t: float) -> int:
        return bisect.bisect_right(self.downbeats, t + 1e-2) - 1

    def beat(self, t: float) -> int:
        return max(bisect.bisect_right(self.beats, t + 1e-2) - 1, 0)


# ---------------------------------------------------------------------------
# Structure: roles and symmetry
# ---------------------------------------------------------------------------


def _effect(model, layer, name, start_ms, end_ms, settings) -> EffectPlacement:
    return EffectPlacement(model_name=model, layer=layer, effect_name=name,
                           start_time_ms=start_ms, end_time_ms=end_ms, settings=settings)


def apply_structure(
    rules: ShowRules,
    effects: list[EffectPlacement],
    analysis: SongAnalysis,
    tiers: list[str],
    show_config: ShowConfig,
    priority_of: dict[str, str],
    final_hit: float | None,
    preset,  # variant key → (xLights effect name, settings)
) -> list[EffectPlacement]:
    """Floods, final-hit props, symmetry, twinkle props and the arch chase."""
    clock = _Clock(analysis, tiers)
    sections = analysis.sections
    singers = {e.model_name for e in effects if e.effect_name == "Faces"}

    # Floods: one solid color per section, changing only at section boundaries
    floods = set(expand_models(rules.floods, show_config))
    on_name, on_settings = preset("On_solid")
    for name in floods:
        effects = [e for e in effects if e.model_name != name]
        effects += [_effect(name, 0, on_name, s.start_time_ms, s.end_time_ms, on_settings) for s in sections]

    # Final-hit props stay dark until the final hit, then hold and fade
    hit_models = set(expand_models(rules.final_hit.models, show_config)) if rules.final_hit else set()
    for name in hit_models:
        effects = [e for e in effects if e.model_name != name]
        if final_hit is not None:
            bar_ms = 4 * 60000 / max(analysis.beats.tempo, 1)
            start = int(final_hit * 1000)
            end = min(start + int(rules.final_hit.bars * bar_ms), analysis.duration_ms)
            fade = f"{(end - start) / 1000 * 0.75:.2f}"
            effects.append(_effect(name, 0, on_name, start, end, {**on_settings, "T_TEXTCTRL_Fadeout": fade}))

    # Symmetry: rebuild each right-hand prop from its left-hand partner
    if rules.symmetry:
        special = floods | hit_models | singers
        for left, right in rules.symmetry.pairs:
            if {left, right} & special:
                continue
            if priority_of.get(left, "normal") != priority_of.get(right, "normal"):
                logger.warning(f"Skipping symmetry for {left}/{right}: different priorities")
                continue
            left_fx = [e for e in effects if e.model_name == left]
            effects = [e for e in effects if e.model_name not in (left, right)]
            for e in left_fx:
                t = e.start_time_ms / 1000
                mode = rules.symmetry.by_tier.get(tiers[clock.section(t)], rules.symmetry.default)
                mirrored = e.model_copy(update={"model_name": right, "settings": {**FLIP, **e.settings}})
                one_bar = clock.bar(t) == clock.bar(e.end_time_ms / 1000 - 0.05)
                if mode == "sync":
                    effects += [e, e.model_copy(update={"model_name": right})]
                elif mode == "call_response" and e.layer > 0 and one_bar:
                    # Left calls on even bars, right answers on odd bars
                    effects.append(e if clock.bar(t) % 2 == 0 else mirrored)
                else:
                    effects += [e, mirrored]

    # Twinkle props: only twinkle in their tiers (intro/outro are breathers)
    if rules.twinkle:
        names = expand_models(rules.twinkle.models, show_config)
        tw_name, tw_settings = preset("Twinkle_ambient")
        for i, s in enumerate(sections):
            if tiers[i] in rules.twinkle.tiers:
                effects = clip_out(effects, names, None, s.start_time_ms, s.end_time_ms)
                effects += [_effect(n, 0, tw_name, s.start_time_ms, s.end_time_ms, tw_settings) for n in names]

    # Arch chase: one arch per beat, left to right, starting on the bar line
    if rules.arch_chase:
        slots = [
            (a.model, a.count - 1 - i if a.reverse else i, a.count)
            for a in rules.arch_chase.arches for i in range(a.count)
        ]
        arch_names = {a.model for a in rules.arch_chase.arches}
        effects = [e for e in effects if not (e.model_name in arch_names and e.layer > 0)]
        beats = clock.beats
        first_bar = beats.index(clock.downbeats[0]) if clock.downbeats and clock.downbeats[0] in beats else 0
        for i, s in enumerate(sections):
            if tiers[i] == "breather" or s.label in QUIET_LABELS:
                continue
            for bi, b in enumerate(beats):
                if not s.start_time <= b < s.end_time:
                    continue
                model, arch, count = slots[(bi - first_bar) % len(slots)]
                end = min(beats[bi + 1] if bi + 1 < len(beats) else s.end_time, s.end_time)
                sub = f"{arch * 100 / count:.2f}x0.00x{(arch + 1) * 100 / count:.2f}x100.00"
                effects.append(_effect(model, 1, on_name, int(b * 1000), int(end * 1000), {
                    **on_settings, "B_CHOICE_BufferStyle": "Single Line", "B_CUSTOM_SubBuffer": sub,
                }))

    return effects


# ---------------------------------------------------------------------------
# Palettes
# ---------------------------------------------------------------------------


def _palette(colors: list[str]) -> ColorPalette:
    return ColorPalette(colors=colors[:8], active_colors=list(range(1, min(len(colors), 8) + 1)))


def pick_signatures(
    rules: ShowRules, show_path: Path, song: str, override: list[str] | None,
) -> list[str]:
    """This song's signature accent colors, avoiding colors other songs in the show use."""
    if override:
        return override
    registry = _read_registry(show_path)
    if song in registry:
        return registry[song]
    pool = rules.palettes.accent_pool
    if not pool:
        return []
    used = {c for s, colors in registry.items() if s != song for c in colors}
    start = int(hashlib.md5(song.encode()).hexdigest(), 16) % len(pool)
    ordered = pool[start:] + pool[:start]
    free = [c for c in ordered if c not in used] + [c for c in ordered if c in used]
    return free[:rules.palettes.signatures_per_song]


def _read_registry(show_path: Path) -> dict[str, list[str]]:
    path = show_path / SIGNATURES_FILENAME
    try:
        return json.loads(path.read_text()).get("songs", {})
    except (OSError, json.JSONDecodeError):
        return {}


def record_signatures(show_path: Path, song: str, colors: list[str]) -> None:
    registry = _read_registry(show_path)
    registry[song] = colors
    (show_path / SIGNATURES_FILENAME).write_text(json.dumps({"songs": registry}, indent=2) + "\n")


def apply_palettes(
    rules: ShowRules,
    effects: list[EffectPlacement],
    analysis: SongAnalysis,
    tiers: list[str],
    show_config: ShowConfig,
    priority_of: dict[str, str],
    signatures: list[str],
) -> tuple[list[EffectPlacement], float]:
    """Repaint every effect from the core palettes, then place signature accents sparingly.

    Returns the effects and the share of effect time that carries an accent color.
    """
    pal = rules.palettes
    clock = _Clock(analysis, tiers)
    model_core: dict[str, str] = {}
    for name, core in pal.by_model.items():
        for m in expand_models([name], show_config):
            model_core.setdefault(m, core)
    arch_names = {a.model for a in rules.arch_chase.arches} if rules.arch_chase else set()
    no_accent = (set(expand_models(rules.floods, show_config))
                 | set(expand_models(rules.final_hit.models, show_config) if rules.final_hit else []))

    def core_colors(e: EffectPlacement) -> list[str]:
        t = e.start_time_ms / 1000
        sec = clock.section(t)
        if e.model_name in arch_names and e.layer == 1 and pal.arch_chase:
            name = pal.arch_chase
        else:
            name = (model_core.get(e.model_name)
                    or pal.by_section_label.get(analysis.sections[sec].label)
                    or pal.by_tier.get(tiers[sec])
                    or pal.default)
        colors = pal.core[name]
        k = clock.beat(t) % len(colors)  # rotate for variety; one-color effects step through it
        return colors[k:] + colors[:k]

    out: list[EffectPlacement] = []
    accented: list[tuple[int, int]] = []  # (revert order, index into out)
    normal = [i for i, t in enumerate(tiers) if t == "normal"]
    feature = max(normal, key=lambda i: analysis.sections[i].energy_level) if normal else None

    for e in effects:
        colors = core_colors(e)
        sec = clock.section(e.start_time_ms / 1000)
        hero = priority_of.get(e.model_name) == "hero"
        eligible = signatures and e.model_name not in no_accent and e.effect_name != "Faces" \
            and not (e.model_name in arch_names and e.layer == 1)
        accent = None
        if eligible and tiers[sec] == "peak":
            if not hero and e.layer > 0:
                accent = [signatures[0], colors[0]]          # secondary props carry it
            elif hero and e.layer == 2 and clock.beat(e.start_time_ms / 1000) % 2:
                accent = [colors[0], signatures[0]]          # heroes: every other hit, paired
        elif eligible and sec == feature and not hero and e.layer == 0:
            accent = [signatures[-1], colors[0]]             # the one featured section
        if accent:
            order = 0 if tiers[sec] != "peak" else (1 if e.layer == 1 else 2)
            accented.append((order, len(out)))
        out.append(e.model_copy(update={"palette": _palette(accent or colors)}))

    # Keep accents within budget: revert the featured section first, then the longest
    total = sum(e.end_time_ms - e.start_time_ms for e in out) or 1
    share = sum(out[i].end_time_ms - out[i].start_time_ms for _, i in accented) / total
    for _, i in sorted(accented, key=lambda a: (a[0], -(out[a[1]].end_time_ms - out[a[1]].start_time_ms))):
        if share <= pal.max_accent_share:
            break
        e = out[i]
        share -= (e.end_time_ms - e.start_time_ms) / total
        out[i] = e.model_copy(update={"palette": _palette(core_colors(e))})
    return out, round(share, 3)


def accent_share(effects: list[EffectPlacement], signatures: list[str]) -> float:
    """Share of effect time whose palette includes a signature color."""
    sig = set(signatures)
    total = sum(e.end_time_ms - e.start_time_ms for e in effects) or 1
    return sum(e.end_time_ms - e.start_time_ms for e in effects
               if e.palette and sig & set(e.palette.colors)) / total
