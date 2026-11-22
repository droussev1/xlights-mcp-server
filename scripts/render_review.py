"""Render a sequence in the running xLights, export its house-preview video, and pull frames.

Needs xLights open with its automation port on (Preferences: xFade/xSchedule → Port A,
which listens on 127.0.0.1:49913). Uses the same commands as xLights' bundled
BatchVideoExport.lua: openSequence → renderAll → exportVideoPreview → closeSequence.

    uv run python scripts/render_review.py "<show>/07 Winter Wonderland (generated 1).xsq" \\
        --at 5.2 11.6 50.0 85.3 93.0 --out "<show>/renders"

Writes <out>/<sequence>.mp4, one PNG per --at time (the time is in the file name), and
<sequence>-sheet.png (all frames tiled in --at order, left to right, top to bottom). The
video is the size of xLights' House Preview pane; enlarge it for sharper frames.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import urllib.request
from pathlib import Path

URL = "http://127.0.0.1:49913/xlDoAutomation"


def command(cmd: str, timeout: float = 900, **params) -> dict:
    body = json.dumps({"cmd": cmd, **{k: str(v).lower() if isinstance(v, bool) else str(v) for k, v in params.items()}})
    req = urllib.request.Request(URL, data=body.encode(), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        text = resp.read().decode() or "{}"
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {"raw": text}


def _cell(n: int, unit: str) -> str:
    return "+".join([unit] * n) if n else "0"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("sequence", type=Path)
    ap.add_argument("--at", type=float, nargs="*", default=[], help="seconds to grab frames at")
    ap.add_argument("--out", type=Path, required=True, help="folder xLights may write to (e.g. <show>/renders)")
    ap.add_argument("--frames-only", action="store_true", help="reuse an exported video")
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    video = args.out / f"{args.sequence.stem}.mp4"

    if not args.frames_only:
        print(command("openSequence", seq=args.sequence, promptIssues=False))
        print(command("renderAll", highdef=False))
        print(command("exportVideoPreview", filename=video))
        print(command("closeSequence", quiet=True, force=True))
    if not video.exists():
        print(f"No video at {video}", file=sys.stderr)
        return 1

    frames = []
    for t in args.at:
        png = args.out / f"{args.sequence.stem}-{t:07.2f}.png"
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{t}", "-i", str(video), "-frames:v", "1",
                        str(png)], check=True)
        frames.append(png)
    if frames:
        sheet = args.out / f"{args.sequence.stem}-sheet.png"
        cols = min(3, len(frames))
        inputs = [x for f in frames for x in ("-i", str(f))]
        rows = -(-len(frames) // cols)
        subprocess.run(["ffmpeg", "-v", "error", "-y", *inputs, "-filter_complex",
                        f"{''.join(f'[{i}:v]scale=-2:520[v{i}];' for i in range(len(frames)))}"
                        f"{''.join(f'[v{i}]' for i in range(len(frames)))}xstack=inputs={len(frames)}:"
                        f"layout={'|'.join(_cell(i % cols, 'w0') + '_' + _cell(i // cols, 'h0') for i in range(len(frames)))}:fill=black",
                        str(sheet)], check=True)
        print(sheet, f"({rows}×{cols})")
    print(video)
    return 0


if __name__ == "__main__":
    sys.exit(main())
