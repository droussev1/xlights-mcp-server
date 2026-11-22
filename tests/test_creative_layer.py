"""Lyric cues, moments that dim the yard or stagger in, negative space, snap clicks, the
calibrated hit budget, and the show design ledger's limits."""

from __future__ import annotations

import pytest

import test_design_brief as tdb
from test_show_rules import RULES, _on
from xlights_mcp.audio.lyrics import LyricTrack, LyricWord
from xlights_mcp.sequencer import engine, show_rules
from xlights_mcp.sequencer.show_rules import ShowRules
from xlights_mcp.sequencer.song_settings import SongSettings
from xlights_mcp.xlights.models import ShowConfig

WORDS = [LyricWord(word=w, start_time=t, end_time=t + 0.4) for w, t in
         [("have", 4.2), ("a", 4.7), ("cup", 5.2), ("of", 5.7), ("cheer", 6.2), ("kiss", 9.0), ("her", 9.5)]]


def _tracks(mix_words=None):
    stem = LyricTrack(track_name="Vocals", source="vocals_stem", words=WORDS, phonemes=[], available=True, confidence=0.9)
    mix = LyricTrack(track_name="Full Mix Vocals", source="full_mix", words=mix_words or WORDS, phonemes=[],
                     available=True, confidence=0.9)
    return [stem, mix]


def _run(tmp_path, monkeypatch, brief, rules=None, tracks=None, song="song", models=None, write=False):
    monkeypatch.setattr(engine, "_try_extract_vocal_tracks", lambda _: tracks if tracks is not None else _tracks())
    show = ShowConfig(show_path=str(tmp_path), show_name="t", models=models or tdb.MODELS, model_groups=tdb.GROUPS)
    return engine._generate_auto(
        tdb._analysis(), show, tmp_path / f"{song}.mp3", None, "christmas", model_priorities={"TREES": "hero"},
        song_settings=SongSettings.model_validate({**tdb.SETTINGS, "brief": brief}),
        show_rules=ShowRules.model_validate(rules or RULES), write=write)


def test_lyric_cues_start_on_their_words(tmp_path, monkeypatch):
    cue = {"word": "cup of cheer", "time": 5.2, "models": ["TREES"], "effect": "Meteors_rain", "layer": 1}
    plan = _run(tmp_path, monkeypatch, {"mood": "m", "lyric_cues": [cue]})
    [fx] = [e for e in _on(plan["spec"], "Tree L", 1) if e.effect_name == "Meteors"]
    assert (fx.start_time_ms, fx.end_time_ms) == (5200, 6600)  # "cup" to the end of "cheer"
    assert plan["design"]["lyric_cues"][0]["at"] == "5.20s"
    with pytest.raises(ValueError, match="has 'cup' at 5.20s"):
        _run(tmp_path, monkeypatch, {"mood": "m", "lyric_cues": [{**cue, "word": "snow"}]})
    misheard = [w.model_copy(update={"word": "cop"}) if w.word == "cup" else w for w in WORDS]
    with pytest.raises(ValueError, match="failed the cross-check"):
        _run(tmp_path, monkeypatch, {"mood": "m", "lyric_cues": [cue]}, tracks=_tracks(misheard))


def test_moments_can_dim_the_rest_of_the_yard(tmp_path, monkeypatch):
    moment = {"name": "solo", "start": 4.0, "end": 8.0, "models": ["TREES"], "layer": 1, "effect": "Spirals_fast",
              "others": "off"}
    plan = _run(tmp_path, monkeypatch, {"mood": "m", "signature_moment": moment})
    spec = plan["spec"]
    assert not [e for e in spec.effects if e.model_name in ("Cane L", "Flake", "Arch L")
                and e.start_time_ms < 8000 and e.end_time_ms > 4000]   # dark
    assert [e for e in _on(spec, "Flood") if e.start_time_ms < 8000 and e.end_time_ms > 4000]  # floods keep their rule
    embers = {**moment, "others": "Twinkle_ambient"}
    spec = _run(tmp_path, monkeypatch, {"mood": "m", "signature_moment": embers})["spec"]
    assert {e.effect_name for e in _on(spec, "Cane L", 0) if e.start_time_ms == 4000} == {"Twinkle"}


