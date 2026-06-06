"""Convert the user's 80s desktop image into Kodi addon icon + fanart."""
from PIL import Image, ImageFilter
import os

SRC = r"C:\Users\israe\Desktop\Gemini_Generated_Image_u06n87u06n87u06n.clean.png"
DST_ICON = r"C:\Users\israe\Desktop\Nueva App Kodi\plugin.video.mejorwolf\icon.png"
DST_FAN  = r"C:\Users\israe\Desktop\Nueva App Kodi\plugin.video.mejorwolf\fanart.jpg"

img = Image.open(SRC).convert("RGB")
print(f"src size: {img.size}")

# --- Icon: square 512x512, fit (no crop), pad with sampled corner color ---
# The image is portrait. Resize so the longest side = 512, paste centered
# on a 512x512 background sampled from the top-left corner (deep blue).
TARGET = 512
w, h = img.size
scale = TARGET / max(w, h)
nw, nh = int(w*scale), int(h*scale)
resized = img.resize((nw, nh), Image.LANCZOS)
bg_color = img.getpixel((0, 0))
canvas = Image.new("RGB", (TARGET, TARGET), bg_color)
canvas.paste(resized, ((TARGET-nw)//2, (TARGET-nh)//2))
canvas.save(DST_ICON, "PNG", optimize=True)
print(f"icon saved: {DST_ICON}  ({canvas.size})")

# --- Fanart: 1280x720 with the icon centered + soft blurred extension ---
FW, FH = 1280, 720
# Background: stretch + blur the original to fill 1280x720
bg = img.resize((FW, FH), Image.LANCZOS).filter(ImageFilter.GaussianBlur(40))
# Darken a bit
from PIL import ImageEnhance
bg = ImageEnhance.Brightness(bg).enhance(0.55)
# Foreground: original at full height
fg_h = FH - 40
fg_w = int(w * (fg_h / h))
fg = img.resize((fg_w, fg_h), Image.LANCZOS)
bg.paste(fg, ((FW-fg_w)//2, (FH-fg_h)//2))
bg.save(DST_FAN, "JPEG", quality=88)
print(f"fanart saved: {DST_FAN}  ({bg.size})")
