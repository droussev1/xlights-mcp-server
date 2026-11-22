"""A song's design brief: motion style, effect families, stem mapping, arch pattern, and
the signature moment — and variety between neighboring songs."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from test_show_rules import BEATS, DOWNBEATS, GROUPS, MODELS, RULES, SECTIONS, SETTINGS, _on
from xlights_mcp.audio.analyzer import SongAnalysis, StemAnalysis, StemOnsets
from xlights_mcp.audio.beats import BeatMap
from xlights_mcp.sequencer import engine, show_rules
from xlights_mcp.sequencer.show_rules import ShowRules
from xlights_mcp.sequencer.song_settings import SongSettings
from xlights_mcp.xlights.models import ShowConfig

FRAMES = [i / 10 for i in range(320)]
OTHER_ONSETS = [5.1, 5.3, 7.7, 9.9, 20.1]


def _analysis(other_level=lambda t: 1.0):
    stems = StemAnalysis(available=True, stems={
        "other": StemOnsets(name="other", energy=[other_level(t) for t in FRAMES], energy_times=FRAMES,
                            peak_rms=1.0, onset_times=OTHER_ONSETS),
        "drums": StemOnsets(name="drums", energy=[1.0] * len(FRAMES), energy_times=FRAMES, peak_rms=1.0,
                            onset_times=BEATS),
    })
    return SongAnalysis(
        file_path="s.mp3", file_name="s.mp3", duration_seconds=32.0,
        beats=BeatMap(tempo=120.0, beat_times=BEATS, downbeat_times=DOWNBEATS), sections=SECTIONS,
        stem_analysis=stems,
    )


def _gen(tmp_path, monkeypatch, brief=None, song="song", analysis=None, write=False):
    monkeypatch.setattr(engine, "_try_extract_vocal_tracks", lambda _: [])
    captured = {}
    real_write = engine.write_xsq
    monkeypatch.setattr(engine, "write_xsq", lambda spec, cfg, path: (captured.setdefault("spec", spec),
                                                                       real_write(spec, cfg, path)))
    show = ShowConfig(show_path=str(tmp_path), show_name="t", models=MODELS, model_groups=GROUPS)
    result = engine._generate_auto(
        analysis or _analysis(), show, tmp_path / f"{song}.mp3", None, "christmas",
        model_priorities={"TREES": "hero"},
        song_settings=SongSettings.model_validate({**SETTINGS, **({"brief": brief} if brief else {})}),
        show_rules=ShowRules.model_validate(RULES), write=write,
    )
    return result, (captured.get("spec") or result["spec"])


def _layer(spec, model, layer, lo, hi):
    return [e for e in _on(spec, model, layer) if lo * 1000 <= e.start_time_ms < hi * 1000]


@pytest.mark.parametrize("pattern, steps", [
    ("chase", [[0], [1], [2], [3]]),
    ("bounce", [[0], [1], [2], [3], [2], [1]]),
    ("center_out", [[1, 2], [0, 3]]),
    ("fill", [[0], [0, 1], [0, 1, 2], [0, 1, 2, 3]]),
])
def test_arch_patterns_take_one_step_per_beat(pattern, steps):
    assert show_rules.arch_steps(pattern, 4) == steps
    assert show_rules.arch_steps("center_out", 5) == [[2], [1, 3], [0, 4]]


def _subs(spec, t):
    return {e.model_name: e.settings["B_CUSTOM_SubBuffer"] for e in spec.effects
            if e.model_name in ("Arch L", "Arch R") and e.layer == 1 and e.start_time_ms == t}


def test_the_brief_picks_the_arch_pattern(tmp_path, monkeypatch):
    # 8 arches (4 per model); 4.0 s is beat 8 of the cycle from the first bar
    _, spec = _gen(tmp_path, monkeypatch, {"mood": "m", "arch_pattern": "center_out"})
    assert _subs(spec, 4000) == {"Arch L": "75.00x0.00x100.00x100.00", "Arch R": "0.00x0.00x25.00x100.00"}
    _, spec = _gen(tmp_path, monkeypatch, {"mood": "m", "arch_pattern": "fill"})
    assert _subs(spec, 4000) == {"Arch L": "0.00x0.00x25.00x100.00"}
    assert _subs(spec, 5500) == {"Arch L": "0.00x0.00x100.00x100.00"}   # four arches lit
    assert _subs(spec, 7500) == {"Arch L": "0.00x0.00x100.00x100.00", "Arch R": "0.00x0.00x100.00x100.00"}


def test_hero_effects_and_effect_families_come_from_the_brief(tmp_path, monkeypatch):
    brief = {"mood": "m", "hero_effects": ["Pinwheel_sweep"], "effects": {"custom": ["Butterfly_gentle"]}}
    result, spec = _gen(tmp_path, monkeypatch, brief)
    assert {e.effect_name for e in _layer(spec, "Tree L", 1, 4, 28)} == {"Pinwheel"}
    assert {e.effect_name for e in _layer(spec, "Flake", 1, 4, 16)} == {"Butterfly"}
    assert result["design"]["hero_effects"] == ["Pinwheel_sweep"]


def test_flowing_motion_has_sparser_softer_accents(tmp_path, monkeypatch):
    _, punchy = _gen(tmp_path, monkeypatch, {"mood": "m", "motion": "punchy"})
    _, flowing = _gen(tmp_path, monkeypatch, {"mood": "m", "motion": "flowing"})
    hits = lambda spec: _layer(spec, "Tree L", 2, 16, 28)  # noqa: E731  (peak: every beat when punchy)
    assert len(hits(flowing)) * 2 == pytest.approx(len(hits(punchy)), abs=1)
    assert {e.effect_name for e in hits(flowing)} == {"Morph"}
    assert {e.effect_name for e in hits(punchy)} == {"Shockwave"}


def test_mapped_props_accent_their_stems_onsets(tmp_path, monkeypatch):
    analysis = _analysis(other_level=lambda t: 0.0 if 16 <= t < 28 else 1.0)  # "other" drops out in the peak
    _, spec = _gen(tmp_path, monkeypatch, {"mood": "m", "stems": {"other": ["Flake"]}}, analysis=analysis)
    # Normal section 4–16 s: once per bar at most, on the stem's onsets
    assert [e.start_time_ms for e in _layer(spec, "Flake", 2, 4, 16)] == [5100, 7700, 9900]
    assert _layer(spec, "Flake", 2, 16, 28) == []  # its stem is quiet there


def test_signature_moment_is_placed_and_follows_the_rules(tmp_path, monkeypatch):
    moment = {"name": "snowfall", "description": "meteors rain down the trees", "section": "finale",
              "models": ["TREES"], "layer": 1, "effect": "Meteors_rain"}
    result, spec = _gen(tmp_path, monkeypatch, {"mood": "m", "signature_moment": moment})
    assert {e.effect_name for e in _layer(spec, "Tree L", 1, 16, 28)} == {"Meteors"}
    assert result["design"]["signature_moment"]["name"] == "snowfall"
    strobe = {**moment, "section": None, "start": 4.0, "end": 8.0, "effect": "Strobe_flash", "every_beats": 2}
    with pytest.raises(ValueError, match="strobes only in the finale"):
        _gen(tmp_path, monkeypatch, {"mood": "m", "signature_moment": strobe})


def test_invalid_briefs_are_rejected(tmp_path, monkeypatch):
    with pytest.raises(ValueError, match="unknown effect preset or look 'Sparkle_wild'"):
        _gen(tmp_path, monkeypatch, {"mood": "m", "hero_effects": ["Sparkle_wild"]})
    with pytest.raises(ValueError, match="unknown prop type 'tower'"):
        _gen(tmp_path, monkeypatch, {"mood": "m", "effects": {"tower": ["Spirals_fast"]}})
    with pytest.raises(ValueError, match="strobes belong in the finale"):
        _gen(tmp_path, monkeypatch, {"mood": "m", "effects": {"tree": ["Strobe_flash"]}})
    with pytest.raises(ValueError, match="mapped to both"):
        SongSettings.model_validate({"brief": {"mood": "m", "stems": {"bass": ["Flake"], "other": ["Flake"]}}})


def _record(tmp_path, song, heroes, arch):
    show_rules.record_design(tmp_path, song, show_rules.Design(heroes, arch, None, []))


def test_neighbors_in_show_order_dont_share_hero_effects_or_arch_pattern(tmp_path):
    rules = ShowRules.model_validate({**RULES, "style": {**RULES["style"], "variety": {}}})
    _record(tmp_path, "05 Jingle", ["Pinwheel_sweep", "Spirals_fast"], "bounce")
    _record(tmp_path, "09 Far Away", ["Meteors_rain"], "fill")  # not a neighbor once 07 exists
    _record(tmp_path, "07 Silent", ["Meteors_rain"], "fill")
    brief = SongSettings.model_validate({"brief": {"mood": "m", "arch_pattern": "bounce"}}).brief
    with pytest.raises(ValueError, match="'bounce' is also used by '05 Jingle'"):
        show_rules.pick_design(rules, tmp_path, "06 Holly", brief, [])
    brief = SongSettings.model_validate({"brief": {"mood": "m", "hero_effects": ["Meteors_rain"]}}).brief
    with pytest.raises(ValueError, match="'Meteors_rain' .* is also a hero effect of '07 Silent'"):
        show_rules.pick_design(rules, tmp_path, "06 Holly", brief, [])
    # Without a brief, picks avoid both neighbors
    pool = ["Pinwheel_sweep", "Spirals_fast", "Meteors_rain", "Spirals_reverse", "Chase_left"]
    design = show_rules.pick_design(rules, tmp_path, "06 Holly", None, pool)
    assert design.neighbors == ["05 Jingle", "07 Silent"]
    assert set(design.hero_effects) <= {"Spirals_reverse", "Chase_left"}
    assert design.arch_pattern in {"chase", "center_out"}
    # Songs with no track number have no neighbors; without the variety rule, anything goes
    off = ShowRules.model_validate(RULES)
    assert show_rules.pick_design(off, tmp_path, "06 Holly", brief, []).hero_effects == ["Meteors_rain"]
    assert show_rules.pick_design(rules, tmp_path, "Untracked", brief, []).neighbors == []


def test_the_design_is_recorded_when_the_sequence_is_written(tmp_path, monkeypatch):
    moment = {"name": "snowfall", "section": "finale", "models": ["TREES"], "layer": 1, "effect": "Meteors_rain"}
    _gen(tmp_path, monkeypatch, {"mood": "m", "hero_effects": ["Pinwheel_sweep"], "arch_pattern": "fill",
                                 "signature_moment": moment}, song="06 song", write=True)
    designs = json.loads((Path(tmp_path) / show_rules.DESIGNS_FILENAME).read_text())["songs"]
    entry = designs["06 song"]
    assert {k: entry[k] for k in ("hero_effects", "arch_pattern", "signature_moment", "faces")} == {
        "hero_effects": ["Pinwheel_sweep"], "arch_pattern": "fill", "signature_moment": "snowfall", "faces": False}
    assert entry["signature"] == {"name": "snowfall", "effect": "Meteors_rain", "models": ["TREES"]}
