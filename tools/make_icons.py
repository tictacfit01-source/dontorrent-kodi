"""Generate flat circular section icons for the DonTorrent addon.

Run from the project root:
    python tools/make_icons.py

Writes 512x512 PNGs into:
    plugin.video.dontorrent/resources/media/
"""
import os
from PIL import Image, ImageDraw

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "plugin.video.dontorrent", "resources", "media")
os.makedirs(OUT, exist_ok=True)

SIZE = 512
PAD = 64                       # symbol stays inside this padding
CENTER = (SIZE // 2, SIZE // 2)
SYM = (255, 255, 255, 255)     # white symbol over color disc

# (filename, hex_bg, hex_accent, symbol_drawer)
def disc(draw, color, ring=None):
    draw.ellipse((0, 0, SIZE, SIZE), fill=color)
    if ring:
        draw.ellipse((8, 8, SIZE - 8, SIZE - 8), outline=ring, width=6)


def _hex(c):
    c = c.lstrip("#")
    return tuple(int(c[i:i + 2], 16) for i in (0, 2, 4)) + (255,)


# ---- symbol primitives ----------------------------------------------------

def sym_star(d):
    import math
    cx, cy = CENTER
    r_out = (SIZE - 2 * PAD) // 2
    r_in = int(r_out * 0.42)
    pts = []
    for i in range(10):
        ang = -math.pi / 2 + i * math.pi / 5
        r = r_out if i % 2 == 0 else r_in
        pts.append((cx + int(r * math.cos(ang)), cy + int(r * math.sin(ang))))
    d.polygon(pts, fill=SYM)


def sym_clapper(d):
    # film clapper: trapezoid top + rectangle body
    cx, cy = CENTER
    w = SIZE - 2 * PAD
    h = int(w * 0.7)
    body = (cx - w // 2, cy - h // 2 + 40, cx + w // 2, cy + h // 2 + 40)
    d.rectangle(body, fill=SYM)
    # diagonal stripes (top "clapper")
    top_h = 70
    top = (cx - w // 2, cy - h // 2 - top_h + 40, cx + w // 2, cy - h // 2 + 40)
    d.polygon(
        [(top[0], top[3]), (top[2], top[3]), (top[2], top[1]),
         (top[0] + 30, top[1])],
        fill=SYM,
    )
    # punch holes (color showing through) — simulate by drawing small diagonal
    # rectangles in bg color
    bg = d.im.getpixel((10, 10))[:3] + (255,)
    stripe_w = w // 6
    for i in range(3):
        x0 = top[0] + 20 + i * stripe_w * 2
        d.polygon(
            [(x0, top[3] - 10), (x0 + stripe_w, top[1] + 10),
             (x0 + stripe_w + 30, top[1] + 10), (x0 + 30, top[3] - 10)],
            fill=bg,
        )


def sym_tv(d):
    # rounded rect screen + antennae
    cx, cy = CENTER
    w = SIZE - 2 * PAD
    h = int(w * 0.62)
    screen = (cx - w // 2, cy - h // 2 + 30, cx + w // 2, cy + h // 2 + 30)
    d.rounded_rectangle(screen, radius=24, fill=SYM)
    # punch inner darker rect
    bg = d.im.getpixel((10, 10))[:3] + (255,)
    inner = (screen[0] + 24, screen[1] + 24, screen[2] - 24, screen[3] - 24)
    d.rounded_rectangle(inner, radius=12, fill=bg)
    # antennae
    d.line((cx - 60, cy - h // 2 - 50, cx, cy - h // 2 + 30), fill=SYM, width=10)
    d.line((cx + 60, cy - h // 2 - 50, cx, cy - h // 2 + 30), fill=SYM, width=10)
    # base / stand
    d.rectangle((cx - 50, screen[3], cx + 50, screen[3] + 20), fill=SYM)


def sym_book(d):
    cx, cy = CENTER
    w = SIZE - 2 * PAD
    h = int(w * 0.78)
    left = (cx - w // 2, cy - h // 2, cx - 6, cy + h // 2)
    right = (cx + 6, cy - h // 2, cx + w // 2, cy + h // 2)
    d.rectangle(left, fill=SYM)
    d.rectangle(right, fill=SYM)
    bg = d.im.getpixel((10, 10))[:3] + (255,)
    # page lines
    for i in range(3):
        y = cy - h // 4 + i * 60
        d.line((left[0] + 30, y, left[2] - 20, y), fill=bg, width=8)
        d.line((right[0] + 20, y, right[2] - 30, y), fill=bg, width=8)


def sym_search(d):
    cx, cy = CENTER
    r = (SIZE - 2 * PAD) // 3
    # circle (lens)
    box = (cx - r - 30, cy - r - 30, cx + r - 30, cy + r - 30)
    d.ellipse(box, outline=SYM, width=24)
    # handle
    d.line((cx + r - 60, cy + r - 60, cx + r + 60, cy + r + 60), fill=SYM, width=28)


def sym_refresh(d):
    cx, cy = CENTER
    r = (SIZE - 2 * PAD) // 2 - 20
    # arc 30° to 330° (open at right)
    d.arc((cx - r, cy - r, cx + r, cy + r), start=300, end=240, fill=SYM, width=28)
    # arrow head at end of arc (around angle 240°)
    import math
    ang = math.radians(240)
    px = cx + int(r * math.cos(ang))
    py = cy + int(r * math.sin(ang))
    d.polygon([
        (px - 30, py - 50),
        (px + 30, py - 50),
        (px, py + 30),
    ], fill=SYM)


def sym_check(d):
    cx, cy = CENTER
    d.line((cx - 110, cy + 10, cx - 30, cy + 80), fill=SYM, width=42)
    d.line((cx - 30, cy + 80, cx + 130, cy - 80), fill=SYM, width=42)


def sym_question(d):
    # Drawn as arc + dot (no font dependency).
    cx, cy = CENTER
    r = 90
    d.arc((cx - r, cy - r - 60, cx + r, cy + r - 60), start=180, end=20, fill=SYM, width=36)
    d.line((cx + r - 20, cy - 30, cx, cy + 40), fill=SYM, width=36)
    d.ellipse((cx - 28, cy + 90, cx + 28, cy + 146), fill=SYM)


def sym_gear(d):
    import math
    cx, cy = CENTER
    r_out = (SIZE - 2 * PAD) // 2 - 20
    r_in = int(r_out * 0.62)
    teeth = 10
    pts = []
    for i in range(teeth * 2):
        ang = i * math.pi / teeth
        r = r_out if i % 2 == 0 else r_in
        # widen each tooth by drawing two points per "out" position
        pts.append((cx + int(r * math.cos(ang)), cy + int(r * math.sin(ang))))
    d.polygon(pts, fill=SYM)
    bg = d.im.getpixel((10, 10))[:3] + (255,)
    # center hole
    h = int(r_out * 0.42)
    d.ellipse((cx - h, cy - h, cx + h, cy + h), fill=bg)


def sym_back(d):
    cx, cy = CENTER
    # left-pointing triangle + horizontal bar = "previous/back"
    d.polygon([(cx - 130, cy), (cx + 30, cy - 110), (cx + 30, cy + 110)], fill=SYM)
    d.rectangle((cx + 30, cy - 50, cx + 130, cy + 50), fill=SYM)


def sym_arrow_right(d):
    cx, cy = CENTER
    d.polygon([(cx + 130, cy), (cx - 30, cy - 110), (cx - 30, cy + 110)], fill=SYM)
    d.rectangle((cx - 130, cy - 50, cx - 30, cy + 50), fill=SYM)


# ---- icon catalog ---------------------------------------------------------

ICONS = [
    # (filename, bg color, symbol drawer)
    ("estrenos.png",       "#ff6b35", sym_star),       # vibrant orange
    ("movie.png",          "#c0392b", sym_clapper),    # cinema red
    ("tvshow.png",         "#2980b9", sym_tv),         # tv blue
    ("documentary.png",    "#27ae60", sym_book),       # doc green
    ("search.png",         "#8e44ad", sym_search),     # purple
    ("refresh.png",        "#16a085", sym_refresh),    # teal
    ("diagnose.png",       "#d4ac0d", sym_check),      # amber
    ("help.png",           "#3498db", sym_question),   # light blue
    ("settings.png",       "#7f8c8d", sym_gear),       # gray
    ("next_page.png",      "#34495e", sym_arrow_right),# slate
]


def make_icon(filename, bg_hex, draw_symbol):
    img = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    disc(d, _hex(bg_hex))
    draw_symbol(d)
    img.save(os.path.join(OUT, filename), "PNG", optimize=True)


def make_addon_icon():
    """Bigger, more striking addon icon (the one shown in Kodi's complement
    list). Uses the cinema red disc with a clapper symbol over a dark ring."""
    img = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse((0, 0, SIZE, SIZE), fill=_hex("#c0392b"))
    d.ellipse((16, 16, SIZE - 16, SIZE - 16), outline=(255, 255, 255, 60), width=8)
    sym_clapper(d)
    img.save(os.path.join(ROOT, "plugin.video.dontorrent", "icon.png"), "PNG", optimize=True)
    img.save(os.path.join(ROOT, "repository.dontorrent", "icon.png"), "PNG", optimize=True)


def make_fanart():
    """Subtle dark-red gradient fanart so the addon doesn't reuse a generic photo."""
    w, h = 1920, 1080
    img = Image.new("RGB", (w, h), (40, 0, 0))
    px = img.load()
    for y in range(h):
        # vertical gradient: deep red at top to near-black at bottom
        t = y / h
        r = int(80 * (1 - t) + 12 * t)
        g = int(10 * (1 - t) + 2 * t)
        b = int(15 * (1 - t) + 4 * t)
        for x in range(w):
            # add radial vignette
            dx = (x - w / 2) / (w / 2)
            dy = (y - h / 2) / (h / 2)
            v = max(0.3, 1 - (dx * dx + dy * dy) * 0.6)
            px[x, y] = (int(r * v), int(g * v), int(b * v))
    # central glow disc
    od = ImageDraw.Draw(img, "RGBA")
    od.ellipse((w // 2 - 260, h // 2 - 260, w // 2 + 260, h // 2 + 260),
               fill=(192, 57, 43, 90))
    img.save(os.path.join(ROOT, "plugin.video.dontorrent", "fanart.jpg"),
             "JPEG", quality=82, optimize=True)
    img.save(os.path.join(ROOT, "repository.dontorrent", "fanart.jpg"),
             "JPEG", quality=82, optimize=True)


def main():
    for fn, bg, sym in ICONS:
        make_icon(fn, bg, sym)
        print(" .", fn)
    make_addon_icon()
    print(" . icon.png (addon + repo)")
    make_fanart()
    print(" . fanart.jpg (addon + repo)")


if __name__ == "__main__":
    main()
