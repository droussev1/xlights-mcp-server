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
- ``white``: every white is tinted and capped at ``max_brightness``; full white only in
  strobes and the final hit; no prop shows only white for longer than ``max_seconds`` at a
  time or more than ``max_share`` of the song in total

Signature colors are remembered per song in ``xlights_mcp_signatures.json`` next to the
rules file so songs in the same show don't repeat them.
"""

from __future__ import annotations

import bisect
import colorsys
import difflib
import hashlib
import itertools
import json
import logging
import math
import statistics
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
    overrides_calm_bed: bool = True  # in a singing moment, the whole yard hits with the star


class SingingMoments(_Strict):
    """When faces sing: only in vocal feature moments, as whole phrases, capped.

    Vocal exposure (vocal stem level ÷ drum stem level) is ranked within each song, like
    section energy, so the faces get the song's most exposed phrases whatever its mix.
    """

    exposed_fraction: float = Field(default=0.25, gt=0, le=1)  # most exposed share of eligible phrases
    min_vocal_drum_ratio: float = Field(default=1.0, ge=0)  # floor: vocals at least as loud as drums
    max_drum_onsets_per_beat: float = Field(default=1.0, gt=0)  # at or under: sparse drums
    max_drum_irregularity: float = Field(default=0.35, ge=0)  # inter-onset spread ÷ mean: steady
    max_share: float = Field(default=0.25, ge=0, le=1)  # of the song
    min_phrase_bars: float = Field(default=1.0, ge=0)
    max_phrase_bars: float = Field(default=8.0, gt=0)  # longer phrases split at their longest pauses
    phrase_gap_seconds: float = Field(default=0.75, gt=0)  # a longer silence ends a phrase
    cross_check: bool = True  # no singing where the vocal-stem and full-mix transcriptions disagree
    merge_gap_bars: float = Field(default=1.0, ge=0)  # phrases closer than this sing as one moment
    avoid_tiers: list[Tier] = Field(default_factory=lambda: ["peak"])
    peak_lead_bars: float = Field(default=1.0, ge=0)  # moments end at least this long before a peak
    protect_builds: bool = True  # never sing over the section leading into a peak
    between_moments: Priority = "optional"  # how singers behave when not singing
    gentle_motion: list[str] = Field(default_factory=lambda: [
        "Spirals_slow", "Plasma_slow", "Butterfly_gentle", "ColorWash_slow", "ColorWash_cycling",
        "Twinkle_ambient",
    ])  # motion presets other props may keep while a face sings


class Singing(_Strict):
    singers: list[str]
    without_lyrics: dict[str, Priority] = Field(default_factory=dict)
    min_confidence: float = Field(default=0.5, ge=0, le=1)  # below this, the song has no faces
    moments: SingingMoments | None = None  # None: singers sing the whole song

    def moments_for(self, override: dict | None) -> SingingMoments | None:
        """The singing-moment settings with a song's overrides applied (ValueError if invalid)."""
        if self.moments is None:
            return None
        if not override:
            return self.moments
        try:
            return SingingMoments.model_validate({**self.moments.model_dump(), **override})
        except ValidationError as exc:
            raise ValueError(f"Invalid singing settings for this song: {exc}") from exc


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
    adjacent_songs_distinct: bool = True  # songs next to each other in show order share no signature color
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


WARM_HUE = 35 / 360  # tint given to neutral white


class WhiteLimit(_Strict):
    colors: list[str] = Field(default_factory=list)  # always count as white
    max_saturation: float = Field(default=0.25, ge=0, le=1)  # paler than this also counts
    max_seconds: float = Field(default=1.0, ge=0)
    max_share: float = Field(default=0.05, ge=0, le=1)  # of the song, per prop
    max_brightness: float = Field(default=0.6, gt=0, le=1)
    min_tint: float = Field(default=0.12, ge=0, le=1)  # minimum saturation of a softened white
    full_white: str = "#FFFFFF"
    fallback_colors: list[str] = Field(default_factory=lambda: ["#FF0000", "#0B6E2E"])  # replace all-white palettes
    full_white_effects: list[str] = Field(default_factory=lambda: ["Strobe"])
    full_white_final_hit: bool = True

    @field_validator("colors", "fallback_colors")
    @classmethod
    def _white_colors(cls, v):
        return _hex(v)

    @field_validator("full_white")
    @classmethod
    def _full_white(cls, v):
        return _hex([v])[0]

    def is_white(self, color: str) -> bool:
        if color in self.colors:
            return True
        _, s, v = _hsv(color)
        return s <= self.max_saturation and v > 0.5

    def soften(self, color: str) -> str:
        """A white tinted to at least ``min_tint`` and capped at ``max_brightness``."""
        h, s, v = _hsv(color)
        if s < 0.02:
            h = WARM_HUE
        rgb = [c * 255 for c in colorsys.hsv_to_rgb(h, max(s, self.min_tint), min(v, self.max_brightness))]
        # Round down so 8-bit rounding never pushes brightness over the cap or tint under the minimum
        return "#" + "".join(f"{math.floor(c + 1e-9):02X}" for c in rgb)


