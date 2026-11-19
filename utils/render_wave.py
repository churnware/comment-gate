"""Write media/wave.svg, the layered swell used as the README's dividers.

    python utils/render_wave.py
"""
import math

from _common import MEDIA

W, H = 880, 28
# (amplitude, period, phase, baseline, colour, opacity, stroke width)
LAYERS = [
    (5.0, 220, 0.0, 14, "#4e76a0", 0.55, 1.6),
    (3.5, 160, 1.9, 15, "#8aaccc", 0.40, 1.2),
    (2.5, 300, 3.6, 13, "#2e4a70", 0.45, 1.0),
]


def path(amp, period, phase, y0):
    return "M" + " L".join(f"{x},{y0 + amp * math.sin(2 * math.pi * x / period + phase):.2f}"
                           for x in range(0, W + 1, 4))


def main():
    # The mask fades both ends out so the divider has no hard edges.
    svg = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}" '
        'fill="none" stroke-linecap="round">',
        '<defs><linearGradient id="fade" x1="0" x2="1">'
        '<stop offset="0" stop-color="#fff" stop-opacity="0"/><stop offset=".2" stop-color="#fff"/>'
        '<stop offset=".8" stop-color="#fff"/><stop offset="1" stop-color="#fff" stop-opacity="0"/>'
        '</linearGradient>',
        f'<mask id="m"><rect width="{W}" height="{H}" fill="url(#fade)"/></mask></defs><g mask="url(#m)">',
    ]
    for amp, period, phase, y0, colour, opacity, width in LAYERS:
        svg.append(f'<path d="{path(amp, period, phase, y0)}" stroke="{colour}" '
                   f'stroke-opacity="{opacity}" stroke-width="{width}"/>')
    svg.append("</g></svg>")
    out = MEDIA / "wave.svg"
    out.write_text("\n".join(svg) + "\n", encoding="utf-8")
    print(out)


if __name__ == "__main__":
    main()
