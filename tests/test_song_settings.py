"""Per-song settings files: bar anchor, section marks and custom effects."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from xlights_mcp.audio.analyzer import SongAnalysis
from xlights_mcp.audio.beats import BeatMap
from xlights_mcp.audio.structure import SongSection
from xlights_mcp.sequencer import engine
from xlights_mcp.sequencer.song_settings import (
    SongSettings, apply_custom_effects, apply_to_analysis, load_song_settings, settings_path_for,
)
from xlights_mcp.xlights.models import LightModel, ModelGroup, ShowConfig
from xlights_mcp.xlights.xsq_writer import EffectPlacement

BEAT = 0.5  # 120 BPM
BEATS = [round(i * BEAT, 3) for i in range(64)]  # 32 s

SHOW = ShowConfig(
    show_path="",
    show_name="test",
    models=[
        LightModel(name="Column 1", display_as="Matrix", pixel_count=700),
        LightModel(name="Column 2", display_as="Matrix", pixel_count=700),
        LightModel(name="Eaves", display_as="Single Line", pixel_count=47),
    ],
    model_groups=[ModelGroup(name="COLUMNS", members=["Column 1", "Column 2"])],
)


def _analysis():
    return SongAnalysis(
        file_path="song.mp3",
        file_name="song.mp3",
        duration_seconds=32.0,
        beats=BeatMap(tempo=120.0, beat_times=BEATS, downbeat_times=BEATS[::4]),
        sections=[
            SongSection(label="intro", start_time=0.0, end_time=4.0, energy_level=0.2),
            SongSection(label="chorus", start_time=4.0, end_time=16.0, energy_level=0.8),
            SongSection(label="chorus", start_time=16.0, end_time=32.0, energy_level=0.9),
        ],
    )


def _write(tmp_path, data) -> Path:
    mp3 = tmp_path / "song.mp3"
    settings_path_for(mp3).write_text(json.dumps(data))
    return mp3


def test_missing_file_means_no_settings(tmp_path):
    assert load_song_settings(tmp_path / "song.mp3") == (None, None)


@pytest.mark.parametrize("data, message", [
    ({"bar_anchr": 1}, "bar_anchr"),
    ({"effects": [{"models": ["Eaves"], "effect": "On_solid", "section": "nope"}]}, "unknown section 'nope'"),
    ({"effects": [{"models": ["Eaves"], "effect": "On_solid", "start": 1}]}, "needs a section"),
    ({"sections": [{"name": "x", "start": 5, "end": 2, "tier": "peak"}]}, "ends before it starts"),
])
def test_invalid_files_are_reported(tmp_path, data, message):
    with pytest.raises(ValueError, match=message):
        load_song_settings(_write(tmp_path, data))


def test_bar_anchor_realigns_downbeats():
    settings = SongSettings(bar_anchor=5.4)  # nearest beat 5.5 → index 11
    analysis, _ = apply_to_analysis(settings, _analysis())
    assert analysis.beats.downbeat_times[:3] == [1.5, 3.5, 5.5]


def test_marks_split_sections_and_force_tiers():
    settings = SongSettings(
        unmarked_tier="normal",
        sections=[
            # 16.1 snaps to the existing 16.0 boundary; 24.0 splits the last chorus
            {"name": "drop", "start": 16.1, "end": 24.0, "tier": "peak", "label": "bridge"},
        ],
    )
    analysis, tiers = apply_to_analysis(settings, _analysis())
    assert [(s.start_time, s.end_time, s.label) for s in analysis.sections] == [
        (0.0, 4.0, "intro"), (4.0, 16.0, "chorus"), (16.0, 24.0, "bridge"), (24.0, 32.0, "chorus"),
    ]
    assert tiers == ["breather", "normal", "peak", "normal"]


def test_unmarked_sections_fall_back_to_ranking():
    _, tiers = apply_to_analysis(SongSettings(), _analysis())
    assert tiers == ["breather", None, None]
    assert engine._rank_section_energy(_analysis().sections, tiers) == ["breather", "normal", "peak"]


def test_custom_effects_clip_generated_and_follow_the_bar():
    settings = SongSettings(
        bar_anchor=1.0,  # bars start at 1.0, 3.0, ...
        sections=[{"name": "drop", "start": 8.0, "end": 12.0, "tier": "peak"}],
        effects=[{"section": "drop", "models": ["COLUMNS"], "layer": 2,
                  "effect": "Strobe_flash", "every_beats": 2, "length_beats": 1}],
    )
    analysis, _ = apply_to_analysis(settings, _analysis())
    generated = [
        EffectPlacement(model_name="Column 1", layer=2, effect_name="Shockwave",
                        start_time_ms=6000, end_time_ms=14000),
        EffectPlacement(model_name="Eaves", layer=2, effect_name="Shockwave",
                        start_time_ms=6000, end_time_ms=14000),
    ]
    effects = apply_custom_effects(settings, generated, analysis, SHOW, [])
    col1 = sorted(
        ((e.effect_name, e.start_time_ms, e.end_time_ms) for e in effects if e.model_name == "Column 1"),
        key=lambda t: t[1],
    )
    assert col1 == [
        ("Shockwave", 6000, 8000),
        # bars at 7.0, 9.0, 11.0 → beats 1 and 3 in range are 8.0, 9.0, 10.0, 11.0
        ("Strobe", 8000, 8500), ("Strobe", 9000, 9500), ("Strobe", 10000, 10500), ("Strobe", 11000, 11500),
        ("Shockwave", 12000, 14000),
    ]
    assert [e for e in effects if e.model_name == "Column 2" and e.effect_name == "Strobe"]
    assert [(e.start_time_ms, e.end_time_ms) for e in effects if e.model_name == "Eaves"] == [(6000, 14000)]
    strobe = next(e for e in effects if e.effect_name == "Strobe")
    assert strobe.settings == engine._get_settings("Strobe_flash")


def test_span_effect_with_raw_xlights_name():
    settings = SongSettings(effects=[{"start": 4, "end": 8, "models": ["Eaves"],
                                      "effect": "Fire", "settings": {"E_SLIDER_Fire_Height": "50"}}])
    [fire] = apply_custom_effects(settings, [], _analysis(), SHOW, [])
    assert (fire.effect_name, fire.start_time_ms, fire.end_time_ms, fire.settings) == (
        "Fire", 4000, 8000, {"E_SLIDER_Fire_Height": "50"})


def test_generate_sequence_reads_the_settings_file(tmp_path, monkeypatch):
    mp3 = _write(tmp_path, {
        "unmarked_tier": "normal",
        "sections": [{"name": "drop", "start": 16, "end": 32, "tier": "peak"}],
        "effects": [{"section": "drop", "models": ["Eaves"], "layer": 0, "effect": "On_solid"}],
    })
    mp3.write_bytes(b"")
    show = SHOW.model_copy(update={"show_path": str(tmp_path)})
    monkeypatch.setattr(engine, "load_show_config", lambda _: show)
    monkeypatch.setattr(engine, "full_analysis", lambda *_: _analysis())
    captured = {}
    monkeypatch.setattr(engine, "write_xsq", lambda spec, *_: captured.setdefault("spec", spec))

    result = engine.generate_sequence(mp3, tmp_path, vocal_assignments={})
    assert result["song_settings"] == str(settings_path_for(mp3))
    beds = [(e.effect_name, e.start_time_ms) for e in captured["spec"].effects
            if e.model_name == "Eaves" and e.layer == 0]
    assert ("On", 16000) in beds


def test_generate_sequence_rejects_unknown_models(tmp_path, monkeypatch):
    mp3 = _write(tmp_path, {"effects": [{"start": 0, "end": 1, "models": ["Nope"], "effect": "On_solid"}]})
    mp3.write_bytes(b"")
    monkeypatch.setattr(engine, "load_show_config", lambda _: SHOW)
    result = engine.generate_sequence(mp3, tmp_path)
    assert "Unknown model or group" in result["error"]


def test_clipping_drops_slivers():
    settings = SongSettings(effects=[{"start": 8.0, "end": 12.0, "models": ["Eaves"], "effect": "On_solid"}])
    generated = [EffectPlacement(model_name="Eaves", layer=0, effect_name="Twinkle",
                                 start_time_ms=7990, end_time_ms=12030)]
    effects = apply_custom_effects(settings, generated, _analysis(), SHOW, [])
    assert [(e.effect_name, e.start_time_ms) for e in effects] == [("On", 8000)]