def test_staggered_moments_fill_from_the_center_out(tmp_path, monkeypatch):
    models = [m.model_copy(update={"x": x}) for m, x in zip(tdb.MODELS, [-400, 400, -300, 300, -100, 100,
                                                                          0, 0, 0, 0, 0, 0])]
    moment = {"name": "everybody", "start": 4.0, "end": 12.0, "models": ["Tree L", "Tree R", "Cane L", "Cane R"],
              "layer": 1, "effect": "Twinkle_dense", "stagger": "center_out"}
    spec = _run(tmp_path, monkeypatch, {"mood": "m", "signature_moment": moment}, models=models)["spec"]
    start = {n: min(e.start_time_ms for e in _on(spec, n, 1) if e.effect_name == "Twinkle" and e.start_time_ms >= 4000)
             for n in ("Cane L", "Cane R", "Tree L", "Tree R")}
    assert start["Cane L"] == start["Cane R"] == 4000 < start["Tree L"] == start["Tree R"]  # pairs together


def test_negative_space_and_clicks(tmp_path, monkeypatch):
    brief = {"mood": "m", "negative_space": {}, "clicks": [{"stem": "drums", "models": ["CANES"]}]}
    spec = _run(tmp_path, monkeypatch, brief)["spec"]
    # Outside the peak (16–28 s) the generic props stay dark...
    assert not [e for e in spec.effects if e.model_name in ("Tree L", "Flake") and e.start_time_ms < 16000
                and e.layer < 2 and e.start_time_ms >= 4000]
    assert [e for e in _on(spec, "Tree L") if 16000 <= e.start_time_ms < 28000]  # ...and fill in it
    clicks = [e for e in _on(spec, "Cane L", 2) if 4000 <= e.start_time_ms < 16000]
    assert clicks and all(e.end_time_ms - e.start_time_ms == 120 for e in clicks)  # one crisp click per onset


def test_generated_hits_stay_within_the_calibrated_budget(tmp_path, monkeypatch):
    rules = {**RULES, "style": {**RULES["style"], "calibration": {"max_hits_per_minute": 60}}}
    plan = _run(tmp_path, monkeypatch, {"mood": "m"}, rules=rules)
    hits = [e for e in plan["spec"].effects if e.layer == 2]
    assert plan["style_fixes"]["hits_over_budget_dropped"] > 0
    assert len(hits) <= 60 * 32 / 60 + 2   # budget (+ the final-hit accents placed after it)
    peak = [e for e in hits if 16000 <= e.start_time_ms < 28000]
    assert len(peak) >= len(hits) // 2   # peaks keep their hits first


def _ledger(tmp_path, song, **kw):
    show_rules.record_design(tmp_path, song, show_rules.Design(kw.get("heroes", []), kw.get("arch"), kw.get("moment"),
                                                               [], signature=kw.get("signature"), faces=kw.get("faces", False)))


def test_ledger_limits_signature_reuse_and_faces(tmp_path):
    rules = ShowRules.model_validate({**RULES, "style": {**RULES["style"], "variety": {"faces_max_share": 0.34}}})
    _ledger(tmp_path, "02 Other", moment="Snow globe",
            signature={"name": "Snow globe", "effect": "Twinkle_dense", "models": ["TREES"]}, faces=True)
    brief = lambda **b: SongSettings.model_validate({"brief": {"mood": "m", **b}}).brief  # noqa: E731
    same = {"name": "Globe again", "start": 1, "end": 2, "models": ["TREES"], "effect": "Twinkle_dense"}
    with pytest.raises(ValueError, match="repeats '02 Other's signature moment"):
        show_rules.pick_design(rules, tmp_path, "09 New", brief(signature_moment=same), [])
    assert show_rules.pick_design(rules, tmp_path, "09 New", brief(signature_moment={**same, "effect": "Fire"}), [])
    with pytest.raises(ValueError, match="Faces already sing in 1"):
        show_rules.pick_design(rules, tmp_path, "09 New", brief(faces=True), [], show_size=3)
    assert show_rules.pick_design(rules, tmp_path, "09 New", brief(faces=True), [], show_size=6).faces


def test_neighbors_compare_hero_effect_families(tmp_path):
    rules = ShowRules.model_validate({**RULES, "style": {**RULES["style"], "variety": {}}})
    show_rules.record_design(tmp_path, "06 Holly", show_rules.Design(["chrome_bars"], "bounce", None, [],
                                                                     hero_families=["Bars"]))
    brief = SongSettings.model_validate({"brief": {"mood": "m", "hero_effects": ["harmony_bars"]}}).brief
    fam = {"harmony_bars": "Bars"}.get
    with pytest.raises(ValueError, match=r"'harmony_bars' \(Bars\) is also a hero effect of '06 Holly'"):
        show_rules.pick_design(rules, tmp_path, "07 Winter", brief, [], family=fam)