def _hsv(color: str) -> tuple[float, float, float]:
    return colorsys.rgb_to_hsv(*(int(color[i:i + 2], 16) / 255 for i in (1, 3, 5)))


class SafetyRules(_Strict):
    """Rules every write path enforces: auto, guided, template and remap_sequence."""

    notes: list[str] = Field(default_factory=list)  # the rules in plain words
    exclude: list[str] = Field(default_factory=list)
    white: WhiteLimit | None = None
    strobes: Strobes | None = None


class StyleRules(_Strict):
    """Look-and-feel rules applied by auto generation only."""

    notes: list[str] = Field(default_factory=list)
    priorities: dict[str, Priority] = Field(default_factory=dict)
    symmetry: Symmetry | None = None
    arch_chase: ArchChase | None = None
    floods: list[str] = Field(default_factory=list)
    twinkle: Twinkle | None = None
    final_hit: FinalHit | None = None
    singing: Singing | None = None
    palettes: Palettes | None = None


class ShowRules(_Strict):
    safety: SafetyRules = Field(default_factory=SafetyRules)
    style: StyleRules = Field(default_factory=StyleRules)

    def model_names(self) -> list[str]:
        names = [*self.safety.exclude, *self.style.floods, *self.style.priorities]
        st = self.style
        if st.arch_chase:
            names += [a.model for a in st.arch_chase.arches]
        for part in (st.twinkle, st.final_hit):
            if part:
                names += part.models
        if st.singing:
            names += [*st.singing.singers, *st.singing.without_lyrics]
        if st.palettes:
            names += list(st.palettes.by_model)
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
    """Raise ValueError if the rules name a model or group the show doesn't have.

    Symmetry pairs must name models: effects are mirrored model by model, so a group
    name there would silently do nothing.
    """
    for name in rules.model_names():
        expand_models([name], show_config)
    if rules.style.symmetry:
        models = {m.name for m in show_config.models}
        groups = {g.name for g in show_config.model_groups}
        seen: set[str] = set()
        for left, right in rules.style.symmetry.pairs:
            if left == right:
                raise ValueError(f"Symmetry pair pairs '{left}' with itself")
            for name in (left, right):
                if name in groups and name not in models:
                    raise ValueError(f"Symmetry pairs must name models, not groups: '{name}'")
                if name not in models:
                    raise ValueError(f"Unknown model in symmetry pairs: '{name}'")
                if name in seen:
                    raise ValueError(f"Model '{name}' appears in more than one symmetry pair")
                seen.add(name)


def excluded_models(rules: ShowRules | None, show_config: ShowConfig) -> set[str]:
    return set(expand_models(rules.safety.exclude, show_config)) if rules else set()


def generic_skip(rules: ShowRules | None, show_config: ShowConfig) -> set[str]:
    """Models the generic generator should leave alone because the rules own them."""
    if not rules:
        return set()
    names = [*rules.safety.exclude, *rules.style.floods, *(rules.style.final_hit.models if rules.style.final_hit else [])]
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


FINAL_HIT_WINDOW = 15.0  # seconds: no downbeats → the strongest hit this close to the end
SILENCE_LEVEL = 0.05  # normalized RMS under this is silence: where the music ends
STRONG_ATTACK = 0.75  # a strong downbeat's attack is at least this × the song's median downbeat
STRONG_LEVEL = 0.5  # ...and its loudness at least this × the median downbeat's (no faint clicks)
DOWNBEAT_WINDOW = (-0.05, 0.15)  # seconds around a downbeat that its attack can land in
ONSET_SNAP = 0.06  # seconds: the hit snaps to an onset this close


def music_end_time(analysis: SongAnalysis) -> float:
    """Where the audio goes silent for good (the file's end if it never does)."""
    spec = analysis.spectrum
    loud = [t for r, t in zip(spec.rms_energy, spec.rms_times) if r >= SILENCE_LEVEL]
    return min(loud[-1], analysis.duration_seconds) if loud else analysis.duration_seconds


