# CLAUDE.md

## Show rules

The active show's sequencing rules live in its show folder, not in this repo:

**`/Volumes/Personal-Drive/XLIGHTS/XMAS_2026/xlights_mcp_rules.json`**

`create_sequence` reads it on every run and the generator enforces it
(`src/xlights_mcp/sequencer/show_rules.py`). Its `notes` list states the rules in
plain words; the other fields are what the generator acts on. Read it before
planning or generating a sequence, and treat it as the source of truth:

- When the user states a new show-wide rule, add it to the rules file (`notes` plus the
  structured field) and make sure the generator enforces it — not just this file.
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
  `<song> (generated N).xsq` when the file exists.
