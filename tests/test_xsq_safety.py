"""Safety rules on imported (.xsq) sequences, preview/create parity and guided mode."""

from __future__ import annotations

from pathlib import Path

from lxml import etree

from xlights_mcp.audio.analyzer import SongAnalysis
from xlights_mcp.audio.beats import BeatMap
from xlights_mcp.audio.structure import SongSection
from xlights_mcp.sequencer import engine
from xlights_mcp.sequencer.show_rules import ShowRules
from xlights_mcp.sequencer.xsq_safety import apply_safety, unsafe_targets
from xlights_mcp.xlights.models import LightModel, ModelGroup, ShowConfig

BEATS = [round(i * 0.5, 3) for i in range(64)]
SHOW = ShowConfig(
    show_path="", show_name="t",
    models=[LightModel(name=n, display_as="Custom", pixel_count=50) for n in ("Sign", "Tree", "Cane")],
    model_groups=[ModelGroup(name="ALL", members=["Sign", "Tree", "Cane"]),
                  ModelGroup(name="PROPS", members=["Tree", "Cane"]),
                  ModelGroup(name="EVERYTHING", members=["ALL"])],
)
RULES = ShowRules.model_validate({"safety": {
    "exclude": ["Sign"],
    "strobes": {"every_beats": 2},
    "white": {"colors": ["#FFFFFF"], "max_seconds": 1.0, "max_share": 0.05},
}})
WHITE = "C_BUTTON_Palette1=#FFFFFF,C_BUTTON_Palette2=#FF0000,C_CHECKBOX_Palette1=1,C_SLIDER_Brightness=100"
MIXED = "C_BUTTON_Palette1=#FFFFFF,C_BUTTON_Palette2=#00FF00,C_CHECKBOX_Palette1=1,C_CHECKBOX_Palette2=1"


def _tree():
    xml = f"""<xsequence>
      <head><sequenceDuration>32.000</sequenceDuration></head>
      <ColorPalettes><ColorPalette>{WHITE}</ColorPalette><ColorPalette>{MIXED}</ColorPalette></ColorPalettes>
      <EffectDB><Effect>E_SLIDER_Number_Strobes=3</Effect></EffectDB>
      <DisplayElements>
        <Element type="model" name="Sign"/><Element type="model" name="ALL"/><Element type="model" name="Tree"/>
      </DisplayElements>
      <ElementEffects>
        <Element type="model" name="Sign"><EffectLayer><Effect name="On" startTime="0" endTime="500" palette="1"/></EffectLayer></Element>
        <Element type="model" name="ALL"><EffectLayer><Effect name="On" startTime="0" endTime="500" palette="1"/></EffectLayer></Element>
        <Element type="model" name="Tree"><EffectLayer>
          <Effect name="On" startTime="0" endTime="5000" palette="0"/>
          <Effect name="Twinkle" startTime="5000" endTime="6000" palette="1"/>
          <Effect name="Strobe" ref="0" startTime="2000" endTime="6000" palette="0"/>
          <Effect name="Strobe" ref="0" startTime="24000" endTime="28000" palette="0"/>
        </EffectLayer></Element>
      </ElementEffects>
    </xsequence>"""
    return etree.fromstring(xml)


def _analysis():
    return SongAnalysis(
        file_path="s.mp3", file_name="s.mp3", duration_seconds=32.0,
        beats=BeatMap(tempo=120.0, beat_times=BEATS, downbeat_times=BEATS[::4]),
        sections=[SongSection(label="chorus", start_time=0, end_time=24, energy_level=0.5),
                  SongSection(label="chorus", start_time=24, end_time=32, energy_level=0.9)],
    )


def _effects(root, model):
    el = root.find(f"ElementEffects/Element[@name='{model}']")
    return [] if el is None else el.findall("EffectLayer/Effect")


def _colors(root, eff):
    text = root.findall("ColorPalettes/ColorPalette")[int(eff.get("palette"))].text
    return dict(kv.split("=", 1) for kv in text.split(","))


