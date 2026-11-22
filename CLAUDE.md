# CLAUDE.md

## Show rules

The active show's sequencing rules live in its show folder, not in this repo:

**`/Volumes/Personal-Drive/XLIGHTS/XMAS_2026/xlights_mcp_rules.json`**

It has two parts, each with a `notes` list stating the rules in plain words:

- **`safety`** (TUNE_TO_90.1 exclusion, white/brightness cap, strobe limit) — enforced on
  every write path: `create_sequence` in all modes, `preview_plan`, and `remap_sequence`
  (`src/xlights_mcp/sequencer/xsq_safety.py` for imported sequences). Guided-mode output
  includes them; respect them when planning effects by hand.
- **`style`** (everything else) — applied by auto generation only
  (`src/xlights_mcp/sequencer/show_rules.py`).

Read it before planning or generating a sequence, and treat it as the source of truth:

- Before any change to the rules file, back it up to `<show>/backups/xlights_mcp_rules.<YYYY-MM-DD-HHMMSS>.json`.
- When the user states a new show-wide rule, add it to the rules file (`notes` plus the
  structured field, under `safety` if it protects people or hardware, else `style`) and
  make sure the generator enforces it — not just this file.
- Run `preview_plan` before `create_sequence` when the user wants to review a plan; the
  two `plan_id`s match when the written sequence is the one previewed.
- Don't hardcode show-specific model names in the code; reference them from the rules file.
- A per-song setting that breaks a rule (e.g. a strobe outside the finale) makes
  `create_sequence` return an error — fix the song settings, don't bypass the check.

Related per-show and per-song state:

| File | What it holds |
|------|---------------|
| `<show>/xlights_mcp_rules.json` | Show rules (above) |
| `<show>/xlights_mcp_signatures.json` | Signature accent colors already given to each song (written by the generator) |
| `<music file stem>.sequence.json` next to the MP3 | Per-song bar anchor, section marks, custom effects, signature/final-hit overrides (see README) |
| `~/.xlights-mcp/config.json` | Show folders and model priorities (hero / key / optional) set with `set_model_priority` |

## Development

- Run tests: `uv run --inexact --extra dev pytest -q` (`--inexact` keeps the installed
  separation/lyrics/beats extras).
- The xlights MCP server runs from this checkout; after code changes, reconnect it
  (`/mcp` → reconnect xlights) before calling its tools.
- Never overwrite a sequence in the show folder without asking — the generator writes
  `<song> (generated N).xsq` when the file exists, and moves that song's earlier
  generated drafts (with their .fseq) into `<show>/archive/`. Never delete superseded
  drafts; archive them.