def _extended_downbeats(analysis: SongAnalysis, end: float) -> list[float]:
    """The downbeat grid carried on to the end of the music: beat trackers often stop
    before a song's closing button, which then falls a bar past the last tracked downbeat."""
    downbeats = [d for d in analysis.beats.downbeat_times if d < end]
    if len(downbeats) >= 2:
        gaps = sorted(b - a for a, b in itertools.pairwise(downbeats))
        bar = gaps[len(gaps) // 2]
        while downbeats[-1] + bar < end:
            downbeats.append(downbeats[-1] + bar)
    return downbeats


def _window_max(values: list[float], times: list[float], t: float) -> float:
    lo, hi = bisect.bisect_left(times, t + DOWNBEAT_WINDOW[0]), bisect.bisect_right(times, t + DOWNBEAT_WINDOW[1])
    return max(values[lo:hi], default=0.0)


def _snap_to_onset(analysis: SongAnalysis, t: float) -> float:
    onsets = [o for o in analysis.beats.onset_times if abs(o - t) <= ONSET_SNAP]
    return min(onsets, key=lambda o: abs(o - t)) if onsets else t


def final_hit_time(
    analysis: SongAnalysis, tiers: list[str], song_settings: SongSettings | None,
) -> float | None:
    """The final hit: per-song override, else the last strong downbeat before the music
    ends, snapped to its onset.

    The music ends where the audio goes silent (``SILENCE_LEVEL``). A downbeat is strong
    when it has a real attack (spectral flux) and loudness compared with the song's
    median downbeat, so a chord left ringing into the fade doesn't count. Without
    downbeats, the strongest attack in the last 15 seconds of music.
    """
    if song_settings and song_settings.final_hit is not None:
        return song_settings.final_hit
    spec = analysis.spectrum
    end = music_end_time(analysis)
    downbeats = _extended_downbeats(analysis, end)
    if downbeats:
        if not spec.spectral_flux:
            return downbeats[-1]
        attack = [_window_max(spec.spectral_flux, spec.rms_times, d) for d in downbeats]
        level = ([_window_max(spec.rms_energy, spec.rms_times, d) for d in downbeats]
                 if spec.rms_energy else [1.0] * len(downbeats))
        typical_attack, typical_level = statistics.median(attack), statistics.median(level)
        for d, a, lv in zip(reversed(downbeats), reversed(attack), reversed(level)):
            if a >= STRONG_ATTACK * typical_attack and lv >= STRONG_LEVEL * typical_level:
                return _snap_to_onset(analysis, d)
        return _snap_to_onset(analysis, downbeats[-1])
    window = [(f, t) for f, t in zip(spec.spectral_flux, spec.rms_times) if end - FINAL_HIT_WINDOW <= t <= end]
    if not window:
        return None
    return _snap_to_onset(analysis, max(window)[1])  # largest spectral flux = the sharpest attack


def check_strobes(
    rules: ShowRules | None, song_settings: SongSettings | None,
    finale: tuple[float, float] | None, strobe_presets: set[str],
) -> None:
    """Strobes may only run in the finale at the configured spacing (ValueError if not)."""
    if not (rules and rules.safety.strobes and song_settings):
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
        if e.every_beats != rules.safety.strobes.every_beats:
            raise ValueError(
                f"Show rules require strobes every {rules.safety.strobes.every_beats} beats "
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
    floods = set(expand_models(rules.style.floods, show_config))
    on_name, on_settings = preset("On_solid")
    for name in floods:
        effects = [e for e in effects if e.model_name != name]
        effects += [_effect(name, 0, on_name, s.start_time_ms, s.end_time_ms, on_settings) for s in sections]

    # Final-hit props stay dark until the final hit, then hold and fade
    hit_models = set(expand_models(rules.style.final_hit.models, show_config)) if rules.style.final_hit else set()
    for name in hit_models:
        effects = [e for e in effects if e.model_name != name]
        if final_hit is not None:
            bar_ms = 4 * 60000 / max(analysis.beats.tempo, 1)
            start = int(final_hit * 1000)
            end = min(start + int(rules.style.final_hit.bars * bar_ms), analysis.duration_ms)
            fade = f"{(end - start) / 1000 * 0.75:.2f}"
            effects.append(_effect(name, 0, on_name, start, end, {**on_settings, "T_TEXTCTRL_Fadeout": fade}))

    # Symmetry: rebuild each right-hand prop from its left-hand partner
    if rules.style.symmetry:
        special = floods | hit_models
        for left, right in rules.style.symmetry.pairs:
            if {left, right} & special:
                continue
            sing = {left, right} & singers
            if len(sing) == 1:
                logger.warning(f"Skipping symmetry for {left}/{right}: only {sing.pop()} sings")
                continue
            if not sing and priority_of.get(left, "normal") != priority_of.get(right, "normal"):
                logger.warning(f"Skipping symmetry for {left}/{right}: different priorities")
                continue
            # Singing pairs keep their own faces; only the background bed (layer 0) mirrors
            layers = {0} if sing else {0, 1, 2}
            left_fx = [e for e in effects if e.model_name == left and e.layer in layers]
            effects = [e for e in effects
                       if not (e.model_name in (left, right) and e.layer in layers)]
            for e in left_fx:
                t = e.start_time_ms / 1000
                mode = rules.style.symmetry.by_tier.get(tiers[clock.section(t)], rules.style.symmetry.default)
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
    if rules.style.twinkle:
        names = expand_models(rules.style.twinkle.models, show_config)
        tw_name, tw_settings = preset("Twinkle_ambient")
        for i, s in enumerate(sections):
            if tiers[i] in rules.style.twinkle.tiers:
                effects = clip_out(effects, names, None, s.start_time_ms, s.end_time_ms)
                effects += [_effect(n, 0, tw_name, s.start_time_ms, s.end_time_ms, tw_settings) for n in names]

    # Arch chase: one arch per beat, left to right, starting on the bar line
    if rules.style.arch_chase:
        slots = [
            (a.model, a.count - 1 - i if a.reverse else i, a.count)
            for a in rules.style.arch_chase.arches for i in range(a.count)
        ]
        arch_names = {a.model for a in rules.style.arch_chase.arches}
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
# Singing moments
# ---------------------------------------------------------------------------


@dataclass
class SingingMoment:
    start: float
    end: float
    vocal_drum_ratio: float
    lyrics: str


@dataclass
class DisputedPassage:
    start: float
    end: float
    stem: str  # what the vocal-stem transcription heard
    mix: str   # what the full-mix transcription heard over the same stretch


@dataclass
class TrimmedPhrase:
    start: float
    end: float
    reason: str
    lyrics: str


def _peak_limits(analysis: SongAnalysis, tiers: list[str], cfg: SingingMoments, bar: float):
    """For each peak: (latest time a moment may end before it, why)."""
    sections = analysis.sections
    limits = []
    for i, t in enumerate(tiers):
        if t != "peak" or (i > 0 and tiers[i - 1] == "peak"):
            continue
        start = sections[i].start_time
        if cfg.protect_builds and i > 0:  # checked first: named as the reason when both apply
            build = sections[i - 1]
            limits.append((build.start_time, start,
                           f"runs into the build ({build.start_time:.1f}-{build.end_time:.1f}s) before the peak at {start:.1f}s"))
        limits.append((start - cfg.peak_lead_bars * bar, start,
                       f"ends less than {cfg.peak_lead_bars:g} bar(s) before the peak at {start:.1f}s"))
    return limits


def _split_phrase(words: list, max_seconds: float, min_seconds: float) -> list[list]:
    """Split a phrase longer than ``max_seconds`` at its longest pauses, recursively,
    never leaving a piece shorter than ``min_seconds``; a phrase that can't be split that
    way stays whole."""
    span = words[-1].end_time - words[0].start_time
    if span <= max_seconds or len(words) < 2:
        return [words]
    mid = (words[0].start_time + words[-1].end_time) / 2
    cuts = sorted(range(1, len(words)), key=lambda i: (
        -(words[i].start_time - words[i - 1].end_time),  # longest pause first
        abs(words[i].start_time - mid),                  # then the most even split
    ))
    for i in cuts:
        left, right = words[:i], words[i:]
        if (left[-1].end_time - left[0].start_time >= min_seconds
                and right[-1].end_time - right[0].start_time >= min_seconds):
            return _split_phrase(left, max_seconds, min_seconds) + _split_phrase(right, max_seconds, min_seconds)
    return [words]


def _norm_word(word: str) -> str:
    return "".join(ch for ch in word.lower() if ch.isalnum())


def cross_check(words, reference) -> tuple[set[int], set[int], list[DisputedPassage]]:
    """Where the vocal-stem transcription (``words``) and the full-mix one (``reference``)
    disagree on the words.

    The two are aligned word by word. Stem words the mix heard differently or not at all
    (a different word count) are disputed; words only the mix heard break the stem phrase
    where they fall. Words both heard are kept with the stem's timing, however far apart
    the two placed them. Returns the disputed stem word indices, the indices a phrase must
    break before, and the passages.
    """
    matcher = difflib.SequenceMatcher(None, [_norm_word(w.word) for w in words],
                                      [_norm_word(w.word) for w in reference], autojunk=False)
    disputed: set[int] = set()
    breaks: set[int] = set()
    passages: list[DisputedPassage] = []

    def passage(i1, i2, j1, j2):
        ours, theirs = words[i1:i2], reference[j1:j2]
        times = [w.start_time for w in ours + theirs] + [w.end_time for w in ours + theirs]
        passages.append(DisputedPassage(round(min(times), 2), round(max(times), 2),
                                        " ".join(w.word for w in ours), " ".join(w.word for w in theirs)))

    for op, i1, i2, j1, j2 in matcher.get_opcodes():
        if op in ("replace", "delete"):
            disputed.update(range(i1, i2))
            passage(i1, i2, j1, j2)
        elif op == "insert":
            breaks.add(i1)
            passage(i1, i1, j1, j2)
    return disputed, breaks, passages


def pick_singing_moments(
    words, analysis: SongAnalysis, tiers: list[str], cfg: SingingMoments, trimmed: list | None = None,
    reference_words=None, disputed: list | None = None,
) -> list[SingingMoment]:
    """The song's most exposed vocal phrases over steady or sparse drums, most exposed
    first, up to ``max_share`` of the song. Needs separated stems.

    Exposure (vocal stem level ÷ drum stem level) is ranked within the song: of the
    eligible phrases, the top ``exposed_fraction`` qualify (at least one, and never below
    ``min_vocal_drum_ratio``). Phrases longer than ``max_phrase_bars`` are first split at
    their longest pauses, never into pieces shorter than ``min_phrase_bars``.

    With ``reference_words`` (the full-mix transcription) and ``cross_check``, words the
    two transcriptions disagree on are never sung and break their phrase; the passages are
    appended to ``disputed`` if given.

    Qualifying phrases that would end too close to a peak, or run into the build before
    one, are dropped from their moment (it's trimmed to its earlier phrases); they're
    appended to ``trimmed`` if given.
    """
    stems = analysis.stem_analysis
    if not (words and stems.available and "vocals" in stems.stems and "drums" in stems.stems):
        return []
    beat = 60 / max(analysis.beats.tempo, 1)
    bar = 4 * beat
    avoid = [s for s, t in zip(analysis.sections, tiers) if t in cfg.avoid_tiers]
    limits = _peak_limits(analysis, tiers, cfg, bar)

    # Never sing where the stem and mix transcriptions disagree: drop those words and
    # break the phrase there
    bad: set[int] = set()
    breaks: set[int] = set()
    if cfg.cross_check and reference_words:
        bad, breaks, passages = cross_check(words, reference_words)
        if disputed is not None:
            disputed.extend(passages)
    phrases: list[list] = []
    current: list = []
    for i, w in enumerate(words):
        if i in bad or i in breaks or (current and w.start_time - current[-1].end_time > cfg.phrase_gap_seconds):
            if current:
                phrases.append(current)
            current = []
        if i not in bad:
            current.append(w)
    if current:
        phrases.append(current)
    phrases = [piece for p in phrases
               for piece in _split_phrase(p, cfg.max_phrase_bars * bar, cfg.min_phrase_bars * bar)]

    eligible = []
    for p in phrases:
        a, b = p[0].start_time, p[-1].end_time
        if b - a < cfg.min_phrase_bars * bar:
            continue  # never sing fragments
        if sum(max(0.0, min(b, s.end_time) - max(a, s.start_time)) for s in avoid) > bar / 2:
            continue
        drums = stems.get_level_in_range("drums", a, b)
        ratio = stems.get_level_in_range("vocals", a, b) / drums if drums > 0 else float("inf")
        if ratio < cfg.min_vocal_drum_ratio:
            continue
        onsets = stems.get_onsets_in_range("drums", a, b)
        sparse = len(onsets) / ((b - a) / beat) <= cfg.max_drum_onsets_per_beat
        gaps = [y - x for x, y in zip(onsets, onsets[1:])]
        mean = sum(gaps) / len(gaps) if gaps else 0
        steady = len(gaps) >= 2 and mean > 0 and (
            (sum((g - mean) ** 2 for g in gaps) / len(gaps)) ** 0.5 / mean <= cfg.max_drum_irregularity)
        if not (sparse or steady):
            continue
        eligible.append((ratio, a, b, " ".join(w.word for w in p)))

    # Rank within the song, like section energy: only the most exposed phrases qualify
    eligible.sort(key=lambda c: -c[0])
    exposed = eligible[:max(1, round(len(eligible) * cfg.exposed_fraction))] if eligible else []

    candidates = []
    for ratio, a, b, text in sorted(exposed, key=lambda c: c[1]):
        # Must finish before every peak's lead-in (and its build) that comes after it starts
        breach = next((why for latest, peak, why in limits if a < peak and b > latest), None)
        if breach:
            if trimmed is not None:
                trimmed.append(TrimmedPhrase(round(a, 2), round(b, 2), breach, text))
            continue
        candidates.append((ratio, a, b, text))

    def merged(chosen):
        out: list[list] = []
        for ratio, a, b, text in sorted(chosen, key=lambda c: c[1]):
            if out and a - out[-1][1] < cfg.merge_gap_bars * bar:
                out[-1][1] = b
                out[-1][2] = min(out[-1][2], ratio)
                out[-1][3] += " / " + text
            else:
                out.append([a, b, ratio, text])
        return out

    budget = cfg.max_share * analysis.duration_seconds
    chosen: list = []
    for c in sorted(candidates, key=lambda c: -c[0]):
        if sum(b - a for a, b, *_ in merged(chosen + [c])) <= budget:
            chosen.append(c)
    return [SingingMoment(a, b, round(r, 2) if r != float("inf") else 999.0, t) for a, b, r, t in merged(chosen)]


def apply_singing(
    effects: list[EffectPlacement],
    moments: list[SingingMoment],
    singers: dict[str, str],  # model → face definition
    tracks: dict[str, str],   # model → timing track
    cfg: SingingMoments,
    show_config: ShowConfig,
    rules: ShowRules,
    preset,
    final_hit: float | None = None,
    beat_seconds: float = 0.5,
    hit_effect=None,  # model name → (xLights effect name, settings) for its final-hit accent
) -> list[EffectPlacement]:
    """Faces sing in the moments; everything else drops to a calm bed meanwhile.

    With ``final_hit.overrides_calm_bed``, a moment the final hit falls in keeps the calm
    bed only until the hit: from there the other props get their regular effects back and
    the whole yard accents the hit with the star, while the faces finish the phrase.
    Floods and excluded models are left alone.
    """
    gentle = [preset(k) for k in cfg.gentle_motion]
    hit_models = set(expand_models(rules.style.final_hit.models, show_config)) if rules.style.final_hit else set()
    yard_hits = bool(rules.style.final_hit and rules.style.final_hit.overrides_calm_bed and final_hit is not None)
    skip = set(expand_models([*rules.style.floods, *rules.safety.exclude], show_config))
    bed_name, bed_settings = preset("ColorWash_slow")
    for m in moments:
        lo, hi = int(m.start * 1000), int(m.end * 1000)
        others = [x.name for x in show_config.models if x.name not in singers and x.name not in hit_models]
        calm_hi = hi
        if yard_hits and lo <= final_hit * 1000 < hi:
            calm_hi = int(final_hit * 1000)
            name_hit = hit_effect or (lambda _: preset("Shockwave_hit"))
            yard = [name for name in others if name not in skip]
            hit_end = calm_hi + int(beat_seconds * 1000)
            effects = clip_out(effects, yard, 2, calm_hi, hit_end)
            for name in yard:
                effect, settings = name_hit(name)
                effects.append(_effect(name, 2, effect, calm_hi, hit_end, settings))
        # Singers: just a calm bed and the face
        effects = clip_out(effects, list(singers), None, lo, hi)
        for name, face in singers.items():
            effects.append(_effect(name, 0, bed_name, lo, hi, bed_settings))
            effects.append(_effect(name, 1, "Faces", lo, hi, {
                "E_CHECKBOX_Faces_Outline": "1",
                "E_CHOICE_Faces_EyeBlinkDuration": "Normal",
                "E_CHOICE_Faces_EyeBlinkFrequency": "Normal",
                "E_CHOICE_Faces_Eyes": "Auto",
                "E_CHOICE_Faces_FaceDefinition": face,
                "E_CHOICE_Faces_TimingTrack": tracks[name],
                "T_TEXTCTRL_Fadein": "0.3",
                "T_TEXTCTRL_Fadeout": "0.3",
            }))
        # Everyone else: no accent hits, and only gentle motion (until the final hit)
        if calm_hi <= lo:
            continue
        effects = clip_out(effects, others, 2, lo, calm_hi)
        busy = [e for e in effects if e.layer == 1 and e.model_name in others
                and e.end_time_ms > lo and e.start_time_ms < calm_hi
                and (e.effect_name, e.settings) not in gentle]
        busy_ids = {id(e) for e in busy}
        effects = [e for e in effects if id(e) not in busy_ids]
        for e in busy:  # keep the parts outside the calm stretch
            effects += clip_out([e], [e.model_name], 1, lo, calm_hi)
    return effects


def hold_floods(
    effects: list[EffectPlacement], moments: list[SingingMoment], rules: ShowRules, show_config: ShowConfig,
) -> list[EffectPlacement]:
    """Floods keep the color they had when a singing moment began until it ends."""
    for name in expand_models(rules.style.floods, show_config):
        for m in moments:
            lo, hi = int(m.start * 1000), int(m.end * 1000)
            current = next((e for e in effects if e.model_name == name and e.layer == 0
                            and e.start_time_ms <= lo < e.end_time_ms), None)
            if current is None or current.end_time_ms >= hi:
                continue
            effects = clip_out(effects, [name], 0, lo, hi)
            effects.append(current.model_copy(update={"start_time_ms": lo, "end_time_ms": hi}))
    return effects


# ---------------------------------------------------------------------------
# Palettes
# ---------------------------------------------------------------------------


def _palette(colors: list[str]) -> ColorPalette:
    return ColorPalette(colors=colors[:8], active_colors=list(range(1, min(len(colors), 8) + 1)))


def pick_signatures(
    rules: ShowRules, show_path: Path, song: str, override: list[str] | None,
) -> list[str]:
    """This song's signature accent colors: a set no other song in the show has.

    Single colors may recur in different sets, but with ``adjacent_songs_distinct`` the
    songs either side of this one in show order (by track-number prefix, see
    ``show_position``) share none of its colors. With two per song, a pool of n colors
    gives n*(n-1)/2 unique pairs. Raises ValueError if an override breaks either rule or no
    usable set is left in the pool.
    """
    registry = _read_registry(show_path)
    taken = {frozenset(colors): s for s, colors in registry.items() if s != song}
    neighbors = (adjacent_songs(song, registry) if rules.style.palettes.adjacent_songs_distinct else [])
    near = {c: s for s in neighbors for c in registry[s]}

    def clash(colors):
        return next(((c, near[c]) for c in colors if c in near), None)

    if override:
        if frozenset(override) in taken:
            raise ValueError(
                f"Signature colors {override} are already used by '{taken[frozenset(override)]}'"
            )
        if hit := clash(override):
            raise ValueError(f"Signature color {hit[0]} is also used by '{hit[1]}', next to this song in show order")
        return override
    if song in registry and frozenset(registry[song]) not in taken and not clash(registry[song]):
        return registry[song]
    pool = rules.style.palettes.accent_pool
    n = rules.style.palettes.signatures_per_song
    if len(pool) < n:
        return []
    options = [list(c) for c in itertools.combinations(pool, n)]
    start = int(hashlib.md5(song.encode()).hexdigest(), 16) % len(options)
    for colors in options[start:] + options[:start]:
        if frozenset(colors) not in taken and not clash(colors):
            return colors
    if near:
        raise ValueError(
            f"No signature color set in the accent pool is both unused and free of the colors of "
            f"{', '.join(repr(s) for s in neighbors)} (next to this song in show order); "
            "add colors to palettes.accent_pool"
        )
    raise ValueError(
        f"All {len(options)} signature color sets in the accent pool are used by other songs; "
        "add colors to palettes.accent_pool"
    )


def show_position(song: str) -> int | None:
    """A song's place in the show: its track-number prefix ("06 Holly Jolly" → 6)."""
    digits = ""
    for ch in song.lstrip():
        if not ch.isdigit():
            break
        digits += ch
    return int(digits) if digits else None


def adjacent_songs(song: str, registry: dict[str, list[str]]) -> list[str]:
    """The registered songs immediately before and after this one in show order (none
    for a song without a track-number prefix)."""
    me = show_position(song)
    if me is None:
        return []
    placed = [(pos, s) for s in registry if s != song and (pos := show_position(s)) is not None and pos != me]
    before = [x for x in placed if x[0] < me]
    after = [x for x in placed if x[0] > me]
    return [x[1] for x in (max(before, default=None), min(after, default=None)) if x]


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
    pal = rules.style.palettes
    clock = _Clock(analysis, tiers)
    model_core: dict[str, str] = {}
    for name, core in pal.by_model.items():
        for m in expand_models([name], show_config):
            model_core.setdefault(m, core)
    arch_names = {a.model for a in rules.style.arch_chase.arches} if rules.style.arch_chase else set()
    floods = set(expand_models(rules.style.floods, show_config))
    no_accent = floods | set(expand_models(rules.style.final_hit.models, show_config) if rules.style.final_hit else [])
    is_white = rules.safety.white.is_white if rules.safety.white else (lambda c: False)

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
        if e.model_name in floods:
            # Step through the palette's non-white colors section by section, so each
            # boundary brings a new color and floods never sit on white
            leads = [c for c in colors if not is_white(c)] or colors
            lead = leads[sec % len(leads)]
            return [lead] + [c for c in colors if c != lead]
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


SINGLE_COLOR_EFFECTS = {"On"}  # render only the palette's first color


def shows_only_white(e: EffectPlacement, is_white) -> bool:
    """Whether the effect lights its whole prop in white and nothing else."""
    if not e.palette or "B_CUSTOM_SubBuffer" in e.settings:
        return False  # partial-prop effects (e.g. one arch of a chase) don't count
    colors = e.palette.colors[:len(e.palette.active_colors)] or e.palette.colors
    if e.effect_name in SINGLE_COLOR_EFFECTS:
        return is_white(colors[0])
    return all(is_white(c) for c in colors)


def white_plan(effects: list[EffectPlacement], limit: WhiteLimit, duration_ms: int, is_moment) -> list[str]:
    """Per effect: "full" (a full-white moment), "recolor" (white-only for too long, or
    past the prop's white budget) or "soften" (dim and tint any whites)."""
    def white_only(e):
        return not is_moment(e) and shows_only_white(e, limit.is_white)

    actions = ["full" if is_moment(e) else "soften" for e in effects]
    for i, e in enumerate(effects):
        if white_only(e) and e.end_time_ms - e.start_time_ms > limit.max_seconds * 1000:
            actions[i] = "recolor"
    budget = limit.max_share * duration_ms
    by_model: dict[str, list[int]] = {}
    for i, e in enumerate(effects):
        if actions[i] == "soften" and white_only(e):
            by_model.setdefault(e.model_name, []).append(i)
    for idx in by_model.values():
        total = sum(effects[i].end_time_ms - effects[i].start_time_ms for i in idx)
        for i in sorted(idx, key=lambda i: effects[i].start_time_ms - effects[i].end_time_ms):  # longest first
            if total <= budget:
                break
            total -= effects[i].end_time_ms - effects[i].start_time_ms
            actions[i] = "recolor"
    return actions


def limit_white(
    rules: ShowRules, effects: list[EffectPlacement], duration_ms: int, show_config: ShowConfig,
) -> list[EffectPlacement]:
    """Apply the white rule.

    Strobes and the final hit may use full white. Everywhere else, white-only looks that
    run too long, or add up to too much per prop, lead with a non-white color instead, and
    every remaining white is tinted and dimmed.
    """
    limit = rules.safety.white
    fallback = (rules.style.palettes.core[rules.style.palettes.default]
                if rules.style.palettes else limit.fallback_colors)
    hit_models = set(expand_models(rules.style.final_hit.models, show_config)) if rules.style.final_hit else set()

    def full_white_moment(e: EffectPlacement) -> bool:
        return e.effect_name in limit.full_white_effects or (
            limit.full_white_final_hit and e.model_name in hit_models)

    out: list[EffectPlacement] = []
    for e, action in zip(effects, white_plan(effects, limit, duration_ms, full_white_moment)):
        if not e.palette:
            out.append(e)
            continue
        colors = e.palette.colors
        if action == "recolor":
            k = next((i for i, c in enumerate(colors) if not limit.is_white(c)), None)
            if k is not None:  # lead with the palette's first non-white color
                colors = colors[k:] + colors[:k]
            else:
                k = next((i for i, c in enumerate(fallback) if not limit.is_white(c)), 0)
                colors = fallback[k:] + fallback[:k]
        if action == "full":
            colors = [limit.full_white if limit.is_white(c) else c for c in colors]
            if e.model_name in hit_models:  # the final hit leads with full white
                colors = [limit.full_white] + [c for c in colors if c != limit.full_white]
        else:
            colors = [limit.soften(c) if limit.is_white(c) else c for c in colors]
        colors = list(dict.fromkeys(colors))
        out.append(e if colors == e.palette.colors else e.model_copy(update={"palette": _palette(colors)}))
    return out


def accent_share(effects: list[EffectPlacement], signatures: list[str]) -> float:
    """Share of effect time whose palette includes a signature color."""
    sig = set(signatures)
    total = sum(e.end_time_ms - e.start_time_ms for e in effects) or 1
    return sum(e.end_time_ms - e.start_time_ms for e in effects
               if e.palette and sig & set(e.palette.colors)) / total
