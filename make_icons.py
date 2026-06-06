"""Create minimal PNG icons for the Chrome extension."""
import struct, zlib, os

def create_png(size, filename):
    r, g, b = 0, 180, 180  # Teal color
    raw = b""
    for y in range(size):
        raw += b"\x00"
        for x in range(size):
            dx = x - size // 2
            dy = y - size // 2
            dist = (dx * dx + dy * dy) ** 0.5
            if dist < size * 0.4:
                raw += bytes([r, g, b, 255])
            elif dist < size * 0.45:
                raw += bytes([r // 2, g // 2, b // 2, 255])
            else:
                raw += bytes([0, 0, 0, 0])

    def chunk(ctype, data):
        c = ctype + data
        return struct.pack(">I", len(data)) + c + struct.pack(">I", zlib.crc32(c) & 0xFFFFFFFF)

    ihdr = struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)
    with open(filename, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n")
        f.write(chunk(b"IHDR", ihdr))
        f.write(chunk(b"IDAT", zlib.compress(raw)))
        f.write(chunk(b"IEND", b""))

base = r"C:\Users\israe\Desktop\Nueva App Kodi\chrome_extension_sly"
create_png(48, os.path.join(base, "icon48.png"))
create_png(128, os.path.join(base, "icon128.png"))
print("Icons created OK")
