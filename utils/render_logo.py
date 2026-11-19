"""Render the CLI banner to media/logo-{dark,light}.webp.

Each animated WebP plays the banner's wave once and holds the last frame.
Frames use ANSI output from ui._banner_lines, so they track changes to the
banner's art, subtitle or colours.

    python utils/render_logo.py [--subtitle TEXT] [--frames N]
"""
import argparse
import functools
import os

from _common import MEDIA
from ansi import Terminal

from comment_gate import ui

# The version line is left out so the image doesn't go stale on each release.
ROWS = 4

# The light theme walks the same deep-to-foam ramp, but towards ink.
L_DEEP, L_MIST, L_FOAM = (168, 192, 218), (70, 110, 156), (22, 42, 72)
_RAMP = [(t / 1000, ui._water(t / 1000)) for t in range(1001)]


@functools.lru_cache(None)
def light(rgb):
    t = min(_RAMP, key=lambda p: sum((a - b) ** 2 for a, b in zip(p[1], rgb)))[0]
    if t < 0.7:
        return ui._mix(L_DEEP, L_MIST, t / 0.7)
    return ui._mix(L_MIST, L_FOAM, (t - 0.7) / 0.3)


def dark(rgb):
    return rgb


def ease(t):
    """Cubic ease-out: a slower, calmer settle than the CLI's quadratic."""
    return 1 - (1 - t) ** 3


def render(term, palette, path, frames, frame_ms, subtitle):
    images = []
    for f in range(frames):
        lines = ui._banner_lines("", subtitle, 6.6 * ease(f / (frames - 1)))[:ROWS]
        images.append(term.render(lines, ui.FOAM, palette))
    # loop=1 plays once, then the last frame stays.
    images[0].save(path, save_all=True, append_images=images[1:], duration=frame_ms, loop=1,
                   lossless=True, method=4, background=(0, 0, 0, 0))
    print(f"{path} {images[0].size[0]}x{images[0].size[1]}, {os.path.getsize(path) // 1024} KB")


def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--subtitle", default=ui.banner.__defaults__[0])
    p.add_argument("--frames", type=int, default=54)
    p.add_argument("--frame-ms", type=int, default=30)
    a = p.parse_args()
    term = Terminal(40)
    for name, palette in (("dark", dark), ("light", light)):
        render(term, palette, str(MEDIA / f"logo-{name}.webp"), a.frames, a.frame_ms, a.subtitle)


if __name__ == "__main__":
    main()
