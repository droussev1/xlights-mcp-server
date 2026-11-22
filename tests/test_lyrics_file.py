"""A lyrics text file next to the music supplies the words; Whisper supplies the timing."""

from __future__ import annotations

import pytest

from xlights_mcp.audio.beats import BeatMap
from xlights_mcp.audio.lyrics import (
    LyricTrack, LyricWord, align_lyrics, lyrics_path_for, read_lyrics_file,
)
from xlights_mcp.sequencer import engine


def _heard(*items):
    """(word, start) pairs, each word 0.3 s long."""
    return [LyricWord(word=w, start_time=t, end_time=t + 0.3) for w, t in items]


def _track(words):
    return LyricTrack(words=words, phonemes=[], track_name="Vocals", source="vocals_stem",
                      available=True, confidence=0.4)


def test_reads_words_and_skips_tags_and_comments(tmp_path):
    path = tmp_path / "Song.lyrics.txt"
    path.write_text("# from the liner notes\n[Verse 1]\nSleigh bells ring,\n\nare you listening?\n")
    assert read_lyrics_file(path) == ["Sleigh", "bells", "ring,", "are", "you", "listening?"]
    assert lyrics_path_for(tmp_path / "Song.mp3") == path


def test_known_words_take_whispers_timing():
    heard = _heard(("Slate", 1.0), ("bells", 1.4), ("ring", 1.8), ("are", 3.0), ("you", 3.4),
                   ("listening", 3.8))
    aligned = align_lyrics(_track(heard), ["Sleigh", "bells", "ring,", "are", "you", "listening?"])
    assert [(w.word, w.start_time) for w in aligned.words] == [
        ("Sleigh", 1.0), ("bells", 1.4), ("ring,", 1.8), ("are", 3.0), ("you", 3.4), ("listening?", 3.8)]
    assert aligned.confidence == 1.0 and aligned.lyrics_source == "lyrics_file"
    assert aligned.phonemes and aligned.source == "vocals_stem"


def test_misheard_runs_share_their_span_and_extra_words_are_dropped():
    heard = _heard(("in", 1.0), ("the", 1.4), ("lane", 1.8), ("uh", 2.2), ("snow", 3.0), ("is", 3.4),
                   ("glistening", 3.8))
    aligned = align_lyrics(_track(heard), ["in", "the", "lane,", "snow", "is", "glistening"])
    assert [w.word for w in aligned.words] == ["in", "the", "lane,", "snow", "is", "glistening"]
    # A run of different length is spread over the misheard words' span
    heard = _heard(("a", 1.0), ("beauty", 1.4), ("full", 1.8), ("site", 2.2), ("we're", 3.0))
    aligned = align_lyrics(_track(heard), ["a", "beautiful", "sight,", "we're"])
    beautiful, sight = aligned.words[1], aligned.words[2]
    assert beautiful.start_time == 1.4 and sight.end_time == pytest.approx(2.5)
    assert beautiful.end_time == pytest.approx(sight.start_time)


def test_unheard_words_fill_tight_gaps_only():
    heard = _heard(("happy", 1.0), ("tonight", 2.0), ("walking", 20.0))
    aligned = align_lyrics(_track(heard), ["happy", "to", "night,", "walking"])
    # "to night," replaces "tonight" (1 heard word, 2 lyric words): spread over its span
    assert [w.word for w in aligned.words] == ["happy", "to", "night,", "walking"]
    heard = _heard(("we're", 1.0), ("happy", 1.4), ("walking", 30.0))
    aligned = align_lyrics(_track(heard), ["we're", "happy", "tonight", "walking"])
    # 29 s of instrumental is no place for one unheard word: it's left out
    assert [w.word for w in aligned.words] == ["we're", "happy", "walking"]
    assert aligned.confidence == pytest.approx(0.75)
    heard = _heard(("we're", 1.0), ("happy", 1.4), ("walking", 2.4))
    aligned = align_lyrics(_track(heard), ["we're", "happy", "tonight", "walking"])
    assert [w.word for w in aligned.words] == ["we're", "happy", "tonight", "walking"]
    assert 1.7 <= aligned.words[2].start_time < aligned.words[2].end_time <= 2.4


def test_engine_prefers_the_lyrics_file_words_over_the_transcription(tmp_path, monkeypatch):
    mp3 = tmp_path / "Song.mp3"
    mp3.write_bytes(b"")
    heard = _track(_heard(("Slate", 1.0), ("bells", 1.4), ("ring", 1.8)))
    calls = []
    monkeypatch.setattr(engine, "_transcribe_vocal_tracks", lambda p: calls.append(p) or [heard])
    assert [w.word for w in engine._try_extract_vocal_tracks(mp3)[0].words] == ["Slate", "bells", "ring"]
    (tmp_path / "Song.lyrics.txt").write_text("Sleigh bells ring\n")
    [track] = engine._try_extract_vocal_tracks(mp3)
    assert [w.word for w in track.words] == ["Sleigh", "bells", "ring"]
    assert track.lyrics_source == "lyrics_file"


def test_madmom_downbeat_confidence_is_reported_as_not_applicable():
    beats = BeatMap(tempo=120, rhythm_method="madmom", downbeat_confidence=0.0, meter_confidence=0.0)
    assert beats.downbeat_confidence is None
    dumped = beats.model_dump()
    assert dumped["downbeat_confidence"] == "n/a" and dumped["meter_confidence"] == "n/a"
    assert BeatMap.model_validate_json(beats.model_dump_json()).downbeat_confidence is None
    librosa = BeatMap(tempo=120, rhythm_method="librosa", downbeat_confidence=0.7)
    assert librosa.model_dump()["downbeat_confidence"] == 0.7
