"""The always-lit sign, hands-off models, and archiving superseded drafts."""

from __future__ import annotations

import pytest

import test_design_brief as tdb
from test_creative_rules import app  # noqa: F401  (fixture: a stand-in xLights effect catalog)
from test_show_rules import RULES, SAFETY
from xlights_mcp.sequencer import engine, show_rules
from xlights_mcp.sequencer.show_rules import ShowRules
from xlights_mcp.sequencer.song_settings import SongSettings
from xlights_mcp.xlights.models import ShowConfig

SIGN = {"model": "Sign", "colors": ["#FF0000", "#FFB300"],
        "looks": {"embers": {"effect": "Fire", "settings": {"Fire_Height": 40}},
                  "wash": {"effect": "Color Wash", "settings": {"ColorWash_Shimmer": False}}}}
SIGN_RULES = {"safety": {**SAFETY, "exclude": [], "sign": SIGN},
              "style": {**RULES["style"], "hands_off": ["Flake"], "variety": {}}}


def _plan(tmp_path, monkeypatch, brief=None, song="song", settings=None, write=False):
    monkeypatch.setattr(engine, "_try_extract_vocal_tracks", lambda _: [])
    show = ShowConfig(show_path=str(tmp_path), show_name="t", models=tdb.MODELS, model_groups=tdb.GROUPS)
    s = {**tdb.SETTINGS, **(settings or {}), **({"brief": brief} if brief else {})}
    return engine._generate_auto(tdb._analysis(), show, tmp_path / f"{song}.mp3", None, "christmas",
                                 model_priorities={"TREES": "hero"}, song_settings=SongSettings.model_validate(s),
                                 show_rules=ShowRules.model_validate(SIGN_RULES), write=write)


def test_the_sign_gets_exactly_one_effect_for_the_whole_song(tmp_path, monkeypatch, app):  # noqa: F811
    moment = {"name": "dark", "start": 4, "end": 8, "models": ["TREES"], "layer": 1, "effect": "Spirals_fast",
              "others": "off"}
    plan = _plan(tmp_path, monkeypatch, {"mood": "m", "signature_moment": moment})
    [sign] = [e for e in plan["spec"].effects if e.model_name == "Sign"]  # not blacked out by the moment
    assert (sign.start_time_ms, sign.end_time_ms, sign.layer) == (0, 32000, 0)
    assert sign.palette.colors[:2] == ["#FF0000", "#FFB300"]  # its own colors, not the song's
    assert plan["sign"]["look"] in SIGN["looks"]


def test_neighbors_get_different_sign_looks_and_the_ledger_tracks_it(tmp_path, monkeypatch, app):  # noqa: F811
    first = _plan(tmp_path, monkeypatch, song="05 A", write=True)
    second = _plan(tmp_path, monkeypatch, song="06 B", write=True)
    assert first["design"]["sign_effect"] if "sign_effect" in (first.get("design") or {}) else True
    ledger = show_rules._read_designs(tmp_path)
    assert ledger["05 A"]["sign_effect"] != ledger["06 B"]["sign_effect"]
    assert second["success"]


def test_sign_looks_that_flash_or_follow_the_music_are_rejected():
    for look, msg in [({"effect": "Strobe"}, "go dark or flash"),
                      ({"effect": "Fire", "settings": {"Fire_GrowWithMusic": True}}, "reacts to the music")]:
        with pytest.raises(ValueError, match=msg):
            ShowRules.model_validate({"safety": {"sign": {**SIGN, "looks": {"x": look}}}})
    with pytest.raises(ValueError, match="both the sign"):
        ShowRules.model_validate({"safety": {"exclude": ["Sign"], "sign": SIGN}})