def test_travelling_clicks_light_one_prop_per_snap_balanced_each_bar(tmp_path, monkeypatch):
    models = [m.model_copy(update={"x": x}) for m, x in zip(tdb.MODELS, [-400, 400, -300, 300, -100, 100,
                                                                          0, 0, 0, 0, 0, 0])]
    rules = {**RULES, "style": {**RULES["style"], "calibration": {"max_hits_per_minute": 100000}}}
    brief = {"mood": "m", "clicks": [{"stem": "drums", "models": ["CANES", "TREES"], "mode": "travel"}]}
    plan = _run(tmp_path, monkeypatch, brief, rules=rules, models=models)
    clicks = sorted((e for e in plan["spec"].effects if e.layer == 2 and e.effect_name == "On"
                     and e.end_time_ms - e.start_time_ms == 120 and 4000 <= e.start_time_ms < 16000),
                    key=lambda e: e.start_time_ms)
    starts = [e.start_time_ms for e in clicks]
    assert len(starts) == len(set(starts))  # one prop per snap
    first = [e.model_name for e in clicks[:4]]
    assert first == ["Cane L", "Cane R", "Tree L", "Tree R"]  # from the middle out, left then right
    by_bar: dict[int, list[str]] = {}
    for e in clicks:
        by_bar.setdefault(e.start_time_ms // 2000, []).append(e.model_name)
    for names in by_bar.values():  # each bar balanced left/right to within one click
        lefts = sum(n.endswith(" L") for n in names)
        assert abs(lefts - (len(names) - lefts)) <= 1
    pairs = [(clicks[i].model_name, clicks[i + 1].model_name) for i in range(0, len(clicks) - 1, 2)]
    assert all(a[:-1] == b[:-1] and a.endswith("L") and b.endswith("R") for a, b in pairs)  # never skips


def test_clicks_count_toward_the_hit_budget(tmp_path, monkeypatch):
    rules = {**RULES, "style": {**RULES["style"], "calibration": {"max_hits_per_minute": 30}}}
    brief = {"mood": "m", "clicks": [{"stem": "drums", "models": ["CANES"], "mode": "travel"}]}
    plan = _run(tmp_path, monkeypatch, brief, rules=rules)
    hits = [e for e in plan["spec"].effects if e.layer == 2]
    assert len(hits) <= 30 * 32 / 60 + 4  # the budget, plus the final-hit yard accents placed after it


def test_clicks_are_kept_ahead_of_other_hits_but_still_counted(tmp_path, monkeypatch):
    rules = {**RULES, "style": {**RULES["style"], "calibration": {"max_hits_per_minute": 60}}}
    brief = {"mood": "m", "clicks": [{"stem": "drums", "models": ["CANES"], "mode": "travel"}]}
    plan = _run(tmp_path, monkeypatch, brief, rules=rules)
    hits = [e for e in plan["spec"].effects if e.layer == 2]
    clicks = sorted((e for e in hits if e.effect_name == "On" and e.end_time_ms - e.start_time_ms == 120),
                    key=lambda e: e.start_time_ms)
    assert len(hits) <= 60 * 32 / 60 + 4 and len(clicks) >= 20  # clicks fill the budget first
    gaps = [b.start_time_ms - a.start_time_ms for a, b in zip(clicks, clicks[1:])]
    assert max(gaps) < 4000  # thinned evenly, not cut off


def test_exclusive_moments_own_their_props_on_every_layer(tmp_path, monkeypatch):
    base = {"name": "solo", "start": 4.0, "end": 8.0, "models": ["TREES"], "layer": 1, "effect": "Spirals_fast"}
    for exclusive, others_show in [(False, True), (True, False)]:
        plan = _run(tmp_path, monkeypatch, {"mood": "m", "signature_moment": {**base, "exclusive": exclusive}})
        under = [e for e in _on(plan["spec"], "Tree L") if e.layer != 1 and e.start_time_ms < 8000 and e.end_time_ms > 4000]
        assert bool(under) is others_show
