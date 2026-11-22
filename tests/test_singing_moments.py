"""Faces sing only in vocal feature moments; other props calm down meanwhile."""

from __future__ import annotations

from pathlib import Path

import pytest

from test_show_rules import BEATS, DOWNBEATS, GROUPS, MODELS, SAFETY, STYLE, _on
from xlights_mcp.audio.analyzer import SongAnalysis, StemAnalysis, StemOnsets
from xlights_mcp.audio.beats import BeatMap
from xlights_mcp.audio.lyrics import LyricTrack, LyricWord
from xlights_mcp.audio.structure import SongSection
from xlights_mcp.sequencer import engine, show_rules
from xlights_mcp.sequencer.show_rules import ShowRules, SingingMoments
from xlights_mcp.sequencer.song_settings import SongSettings
from xlights_mcp.xlights.models import ShowConfig

# Sections: intro 0–4, normal 4–6 and 6–12, build 12–16, peak 16–28, outro 28–32
SECTIONS = [
    SongSection(label="intro", start_time=0, end_time=4, energy_level=0.3),
    SongSection(label="chorus", start_time=4, end_time=6, energy_level=0.6),
    SongSection(label="chorus", start_time=6, end_time=12, energy_level=0.7),
    SongSection(label="chorus", start_time=12, end_time=16, energy_level=0.8),
    SongSection(label="chorus", start_time=16, end_time=28, energy_level=0.9),
    SongSection(label="outro", start_time=28, end_time=32, energy_level=0.2),
]
SETTINGS = {"unmarked_tier": "normal",
            "sections": [{"name": "finale", "start": 16, "end": 28, "tier": "peak"}]}
FRAMES = [i / 10 for i in range(320)]
TIERS = ["breather", "normal", "normal", "normal", "peak", "breather"]


def _drum_level(t):
    return 0.1 if 4 <= t < 12 else 1.0  # drums drop out 4–12 s


def _words(a, b, step=0.5):
    return [LyricWord(word=f"w{t:.1f}", start_time=t, end_time=t + step * 0.8)
            for t in [a + i * step for i in range(int((b - a) / step))]]


# Phrases: 4.2–7.6 s (drums out: sings), 9.0–9.6 s (a fragment), 13–15.5 s (drums loud),
# 18–22 s (in the peak)
WORDS = _words(4.2, 8.0) + _words(9.0, 9.8) + _words(13.0, 15.6) + _words(18.0, 22.2)


def _analysis():
    stems = StemAnalysis(available=True, stems={
        "vocals": StemOnsets(name="vocals", energy=[1.0] * len(FRAMES), energy_times=FRAMES, peak_rms=1.0),
        "drums": StemOnsets(name="drums", energy=[_drum_level(t) for t in FRAMES], energy_times=FRAMES,
                            peak_rms=1.0, onset_times=[b for b in BEATS if not 4 <= b < 12 or b % 1 == 0]),
    })
    return SongAnalysis(
        file_path="s.mp3", file_name="s.mp3", duration_seconds=32.0,
        beats=BeatMap(tempo=120.0, beat_times=BEATS, downbeat_times=DOWNBEATS), sections=SECTIONS,
        stem_analysis=stems,
    )


MOMENTS = {"max_share": 0.25}
SINGING = {"singers": ["Singer", "Screen"], "without_lyrics": {"Singer": "optional", "Screen": "optional"},
           "moments": MOMENTS}
RULES = {"safety": SAFETY, "style": {**STYLE, "priorities": {}, "singing": SINGING,
                                     "floods": ["Flood"]}}


def _run(tmp_path, monkeypatch, rules=None, settings=None):
    track = LyricTrack(track_name="Vocals", source="vocals_stem", words=WORDS, phonemes=[],
                       available=True, confidence=0.9)
    monkeypatch.setattr(engine, "_try_extract_vocal_tracks", lambda _: [track])
    captured = {}
    monkeypatch.setattr(engine, "write_xsq", lambda spec, *_: captured.setdefault("spec", spec))
    show = ShowConfig(show_path=str(tmp_path), show_name="t", models=MODELS, model_groups=GROUPS)
    result = engine._generate_auto(
        _analysis(), show, Path(tmp_path) / "song.mp3", None, "christmas",
        model_priorities={"TREES": "hero"},
        song_settings=SongSettings.model_validate(settings or SETTINGS),
        show_rules=ShowRules.model_validate(rules or RULES),
    )
    return result, captured["spec"]