def test_unsafe_targets_include_groups_containing_excluded_models():
    assert unsafe_targets(RULES, SHOW) == {"Sign", "ALL", "EVERYTHING"}


def test_excluded_models_and_their_groups_lose_effects():
    root = _tree()
    warnings = apply_safety(root, RULES, SHOW, _analysis(), ["normal", "peak"])
    assert not _effects(root, "Sign") and not _effects(root, "ALL")
    assert root.find("DisplayElements/Element[@name='Sign']") is None
    assert any("'ALL'" in w for w in warnings)


def test_strobes_only_in_finale_at_half_time():
    root = _tree()
    apply_safety(root, RULES, SHOW, _analysis(), ["normal", "peak"])
    strobes = [e for e in _effects(root, "Tree") if e.get("name") == "Strobe"]
    assert [(int(e.get("startTime")), int(e.get("endTime"))) for e in strobes] == [
        (24000, 24500), (25000, 25500), (26000, 26500), (27000, 27500)]


def test_strobes_removed_without_audio():
    root = _tree()
    warnings = apply_safety(root, RULES, SHOW)
    assert not [e for e in _effects(root, "Tree") if e.get("name") == "Strobe"]
    assert any("no audio" in w for w in warnings)


def test_whites_softened_limited_and_kept_full_only_for_strobes():
    root = _tree()
    apply_safety(root, RULES, SHOW, _analysis(), ["normal", "peak"])
    on, twinkle = [e for e in _effects(root, "Tree") if e.get("name") in ("On", "Twinkle")]
    soft = RULES.safety.white.soften("#FFFFFF")
    # 5 s white-only On: recolored to a fallback color, other palette settings kept
    on_colors = _colors(root, on)
    assert on_colors["C_BUTTON_Palette1"] == "#FF0000" and on_colors["C_SLIDER_Brightness"] == "100"
    # Mixed palette: white softened, green untouched
    tw = _colors(root, twinkle)
    assert (tw["C_BUTTON_Palette1"], tw["C_BUTTON_Palette2"]) == (soft, "#00FF00")
    # Strobes keep full white
    strobe = next(e for e in _effects(root, "Tree") if e.get("name") == "Strobe")
    assert _colors(root, strobe)["C_BUTTON_Palette1"] == "#FFFFFF"


def test_preview_matches_create(tmp_path, monkeypatch):
    mp3 = tmp_path / "song.mp3"
    mp3.write_bytes(b"")
    (tmp_path / "xlights_mcp_rules.json").write_text(RULES.model_dump_json())
    show = SHOW.model_copy(update={"show_path": str(tmp_path)})
    monkeypatch.setattr(engine, "load_show_config", lambda _: show)
    monkeypatch.setattr(engine, "full_analysis", lambda *_: _analysis())
    monkeypatch.setattr(engine, "_try_extract_vocal_tracks", lambda _: [])
    preview = engine.preview_sequence_plan(mp3, tmp_path)
    created = engine.generate_sequence(mp3, tmp_path)
    assert preview["plan_id"] == created["plan_id"]
    assert preview["rules"]["excluded"] == ["Sign"]
    assert [s["tier"] for s in preview["sections"]] == ["normal", "peak"]
    assert "Sign" not in preview["props"]


def test_guided_mode_states_safety_rules_and_omits_excluded(tmp_path):
    out = engine._generate_guided_preview(_analysis(), SHOW, RULES)
    assert "Sign" not in [m for models in out["models_by_category"].values() for m in models]
    assert out["safety_rules"]["exclude"] == ["Sign"]
    assert out["safety_rules"]["strobes"] == {"every_beats": 2}


