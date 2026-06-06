"""Genera icono y fanart con estetica 80s synthwave/neon para MejorWolf.

Concepto:
  - fondo: gradiente magenta -> purpura -> azul medianoche (sunset 80s)
  - sol/disco con bandas horizontales (sun-stripes)
  - rejilla en perspectiva (gridlines hacia el horizonte)
  - silueta de lobo aullando contra el sol
  - texto 'MEJORWOLF' en chrome cyan con glow
"""
from PIL import Image, ImageDraw, ImageFont, ImageFilter
import math, os

OUT_DIR = "plugin.video.mejorwolf"

# === Paleta 80s neon ===
NEON_PINK   = (255, 56, 200)
NEON_MAG    = (216, 39, 152)
NEON_PURP   = (140, 38, 200)
DEEP_PURP   = (62, 18, 110)
NIGHT_BLUE  = (24, 14, 60)
SUN_YELLOW  = (255, 224, 96)
SUN_ORANGE  = (255, 110, 80)
NEON_CYAN   = (88, 244, 255)
WOLF_BLACK  = (8, 4, 22)


def _gradient(size, top, bottom):
    img = Image.new("RGB", size, top)
    px = img.load()
    w, h = size
    for y in range(h):
        t = y / max(1, h - 1)
        r = int(top[0] * (1 - t) + bottom[0] * t)
        g = int(top[1] * (1 - t) + bottom[1] * t)
        b = int(top[2] * (1 - t) + bottom[2] * t)
        for x in range(w):
            px[x, y] = (r, g, b)
    return img


