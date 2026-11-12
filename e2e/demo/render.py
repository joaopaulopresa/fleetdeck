"""Turn the raw Playwright recordings into the demo videos and the README hero animation.

    python e2e/demo/render.py            # every recording found in e2e/output
    python e2e/demo/render.py video-3    # one of them

Stretches the recording script marked as waiting are sped up (the caption on screen says so).
Output: docs/media/<name>.mp4 (H.264) and, for the storyline, docs/media/hero.gif.
"""

import json
import subprocess
import sys
from pathlib import Path

E2E = Path(__file__).parents[1]
MEDIA = E2E.parent / "docs" / "media"


def run(*args: str) -> None:
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", *args], check=True)


def render(segments_file: Path) -> None:
    name = segments_file.name.removesuffix(".segments.json")
    timeline = json.loads(segments_file.read_text())
    folder = next(p for p in (E2E / "output").iterdir() if p.is_dir() and p.name.startswith(name))
    source = next(folder.glob("*.webm"))
    cuts: list[tuple[float, float, float]] = []
    cursor = 0.0
    for segment in timeline["segments"]:
        if segment["from"] > cursor:
            cuts.append((cursor, segment["from"], 1.0))
        cuts.append((segment["from"], segment["to"], float(segment["speed"])))
        cursor = segment["to"]
    cuts.append((cursor, timeline["total"], 1.0))
    parts = [
        f"[0:v]trim={start:.2f}:{end:.2f},setpts=(PTS-STARTPTS)/{speed}[v{index}]"
        for index, (start, end, speed) in enumerate(cuts)
        if end - start > 0.05
    ]
    labels = "".join(
        f"[v{index}]" for index, (start, end, _) in enumerate(cuts) if end - start > 0.05
    )
    graph = ";".join(parts) + f";{labels}concat=n={len(parts)}:v=1:a=0,fps=25[out]"
    MEDIA.mkdir(parents=True, exist_ok=True)
    target = MEDIA / f"{name}.mp4"
    run(
        "-i",
        str(source),
        "-filter_complex",
        graph,
        "-map",
        "[out]",
        "-c:v",
        "libx264",
        "-preset",
        "slow",
        "-crf",
        "24",
        "-pix_fmt",
        "yuv420p",
        "-movflags",
        "+faststart",
        str(target),
    )
    length = sum((end - start) / speed for start, end, speed in cuts)
    print(
        f"{target.relative_to(E2E.parent)}: {length:.0f} s from a {timeline['total']:.0f} s recording"
    )


def hero(source: Path, start: float, seconds: float) -> None:
    """Cut a looping GIF for the README: 960 px wide, 12 fps, generated palette."""
    palette, target = MEDIA / "hero-palette.png", MEDIA / "hero.gif"
    scale = "fps=12,scale=960:-1:flags=lanczos"
    run(
        "-ss",
        str(start),
        "-t",
        str(seconds),
        "-i",
        str(source),
        "-vf",
        f"{scale},palettegen=max_colors=96",
        str(palette),
    )
    run(
        "-ss",
        str(start),
        "-t",
        str(seconds),
        "-i",
        str(source),
        "-i",
        str(palette),
        "-lavfi",
        f"{scale}[x];[x][1:v]paletteuse=dither=bayer:bayer_scale=4",
        str(target),
    )
    palette.unlink()
    print(f"{target.relative_to(E2E.parent)}: {target.stat().st_size / 1e6:.1f} MB")


if __name__ == "__main__":
    wanted = sys.argv[1] if len(sys.argv) > 1 else ""
    for file in sorted((E2E / "output").glob("*.segments.json")):
        if wanted in file.name:
            render(file)
    storyline = MEDIA / "video-1-storyline.mp4"
    if storyline.exists() and wanted in ("", "hero", "video-1"):
        hero(storyline, start=6, seconds=18)