async def test_remap_sequence_applies_safety_rules(tmp_path, monkeypatch):
    from xlights_mcp import server
    from xlights_mcp.config import ServerConfig
    from xlights_mcp.xlights.palettes import ColorPalette
    from xlights_mcp.xlights.xsq_writer import EffectPlacement, SequenceSpec, write_xsq

    show_dir = tmp_path / "show"
    show_dir.mkdir()
    (show_dir / "xlights_rgbeffects.xml").write_text(
        '<xrgb><models>'
        '<model name="Sign" DisplayAs="Custom" PixelCount="50"/>'
        '<model name="Tree" DisplayAs="Tree" PixelCount="500"/>'
        '</models><modelGroups><modelGroup name="ALL" models="Sign,Tree"/></modelGroups></xrgb>'
    )
    (show_dir / "xlights_mcp_rules.json").write_text(RULES.model_dump_json())

    white = ColorPalette(colors=["#FFFFFF"], active_colors=[1])
    imported = SequenceSpec(
        song_title="Imported", duration_ms=32000, timing_ms=25, palettes=[white],
        effects=[
            EffectPlacement(model_name="Sign", effect_name="On", start_time_ms=0, end_time_ms=4000, palette=white),
            EffectPlacement(model_name="Tree", effect_name="On", start_time_ms=0, end_time_ms=4000, palette=white),
            EffectPlacement(model_name="Tree", effect_name="Strobe", start_time_ms=5000, end_time_ms=6000,
                            palette=white, layer=1),
        ],
    )
    src_show = ShowConfig(show_path=str(tmp_path), show_name="src", models=[
        LightModel(name="Sign", display_as="Custom", pixel_count=50),
        LightModel(name="Tree", display_as="Tree", pixel_count=500)])
    import_path = tmp_path / "imported.xsq"
    write_xsq(imported, src_show, import_path)

    monkeypatch.setattr(server, "get_config", lambda: ServerConfig(show_folders={"t": str(show_dir)}, active_show="t"))
    result = await server.remap_sequence(str(import_path), show_name="t")
    assert result["success"], result
    out = etree.parse(result["output_path"]).getroot()
    assert not _effects(out, "Sign")
    tree = _effects(out, "Tree")
    assert [e.get("name") for e in tree] == ["On"]  # strobe dropped: no audio to find the finale
    assert _colors(out, tree[0])["C_BUTTON_Palette1"] != "#FFFFFF"  # 4 s of white-only recolored
    assert any("Safety rules" in w for w in result["mapping_report"]["warnings"])


def test_find_matching_audio_by_title(tmp_path):
    from xlights_mcp.sequencer.xsq_safety import find_matching_audio, normalize_title
    music = tmp_path / "Music"
    (music / "Album").mkdir(parents=True)
    for name in ("06 Holly Jolly Christmas.mp3", "02 - Silver Bells (Remastered).mp3", "Album/01-02 Frosty the Snowman.MP3",
                 "07 Holly Jolly Christmas.m4a"):
        (music / name).write_bytes(b"")
    assert normalize_title(r"C:\Music\01 - Light Of Christmas [feat. TobyMac].mp3") == "light of christmas"
    assert find_matching_audio([None, r"D:\Audio\A Holly Jolly Christmas.mp3"], [music]).name == "06 Holly Jolly Christmas.mp3"
    assert find_matching_audio(["Frosty The Snowman (Remix)"], [music]).name == "01-02 Frosty the Snowman.MP3"
    assert find_matching_audio(["Christmas"], [music]) is None
    assert find_matching_audio(["Jingle Bell Rock"], [tmp_path / "missing"]) is None


