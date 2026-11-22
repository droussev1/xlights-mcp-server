"""Model priorities and group selection in auto generation."""

from __future__ import annotations

from pathlib import Path

from xlights_mcp.audio.analyzer import SongAnalysis
from xlights_mcp.audio.beats import BeatMap
from xlights_mcp.audio.structure import SongSection
from xlights_mcp.sequencer import engine
from xlights_mcp.xlights.models import LightModel, ModelGroup, ShowConfig

BEAT = 0.5  # 120 BPM
BEATS = [round(i * BEAT, 3) for i in range(64)]  # 32 s
DOWNBEATS = BEATS[::4]

TREE_A = LightModel(name="Mega Tree", display_as="Tree", pixel_count=1472, face_definitions=["Santa Face"])
TREE_B = LightModel(name="Mega Tree-2", display_as="Tree", pixel_count=1600)
MATRIX = LightModel(name="Matrix", display_as="Matrix", pixel_count=32768)
EAVES = LightModel(name="Eaves", display_as="Single Line", pixel_count=47)
GROUPS = [
    ModelGroup(name="ALL", members=["Mega Tree", "Mega Tree-2", "Matrix", "Eaves"]),
    ModelGroup(name="MEGA-TREES", members=["Mega Tree", "Mega Tree-2"]),
]

# Four 8 s choruses ranked breather (0–8), normal (8–16, 16–24), peak (24–32)
SECTIONS = [
    SongSection(label="chorus", start_time=0.0, end_time=8.0, energy_level=0.3),
    SongSection(label="chorus", start_time=8.0, end_time=16.0, energy_level=0.6),
    SongSection(label="chorus", start_time=16.0, end_time=24.0, energy_level=0.7),
    SongSection(label="chorus", start_time=24.0, end_time=32.0, energy_level=0.9),
]


def _effects(tmp_path, monkeypatch, priorities=None, models=None):
    analysis = SongAnalysis(
        file_path="song.mp3",
        file_name="song.mp3",
        duration_seconds=32.0,
        beats=BeatMap(tempo=120.0, beat_times=BEATS, downbeat_times=DOWNBEATS),
        sections=SECTIONS,
    )
    show = ShowConfig(
        show_path=str(tmp_path),
        show_name="test",
        models=models or [TREE_A, TREE_B, MATRIX, EAVES],
        model_groups=GROUPS,
    )
    captured = {}
    monkeypatch.setattr(engine, "write_xsq", lambda spec, *_: captured.setdefault("spec", spec))
    monkeypatch.setattr(engine, "_try_extract_vocal_tracks", lambda _: [])
    engine._generate_auto(
        analysis, show, Path(tmp_path) / "song.mp3", None, "christmas", model_priorities=priorities
    )
    return captured["spec"].effects


def _on(effects, model, layer=None):
    return [e for e in effects if e.model_name == model and (layer is None or e.layer == layer)]


def test_umbrella_group_does_not_stack_effects(tmp_path, monkeypatch):
    effects = _effects(tmp_path, monkeypatch)
    # One bed per section — not a second set from the ALL group
    assert len(_on(effects, "Mega Tree-2", layer=0)) == len(SECTIONS)
    assert len(_on(effects, "Eaves", layer=0)) == len(SECTIONS)


def test_hero_moves_in_breathers_and_skips_faces(tmp_path, monkeypatch):
    effects = _effects(tmp_path, monkeypatch, {"MEGA-TREES": "hero"})
    for tree in ("Mega Tree", "Mega Tree-2"):
        assert any(e.end_time_ms <= 8000 for e in _on(effects, tree, layer=1))
        assert not [e for e in _on(effects, tree) if e.effect_name == "Faces"]
    # Both trees stay in sync as a group
    assert [e.start_time_ms for e in _on(effects, "Mega Tree")] == [
        e.start_time_ms for e in _on(effects, "Mega Tree-2")
    ]


def test_key_moves_in_breathers(tmp_path, monkeypatch):
    normal = _effects(tmp_path, monkeypatch)
    assert not any(e.end_time_ms <= 8000 for e in _on(normal, "Eaves", layer=1))
    key = _effects(tmp_path, monkeypatch, {"Eaves": "key"})
    assert any(e.end_time_ms <= 8000 for e in _on(key, "Eaves", layer=1))


def test_optional_only_in_peaks(tmp_path, monkeypatch):
    effects = _effects(tmp_path, monkeypatch, {"Matrix": "optional"})
    matrix = _on(effects, "Matrix")
    assert matrix
    assert all(e.start_time_ms >= 24000 for e in matrix)


def test_accent_density_by_tier_and_priority(tmp_path, monkeypatch):
    effects = _effects(tmp_path, monkeypatch, {"MEGA-TREES": "hero"})

    def starts(model, lo, hi):
        return sorted(e.start_time_ms / 1000 for e in _on(effects, model, layer=2) if lo * 1000 <= e.start_time_ms < hi * 1000)

    beats = lambda lo, hi: [b for b in BEATS if lo <= b < hi]  # noqa: E731
    bars = lambda lo, hi: [b for b in DOWNBEATS if lo <= b < hi]  # noqa: E731
    # Breather: no accents for anyone
    assert starts("Mega Tree", 0, 8) == starts("Eaves", 0, 8) == []
    # Normal: heroes every other beat, others once per bar
    assert starts("Mega Tree", 8, 24) == beats(8, 16)[::2] + beats(16, 24)[::2]
    assert starts("Eaves", 8, 24) == bars(8, 24)
    # Peak: every beat for everyone
    assert starts("Mega Tree", 24, 32) == starts("Eaves", 24, 32) == beats(24, 32)


def test_rank_section_energy():
    secs = [SongSection(label="intro", start_time=0, end_time=1, energy_level=0.99)] + SECTIONS
    assert engine._rank_section_energy(secs) == ["breather", "breather", "normal", "normal", "peak"]


def test_model_priority_overrides_group():
    show = ShowConfig(show_path="", show_name="t", models=[TREE_A, TREE_B], model_groups=GROUPS)
    resolved = engine._resolve_model_priorities({"MEGA-TREES": "hero", "Mega Tree-2": "key"}, show)
    assert resolved == {"Mega Tree": "hero", "Mega Tree-2": "key"}
