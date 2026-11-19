"""Render the menu and review log as side-by-side terminal windows in
media/showcase.png.

The live CLI supplies the current menu and banner. The log comes from
media/rewrite-examples.png.

    python utils/render_showcase.py
"""
import contextlib
import io

from _common import MEDIA, find_font
from ansi import Terminal
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from comment_gate import cli, ui

DEEP, TIDE, MIST = (46, 74, 112), (78, 118, 160), (138, 172, 204)
PAD, BAR, RADIUS, GAP, MARGIN = 16, 40, 14, 40, 36
# Matches the log screenshot: Cascadia Mono on a 23px line, default text colour.
LINE_PX, DEFAULT_FG = 23, (204, 204, 204)
# Crop of the log screenshot. It keeps a margin of plain background so the
# feathered edge never touches text.
LOG = ("rewrite-examples.png", (2, 338, 1245, 688))
font = ImageFont.truetype(find_font("CascadiaMono.ttf"), 15)


class _MenuShown(Exception):
    pass


def menu_lines():
    """Capture ANSI output from the main menu until it prompts for a choice."""
    def stop(*_args, **_kwargs):
        raise _MenuShown

    ui.interactive_input = lambda: False
    ui.ask = stop
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        try:
            cli.interactive()
        except _MenuShown:
            pass
    lines = out.getvalue().split("\n")
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    return lines


def render_menu(bg):
    term = next(Terminal(s, pad=(18, 18)) for s in range(14, 30) if Terminal(s).line >= LINE_PX)
    text = term.render(menu_lines(), DEFAULT_FG)
    shot = Image.new("RGBA", text.size, bg + (255,))
    shot.alpha_composite(text)
    return shot.convert("RGB")


def border_colour(im):
    """Mean of the screenshot's top and bottom edges, which are pure terminal
    background."""
    strips = [im.crop((0, 0, im.width, 4)), im.crop((0, im.height - 4, im.width, im.height))]
    means = [s.resize((1, 1), Image.BOX).getpixel((0, 0)) for s in strips]
    return tuple(round(sum(c) / len(c)) for c in zip(*means))


def window(title, shot, bg, height):
    w = shot.width + 2 * PAD
    win = Image.new("RGBA", (w, height), bg + (255,))
    d = ImageDraw.Draw(win)
    d.rectangle([0, 0, w, BAR], fill=tuple(max(0, c - 9) for c in bg))
    for i, c in enumerate((DEEP, TIDE, MIST)):
        cx = 22 + i * 20
        d.ellipse([cx - 6, BAR / 2 - 6, cx + 6, BAR / 2 + 6], fill=c)
    d.text((w / 2, BAR / 2), title, font=font, fill=TIDE, anchor="mm")
    # Feather slightly noisy screenshot backgrounds into the window's
    # flat fill
    # to avoid a visible seam.
    feather = Image.new("L", shot.size, 0)
    ImageDraw.Draw(feather).rectangle([10, 10, shot.width - 11, shot.height - 11], fill=255)
    win.paste(shot, (PAD, BAR + PAD), feather.filter(ImageFilter.GaussianBlur(5)))
    mask = Image.new("L", win.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, w - 1, height - 1], RADIUS, fill=255)
    win.putalpha(mask)
    ImageDraw.Draw(win).rounded_rectangle([0, 0, w - 1, height - 1], RADIUS,
                                          outline=TIDE + (110,), width=1)
    return win


def main():
    log = Image.open(MEDIA / LOG[0]).convert("RGB")
    bg = border_colour(log)
    shots = [
        ("comment-gate", render_menu(bg), bg),
        ("comment-gate log", log.crop(LOG[1]), bg),
    ]
    height = BAR + 2 * PAD + max(s.height for _, s, _ in shots)
    wins = [window(t, s, bg, height) for t, s, bg in shots]

    size = (sum(w.width for w in wins) + GAP * (len(wins) - 1) + 2 * MARGIN, height + 2 * MARGIN)
    canvas = Image.new("RGBA", size, (0, 0, 0, 0))
    shadow = Image.new("RGBA", size, (0, 0, 0, 0))
    xs, x = [], MARGIN
    for w in wins:
        ImageDraw.Draw(shadow).rounded_rectangle(
            [x, MARGIN + 10, x + w.width, MARGIN + 10 + height], RADIUS, fill=(16, 28, 48, 110))
        xs.append(x)
        x += w.width + GAP
    canvas.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(14)))
    for w, x in zip(wins, xs):
        canvas.alpha_composite(w, (x, MARGIN))
    out = MEDIA / "showcase.png"
    canvas.save(out, optimize=True)
    print(f"{out} {size[0]}x{size[1]}")


if __name__ == "__main__":
    main()
