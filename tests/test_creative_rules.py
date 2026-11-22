"""Looks from the full xLights effect catalog, and the show profile's rules: no strobes,
no plain color washes, no long fast flashing, faces only when a brief asks."""

from __future__ import annotations

import json

import pytest

from test_design_brief import _gen, _layer
from test_show_rules import RULES
from xlights_mcp.sequencer import engine, show_rules
from xlights_mcp.sequencer.show_rules import ShowRules
from xlights_mcp.xlights import effect_catalog
from xlights_mcp.xlights.xsq_writer import EffectPlacement

FIRE = {"effectName": "Fire", "properties": [
    {"id": "Fire_Height", "type": "int", "controlType": "slider", "min": 1, "max": 100, "default": 50},
    {"id": "Fire_GrowthCycles", "type": "float", "controlType": "slider", "min": 0, "max": 200, "divisor": 10},
    {"id": "Fire_GrowWithMusic", "type": "bool", "controlType": "checkbox"},
    {"id": "Fire_Location", "type": "enum", "controlType": "choice", "options": ["Bottom", "Top"]},
]}
WASH = {"effectName": "Color Wash", "properties": [
    {"id": "ColorWash_Shimmer", "type": "bool", "controlType": "checkbox"}]}
STROBE = {"effectName": "Strobe", "properties": [{"id": "Number_Strobes", "type": "int", "controlType": "slider"}]}


@pytest.fixture
def app(tmp_path, monkeypatch):
    meta = tmp_path / "xLights.app" / effect_catalog.METADATA_SUBDIR
    meta.mkdir(parents=True)
    for j in (FIRE, WASH, STROBE):
        (meta / f"{j['effectName'].replace(' ', '')}.json").write_text(json.dumps(j))
    monkeypatch.setenv("XLIGHTS_APP", str(tmp_path / "xLights.app"))
    return meta


def test_looks_become_xsq_settings(app):
    fx = effect_catalog.effect_settings("Fire", {"Fire_Height": 70, "Fire_GrowthCycles": 1.5,
                                                 "Fire_GrowWithMusic": True, "Fire_Location": "Top",
                                                 "B_CHOICE_BufferStyle": "Per Preview"})
    assert fx == {"E_SLIDER_Fire_Height": "70", "E_TEXTCTRL_Fire_GrowthCycles": "1.5",
                  "E_SLIDER_Fire_GrowthCycles": "15", "E_CHECKBOX_Fire_GrowWithMusic": "1",
                  "E_CHOICE_Fire_Location": "Top", "B_CHOICE_BufferStyle": "Per Preview"}
    for bad, msg in [({"Fire_Height": 300}, "between 1 and 100"), ({"Fire_Location": "Side"}, "one of"),
                     ({"Fire_Size": 3}, "has no setting 'Fire_Size'")]:
        with pytest.raises(ValueError, match=msg):
            effect_catalog.effect_settings("Fire", bad)
    with pytest.raises(ValueError, match="Unknown xLights effect 'Flames'"):
        effect_catalog.effect_settings("Flames", {})


def test_a_brief_can_use_any_effect_through_looks(tmp_path, monkeypatch, app):
    brief = {"mood": "m", "looks": {"embers": {"effect": "Fire", "settings": {"Fire_Height": 30}}},
             "hero_effects": ["embers"]}
    _, spec = _gen(tmp_path, monkeypatch, brief)
    fire = _layer(spec, "Tree L", 1, 4, 28)
    assert fire and {e.effect_name for e in fire} == {"Fire"}
    assert fire[0].settings["E_SLIDER_Fire_Height"] == "30"
    with pytest.raises(ValueError, match="Brief look 'embers': .*between 1 and 100"):
        _gen(tmp_path, monkeypatch, {**brief, "looks": {"embers": {"effect": "Fire", "settings": {"Fire_Height": 0}}}})


AVOID_RULES = {**RULES, "style": {**RULES["style"], "avoid": {"effects": ["Strobe"], "plain_color_wash": True}}}


def _gen_rules(tmp_path, monkeypatch, rules, brief=None):
    monkeypatch.setattr(engine, "_try_extract_vocal_tracks", lambda _: [])
    import test_design_brief as tdb
    from xlights_mcp.sequencer.song_settings import SongSettings
    from xlights_mcp.xlights.models import ShowConfig
    show = ShowConfig(show_path=str(tmp_path), show_name="t", models=tdb.MODELS, model_groups=tdb.GROUPS)
    return engine._generate_auto(
        tdb._analysis(), show, tmp_path / "song.mp3", None, "christmas", model_priorities={"TREES": "hero"},
        song_settings=SongSettings.model_validate({**tdb.SETTINGS, **({"brief": brief} if brief else {})}),
        show_rules=ShowRules.model_validate(rules), write=False)