def test_picks_the_songs_most_exposed_whole_phrases():
    moments = show_rules.pick_singing_moments(WORDS, _analysis(), TIERS,
                                              SingingMoments(**MOMENTS))
    assert [(m.start, round(m.end, 1)) for m in moments] == [(4.2, 7.6)]
    assert moments[0].vocal_drum_ratio == pytest.approx(10.0)


def test_faces_sing_only_in_moments_and_otherwise_peaks_only(tmp_path, monkeypatch):
    result, spec = _run(tmp_path, monkeypatch)
    assert [(m["start"], m["end"]) for m in result["singing_moments"]["moments"]] == [("4.20s", "7.60s")]
    for singer in ("Singer", "Screen"):
        faces = [e for e in _on(spec, singer) if e.effect_name == "Faces"]
        assert [(e.start_time_ms, e.end_time_ms) for e in faces] == [(4200, 7600)]
        others = [e for e in _on(spec, singer) if not 4200 <= e.start_time_ms < 7600]
        assert others and all(16000 <= e.start_time_ms < 28000 for e in others)


def test_other_props_calm_down_while_a_face_sings(tmp_path, monkeypatch):
    _, spec = _run(tmp_path, monkeypatch)
    gentle = [(engine._effect_name_from_key(k), engine._get_settings(k))
              for k in SingingMoments().gentle_motion]
    during = [e for e in spec.effects if e.model_name not in ("Singer", "Screen")
              and e.end_time_ms > 4200 and e.start_time_ms < 7600]
    assert not [e for e in during if e.layer == 2]                       # no accent hits
    assert all((e.effect_name, e.settings) in gentle for e in during if e.layer == 1)  # gentle motion only
    assert not [e for e in during if e.model_name.startswith("Arch") and e.layer == 1]  # no chase
    # ...while the chase and accents still run outside the moment
    assert [e for e in spec.effects if e.model_name.startswith("Arch") and e.layer == 1 and e.start_time_ms >= 8000]


def test_floods_hold_their_color_through_a_moment(tmp_path, monkeypatch):
    _, spec = _run(tmp_path, monkeypatch)
    flood = _on(spec, "Flood")
    [held] = [e for e in flood if e.start_time_ms <= 4200 < e.end_time_ms]
    assert held.end_time_ms >= 7600  # no color change at the 6 s section boundary
    before = next(e for e in flood if e.start_time_ms == 4000)
    assert held.palette.colors[0] == before.palette.colors[0]


def test_cap_keeps_only_the_loudest_vocal_phrases():
    words = _words(4.2, 7.0) + _words(9.0, 11.6)  # 4.2–6.6 s and 9.0–11.4 s: more than a bar apart
    analysis = _analysis()
    analysis.stem_analysis.stems["drums"].energy = [0.1 if 4 <= t < 8 else 0.2 if 8 <= t < 12 else 1.0
                                                    for t in FRAMES]
    tiers = TIERS
    both = show_rules.pick_singing_moments(words, analysis, tiers, SingingMoments(**MOMENTS, exposed_fraction=1))
    assert len(both) == 2
    capped = show_rules.pick_singing_moments(words, analysis, tiers, SingingMoments(exposed_fraction=1, max_share=0.1))
    assert [m.start for m in capped] == [4.2]  # ratio 10 beats ratio 5


def test_per_song_overrides(tmp_path, monkeypatch):
    result, spec = _run(tmp_path, monkeypatch, settings={**SETTINGS, "singing": {"min_vocal_drum_ratio": 20}})
    assert result["singing_moments"]["moments"] == []
    assert not [e for e in spec.effects if e.effect_name == "Faces"]
    with pytest.raises(ValueError, match="Invalid singing settings"):
        _run(tmp_path, monkeypatch, settings={**SETTINGS, "singing": {"exposed_fractoin": 0.5}})


def test_preview_lists_singing_moments(tmp_path, monkeypatch):
    mp3 = tmp_path / "song.mp3"
    mp3.write_bytes(b"")
    (tmp_path / "xlights_mcp_rules.json").write_text(ShowRules.model_validate(RULES).model_dump_json())
    (tmp_path / "song.sequence.json").write_text(SongSettings.model_validate(SETTINGS).model_dump_json())
    show = ShowConfig(show_path=str(tmp_path), show_name="t", models=MODELS, model_groups=GROUPS)
    monkeypatch.setattr(engine, "load_show_config", lambda _: show)
    monkeypatch.setattr(engine, "full_analysis", lambda *_: _analysis())
    track = LyricTrack(track_name="Vocals", source="vocals_stem", words=WORDS, available=True, confidence=0.9)
    monkeypatch.setattr(engine, "_try_extract_vocal_tracks", lambda _: [track])
    preview = engine.preview_sequence_plan(mp3, tmp_path, model_priorities={"TREES": "hero"})
    singing = preview["rules"]["singing"]
    assert singing["moments"][0]["start"] == "4.20s" and singing["share"] == pytest.approx(3.4 / 32, abs=1e-3)
    # The flood is held through the moment, across the 6 s boundary, and the preview says so
    floods = {sec["start"]: sec["flood_color"] for sec in preview["sections"]}
    assert floods["6.0s"] is not None and floods["6.0s"] == floods["4.0s"]
    created = engine.generate_sequence(mp3, tmp_path, model_priorities={"TREES": "hero"})
    assert created["plan_id"] == preview["plan_id"]


