"""Render ANSI-coloured output as it appears in the terminal."""
import os
import re

# Must be set before comment_gate.ui is imported: it reads them at import time.
os.environ["FORCE_COLOR"] = "1"
os.environ["COLORTERM"] = "truecolor"
os.environ.pop("NO_COLOR", None)

from _common import find_font  # noqa: E402
from PIL import Image, ImageChops, ImageDraw, ImageFont  # noqa: E402

SGR = re.compile(r"\x1b\[([0-9;]*)m")
OTHER_ESCAPES = re.compile(r"\x1b\[[0-9;?]*[A-LN-Za-ln-z]")
# Cascadia lacks these glyphs; the terminal falls back to a symbol font too.
FALLBACK_CHARS = set("∼⟶")
SHEAR = 0.2


def parse(line):
    """Split an ANSI-coloured line into (char, rgb, bold, italic) cells."""
    line = OTHER_ESCAPES.sub("", line)
    cells, colour, is_bold, is_italic, pos = [], None, False, False, 0
    for m in SGR.finditer(line):
        cells += [(ch, colour, is_bold, is_italic) for ch in line[pos:m.start()]]
        pos = m.end()
        codes = m.group(1).split(";")
        if codes in (["0"], [""]):
            colour, is_bold, is_italic = None, False, False
        elif codes[0] == "1":
            is_bold = True
        elif codes[0] == "3":
            is_italic = True
        elif codes[:2] == ["38", "2"]:
            colour = tuple(map(int, codes[2:5]))
    return cells + [(ch, colour, is_bold, is_italic) for ch in line[pos:]]


class Terminal:
    def __init__(self, size, pad=(8, 6)):
        mono = find_font("CascadiaMono.ttf")
        self.regular = ImageFont.truetype(mono, size)
        self.regular.set_variation_by_name("Regular")
        self.bold = ImageFont.truetype(mono, size)
        self.bold.set_variation_by_name("Bold")
        self.fallback = ImageFont.truetype(find_font("seguisym.ttf", "DejaVuSans.ttf"), size)
        self.advance = self.regular.getlength("M")
        self.ascent, descent = self.regular.getmetrics()
        self.line = self.ascent + descent
        self.pad_x, self.pad_y = pad

    def _mask(self, cells, width):
        """Coverage mask for one row. Each style is drawn as a whole run so box
        glyphs join exactly as the font spaces them."""
        mask = Image.new("L", (width, self.line), 0)
        for style in {(c[2], c[3]) for c in cells}:
            text = "".join(ch if (b, i) == style and ch not in FALLBACK_CHARS else " "
                           for ch, _, b, i in cells)
            if not text.strip():
                continue
            layer = Image.new("L", (width, self.line), 0)
            font = self.bold if style[0] else self.regular
            ImageDraw.Draw(layer).text((self.pad_x, 0), text, font=font, fill=255)
            if style[1]:
                # Synthetic italic: shear about the baseline.
                layer = layer.transform(layer.size, Image.AFFINE,
                                        (1, SHEAR, -SHEAR * self.ascent, 0, 1, 0),
                                        resample=Image.BICUBIC)
            mask = ImageChops.lighter(mask, layer)
        draw = ImageDraw.Draw(mask)
        for i, (ch, *_) in enumerate(cells):
            if ch in FALLBACK_CHARS:
                x = self.pad_x + i * self.advance + (self.advance - self.fallback.getlength(ch)) / 2
                draw.text((x, self.line / 2), ch, font=self.fallback, fill=255, anchor="lm")
        return mask

    def render(self, lines, default_fg, palette=lambda rgb: rgb, cols=None):
        """Transparent RGBA image of `lines`; uncoloured text uses `default_fg`."""
        rows = [parse(line) for line in lines]
        cols = cols or max(map(len, rows), default=0)
        width = int(self.pad_x * 2 + cols * self.advance + SHEAR * self.line) + 2
        img = Image.new("RGBA", (width, self.pad_y * 2 + self.line * len(rows)), (0, 0, 0, 0))
        for r, cells in enumerate(rows):
            colour = Image.new("RGB", (width, self.line))
            draw = ImageDraw.Draw(colour)
            for i, (_, rgb, *_) in enumerate(cells):
                x = self.pad_x + i * self.advance
                # Overhang into the next cell covers italic glyphs leaning
                # right; the next cell's own fill paints over it.
                draw.rectangle([round(x), 0, round(x + self.advance) + 8, self.line],
                               fill=palette(rgb or default_fg))
            row = colour.convert("RGBA")
            row.putalpha(self._mask(cells, width))
            img.alpha_composite(row, (0, self.pad_y + r * self.line))
        return img