def test_avoided_looks_are_errors_in_briefs_and_replaced_in_generated_beds(tmp_path, monkeypatch, app):
    plan = _gen_rules(tmp_path, monkeypatch, AVOID_RULES)
    assert not [e for e in plan["spec"].effects if show_rules.is_plain_wash(e.effect_name, e.settings)]
    assert plan["style_fixes"]["plain_washes_replaced"] > 0
    with pytest.raises(ValueError, match="plain color wash"):
        _gen_rules(tmp_path, monkeypatch, AVOID_RULES, {"mood": "m", "effects": {"tree": ["ColorWash_slow"]}})
    with pytest.raises(ValueError, match="uses Strobe"):
        _gen_rules(tmp_path, monkeypatch, AVOID_RULES, {"mood": "m", "looks": {"zap": {"effect": "Strobe"}}})
    shimmer = {"mood": "m", "looks": {"glint": {"effect": "Color Wash", "settings": {"ColorWash_Shimmer": True}}}}
    assert _gen_rules(tmp_path, monkeypatch, AVOID_RULES, shimmer)  # a wash with shimmer isn't plain


def _hits(times, model="Tree L"):
    return [EffectPlacement(model_name=model, layer=2, effect_name="Shockwave", start_time_ms=t,
                            end_time_ms=t + 200, settings={}) for t in times]


def test_long_fast_flashing_is_thinned_to_every_other_hit():
    rules = ShowRules.model_validate({**RULES, "style": {**RULES["style"], "fast_flashing": {"max_seconds": 4}}})
    fast = _hits(range(0, 10000, 400))  # every 0.4 s for 10 s
    kept, dropped = show_rules.limit_fast_flashing(fast, rules)
    times = [e.start_time_ms for e in kept]
    assert times[:10] == list(range(0, 4000, 400))  # full speed for the first 4 s
    assert times[10] == 4400 and all(b - a == 800 for a, b in zip(times[10:], times[11:]))  # then every other hit
    assert dropped == len(fast) - len(kept) > 0
    slow = _hits(range(0, 10000, 600))
    assert show_rules.limit_fast_flashing(slow, rules) == (slow, 0)


def test_faces_sing_only_when_the_brief_asks(tmp_path, monkeypatch):
    import test_show_rules as tsr
    rules = {**RULES, "style": {**RULES["style"], "singing": {"singers": ["Singer"], "opt_in": True,
                                                              "not_singing": "normal"}}}
    from xlights_mcp.audio.lyrics import LyricTrack, LyricWord
    words = [LyricWord(word=f"w{i}", start_time=4.2 + i * 0.5, end_time=4.6 + i * 0.5) for i in range(7)]
    track = LyricTrack(track_name="Vocals", source="vocals_stem", words=words, phonemes=[], available=True,
                       confidence=0.9)
    monkeypatch.setattr(engine, "_try_extract_vocal_tracks", lambda _: [track])
    for faces, sings in [(False, False), (True, True)]:
        from xlights_mcp.sequencer.song_settings import SongSettings
        from xlights_mcp.xlights.models import ShowConfig
        show = ShowConfig(show_path=str(tmp_path), show_name="t", models=tsr.MODELS, model_groups=tsr.GROUPS)
        plan = engine._generate_auto(
            tsr.SongAnalysis(file_path="s.mp3", file_name="s.mp3", duration_seconds=32.0,
                             beats=tsr.BeatMap(tempo=120.0, beat_times=tsr.BEATS, downbeat_times=tsr.DOWNBEATS),
                             sections=tsr.SECTIONS),
            show, tmp_path / "song.mp3", None, "christmas", model_priorities={"TREES": "hero"},
            song_settings=SongSettings.model_validate({**tsr.SETTINGS, "brief": {"mood": "m", "faces": faces}}),
            show_rules=ShowRules.model_validate(rules), write=False)
        assert plan["faces"] is sings
        singer = [e for e in plan["spec"].effects if e.model_name == "Singer"]
        assert bool([e for e in singer if e.effect_name == "Faces"]) is sings
        if not sings:  # an ordinary secondary prop: lit outside the peak too
            assert [e for e in singer if e.start_time_ms < 16000]


def test_plain_washes_can_simply_go_dark(tmp_path, monkeypatch, app):
    rules = {**AVOID_RULES, "style": {**AVOID_RULES["style"], "avoid": {"plain_color_wash": True, "replace_plain_with": "off"}}}
    plan = _gen_rules(tmp_path, monkeypatch, rules)
    assert plan["style_fixes"]["plain_washes_replaced"] > 0
    assert not [e for e in plan["spec"].effects if e.effect_name == "Color Wash"]
