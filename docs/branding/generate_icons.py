"""Generates the kbox app icon: a symmetrical K cradling the mic, canted 35deg.

Run with `uv run python docs/branding/generate_icons.py` from the repo root.
Writes apple-touch-icon.png, apple-touch-icon-precomposed.png, and favicon.ico
into kbox/web/static/.

All shapes are drawn in a 64x64 unit design grid, then scaled to the output
size and rotated as a whole. See docs/branding/brand.html for the palette
and rationale.
"""

from pathlib import Path

from PIL import Image, ImageDraw

WHITE = (243, 236, 255, 255)
DARK = (21, 12, 34, 255)
PURPLE = (124, 58, 237, 255)
PINK = (255, 63, 164, 255)

LEAN_DEG = 35
STATIC_DIR = Path(__file__).resolve().parent.parent.parent / "kbox" / "web" / "static"


def _cap(draw, x, y, w, **kw):
    r = w / 2
    draw.ellipse([x - r, y - r, x + r, y + r], **kw)


def _draw_glyph(draw, scale):
    stroke_w = 6.5 * scale

    # the K: a continuous horizontal crossbar with two equal-angle legs
    # branching from its midpoint - not a Y, the crossbar continues on
    # both sides of the branch point.
    y_line = 49 * scale
    x_left, x_right = 15 * scale, 49 * scale
    branch = (32 * scale, y_line)
    draw.line([(x_left, y_line), (x_right, y_line)], fill=WHITE, width=int(stroke_w))

    leg_l = (20 * scale, 29 * scale)
    leg_r = (44 * scale, 29 * scale)
    draw.line([branch, leg_l], fill=WHITE, width=int(stroke_w))
    draw.line([branch, leg_r], fill=WHITE, width=int(stroke_w))

    for point in (branch, (x_left, y_line), (x_right, y_line), leg_l, leg_r):
        _cap(draw, *point, stroke_w, fill=WHITE)

    # the mic head, cradled in the K's legs with a small gap (not touching)
    cap_w, cap_h = 17 * scale, 30 * scale
    ccx, ccy = 32 * scale, 16 * scale
    box = [ccx - cap_w / 2, ccy - cap_h / 2, ccx + cap_w / 2, ccy + cap_h / 2]
    draw.rounded_rectangle(
        box, radius=cap_w / 2, fill=DARK, outline=PINK, width=max(1, int(2.6 * scale))
    )
    for i in range(1, 4):
        ly = box[1] + (cap_h * i / 4)
        draw.line(
            [box[0] + 3 * scale, ly, box[2] - 3 * scale, ly],
            fill=WHITE,
            width=max(1, int(1.6 * scale)),
        )


def make_icon(size, out_path, lean_deg=LEAN_DEG, radius_ratio=0.22):
    scale = size / 64.0

    badge = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    bd = ImageDraw.Draw(badge)
    bd.rounded_rectangle([0, 0, size - 1, size - 1], radius=int(size * radius_ratio), fill=PURPLE)

    glyph = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glyph)
    _draw_glyph(gd, scale)

    if lean_deg:
        glyph = glyph.rotate(-lean_deg, resample=Image.BICUBIC, center=(size / 2, size / 2))

    badge.alpha_composite(glyph)
    badge.save(out_path)


def main():
    STATIC_DIR.mkdir(parents=True, exist_ok=True)

    touch_icon = STATIC_DIR / "apple-touch-icon.png"
    make_icon(180, touch_icon)
    make_icon(180, STATIC_DIR / "apple-touch-icon-precomposed.png")

    base = Image.open(touch_icon)
    sizes = [(16, 16), (32, 32), (48, 48)]
    imgs = [base.resize(s, Image.LANCZOS) for s in sizes]
    imgs[0].save(STATIC_DIR / "favicon.ico", format="ICO", sizes=sizes, append_images=imgs[1:])

    print(f"wrote icons to {STATIC_DIR}")


if __name__ == "__main__":
    main()
