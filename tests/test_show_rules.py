"""Show rules: roles, symmetry, singing fallbacks, strobes and palettes."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from lxml import etree

from xlights_mcp.audio.analyzer import SongAnalysis
from xlights_mcp.audio.beats import BeatMap
from xlights_mcp.audio.structure import SongSection
from xlights_mcp.sequencer import engine, show_rules
from xlights_mcp.sequencer.show_rules import ShowRules
from xlights_mcp.sequencer.song_settings import SongSettings
from xlights_mcp.xlights.models import LightModel, ModelGroup, ShowConfig

BEATS = [round(i * 0.5, 3) for i in range(64)]  # 120 BPM, 32 s
DOWNBEATS = BEATS[::4]
CLASSIC = ["#FF0000", "#0B6E2E", "#FFD7A0"]
CANDY = ["#FF0000", "#FFFFFF"]
POOL = ["#7B2FBE", "#00A6A6", "#E0218A", "#2546F0", "#FF7A1A"]

MODELS = [
    LightModel(name="Arch L", display_as="Arches", pixel_count=200),
    LightModel(name="Arch R", display_as="Arches", pixel_count=200),
    LightModel(name="Tree L", display_as="Tree", pixel_count=1000),
    LightModel(name="Tree R", display_as="Tree", pixel_count=1000),
    LightModel(name="Cane L", display_as="Candy Canes", pixel_count=99),
    LightModel(name="Cane R", display_as="Candy Canes", pixel_count=99),
    LightModel(name="Flood", display_as="Single Line", pixel_count=16),
    LightModel(name="Sign", display_as="Custom", pixel_count=100),
    LightModel(name="Big Star", display_as="Custom", pixel_count=296),
    LightModel(name="Flake", display_as="Custom", pixel_count=48),
    LightModel(name="Singer", display_as="Matrix", pixel_count=168, face_definitions=["Face"]),
    LightModel(name="Screen", display_as="Matrix", pixel_count=4096, face_definitions=["Face"]),
]
GROUPS = [
    ModelGroup(name="TREES", members=["Tree L", "Tree R"]),
    ModelGroup(name="CANES", members=["Cane L", "Cane R"]),
]
RULES = {
    "exclude": ["Sign"],
    "symmetry": {"default": "mirror", "by_tier": {"normal": "call_response", "peak": "sync"},
                 "pairs": [["Arch L", "Arch R"], ["Tree L", "Tree R"], ["Cane L", "Cane R"]]},
    "arch_chase": {"arches": [{"model": "Arch L", "count": 4}, {"model": "Arch R", "count": 4}]},
    "floods": ["Flood"],
    "twinkle": {"models": ["Flake"], "tiers": ["breather"]},
    "final_hit": {"models": ["Big Star"]},
    "priorities": {"Screen": "optional"},
    "singing": {"singers": ["Singer"], "without_lyrics": {"Singer": "optional"}},
    "strobes": {"every_beats": 2},
    "palettes": {
        "core": {"classic": CLASSIC, "gold": ["#FFD7A0", "#FFB300"], "icy": ["#E6F4FF", "#A8D8FF"],
                 "candy_cane": CANDY},
        "default": "classic", "by_model": {"CANES": "candy_cane", "Flake": "icy"},
        "by_section_label": {"intro": "icy", "outro": "icy"}, "by_tier": {"breather": "gold"},
        "arch_chase": "candy_cane", "accent_pool": POOL, "signatures_per_song": 2,
    },
}
# intro 0–4 and outro 28–32 are breathers; 4–16 normal; 16–28 peak (the finale)
SETTINGS = {"unmarked_tier": "normal",
            "sections": [{"name": "finale", "start": 16, "end": 28, "tier": "peak"}]}
SECTIONS = [
    SongSection(label="intro", start_time=0, end_time=4, energy_level=0.3),
    SongSection(label="chorus", start_time=4, end_time=16, energy_level=0.7),
    SongSection(label="chorus", start_time=16, end_time=28, energy_level=0.9),
    SongSection(label="outro", start_time=28, end_time=32, energy_level=0.2),
]


def _run(tmp_path, monkeypatch, rules=None, settings=None, lyrics=False, song="song"):
    analysis = SongAnalysis(
        file_path=f"{song}.mp3", file_name=f"{song}.mp3", duration_seconds=32.0,
        beats=BeatMap(tempo=120.0, beat_times=BEATS, downbeat_times=DOWNBEATS), sections=SECTIONS,
    )
    show = ShowConfig(show_path=str(tmp_path), show_name="t", models=MODELS, model_groups=GROUPS)
    tracks = []
    if lyrics:
        from xlights_mcp.audio.lyrics import LyricTrack
        tracks = [LyricTrack(track_name="Vocals", source="vocals_stem", words=[], phonemes=[])]
    monkeypatch.setattr(engine, "_try_extract_vocal_tracks", lambda _: tracks)
    captured = {}
    real_write = engine.write_xsq

    def write(spec, cfg, path):
        captured["spec"] = spec
        captured["path"] = path
        return real_write(spec, cfg, path)

    monkeypatch.setattr(engine, "write_xsq", write)
    result = engine._generate_auto(
        analysis, show, tmp_path / f"{song}.mp3", None, "christmas",
        vocal_assignments={"all": "Vocals"}, model_priorities={"TREES": "hero"},
        song_settings=SongSettings.model_validate(settings if settings is not None else SETTINGS),
        show_rules=ShowRules.model_validate(rules or RULES),
    )
    return result, captured


def _on(spec, model, layer=None):
    return sorted((e for e in spec.effects if e.model_name == model and (layer is None or e.layer == layer)),
                  key=lambda e: (e.layer, e.start_time_ms))


def test_excluded_model_gets_nothing(tmp_path, monkeypatch):
    _, cap = _run(tmp_path, monkeypatch)
    assert _on(cap["spec"], "Sign") == []


def test_floods_change_only_at_section_boundaries(tmp_path, monkeypatch):
    _, cap = _run(tmp_path, monkeypatch)
    flood = _on(cap["spec"], "Flood")
    assert [(e.layer, e.start_time_ms, e.end_time_ms) for e in flood] == [
        (0, s.start_time_ms, s.end_time_ms) for s in SECTIONS
    ]
    assert len({e.palette.colors[0] for e in flood}) > 1  # colors still change between sections


def test_big_star_only_on_final_hit(tmp_path, monkeypatch):
    _, cap = _run(tmp_path, monkeypatch)
    [hit] = _on(cap["spec"], "Big Star")
    assert hit.start_time_ms == 28000  # beat nearest the end of the last peak
    assert hit.effect_name == "On" and float(hit.settings["T_TEXTCTRL_Fadeout"]) > 0


def test_final_hit_override(tmp_path, monkeypatch):
    _, cap = _run(tmp_path, monkeypatch, settings={**SETTINGS, "final_hit": 27.0})
    assert [e.start_time_ms for e in _on(cap["spec"], "Big Star")] == [27000]


def test_symmetry_modes_by_tier(tmp_path, monkeypatch):
    _, cap = _run(tmp_path, monkeypatch)
    spec = cap["spec"]
    flip = ("B_CHOICE_BufferTransform", "Flip Horizontal")

    def spans(model, layer, lo, hi):
        return [e for e in _on(spec, model, layer) if lo * 1000 <= e.start_time_ms < hi * 1000]

    # Breather (intro): right bed mirrors the left
    assert all(flip in e.settings.items() for e in spans("Cane R", 0, 0, 4))
    assert [e.effect_name for e in spans("Cane R", 0, 0, 4)] == [e.effect_name for e in spans("Cane L", 0, 0, 4)]
    # Normal: motion alternates sides bar by bar (bars at 4, 6, 8, ...; bar index 2 at 4 s)
    left = {e.start_time_ms for e in spans("Tree L", 1, 4, 16)}
    right = {e.start_time_ms for e in spans("Tree R", 1, 4, 16)}
    assert left and right and not left & right
    assert all((t // 2000) % 2 == 0 for t in left) and all((t // 2000) % 2 == 1 for t in right)
    assert all(flip in e.settings.items() for e in spans("Tree R", 1, 4, 16))
    # Peak: both sides identical, no flip
    peak_l = [(e.effect_name, e.start_time_ms, e.settings) for e in spans("Tree L", 1, 16, 28)]
    peak_r = [(e.effect_name, e.start_time_ms, e.settings) for e in spans("Tree R", 1, 16, 28)]
    assert peak_l and peak_l == peak_r


def test_arch_chase_one_arch_per_beat_left_to_right(tmp_path, monkeypatch):
    _, cap = _run(tmp_path, monkeypatch)
    hits = sorted((e for e in cap["spec"].effects if e.model_name in ("Arch L", "Arch R") and e.layer == 1),
                  key=lambda e: e.start_time_ms)
    assert [h.start_time_ms for h in hits] == [int(b * 1000) for b in BEATS if 4 <= b < 28]
    order = [(h.model_name, h.settings["B_CUSTOM_SubBuffer"].split("x")[0]) for h in hits[:9]]
    assert order == [("Arch L", "0.00"), ("Arch L", "25.00"), ("Arch L", "50.00"), ("Arch L", "75.00"),
                     ("Arch R", "0.00"), ("Arch R", "25.00"), ("Arch R", "50.00"), ("Arch R", "75.00"),
                     ("Arch L", "0.00")]
    assert all(h.settings["B_CHOICE_BufferStyle"] == "Single Line" for h in hits)
    assert not [e for e in cap["spec"].effects if e.model_name.startswith("Arch") and e.layer == 2]
    assert {h.palette.colors[0] for h in hits} == set(CANDY)


def test_twinkle_props_only_twinkle_in_breathers(tmp_path, monkeypatch):
    _, cap = _run(tmp_path, monkeypatch)
    intro = [e for e in _on(cap["spec"], "Flake") if e.start_time_ms < 4000]
    assert [(e.effect_name, e.layer) for e in intro] == [("Twinkle", 0)]
    assert intro[0].palette.colors[0] in ("#E6F4FF", "#A8D8FF")


def test_singers_fall_back_to_peaks_without_lyrics(tmp_path, monkeypatch):
    _, cap = _run(tmp_path, monkeypatch, lyrics=False)
    for model in ("Singer", "Screen"):
        fx = _on(cap["spec"], model)
        assert fx and all(16000 <= e.start_time_ms < 28000 for e in fx)


def test_only_listed_singers_sing_with_lyrics(tmp_path, monkeypatch):
    _, cap = _run(tmp_path, monkeypatch, lyrics=True)
    assert [e.effect_name for e in _on(cap["spec"], "Singer", 1)] == ["Faces"]
    screen = _on(cap["spec"], "Screen")
    assert not [e for e in screen if e.effect_name == "Faces"]
    assert screen and all(16000 <= e.start_time_ms < 28000 for e in screen)  # rules keep it peaks only


@pytest.mark.parametrize("strobe, message", [
    ({"start": 6, "end": 10}, "only in the finale"),
    ({"section": "finale", "every_beats": 1}, "every 2 beats"),
])
def test_strobes_only_in_finale_at_half_time(tmp_path, monkeypatch, strobe, message):
    effect = {"models": ["CANES"], "layer": 2, "effect": "Strobe_flash", "every_beats": 2, **strobe}
    with pytest.raises(ValueError, match=message):
        _run(tmp_path, monkeypatch, settings={**SETTINGS, "effects": [effect]})


def test_finale_strobe_at_half_time_is_allowed(tmp_path, monkeypatch):
    effect = {"section": "finale", "models": ["CANES"], "layer": 2, "effect": "Strobe_flash",
              "every_beats": 2, "length_beats": 1}
    _, cap = _run(tmp_path, monkeypatch, settings={**SETTINGS, "effects": [effect]})
    strobes = [e for e in _on(cap["spec"], "Cane L") if e.effect_name == "Strobe"]
    assert [e.start_time_ms for e in strobes] == list(range(16000, 28000, 1000))


def test_palettes_are_weighted_and_heroes_never_carry_accents(tmp_path, monkeypatch):
    result, cap = _run(tmp_path, monkeypatch)
    sig = result["signature_colors"]
    assert len(sig) == 2 and set(sig) <= set(POOL)
    effects = cap["spec"].effects
    share = show_rules.accent_share(effects, sig)
    assert 0 < share <= 0.25 and result["accent_share"] == pytest.approx(share, abs=1e-3)
    for e in effects:
        if e.model_name.startswith("Tree") and set(sig) & set(e.palette.colors):
            assert e.layer == 2 and e.palette.colors[0] not in sig  # paired, core color first
    # Accents always come paired with a core color
    core = {c for colors in RULES["palettes"]["core"].values() for c in colors}
    assert all(core & set(e.palette.colors) for e in effects)


def test_signatures_differ_between_songs_and_can_be_overridden(tmp_path, monkeypatch):
    first, _ = _run(tmp_path, monkeypatch, song="one")
    second, _ = _run(tmp_path, monkeypatch, song="two")
    assert not set(first["signature_colors"]) & set(second["signature_colors"])
    again, _ = _run(tmp_path, monkeypatch, song="one")
    assert again["signature_colors"] == first["signature_colors"]  # remembered
    custom, _ = _run(tmp_path, monkeypatch, song="three", settings={**SETTINGS, "signature_colors": ["#123456"]})
    assert custom["signature_colors"] == ["#123456"]
    registry = json.loads((tmp_path / show_rules.SIGNATURES_FILENAME).read_text())["songs"]
    assert registry["three"] == ["#123456"]


def test_written_effects_reference_their_palettes(tmp_path, monkeypatch):
    _, cap = _run(tmp_path, monkeypatch)
    root = etree.parse(str(cap["path"])).getroot()
    effects = root.findall(".//ElementEffects/Element[@type='model']/EffectLayer/Effect")
    assert effects and all(e.get("palette") is not None for e in effects)


def test_invalid_rules_are_reported(tmp_path):
    (tmp_path / show_rules.RULES_FILENAME).write_text(json.dumps(
        {"palettes": {"core": {"a": ["#FFFFFF"]}, "default": "missing"}}))
    with pytest.raises(ValueError, match="unknown core palettes"):
        show_rules.load_show_rules(tmp_path)


def test_rules_naming_unknown_models_are_rejected():
    show = ShowConfig(show_path="", show_name="t", models=MODELS, model_groups=GROUPS)
    with pytest.raises(ValueError, match="Unknown model or group: 'Nope'"):
        show_rules.validate_models(ShowRules.model_validate({"floods": ["Nope"]}), show)
