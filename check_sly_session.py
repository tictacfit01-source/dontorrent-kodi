"""Comprueba si hay sesion activa de Series.ly en Chrome via CDP."""
import os
import sys
import re
import json
import socket
import struct
import subprocess
import time
import base64
import requests

CDP_PORT = 9223  # Puerto diferente para no conflictar

def _find_chrome():
    paths = [
        os.path.join(os.environ.get("PROGRAMFILES", ""), "Google", "Chrome", "Application", "chrome.exe"),
        os.path.join(os.environ.get("PROGRAMFILES(X86)", ""), "Google", "Chrome", "Application", "chrome.exe"),
        os.path.join(os.environ.get("LOCALAPPDATA", ""), "Google", "Chrome", "Application", "chrome.exe"),
    ]
    for p in paths:
        if os.path.exists(p):
            return p
    return None

def _ws_connect(host, port, path):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(15)
    s.connect((host, port))
    key = base64.b64encode(os.urandom(16)).decode()
    req = (f"GET {path} HTTP/1.1\r\n"
           f"Host: {host}:{port}\r\n"
           f"Upgrade: websocket\r\nConnection: Upgrade\r\n"
           f"Sec-WebSocket-Key: {key}\r\n"
           f"Sec-WebSocket-Version: 13\r\n\r\n")
    s.sendall(req.encode())
    resp = b""
    while b"\r\n\r\n" not in resp:
        resp += s.recv(4096)
    return s

def _ws_send(sock, data):
    payload = json.dumps(data).encode()
    frame = bytearray()
    frame.append(0x81)
    l = len(payload)
    mask_key = os.urandom(4)
    if l < 126:
        frame.append(0x80 | l)
    elif l < 65536:
        frame.append(0x80 | 126)
        frame.extend(struct.pack(">H", l))
    else:
        frame.append(0x80 | 127)
        frame.extend(struct.pack(">Q", l))
    frame.extend(mask_key)
    masked = bytearray(b ^ mask_key[i % 4] for i, b in enumerate(payload))
    frame.extend(masked)
    sock.sendall(frame)

def _ws_recv(sock, timeout=10):
    sock.settimeout(timeout)
    data = sock.recv(2)
    if len(data) < 2:
        return None
    opcode = data[0] & 0x0F
    masked = bool(data[1] & 0x80)
    length = data[1] & 0x7F
    if length == 126:
        length = struct.unpack(">H", sock.recv(2))[0]
    elif length == 127:
        length = struct.unpack(">Q", sock.recv(8))[0]
    if masked:
        mask = sock.recv(4)
    payload = b""
    while len(payload) < length:
        payload += sock.recv(length - len(payload))
    if masked:
        payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
    if opcode == 0x01:
        return json.loads(payload.decode())
    return None

_msg_id = 0
def _cdp(sock, method, params=None, timeout=10):
    global _msg_id
    _msg_id += 1
    mid = _msg_id
    msg = {"id": mid, "method": method}
    if params:
        msg["params"] = params
    _ws_send(sock, msg)
    end = time.time() + timeout
    while time.time() < end:
        r = _ws_recv(sock, timeout=max(1, end - time.time()))
        if r is None:
            continue
        if "id" not in r:
            continue
        if r["id"] == mid:
            return r.get("result", {})
    return {}

chrome_exe = _find_chrome()
if not chrome_exe:
    print("[!] Chrome no encontrado")
    sys.exit(1)

user_data = os.path.join(os.environ.get("LOCALAPPDATA", ""), "Google", "Chrome", "User Data")

# Cerrar Chrome
was_running = "chrome.exe" in subprocess.run(
    ["tasklist", "/FI", "IMAGENAME eq chrome.exe", "/NH"],
    capture_output=True, text=True).stdout.lower()

if was_running:
    print("[*] Cerrando Chrome suavemente (para guardar cookies en disco)...")
    # Cierre suave: sin /f, Chrome guarda estado
    subprocess.run(["taskkill", "/im", "chrome.exe"], capture_output=True, timeout=10)
    for _ in range(16):
        time.sleep(0.5)
        if "chrome.exe" not in subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq chrome.exe", "/NH"],
            capture_output=True, text=True).stdout.lower():
            print("    [OK] Cerrado suavemente")
            break
    else:
        print("    [*] Forzando cierre...")
        subprocess.run(["taskkill", "/f", "/im", "chrome.exe"], capture_output=True, timeout=10)
        for _ in range(10):
            time.sleep(0.5)
            if "chrome.exe" not in subprocess.run(
                ["tasklist", "/FI", "IMAGENAME eq chrome.exe", "/NH"],
                capture_output=True, text=True).stdout.lower():
                break
    time.sleep(1)