def _sun(size, cx, cy, radius, top_color, bot_color, n_stripes=7):
    """Disco con bandas horizontales 80s. Devuelve imagen RGBA del sol solo."""
    img = Image.new("RGBA", size, (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    bbox = (cx - radius, cy - radius, cx + radius, cy + radius)
    # Mascara circular
    mask = Image.new("L", size, 0)
    mdr = ImageDraw.Draw(mask)
    mdr.ellipse(bbox, fill=255)
    # Gradiente vertical dentro del sol
    px = img.load()
    for y in range(cy - radius, cy + radius + 1):
        if y < 0 or y >= size[1]:
            continue
        t = (y - (cy - radius)) / (2 * radius)
        r = int(top_color[0] * (1 - t) + bot_color[0] * t)
        g = int(top_color[1] * (1 - t) + bot_color[1] * t)
        b = int(top_color[2] * (1 - t) + bot_color[2] * t)
        for x in range(cx - radius, cx + radius + 1):
            if 0 <= x < size[0]:
                px[x, y] = (r, g, b, 255)
    img.putalpha(mask)
    # Bandas horizontales (cortar tiras transparentes en el sol)
    band_draw = ImageDraw.Draw(img)
    # Bandas en la mitad inferior, separacion creciente
    half_h = radius
    for i in range(n_stripes):
        # progresion exponencial: bandas mas anchas abajo
        frac_top = i / n_stripes
        frac_bot = (i + 0.55) / n_stripes
        y0 = cy + int(half_h * frac_top * frac_top)
        y1 = cy + int(half_h * frac_bot * frac_bot)
        if y1 <= y0:
            continue
        band_draw.rectangle([cx - radius - 5, y0, cx + radius + 5, y1],
                            fill=(0, 0, 0, 0))
    return img


def _grid(size, horizon_y, color, n_rows=12, n_cols=22):
    """Rejilla en perspectiva sobre el suelo (debajo del horizonte)."""
    img = Image.new("RGBA", size, (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    w, h = size
    cx = w // 2
    # Lineas horizontales: espaciado exponencial (cerca = mas separacion)
    for i in range(1, n_rows + 1):
        t = (i / n_rows) ** 1.7
        y = int(horizon_y + (h - horizon_y) * t)
        if y >= h:
            break
        # Atenuar lejos
        alpha = int(255 * min(1.0, 0.30 + 0.70 * t))
        d.line([(0, y), (w, y)], fill=color + (alpha,), width=2)
    # Lineas radiales que convergen al centro del horizonte
    for i in range(-n_cols // 2, n_cols // 2 + 1):
        x_bottom = cx + i * (w // (n_cols // 2))
        d.line([(cx, horizon_y), (x_bottom, h)],
               fill=color + (180,), width=2)
    return img


def _wolf_silhouette(size):
    """CABEZA DE LOBO en perfil mirando a la izquierda, con hocico largo,
    orejas puntiagudas y pelo del cuello. Mas reconocible que silueta
    completa del cuerpo. Coordenadas (0..1) recorridas en sentido horario
    desde la PUNTA DEL HOCICO (izquierda).
    """
    img = Image.new("RGBA", size, (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    w, h = size
    # Cabeza de lobo, perfil hacia la izquierda. La punta del hocico mira
    # a izquierda; las orejas y pelo de la nuca a la derecha-arriba; el
    # cuello con pelaje denso baja hacia abajo-derecha.
    pts = [
        # Punta del hocico (izquierda, abajo del centro)
        (0.05, 0.55),
        # Borde superior del hocico hacia la frente
        (0.10, 0.50),
        (0.18, 0.46),
        (0.26, 0.43),
        (0.32, 0.38),  # caballete nasal
        (0.36, 0.30),  # frente
        # Oreja izquierda (delantera, puntiaguda)
        (0.40, 0.20),
        (0.46, 0.05),  # punta oreja izda
        (0.52, 0.18),  # base interior
        # Hueco entre orejas
        (0.56, 0.22),
        # Oreja derecha (trasera)
        (0.62, 0.05),  # punta oreja dcha
        (0.70, 0.20),  # base
        # Nuca / pelo erizado
        (0.74, 0.30),
        (0.80, 0.42),  # cresta de pelo
        (0.86, 0.55),
        (0.92, 0.68),  # max ancho del cuello (pelo)
        (0.88, 0.78),
        (0.82, 0.86),
        (0.74, 0.94),  # base cuello derecha
        # Borde inferior del cuello/pecho
        (0.62, 0.96),
        (0.50, 0.94),
        (0.38, 0.90),
        (0.30, 0.84),
        (0.24, 0.78),  # garganta baja
        (0.20, 0.74),
        # Mandibula inferior subiendo hacia el hocico
        (0.18, 0.70),
        (0.14, 0.66),  # comisura
        (0.10, 0.62),  # base mandibula
        (0.07, 0.59),  # cierre boca (linea inferior hocico)
    ]
    poly = [(int(x * w), int(y * h)) for x, y in pts]
    d.polygon(poly, fill=WOLF_BLACK + (255,))
    # Ojo (cyan brillante)
    eye_x = int(0.30 * w); eye_y = int(0.40 * h)
    r_eye = max(3, w // 50)
    d.ellipse([eye_x - r_eye, eye_y - r_eye, eye_x + r_eye, eye_y + r_eye],
              fill=NEON_CYAN + (255,))
    # Nariz (punto mas oscuro en la punta del hocico)
    nose_x = int(0.07 * w); nose_y = int(0.55 * h)
    r_nose = max(3, w // 45)
    d.ellipse([nose_x - r_nose, nose_y - r_nose,
               nose_x + r_nose, nose_y + r_nose],
              fill=(0, 0, 0, 255))
    return img


def _glow_text(text, size, font_size, color, glow_color, stroke=2):
    img = Image.new("RGBA", size, (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    # Cargar fuente — fallback a default si falla
    font = None
    for name in ("arialbd.ttf", "arial.ttf", "DejaVuSans-Bold.ttf",
                 "C:/Windows/Fonts/arialbd.ttf",
                 "C:/Windows/Fonts/Impact.ttf"):
        try:
            font = ImageFont.truetype(name, font_size)
            break
        except Exception:
            continue
    if font is None:
        font = ImageFont.load_default()
    # Centrar
    bb = d.textbbox((0, 0), text, font=font)
    tw = bb[2] - bb[0]; th = bb[3] - bb[1]
    x = (size[0] - tw) // 2 - bb[0]
    y = (size[1] - th) // 2 - bb[1]
    # Glow: dibujar varias copias borrosas
    glow_layer = Image.new("RGBA", size, (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow_layer)
    gd.text((x, y), text, font=font, fill=glow_color + (255,))
    glow_layer = glow_layer.filter(ImageFilter.GaussianBlur(radius=stroke * 4))
    img = Image.alpha_composite(img, glow_layer)
    # Texto principal
    d2 = ImageDraw.Draw(img)
    # Outline oscuro para legibilidad
    for dx in (-1, 0, 1):
        for dy in (-1, 0, 1):
            if dx or dy:
                d2.text((x + dx, y + dy), text, font=font,
                        fill=(0, 0, 0, 200))
    d2.text((x, y), text, font=font, fill=color + (255,))
    return img


def make_icon(size=512):
    """Compone el icono: gradiente + sol + grid + lobo + texto."""
    s = (size, size)
    horizon_y = int(size * 0.62)
    # Fondo gradiente: de magenta arriba a azul medianoche abajo
    bg = _gradient(s, NEON_MAG, NIGHT_BLUE).convert("RGBA")
    # Sol con bandas en el horizonte
    sun_r = int(size * 0.32)
    sun_cx = size // 2
    sun_cy = int(horizon_y - sun_r * 0.20)  # asomando sobre la linea
    sun = _sun(s, sun_cx, sun_cy, sun_r, SUN_YELLOW, SUN_ORANGE,
               n_stripes=7)
    # Glow del sol
    glow = sun.copy().filter(ImageFilter.GaussianBlur(radius=size // 30))
    bg = Image.alpha_composite(bg, glow)
    bg = Image.alpha_composite(bg, sun)
    # Rejilla en el suelo
    grid = _grid(s, horizon_y, NEON_PINK, n_rows=12, n_cols=22)
    bg = Image.alpha_composite(bg, grid)
    # Cabeza de lobo en perfil — centrada delante del sol, ligeramente
    # mas grande para que sea el foco
    wolf_w = int(size * 0.62)
    wolf_h = int(size * 0.62)
    wolf = _wolf_silhouette((wolf_w, wolf_h))
    # Glow cyan detras del lobo
    wolf_glow = wolf.copy()
    # Tintar negro -> cyan
    px = wolf_glow.load()
    for yy in range(wolf_h):
        for xx in range(wolf_w):
            r, g, b, a = px[xx, yy]
            if a > 0:
                px[xx, yy] = NEON_CYAN + (a,)
    wolf_glow = wolf_glow.filter(ImageFilter.GaussianBlur(radius=size // 50))
    pos = ((size - wolf_w) // 2, int(size * 0.28))
    canvas = Image.new("RGBA", s, (0, 0, 0, 0))
    canvas.paste(wolf_glow, pos, wolf_glow)
    canvas.paste(wolf, pos, wolf)
    bg = Image.alpha_composite(bg, canvas)
    # Texto MEJORWOLF en la parte de arriba con glow cyan
    txt = _glow_text("MEJORWOLF", s, font_size=int(size * 0.13),
                     color=NEON_CYAN, glow_color=NEON_PINK, stroke=2)
    # Mover texto a la parte superior
    txt_top = Image.new("RGBA", s, (0, 0, 0, 0))
    # extraer la mitad superior de txt (que ya esta centrado vertical)
    tx_crop = txt.crop((0, 0, size, size))
    # Desplazar arriba
    offset = -int(size * 0.32)
    shifted = Image.new("RGBA", s, (0, 0, 0, 0))
    shifted.paste(tx_crop, (0, offset), tx_crop)
    bg = Image.alpha_composite(bg, shifted)
    return bg


def make_fanart(w=1280, h=720):
    """Fanart 16:9 con la misma estetica."""
    s = (w, h)
    horizon_y = int(h * 0.58)
    bg = _gradient(s, NEON_MAG, NIGHT_BLUE).convert("RGBA")
    sun_r = int(h * 0.40)
    sun = _sun(s, w // 2, int(horizon_y - sun_r * 0.15),
               sun_r, SUN_YELLOW, SUN_ORANGE, n_stripes=8)
    glow = sun.copy().filter(ImageFilter.GaussianBlur(radius=h // 25))
    bg = Image.alpha_composite(bg, glow)
    bg = Image.alpha_composite(bg, sun)
    grid = _grid(s, horizon_y, NEON_PINK, n_rows=14, n_cols=30)
    bg = Image.alpha_composite(bg, grid)
    # Lobo a la izquierda
    wolf_w = int(h * 0.70); wolf_h = int(h * 0.70)
    wolf = _wolf_silhouette((wolf_w, wolf_h))
    wolf_glow = wolf.copy()
    px = wolf_glow.load()
    for yy in range(wolf_h):
        for xx in range(wolf_w):
            r, g, b, a = px[xx, yy]
            if a > 0:
                px[xx, yy] = NEON_CYAN + (a,)
    wolf_glow = wolf_glow.filter(ImageFilter.GaussianBlur(radius=h // 35))
    pos = (int(w * 0.06), int(h * 0.25))
    canvas = Image.new("RGBA", s, (0, 0, 0, 0))
    canvas.paste(wolf_glow, pos, wolf_glow)
    canvas.paste(wolf, pos, wolf)
    bg = Image.alpha_composite(bg, canvas)
    # Texto a la derecha
    txt_layer = Image.new("RGBA", s, (0, 0, 0, 0))
    big = _glow_text("MEJORWOLF", s, font_size=int(h * 0.15),
                     color=NEON_CYAN, glow_color=NEON_PINK, stroke=2)
    bg = Image.alpha_composite(bg, big)
    return bg.convert("RGB")


if __name__ == "__main__":
    icon = make_icon(512)
    icon.save(os.path.join(OUT_DIR, "icon.png"))
    print("icon.png:", icon.size)
    fanart = make_fanart(1280, 720)
    fanart.save(os.path.join(OUT_DIR, "fanart.jpg"), quality=92)
    print("fanart.jpg:", fanart.size)