def _remap_with_media(tmp_path, monkeypatch, music_files):
    """Remap a sequence whose media path doesn't exist, with given files in the show's Music folder."""
    import asyncio
    from xlights_mcp import server
    from xlights_mcp.audio import analyzer
    from xlights_mcp.config import ServerConfig
    from xlights_mcp.xlights.palettes import ColorPalette
    from xlights_mcp.xlights.xsq_writer import EffectPlacement, SequenceSpec, write_xsq

    show_dir = tmp_path / "show"
    (show_dir / "Music").mkdir(parents=True)
    for name in music_files:
        (show_dir / "Music" / name).write_bytes(b"")
    (show_dir / "xlights_rgbeffects.xml").write_text(
        '<xrgb><models><model name="Sign" DisplayAs="Custom" PixelCount="50"/>'
        '<model name="Tree" DisplayAs="Tree" PixelCount="500"/></models></xrgb>')
    (show_dir / "xlights_mcp_rules.json").write_text(RULES.model_dump_json())
    red = ColorPalette(colors=["#FF0000"], active_colors=[1])
    imported = SequenceSpec(
        song_title="Holly Jolly Christmas", duration_ms=32000, timing_ms=25, palettes=[red],
        media_file=r"C:\Users\someone\Music\Holly Jolly Christmas.mp3",
        effects=[EffectPlacement(model_name="Tree", effect_name="Strobe", start_time_ms=24000,
                                 end_time_ms=28000, palette=red)],
    )
    src = ShowConfig(show_path=str(tmp_path), show_name="src",
                     models=[LightModel(name="Tree", display_as="Tree", pixel_count=500)])
    write_xsq(imported, src, tmp_path / "imported.xsq")
    analyzed = []
    monkeypatch.setattr(analyzer, "full_analysis", lambda path, *_: analyzed.append(path) or _analysis())
    monkeypatch.setattr(server, "get_config", lambda: ServerConfig(show_folders={"t": str(show_dir)}, active_show="t"))
    result = asyncio.run(server.remap_sequence(str(tmp_path / "imported.xsq"), show_name="t"))
    assert result["success"], result
    out = etree.parse(result["output_path"]).getroot()
    strobes = [e for e in _effects(out, "Tree") if e.get("name") == "Strobe"]
    return result, analyzed, strobes


def test_remap_finds_audio_in_music_folder_by_title(tmp_path, monkeypatch):
    result, analyzed, strobes = _remap_with_media(tmp_path, monkeypatch, ["06 Holly Jolly Christmas.mp3"])
    assert [p.name for p in analyzed] == ["06 Holly Jolly Christmas.mp3"]
    # The finale (24–32 s) was found, so the strobe is re-timed to half time instead of removed
    assert [int(e.get("startTime")) for e in strobes] == [24000, 25000, 26000, 27000]
    assert any("matched by title" in w for w in result["mapping_report"]["warnings"])


def test_remap_removes_strobes_only_when_no_audio_matches(tmp_path, monkeypatch):
    result, analyzed, strobes = _remap_with_media(tmp_path, monkeypatch, ["02 - Silver Bells.mp3"])
    assert analyzed == [] and strobes == []
    warnings = result["mapping_report"]["warnings"]
    assert any("searched" in w for w in warnings) and any("no audio" in w for w in warnings)


def test_remapped_sequences_give_the_sign_one_steady_effect(tmp_path, monkeypatch):
    import json
    meta = tmp_path / "xLights.app/Contents/Resources/effectmetadata"
    meta.mkdir(parents=True)
    (meta / "Fire.json").write_text(json.dumps({"effectName": "Fire", "properties": [
        {"id": "Fire_Height", "type": "int", "controlType": "slider", "min": 1, "max": 100}]}))
    monkeypatch.setenv("XLIGHTS_APP", str(tmp_path / "xLights.app"))
    rules = ShowRules.model_validate({"safety": {"sign": {
        "model": "Sign", "colors": ["#FF0000"], "looks": {"embers": {"effect": "Fire", "settings": {"Fire_Height": 40}}}}}})
    show = SHOW.model_copy(update={"show_path": str(tmp_path)})
    root = _tree()
    warnings = apply_safety(root, rules, show)
    [sign] = root.findall("ElementEffects/Element[@name='Sign']/EffectLayer/Effect")
    assert (sign.get("name"), sign.get("startTime"), sign.get("endTime")) == ("Fire", "0", "32000")
    assert "E_SLIDER_Fire_Height=40" in root.findall("EffectDB/Effect")[int(sign.get("ref"))].text
    assert "#FF0000" in root.findall("ColorPalettes/ColorPalette")[int(sign.get("palette"))].text
    assert root.find("ElementEffects/Element[@name='ALL']") is None  # groups containing the sign lose effects
    assert any("replaced 1 imported effect" in w for w in warnings)