# Junction
import tempfile
junction = os.path.join(tempfile.gettempdir(), "chrome_debug_check")
if os.path.exists(junction):
    subprocess.run(["cmd", "/c", "rmdir", junction], capture_output=True)
subprocess.run(["cmd", "/c", "mklink", "/J", junction, user_data], capture_output=True)

# Headless
print("[*] Abriendo headless Chrome...")
proc = subprocess.Popen([
    chrome_exe, "--headless=new",
    f"--remote-debugging-port={CDP_PORT}",
    "--remote-allow-origins=*", "--disable-gpu", "--no-first-run",
    f"--user-data-dir={junction}",
])

# Wait for CDP
for _ in range(40):
    try:
        r = requests.get(f"http://127.0.0.1:{CDP_PORT}/json/version", timeout=2)
        if r.status_code == 200:
            break
    except:
        pass
    time.sleep(0.5)
else:
    print("[!] CDP no disponible")
    subprocess.run(["taskkill", "/f", "/im", "chrome.exe"], capture_output=True)
    subprocess.run(["cmd", "/c", "rmdir", junction], capture_output=True)
    sys.exit(1)

print("[OK] CDP disponible")

# Create page and connect
new_tab = requests.put(f"http://127.0.0.1:{CDP_PORT}/json/new?about:blank", timeout=5).json()
page_ws_url = new_tab.get("webSocketDebuggerUrl", "")
page_id = new_tab.get("id", "")
ws_path = "/" + page_ws_url.split("/", 3)[-1]
ws = _ws_connect("127.0.0.1", CDP_PORT, ws_path)

_cdp(ws, "Network.enable")
_cdp(ws, "Page.enable")

# Navigate to series.ly
print("[*] Navegando a series.ly...")
_cdp(ws, "Page.navigate", {"url": "https://series.ly"})
time.sleep(5)

# Read cookies
result = _cdp(ws, "Network.getCookies", {"urls": ["https://series.ly", "https://www.series.ly"]})
cookies = result.get("cookies", [])
print(f"[*] {len(cookies)} cookies:")
for c in cookies:
    name = c.get("name", "")
    val = c.get("value", "")
    exp = c.get("expires", 0)
    print(f"    {name}: {val[:40]}... (expires: {exp})")

session_cookie = None
for c in cookies:
    if c.get("name") == "seriesly_session":
        session_cookie = c.get("value", "")
        break

# Get page HTML to check logged-in status
print("\n[*] Comprobando estado de sesion...")
html_result = _cdp(ws, "Runtime.evaluate", {
    "expression": "document.documentElement.outerHTML.substring(0, 10000)"
})
html = html_result.get("result", {}).get("value", "")

# Check for login indicators
logged_in = False
indicators = ["cerrar-sesion", "logout", "mi-perfil", "perfil", "/usuario/"]
for ind in indicators:
    if ind in html.lower():
        logged_in = True
        print(f"  [OK] Encontrado indicador de login: '{ind}'")
        break

if not logged_in:
    if "ingresar" in html.lower() or "registrar" in html.lower():
        print("  [!] NO LOGUEADO - se ve 'ingresar'/'registrar'")
    else:
        print("  [?] Estado incierto")

# Check the username/email on page
m = re.search(r'(mi.cuenta|perfil|usuario)', html.lower())
if m:
    print(f"  Encontrado: {m.group()}")

# Show relevant HTML snippet
for line in html.split("\n"):
    ll = line.lower()
    if any(x in ll for x in ["ingresar", "registro", "logout", "perfil", "usuario", "cerrar"]):
        print(f"  HTML: {line.strip()[:120]}")

# Cleanup
print("\n[*] Limpiando...")
ws.close()
requests.get(f"http://127.0.0.1:{CDP_PORT}/json/close/{page_id}", timeout=3)
subprocess.run(["taskkill", "/f", "/im", "chrome.exe"], capture_output=True)
time.sleep(2)
subprocess.run(["cmd", "/c", "rmdir", junction], capture_output=True)

if was_running:
    print("[*] Reabriendo Chrome...")
    subprocess.Popen([chrome_exe, "--restore-last-session"],
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

if session_cookie and logged_in:
    print(f"\n[OK] SESION ACTIVA - cookie: {session_cookie[:50]}...")
    print(f"     ({len(session_cookie)} chars)")
elif session_cookie and not logged_in:
    print(f"\n[!] Cookie existe ({len(session_cookie)} chars) pero NO esta logueado")
    print("    Necesitas hacer login en series.ly en Chrome primero.")
else:
    print("\n[!] No hay cookie seriesly_session")
    print("    Necesitas hacer login en series.ly en Chrome primero.")