def test_moments_end_a_bar_before_peaks_and_spare_the_build():
    analysis = _analysis()
    analysis.stem_analysis.stems["drums"].energy = [0.1 if t < 16 else 1.0 for t in FRAMES]  # vocals rule until the peak
    # A: 4.2–7.6 and B: 8.4–11.3 sing (merged); C: 12.4–14.8 is in the build before the peak
    words = _words(4.2, 8.0) + _words(8.4, 11.6) + _words(12.4, 15.0)
    trimmed = []
    moments = show_rules.pick_singing_moments(words, analysis, TIERS, SingingMoments(**{**MOMENTS, "max_share": 1, "exposed_fraction": 1}),
                                              trimmed=trimmed)
    assert [(m.start, round(m.end, 1)) for m in moments] == [(4.2, 11.3)]
    assert [(t.start, "build" in t.reason) for t in trimmed] == [(12.4, True)]


def test_peak_lead_time_is_a_setting():
    analysis = _analysis()
    analysis.stem_analysis.stems["drums"].energy = [0.1 if t < 16 else 1.0 for t in FRAMES]
    words = _words(12.0, 14.6)  # 12.0–14.4 s; the peak starts at 16 s and a bar is 2 s
    cfg = {**MOMENTS, "max_share": 1, "protect_builds": False}
    assert show_rules.pick_singing_moments(words, analysis, TIERS, SingingMoments(**cfg, peak_lead_bars=0.5))
    trimmed = []
    assert not show_rules.pick_singing_moments(words, analysis, TIERS, SingingMoments(**cfg, peak_lead_bars=1),
                                               trimmed=trimmed)
    assert "before the peak at 16.0s" in trimmed[0].reason


def _flat_drums(level):
    analysis = _analysis()
    analysis.stem_analysis.stems["drums"].energy = [level] * len(FRAMES)
    return analysis


def test_exposure_is_ranked_within_the_song():
    # Vocals only twice the drums everywhere: a fixed 3× threshold would never sing
    analysis = _flat_drums(0.5)
    analysis.stem_analysis.stems["drums"].energy = [0.4 if 4 <= t < 8 else 0.5 for t in FRAMES]
    words = _words(4.2, 7.0) + _words(9.0, 11.6)  # 2.5× and 2×
    moments = show_rules.pick_singing_moments(words, analysis, TIERS, SingingMoments(**MOMENTS))
    assert [m.start for m in moments] == [4.2]  # only the most exposed quarter (at least one)
    # The floor still applies: drums louder than the vocals never sing
    loud = _flat_drums(2.0)
    assert not show_rules.pick_singing_moments(words, loud, TIERS, SingingMoments(**MOMENTS))