def test_hands_off_models_get_no_generated_effects(tmp_path, monkeypatch, app):  # noqa: F811
    custom = {"effects": [{"start": 4, "end": 20, "models": ["Flake"], "layer": 0, "effect": "Butterfly_gentle"}]}
    moment = {"name": "dark", "start": 6, "end": 10, "models": ["TREES"], "layer": 1, "effect": "Spirals_fast",
              "others": "off"}
    plan = _plan(tmp_path, monkeypatch, {"mood": "m", "signature_moment": moment}, settings=custom)
    flake = [e for e in plan["spec"].effects if e.model_name == "Flake"]
    assert [(e.effect_name, e.start_time_ms, e.end_time_ms) for e in flake] == [("Butterfly", 4000, 20000)]
    with pytest.raises(ValueError, match="keep hands-off"):
        _plan(tmp_path, monkeypatch, {"mood": "m", "lyric_cues": [], "clicks": [{"stem": "drums", "models": ["Flake"]}]})
    allowed = _plan(tmp_path, monkeypatch, {"mood": "m", "allow_models": ["Flake"],
                                            "clicks": [{"stem": "drums", "models": ["Flake"]}]})
    assert [e for e in allowed["spec"].effects if e.model_name == "Flake" and e.layer == 2]


def test_superseded_drafts_move_to_the_archive(tmp_path):
    for name in ("Song.xsq", "Song (generated 1).xsq", "Song (generated 1).fseq", "Song (generated 2).xsq",
                 "Other (generated 1).xsq"):
        (tmp_path / name).write_text("x")
    moved = engine._archive_drafts(tmp_path, "Song", keep=tmp_path / "Song (generated 2).xsq")
    assert sorted(moved) == ["Song (generated 1).fseq", "Song (generated 1).xsq"]
    assert sorted(p.name for p in tmp_path.iterdir() if p.is_file()) == [
        "Other (generated 1).xsq", "Song (generated 2).xsq", "Song.xsq"]
    assert (tmp_path / "archive" / "Song (generated 1).xsq").exists()


def test_faces_with_missing_images_dont_sing_and_warn(tmp_path, monkeypatch):
    from xlights_mcp.audio.lyrics import LyricTrack, LyricWord
    import test_show_rules as tsr
    (tmp_path / "Faces").mkdir()
    (tmp_path / "Faces" / "here.png").write_text("x")
    models = [m.model_copy(update={"face_images": {"Face": [str(tmp_path / "Faces/here.png"), "/gone/mouth-AI.png"]}})
              if m.name == "Singer" else m for m in tsr.MODELS]
    words = [LyricWord(word=f"w{i}", start_time=4.2 + i * 0.5, end_time=4.6 + i * 0.5) for i in range(7)]
    track = LyricTrack(track_name="Vocals", source="vocals_stem", words=words, phonemes=[], available=True, confidence=0.9)
    monkeypatch.setattr(engine, "_try_extract_vocal_tracks", lambda _: [track])
    rules = {**tsr.RULES, "style": {**tsr.RULES["style"], "singing": {"singers": ["Singer"], "opt_in": True}}}
    show = ShowConfig(show_path=str(tmp_path), show_name="t", models=models, model_groups=tsr.GROUPS)
    analysis = tsr.SongAnalysis(file_path="s.mp3", file_name="s.mp3", duration_seconds=32.0, sections=tsr.SECTIONS,
                                beats=tsr.BeatMap(tempo=120.0, beat_times=tsr.BEATS, downbeat_times=tsr.DOWNBEATS))
    def plan(faces):
        return engine._generate_auto(analysis, show, tmp_path / "song.mp3", None, "christmas",
                                     model_priorities={"TREES": "hero"},
                                     song_settings=SongSettings.model_validate({**tsr.SETTINGS, "brief": {"mood": "m", "faces": faces}}),
                                     show_rules=ShowRules.model_validate(rules), write=False)
    with_faces = plan(True)
    assert not [e for e in with_faces["spec"].effects if e.effect_name == "Faces"]
    assert any("/gone/mouth-AI.png" in w and "'Singer' won't sing" in w for w in with_faces["warnings"])
    assert plan(False)["warnings"] == []  # no faces wanted, nothing to check
