> **⚠️ Beta:** This project is currently in beta. Features may change, and you may encounter bugs. Feedback and contributions are welcome!

# xLights MCP Server

An MCP (Model Context Protocol) server that analyzes music and generates [xLights](https://xlights.org/) light show sequences. Works with any MCP-compatible AI tool — [GitHub Copilot CLI](https://docs.github.com/en/copilot/github-copilot-in-the-cli), [Claude Desktop](https://claude.ai/download), [Cursor](https://cursor.sh), [VS Code + Copilot Chat](https://code.visualstudio.com/), [Windsurf](https://codeium.com/windsurf), [Cline](https://github.com/cline/cline), and more.

Give it an `.mp3`, and it will analyze the beats, song structure, and energy — then generate a valid `.xsq` sequence file with effects placed across all of your light models, synced to the music

---

## What It Does

### 🎵 Audio Analysis
- **Beat & tempo detection** — percussion-aware beats, phase-aligned bars, conservative meter estimates, and local tempo changes
- **Song structure** — beat-synchronized timbre/harmony changes and acoustic repetition, with heuristic section labels
- **Frequency spectrum** — bass, mid, and high energy curves over time
- **Energy profiling** — loudness dynamics, multiscale RMS, spectral flux/brightness, peak detection, and dynamic range
- **Source separation** — isolate vocals, drums, bass, and other stems via [Demucs](https://github.com/facebookresearch/demucs) (optional)

### 💡 Sequence Generation
- **Generates valid `.xsq` files** that open directly in xLights — no xLights GUI required during generation
- **Reads your actual show config** — knows your models, controllers, channel counts, and model types
- **Intelligent effect selection** — picks effects based on model type (arches get chases, trees get spirals, etc.) and musical features (beats → shockwaves, choruses → high energy, verses → gentle)
- **Theme-aware palettes** — Christmas (red/green/gold) and Halloween (orange/purple) color schemes
- **Three generation modes:**
  - **Automatic** — AI picks everything, you review in xLights
  - **Guided** — AI shows song structure, you choose effects per section
  - **Template** — define reusable effect recipes, AI places them on beat
- **Never overwrites** — existing sequences are safe; generated files get a `(generated N)` suffix

### 📦 Sequence Import & Remapping
- **Import community sequences** — take any `.xsq` or `.zip` package from the xLights community and remap it to your show layout
- **Intelligent model matching** — automatically maps imported models to yours by name, type, and pixel count
- **Zip package support** — extracts audio, video, shader, and image assets; rewrites hardcoded file paths
- **Singing model awareness** — singing face models only match to other singing models
- **Full mapping report** — see exactly what matched, how, and what was skipped
- **Manual overrides** — correct any mapping before the remapped sequence is generated

### 📡 FPP Integration
- **Check status** of your Falcon Pi Player
- **Upload sequences** (.fseq + audio) to FPP
- **Manage playlists** — list, start, stop
- Works with FPP's REST API

---

## Prerequisites

- **Python 3.11+** (Python 3.12 recommended for optional audio features)
- **[uv](https://docs.astral.sh/uv/)** (recommended) or pip
- **ffmpeg** — for audio format handling (`brew install ffmpeg` on macOS, `apt install ffmpeg` on Linux)
- **xLights** — installed with at least one show folder configured

---

## Installation

### Step 1: Clone the repository

```bash
git clone https://github.com/JohnBreault/xlights-mcp-server.git
cd xlights-mcp-server
```

### Step 2: Create a virtual environment and install

```bash
uv venv --python 3.12
uv pip install -e .
```

If you already created `.venv` with an unsupported Python version, remove it
and run `uv venv --python 3.12` again. PyTorch wheels for `separation` and
`lyrics` may not be available for the newest Python releases, including 3.15.

**Optional extras for enhanced features:**

```bash
# Stem separation — isolates vocals/drums/bass for smarter sequencing (~2GB model download)
uv pip install -e ".[separation]"

# Lyrics/singing faces — transcribes vocals for lip-sync animation (Whisper medium, ~1.5GB model download)
uv pip install -e ".[lyrics]"

# Better beat detection
uv pip install -e ".[beats]"

# Everything
uv pip install -e ".[all]"
```

The `beats` extra uses a pinned [modernized madmom fork](https://github.com/imcmurray/madmom-modern)
with bundled models. Installing it requires GitHub access and a C compiler
(Xcode Command Line Tools on macOS, or Visual C++ Build Tools on Windows).
The models have a separate non-commercial license; see the fork's README.
Without this extra, core beat detection uses librosa.
Because this extra requires NumPy 2, if you add it to an environment that
already has `separation` or `lyrics`, upgrade the combined extras so PyTorch
is upgraded too (on platforms that support PyTorch 2.3 or later):

```bash
uv pip install --upgrade -e ".[separation,beats,lyrics]"
```

Omit any extras you do not use.
On Intel macOS, `separation` and `lyrics` use PyTorch 2.2.x with NumPy 1.x
because newer PyTorch wheels are unavailable. The `beats` extra skips madmom on
Intel macOS because it requires NumPy 2, so beat detection uses the librosa
fallback there, including when installing `all`.

### Step 3: Show folder configuration

On first run, the server **auto-detects** your xLights show folders by scanning common locations:

| OS | Locations checked |
|----|-------------------|
| macOS | `~/Library/Mobile Documents/com~apple~CloudDocs/xLights/`, `~/Documents/xLights/` |
| Windows | `~/Documents/xLights/` |
| Linux | `~/Documents/xLights/`, `~/xLights/`, `/opt/xLights/` |

It looks for directories containing `xlights_rgbeffects.xml` (the file xLights creates in every show folder).

**If auto-detection doesn't find your folders**, create or edit `~/.xlights-mcp/config.json`:

```json
{
  "show_folders": {
    "christmas": "/path/to/your/xLights/Christmas",
    "halloween": "/path/to/your/xLights/Halloween"
  },
  "active_show": "christmas",
  "fpp": {
    "host": "fpp.local",
    "port": 80
  }
}
```

### Step 4: Connect to your AI tool

The server uses **stdio transport** — your AI tool launches it as a subprocess.

<details>
<summary><strong>GitHub Copilot CLI</strong></summary>

Add to `~/.copilot/mcp-config.json`:

```json
{
  "mcpServers": {
    "xlights": {
      "command": "uv",
      "args": ["run", "--directory", "/path/to/xlights-mcp-server", "xlights-mcp-server"]
    }
  }
}
```

Restart Copilot CLI after saving.
</details>

<details>
<summary><strong>Claude Desktop</strong></summary>

Add to your Claude Desktop config (`~/Library/Application Support/Claude/claude_desktop_config.json` on macOS):

```json
{
  "mcpServers": {
    "xlights": {
      "command": "uv",
      "args": ["run", "--directory", "/path/to/xlights-mcp-server", "xlights-mcp-server"]
    }
  }
}
```

Restart Claude Desktop after saving.
</details>

<details>
<summary><strong>VS Code + Copilot Chat</strong></summary>

Add to your VS Code `settings.json`:

```json
{
  "mcp": {
    "servers": {
      "xlights": {
        "command": "uv",
        "args": ["run", "--directory", "/path/to/xlights-mcp-server", "xlights-mcp-server"]
      }
    }
  }
}
```
</details>

<details>
<summary><strong>Cursor</strong></summary>

Add to Cursor MCP settings (Settings → MCP Servers → Add):

- **Name:** xlights
- **Command:** `uv`
- **Args:** `run --directory /path/to/xlights-mcp-server xlights-mcp-server`
</details>

<details>
<summary><strong>Other MCP clients</strong></summary>

Any tool that supports the [Model Context Protocol](https://modelcontextprotocol.io/) can use this server. Point it at:

```
command: uv
args: run --directory /path/to/xlights-mcp-server xlights-mcp-server
transport: stdio
```
</details>

Replace `/path/to/xlights-mcp-server` with the actual path where you cloned the repo.

---

## Usage

Once connected, interact with the server through natural language in your AI tool. Here are some example workflows:

### Explore your show

```
> List my xLights shows
> Switch to the Halloween show
> List all my light models
> Show me the controllers
> What sequences do I have?
> Inspect the "Deck The Halls" sequence
```

### Analyze a song

```
> Analyze the song ~/Music/Jingle Bell Rock.mp3
> Show me the song structure for ~/Music/Monster Mash.mp3
> Get the beat map for this song
> What's the energy profile look like?
```

The existing audio tools keep their names and arguments. `analyze_song` includes
the same beat and spectrum models returned by `get_beat_map` and
`get_energy_profile`, with these additional fields:

| Result | Additional fields | Units / interpretation |
|--------|-------------------|------------------------|
| Beats | `tempo_curve` | Overlapping 10-second windows, approximately 5 seconds apart; `time` is the window start in seconds, `tempo` is BPM, and `beat_count` records supporting detections. Short clips use one window; insufficient evidence yields zero BPM. |
| Beats | `tempo_changes` | Adjacent positive window estimates differing by more than 10%; `time`, `from_tempo`, and `to_tempo`. These are diagnostic estimates, not exact change boundaries. |
| Beats | `meter_confidence`, `downbeat_confidence`, `rhythm_method`, `warnings` | Heuristic evidence scores (not calibrated probabilities; `"n/a"` when `rhythm_method` is `madmom`, whose model picks the grid without a comparable score), selected backend, and degraded-analysis diagnostics. |
| Spectrum | `spectral_flux` | Positive frame-to-frame magnitude changes, normalized to 0-1. |
| Spectrum | `spectral_centroid`, `spectral_bandwidth`, `spectral_rolloff` | Hz; rolloff marks 85% of cumulative spectral magnitude. |
| Spectrum | `spectral_flatness` | Dimensionless 0-1 noise/tonality descriptor; silence is not evidence of audible noise. |
| Spectrum | `energy_short`, `energy_medium`, `energy_long` | Normalized RMS smoothed over approximately 0.25, 2, and 8 seconds, capped for short clips. |

All new spectral/energy curves use the existing `rms_times` timeline in seconds.
Existing band names, max-normalized band/RMS curves, raw `average_loudness`, and
`dynamic_range` (maximum RMS divided by mean RMS) retain their meanings.
The detailed seven-band Python option remains available.

Meter inference considers simple two-, three-, and four-beat accent patterns.
Ambiguous patterns, including indistinguishable two-/four-beat accents, retain
four-beat bars with zero inference confidence; this does not prove a 4/4 time
signature. Compound meters are not inferred. When installed and working, madmom
supplies a coherent beat/downbeat grid instead. HPSS failures fall back to
full-mix rhythm analysis with diagnostics. Onsets stay on attacks, without
backtracking into preceding quiet audio.

Section boundaries use beat-synchronized MFCC/chroma, brightness, and RMS features.
Local novelty and acoustic repetition inform boundaries and labels; confident
bar positions (or the selected madmom grid) can refine boundary alignment.
Low-evidence audio falls back to conservative energy boundaries or one whole-file
section. Silence returns an `unknown` section with zero confidence. Labels are
estimates, not guaranteed verse/chorus annotations; too few sections retain
energy-based labeling. All timestamps remain on the original, untrimmed audio
timeline. Sequence generation automatically consumes the improved beats,
downbeats, and sections; it does not yet use the local tempo curve or additional
spectral descriptors directly.

Full analysis (including generation/previews) decodes the full mix once on a cache
miss and persists validated core results under `AudioConfig.cache_dir` (default
`~/.xlights-mcp/audio_cache/`). Cache keys include file contents, sample rate,
analysis version, and relevant dependency versions. Writes are atomic; corrupt
or inaccessible caches are logged and recomputed. Transient enhancement failures
are not persisted as authoritative results. Optional stems are reevaluated outside
the core cache, so newly available stems are not hidden by a previous analysis.
Standalone beat/structure/energy tools analyze only the requested data and do not
use the full-analysis cache. No source audio or show configuration is modified.

These features use existing librosa/NumPy dependencies; no additional package,
service, or model is required. Invalid or undecodable audio produces an explicit
error dictionary from the audio MCP tools.

### Generate a sequence

```
> Create a sequence for ~/Music/Jingle Bell Rock.mp3
> Create a sequence for ~/Music/Jingle Bell Rock.mp3 using red and green colors
> Preview what a sequence would look like for this song before creating it
```

When generating, you'll be asked to choose a mode:
- **auto** — fully automatic, AI picks effects and colors
- **guided** — see the song structure first, then choose effects per section
- **template** — apply saved effect recipes to detected sections

#### Model priorities

Mark the props that matter most with `set_model_priority` (saved per show; a group's
priority applies to its members):

- **hero** — the stars: motion in every section but the intro/outro, faster effect changes,
  accents on every other beat
- **key** — keeps moving through quiet sections
- **optional** — only lights up in the song's peaks

Section energy is ranked within each song: the loudest ~20% of sections are *peaks*
(accents on every beat for everyone), the quietest ~25% plus intro/outro are *breathers*
(no accents), and other props accent once per bar.

#### Per-song settings

To hand-tune one song, put a JSON file next to the music file with the same name plus
`.sequence.json` — e.g. `Holly Jolly Christmas.mp3` → `Holly Jolly Christmas.sequence.json`.
`create_sequence` and `preview_plan` pick it up automatically. Every field is optional:

```json
{
  "bar_anchor": 32.5,
  "unmarked_tier": "normal",
  "sections": [
    {"name": "climax", "start": 65.5, "end": 72.4, "tier": "peak"},
    {"name": "finale", "start": 100.6, "end": 113.2, "tier": "peak"}
  ],
  "effects": [
    {"section": "finale", "models": ["PORCH-COLUMNS"], "layer": 2,
     "effect": "Strobe_flash", "every_beats": 2, "length_beats": 1},
    {"start": 32.0, "end": 65.5, "models": ["Matrix"], "layer": 0, "effect": "Butterfly_gentle"}
  ]
}
```

| Field | Meaning |
|-------|---------|
| `bar_anchor` | Seconds; a bar starts on the beat nearest this time (fixes a misdetected bar grid) |
| `sections` | Named marks with `start`, `end`, `tier` (`peak` / `normal` / `breather`) and optional `label` (e.g. `bridge`). Sections are split at marks; marks within 0.25 s of a detected boundary snap to it |
| `unmarked_tier` | Tier for sections no mark covers. Omit it to rank them by energy as usual |
| `signature_colors` | One or two `#RRGGBB` accent colors for this song, overriding the show's pick |
| `final_hit` | Seconds; overrides the detected final hit |
| `singing` | Overrides for the show's `singing.moments` settings, e.g. `{"exposed_fraction": 0.4, "max_share": 0.3}` |
| `effects` | Custom effects on `models` (model or group names) for a named `section` or a `start`/`end` range, on `layer` 0–2. `effect` is an engine preset (`Strobe_flash`, `Butterfly_gentle`, `Spirals_fast`, …) or any xLights effect name; `settings` override preset values. `every_beats` repeats it on every Nth beat from the bar line (`length_beats` long); omit both for one effect across the range. Generated effects on the same models and layer are cleared from that range |

#### Design brief

The settings file's `brief` gives a song its own look; the generator follows it within the
show rules (safety and symmetry are unchanged):

```json
"brief": {
  "mood": "warm, bouncy, 1960s TV special",
  "motion": "punchy",
  "effects": {"tree": ["Pinwheel_sweep", "Spirals_fast"], "custom": ["Butterfly_gentle"]},
  "hero_effects": ["Pinwheel_sweep", "Chase_from_middle"],
  "stems": {"other": ["SNOWFLAKES"], "bass": ["MEGA-TREES"]},
  "arch_pattern": "bounce",
  "signature_moment": {"name": "snowfall", "description": "meteors rain down both mega trees",
                       "section": "climax", "models": ["MEGA-TREES"], "layer": 1, "effect": "Meteors_rain"}
}
```

| Field | Meaning |
|-------|---------|
| `mood` | Free text describing the concept (shown in the preview) |
| `motion` | `punchy` (hardest accents) or `flowing` (motion phrases twice as long, half as many accents, soft morphs) |
| `effects` | Motion presets per prop type (`arch`, `tree`, `single_line`, `poly_line`, `window`, `custom`, `other`), replacing the engine's choices |
| `hero_effects` | Motion presets for hero props (each hero uses the ones in its prop type's family, or all of them if none fit); kept distinct from neighboring songs |
| `stems` | `drums` / `bass` / `other` / `vocals` → models or groups. Their accent hits land on that stem's onsets (at most the usual density for the tier) wherever the stem is playing, in any non-breather section — and not where it's quiet |
| `arch_pattern` | `chase`, `bounce`, `center_out` or `fill`; kept distinct from neighboring songs |
| `signature_moment` | A custom effect (same fields as `effects` below) plus a `name` and `description`; all safety rules apply. `others` gives everyone else in its range a preset or look (or `"off"` for darkness) — such a moment owns its own props on every layer — and `stagger` (`center_out` / `left_to_right`, over `stagger_share` of it) starts its props one after another; symmetry pairs start together. No two songs may share a signature moment |
| `secondary_moments` | More named moments (same fields), placed after the signature |
| `concept`, `colors`, `differences` | The concept's name, its color direction, and "What makes this different" — specific contrasts with the other songs (shown in the preview) |
| `looks` | Named looks: any xLights effect with settings by property id (see `docs/effect_inventory.md`), e.g. `{"embers": {"effect": "Candle", "settings": {"PerNode": true}}}`; usable anywhere the brief names an effect. Names, ranges and choices are checked against the installed xLights (`XLIGHTS_APP` overrides `/Applications/xLights.app`) |
| `faces` | Whether the faces sing in this song (default no; with the rules' `singing.opt_in`, singers are otherwise ordinary props) |
| `lyric_cues` | `{word, time, models, effect, layer, length_beats, stagger}`: an effect starting on a transcribed word. The word must be at that time (±0.15 s) in the vocal-stem transcription and pass the stem/mix cross-check, or generation fails |
| `negative_space` | Props light only in peaks and where `fill_stem` rises above `fill_above` × its usual level, plus moments, cues and clicks |
| `clicks` | `{stem, models, effect, length_seconds, min_gap_seconds}`: one short hit per onset of the stem (e.g. finger snaps), never while a face sings |

#### Lyrics file

Whisper often mishears sung lyrics. To fix the words, put the lyrics in a text file next
to the music file with the same name plus `.lyrics.txt` — e.g. `Winter Wonderland.mp3` →
`Winter Wonderland.lyrics.txt`. Blank lines, `#` comments and `[Chorus]`-style tags are
ignored. The file's words are used in order, and Whisper only supplies the timing: each
known word is matched to the transcription; a misheard run takes the misheard words' time
span; words Whisper didn't hear fill the gap between their neighbours only when it's tight
enough to sing (otherwise they're left out); words Whisper heard that aren't in the file are
dropped. The track's confidence becomes the share of the file's words timed from Whisper's,
and is checked against the rules' `min_confidence` as usual. Transcriptions are cached per
music file, so editing the lyrics file doesn't re-run Whisper.

#### Show rules

Show-wide rules live in `xlights_mcp_rules.json` in the show folder, split in two:

- **`safety`** — applied by every write path: `create_sequence` in every mode,
  `remap_sequence` (imported sequences) and `preview_plan`. Guided mode returns them with
  its analysis so planned effects respect them.
- **`style`** — the look of the show, applied by auto generation only.

A per-song setting that breaks a rule (such as a strobe outside the finale) is rejected
with an error. All fields are optional.

| Safety field | Enforced as |
|--------------|-------------|
| `notes` | The rules in plain words (not used by the generator) |
| `exclude` | Models or groups that never get effects. Imported sequences are never mapped onto them, or onto any group containing them |
| `strobes` | Strobes only inside the finale (a section mark named `finale`, else the last run of peaks), every `every_beats` beats. In imported sequences, finale strobes are re-timed to that spacing and others removed. The finale comes from the sequence's audio — its media path, or else an MP3 in the show's `Music` folder matching it by title (track numbers, bracketed extras and punctuation ignored); only if neither is found are all strobes removed |
| `white` | Whites are the listed `colors` plus anything paler than `max_saturation`. Every white is tinted to at least `min_tint` (neutral white gets a warm tint) and capped at `max_brightness`, except in `full_white_effects` (strobes) and the final hit, which may use `full_white`. No prop shows only white (all active colors white, or a one-color effect like On led by white) for more than `max_seconds` at a time or `max_share` of the song; offenders lead with a non-white color instead (or `fallback_colors` when the palette has none). Effects on part of a prop (one arch of the chase) don't count toward those limits |

| Style field | Enforced as |
|-------------|-------------|
| `notes` | The rules in plain words |
| `priorities` | `hero` / `key` / `normal` / `optional` per model or group, always applied; overrides `set_model_priority` |
| `symmetry` | `pairs` of `[left, right]` **models** (group names are rejected); `default` and `by_tier` modes: `mirror` (right side = left side flipped horizontally), `call_response` (motion and accents alternate sides bar by bar), `sync` (identical). A pair of singers mirrors only its background bed |
| `arch_chase` | Arch models listed left to right with their arch `count` (and `reverse` if wired right to left); their motion becomes one step per beat, starting on the bar line, in every non-breather section, in the song's pattern from `patterns`: `chase` (one arch, left to right), `bounce` (left to right and back), `center_out` (the middle pair, then outward) or `fill` (one more arch per beat until all are lit) |
| `avoid` | `effects` (e.g. `["Strobe"]`) and `plain_color_wash` (a Color Wash with no shimmer or fade) are errors in briefs and custom effects; the generator's own plain washes become `replace_plain_with` (a preset, or `"off"`: dark) |
| `fast_flashing` | Once a prop's accent hits come closer than `min_gap_seconds` for `max_seconds`, the rest of that run keeps every other hit |
| `calibration` | `max_hits_per_minute`: the generator's accent hits (all props together) are cut to this budget, keeping the final hit, peaks, hero props and downbeats first; a brief's moments, cues and clicks aren't counted |
| `variety` | `adjacent_distinct`: what songs next to each other in show order (by track-number prefix) may not share — `hero_effects` and/or `arch_pattern`. A brief that breaks it is an error; songs without a brief get `hero_effects_per_song` hero effects and an arch pattern picked to avoid their neighbors. Hero effects are compared by xLights effect, so two looks of the same effect clash. `unique_signature_moments` forbids reusing another song's signature moment (same name, or the same effect on the same props); `faces_max_share` caps the share of songs whose faces sing (of the songs in the music folder). Each written song is recorded in the show design ledger, `xlights_mcp_design_ledger.json` (concept, hero effects, arch pattern, lyric cues, moments, faces) |
| `floods` | One solid color per section, stepping through the palette's non-white colors — never motion or accents |
| `twinkle` | Props that only twinkle in the given `tiers` (intro/outro count as breathers) |
| `final_hit` | Props kept dark until the final hit, then held for `bars` and faded. The final hit is the per-song `final_hit`, else the last strong downbeat before the music goes silent — the downbeat grid is carried on to the end of the music, and a downbeat is strong when its attack and loudness are near the song's typical downbeat (a chord ringing into the fade isn't) — snapped to its onset; without downbeats, the strongest hit in the last 15 seconds of music. With `overrides_calm_bed` (default), a final hit inside a singing moment ends the calm bed there: the faces finish the phrase while every other prop (except floods) accents the hit and gets its regular effects back |
| `singing` | Which face models sing (`singers`). Each defaults to the vocal-stem track; if that transcription's mean word confidence is below `min_confidence` the song is treated as having no lyrics, and `without_lyrics` priorities apply. With `moments`, faces sing only in vocal feature moments: whole lyric phrases (split at silences over `phrase_gap_seconds`, phrases over `max_phrase_bars` split again at their longest pauses, at least `min_phrase_bars` long) that are the song's most exposed — vocal stem ÷ drum stem is ranked within the song like section energy and the top `exposed_fraction` of eligible phrases qualify (at least one; never below `min_vocal_drum_ratio`) — and, with `cross_check` (default), never where the vocal-stem and full-mix transcriptions disagree on the words (different words or word count: those words are dropped and break their phrase, and the preview lists them as `disputed_passages`; words both heard are kept with the vocal stem's timing) — where the drums are sparse (≤ `max_drum_onsets_per_beat`) or steady (inter-onset spread ≤ `max_drum_irregularity`), outside `avoid_tiers`, ending at least `peak_lead_bars` before any peak and (with `protect_builds`) before the build section leading into it — later phrases are trimmed off the moment and listed in the preview — most exposed first up to `max_share` of the song; phrases within `merge_gap_bars` sing as one moment. During a moment the other props keep their bed, lose accent hits and keep only `gentle_motion` (no chases), and floods hold their color; between moments singers use the `between_moments` priority. Without `moments`, singers sing the whole song |
| `palettes` | `core` palettes chosen by `by_model` → `by_section_label` → `by_tier` → `default` (plus `arch_chase`); per-song signature colors drawn from `accent_pool` go on secondary props at peaks, on every other hero accent hit (always paired with a core color) and on one featured section, capped at `max_accent_share` of effect time. Each song gets a color set (a pair, with `signatures_per_song: 2`) that no other song in the show has — 28 pairs from 8 colors — remembered in `xlights_mcp_signatures.json`; single colors may recur in different sets, but with `adjacent_songs_distinct` songs next to each other in show order (by track-number prefix, e.g. `06 Holly Jolly Christmas`; songs without one have no neighbors) share no signature color. `signature_colors` in a song's settings file overrides the pick but must follow both rules |

`preview_plan` runs exactly the generation `create_sequence` would (same settings, rules,
lyrics and colors) without writing, and summarizes it per section and per rule. Both
return a `plan_id`; matching ids mean the sequence will be the one previewed.

### Import a community sequence

```
> Import the sequence ~/Downloads/Holly Jolly Christmas SD.zip to my show
> Import ~/Downloads/Christmas Time.xsq and show me the model mapping
> Import this sequence but map "Arch Left" to my "Arches-1" model
```

The importer supports both standalone `.xsq` files and `.zip` packages (which include audio, video assets, and the source show's model data). It automatically matches models from the imported sequence to your layout using:
1. **Exact name match** → identical model names
2. **Similar words** → shared words like "snowflake", "arch", "tree"
3. **Model type** → same `display_as` type (e.g., both Arches)
4. **Pixel count** → within 70% of each other
5. **Manual overrides** → you choose specific mappings

### Manage FPP (when controllers are online)

```
> Check the FPP status
> List playlists on FPP
> Start the Christmas playlist
> Stop playback
```

---

## Available Tools

### Show Management
| Tool | Description |
|------|-------------|
| `list_shows` | List all configured xLights show folders |
| `switch_show` | Switch the active show folder |
| `list_models` | List all light models with type, controller, and category info |
| `list_controllers` | List controllers with IPs, protocols, and channel counts |
| `list_sequences` | List all `.xsq` sequence files in the active show |
| `inspect_sequence` | Show song info, duration, effects, and models used in a sequence |
| `list_effects` | List all available xLights effects with descriptions |

### Audio Analysis
| Tool | Description |
|------|-------------|
| `analyze_song` | Full audio analysis: beats, structure, spectrum, energy |
| `get_song_structure` | Detect verse/chorus/bridge/intro/outro sections |
| `get_beat_map` | Get beat timestamps, downbeats, tempo, and onsets |
| `get_energy_profile` | Get loudness curve and bass/mid/high frequency band energy |

### Sequence Generation
| Tool | Description |
|------|-------------|
| `create_sequence` | Generate a `.xsq` file from an `.mp3` with effects on all models |
| `preview_plan` | Dry-run `create_sequence`: per-section tiers, symmetry, chase, floods, lit/dark props and accents, plus rule outcomes (including the chosen singing moments), with a `plan_id` that matches the sequence it previews |
| `set_model_priority` | Mark models or groups as hero / key / normal / optional for generated sequences |

### Sequence Import & Remapping
| Tool | Description |
|------|-------------|
| `import_sequence` | Import a `.xsq` or `.zip` sequence package and remap models to your layout |

### FPP Integration
| Tool | Description |
|------|-------------|
| `fpp_status` | Check FPP connection and playback state |
| `fpp_upload_sequence` | Upload `.fseq` and audio to FPP |
| `fpp_list_playlists` | List all playlists on FPP |
| `fpp_start_playlist` | Start a playlist (with optional repeat) |
| `fpp_stop` | Stop current playback |

---

## How It Works

### Effect Selection Logic

The server maps **model types** to appropriate effects:

| Model Type | Best Effects |
|------------|-------------|
| Arches | SingleStrand, Chase, ColorWash, Morph |
| Tree | Spirals, Pinwheel, Meteors, Circles |
| Single Line | Chase, Morph, SingleStrand, Shimmer |
| Poly Line | Chase, SingleStrand, Twinkle, Morph |
| Window Frame | Marquee, ColorWash, On, Curtain |
| Custom shapes | Shockwave, Circles, Plasma, Twinkle, Warp |

And maps **musical features** to effect choices:

| Musical Feature | Effects |
|----------------|---------|
| Strong beats | Shockwave, Morph, Strobe |
| Rhythmic passages | SingleStrand, Chase, Bars, Marquee |
| High energy (chorus) | Chase, Meteors, SingleStrand |
| Low energy (verse) | Twinkle, Shimmer, ColorWash, Snowflakes |
| Sustained notes | Plasma, Pinwheel, Spirals, Galaxy |
| Transitions | Warp, Curtain, Morph |
| Intro/Outro | Curtain, ColorWash, Twinkle |

### File Format

Generated `.xsq` files are standard xLights XML containing:
- `<head>` — song metadata, media file path, duration, timing (25ms frames)
- `<ColorPalettes>` — themed color palettes for effects
- `<EffectDB>` — deduplicated effect parameter definitions
- `<DisplayElements>` — all models included in the sequence
- `<ElementEffects>` — effect placements per model, per layer, with timing

---

## Project Structure

```
xlights-mcp-server/
├── pyproject.toml
├── README.md
├── src/xlights_mcp/
│   ├── server.py              # MCP server entry point & tool definitions
│   ├── config.py              # Configuration management
│   ├── audio/
│   │   ├── analyzer.py        # Full analysis pipeline orchestrator
│   │   ├── cache.py           # Versioned core-analysis cache
│   │   ├── beats.py           # Beat/tempo/onset detection (librosa + madmom)
│   │   ├── structure.py       # Song section detection (verse/chorus/bridge)
│   │   ├── spectrum.py        # Frequency band & energy analysis
│   │   └── separator.py       # Demucs stem separation (optional)
│   ├── xlights/
│   │   ├── show.py            # Show folder parser (networks + models XML)
│   │   ├── xsq_reader.py     # Parse existing .xsq sequences
│   │   ├── xsq_writer.py     # Generate .xsq XML files
│   │   ├── effects.py         # Effect library & model/music mappings
│   │   ├── palettes.py        # Color palette definitions & themes
│   │   └── models.py          # Data models (Controller, LightModel, etc.)
│   ├── sequencer/
│   │   └── engine.py          # Sequence generation engine (auto/guided/template)
│   └── fpp/
│       ├── client.py          # FPP REST API client
│       ├── upload.py          # Sequence upload to FPP
│       └── schedule.py        # Schedule management
└── tests/
```

---

## Troubleshooting

**MCP server not loading**
- Verify your MCP config file is valid JSON (no comments, no trailing commas)
- Check the `--directory` path points to the repo root (where `pyproject.toml` is)
- Restart your AI tool after config changes
- Test manually: `cd /path/to/xlights-mcp-server && uv run xlights-mcp-server` — should start without errors

**"No xLights show folders found"**
- Make sure xLights is installed and you've opened it at least once (it creates `xlights_rgbeffects.xml` in each show folder)
- If your show folder is in a non-standard location, add it to `~/.xlights-mcp/config.json`

**"No models found" error**
- Verify the show folder contains `xlights_networks.xml` and `xlights_rgbeffects.xml`
- Use `list_shows` to check which show is active and whether the path exists

**Audio analysis is slow**
- First run downloads librosa data (~10MB); subsequent runs are faster
- Demucs stem separation takes 30-60s per song on CPU; results are cached
- Without optional deps, analysis takes ~5s per song

**Generated sequence looks wrong in xLights**
- The `.xsq` is a starting point — tweak effects, timing, and palettes in xLights
- Use `inspect_sequence` to review what was generated before opening

**FPP connection fails**
- Verify FPP is powered on and on the same network
- Check the hostname/IP in `~/.xlights-mcp/config.json`
- FPP tools gracefully report connection errors; core generation works fully offline

---

## License

MIT

### Audio-analysis research provenance

The deterministic audio capabilities were compared with
[twinklr at commit `59bd7e4`](https://github.com/bluewatersql/twinklr/tree/59bd7e4e59c3a45aa812fe03e516b6a7237a224d/packages/twinklr/core/audio).
That snapshot has no identified license granting source-code reuse. This project
therefore implements standard DSP techniques independently using
[librosa's documented APIs](https://librosa.org/doc/latest/), rather than copying
or translating twinklr source, tests, bespoke scoring rules, or tuned constants.
Twinklr is not a dependency, and this acknowledgment does not grant rights to its
code. Its LLM profiling, remote lyrics/metadata services, and optional MIR model
backends are not included.