def test_exposed_fraction_takes_the_top_share_of_phrases():
    analysis = _flat_drums(0.5)
    levels = {4: 0.1, 6: 0.2, 8: 0.3, 10: 0.4}  # four 2 s phrases, most exposed first
    analysis.stem_analysis.stems["drums"].energy = [levels.get(int(t) // 2 * 2, 0.5) for t in FRAMES]
    words = [w for a in (4.0, 6.0, 8.0, 10.0) for w in _words(a, a + 1.5)]  # a to a + 1.4 s
    cfg = SingingMoments(max_share=1, exposed_fraction=0.5, phrase_gap_seconds=0.15, min_phrase_bars=0.5)
    moments = show_rules.pick_singing_moments(words, analysis, TIERS, cfg)
    assert [(m.start, round(m.end, 1)) for m in moments] == [(4.0, 7.4)]  # 4 and 6 qualify, sung as one


def test_long_phrases_split_at_their_longest_pauses():
    analysis = _flat_drums(0.1)
    # One unbroken 4.2–11.7 s phrase (0.1 s gaps) with a 0.9 s pause at 7.3 s
    words = [w for w in _words(4.2, 7.8, step=0.4)] + _words(8.2, 11.8, step=0.4)
    for w in words:
        w.end_time = w.start_time + 0.3
    phrases = show_rules._split_phrase(words, max_seconds=4.0, min_seconds=2.0)
    assert [(p[0].start_time, round(p[-1].end_time, 1)) for p in phrases] == [(4.2, 7.3), (8.2, 11.7)]
    # Pieces are never shorter than min_seconds: a pause near one end is skipped
    assert len(show_rules._split_phrase(words, max_seconds=4.0, min_seconds=4.0)) == 1
    cfg = SingingMoments(max_share=0.12, exposed_fraction=1, max_phrase_bars=2)  # 4 s, 3.84 s budget
    moments = show_rules.pick_singing_moments(words, analysis, TIERS, cfg)
    [moment] = moments  # unsplit, the 7.5 s phrase was over the cap; now one piece fits
    assert (moment.start, round(moment.end, 1)) in [(4.2, 7.3), (8.2, 11.7)]


def test_final_hit_overrides_the_calm_bed(tmp_path, monkeypatch):
    # The final hit (6 s) falls inside the 4.2–7.6 s moment
    _, spec = _run(tmp_path, monkeypatch, settings={**SETTINGS, "final_hit": 6.0})
    for singer in ("Singer", "Screen"):  # the faces finish the phrase
        assert [(e.start_time_ms, e.end_time_ms) for e in _on(spec, singer) if e.effect_name == "Faces"] == [(4200, 7600)]
    yard = {m.name for m in MODELS} - {"Singer", "Screen", "Flood", "Sign", "Big Star"}
    hits = {e.model_name for e in spec.effects if e.layer == 2 and e.start_time_ms == 6000}
    assert hits == yard  # the whole yard hits with the star
    assert [e.start_time_ms for e in _on(spec, "Big Star")] == [6000]
    gentle = [(engine._effect_name_from_key(k), engine._get_settings(k)) for k in SingingMoments().gentle_motion]
    calm = [e for e in spec.effects if e.model_name in yard and e.end_time_ms > 4200 and e.start_time_ms < 6000]
    assert not [e for e in calm if e.layer == 2]  # still calm before the hit
    assert all((e.effect_name, e.settings) in gentle for e in calm if e.layer == 1)
    # Without the override, the calm bed runs to the end of the moment
    rules = {**RULES, "style": {**RULES["style"], "final_hit": {"models": ["Big Star"], "overrides_calm_bed": False}}}
    _, spec = _run(tmp_path, monkeypatch, rules=rules, settings={**SETTINGS, "final_hit": 6.0})
    assert not [e for e in spec.effects if e.layer == 2 and 4200 <= e.start_time_ms < 7600]


def test_no_singing_where_the_transcriptions_disagree():
    # The mix hears the 5.2 and 5.7 s words differently: a different-word passage
    mix = [w.model_copy(update={"word": "uh"}) if 5.2 <= w.start_time < 6.1 else w for w in WORDS]
    bad, _, passages = show_rules.cross_check(WORDS, mix, 0.35)
    assert [(p.start, p.end, p.mix) for p in passages] == [(5.2, 6.1, "uh uh")]
    # ...and hears the 6.7 s word half a second later: a timing disagreement
    mix = [w.model_copy(update={"start_time": w.start_time + 0.5, "end_time": w.end_time + 0.5})
           if 6.6 <= w.start_time < 6.8 else w for w in WORDS]
    disputed = []
    moments = show_rules.pick_singing_moments(WORDS, _analysis(), TIERS, SingingMoments(**MOMENTS),
                                              reference_words=mix, disputed=disputed)
    assert [(d.start, d.end) for d in disputed] == [(6.7, 7.6)]
    # 4.2–6.6 s still sings; after the disputed word only a fragment (7.2–7.6 s) is left
    assert [(m.start, round(m.end, 1)) for m in moments] == [(4.2, 6.6)]
    off = show_rules.pick_singing_moments(WORDS, _analysis(), TIERS, SingingMoments(**MOMENTS, cross_check=False),
                                          reference_words=mix)
    assert [(m.start, round(m.end, 1)) for m in off] == [(4.2, 7.6)]


def test_words_only_the_mix_heard_break_the_phrase():
    stem = _words(4.2, 8.0)
    mix = stem[:4] + [LyricWord(word="extra", start_time=5.9, end_time=6.0)] + stem[4:]
    bad, breaks, passages = show_rules.cross_check(stem, mix, 0.35)
    assert bad == set() and breaks == {4} and passages[0].mix == "extra"
