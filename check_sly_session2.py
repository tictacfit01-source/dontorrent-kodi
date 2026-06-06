"""Check v2: lee cookies SIN navegar (para no sobreescribir la sesion)."""
import os
import sys
import json
import socket
import struct
import subprocess
import time
import base64
import requests

CDP_PORT = 9223

def _find_chrome():
    for env in ["PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"]:
        base = os.environ.get(env, "")
        if base:
            p = os.path.join(base, "Google", "Chrome", "Application", "chrome.exe")
            if os.path.exists(p):
                return p
    return None

def _is_chrome_running():
    try:
        r = subprocess.run(["tasklist", "/FI", "IMAGENAME eq chrome.exe", "/NH"],
                           capture_output=True, text=True, timeout=5)
        return "chrome.exe" in r.stdout.lower()
    except:
        return False

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
    frame = bytearray([0x81])
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
    frame.extend(bytearray(b ^ mask_key[i % 4] for i, b in enumerate(payload)))
    sock.sendall(frame)

def _ws_recv(sock, timeout=10):
    sock.settimeout(timeout)
    data = sock.recv(2)
    if len(data) < 2:
        return None
    length = data[1] & 0x7F
    if length == 126:
        length = struct.unpack(">H", sock.recv(2))[0]
    elif length == 127:
        length = struct.unpack(">Q", sock.recv(8))[0]
    masked = bool(data[1] & 0x80)
    if masked:
        mask = sock.recv(4)
    payload = b""
    while len(payload) < length:
        payload += sock.recv(length - len(payload))
    if masked:
        payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
    if (data[0] & 0x0F) == 0x01:
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
        if r and "id" in r and r["id"] == mid:
            return r.get("result", {})
    return {}

chrome_exe = _find_chrome()
if not chrome_exe:
    print("[!] Chrome not found")
    sys.exit(1)

user_data = os.path.join(os.environ.get("LOCALAPPDATA", ""), "Google", "Chrome", "User Data")
was_running = _is_chrome_running()

if was_running:
    print("[*] Cerrando Chrome suavemente...")
    subprocess.run(["taskkill", "/im", "chrome.exe"], capture_output=True, timeout=10)
    for _ in range(16):
        time.sleep(0.5)
        if not _is_chrome_running():
            print("    [OK] Cerrado")
            break
    else:
        subprocess.run(["taskkill", "/f", "/im", "chrome.exe"], capture_output=True)
        time.sleep(2)
    time.sleep(1)

# Junction
import tempfile
junction = os.path.join(tempfile.gettempdir(), "chrome_debug_check")
if os.path.exists(junction):
    subprocess.run(["cmd", "/c", "rmdir", junction], capture_output=True)
subprocess.run(["cmd", "/c", "mklink", "/J", junction, user_data], capture_output=True)

print("[*] Abriendo headless Chrome...")
proc = subprocess.Popen([
    chrome_exe, "--headless=new",
    f"--remote-debugging-port={CDP_PORT}",
    "--remote-allow-origins=*", "--disable-gpu", "--no-first-run",
    f"--user-data-dir={junction}",
])

for _ in range(40):
    try:
        if requests.get(f"http://127.0.0.1:{CDP_PORT}/json/version", timeout=2).status_code == 200:
            break
    except:
        pass
    time.sleep(0.5)
else:
    print("[!] CDP timeout")
    subprocess.run(["taskkill", "/f", "/im", "chrome.exe"], capture_output=True)
    sys.exit(1)

# Connect to browser-level WS (not a page)
ver = requests.get(f"http://127.0.0.1:{CDP_PORT}/json/version", timeout=5).json()
browser_ws = ver.get("webSocketDebuggerUrl", "")
print(f"[OK] CDP disponible")

# Connect to browser WS
ws_path = "/" + browser_ws.split("/", 3)[-1]
ws = _ws_connect("127.0.0.1", CDP_PORT, ws_path)

# Use Storage.getCookies (browser-level) - reads from Chrome's storage
# without navigating to any page
print("\n[*] Leyendo cookies de Chrome SIN navegar (Storage.getCookies)...")

# Method 1: Try Storage.getCookies (Chrome 100+)
result = _cdp(ws, "Storage.getCookies", {"browserContextId": None})
if result and result.get("cookies"):
    cookies = result["cookies"]
    sly_cookies = [c for c in cookies if "series.ly" in c.get("domain", "")]
    print(f"    Total cookies en Chrome: {len(cookies)}")
    print(f"    Cookies de series.ly: {len(sly_cookies)}")
    for c in sly_cookies:
        print(f"      {c['name']}: {c['value'][:40]}... (domain={c['domain']})")
else:
    print("    Storage.getCookies no disponible, usando Network.getAllCookies en page target...")

    # Create page target (about:blank - no navigation to series.ly!)
    new_tab = requests.put(f"http://127.0.0.1:{CDP_PORT}/json/new?about:blank", timeout=5).json()
    page_ws_url = new_tab.get("webSocketDebuggerUrl", "")
    page_id = new_tab.get("id", "")

    ws.close()
    ws_path = "/" + page_ws_url.split("/", 3)[-1]
    ws = _ws_connect("127.0.0.1", CDP_PORT, ws_path)

    _cdp(ws, "Network.enable")

    # Network.getAllCookies returns ALL cookies from Chrome's jar
    result = _cdp(ws, "Network.getAllCookies")
    cookies = result.get("cookies", [])
    sly_cookies = [c for c in cookies if "series.ly" in c.get("domain", "")]

    print(f"    Total cookies en Chrome: {len(cookies)}")
    print(f"    Cookies de series.ly: {len(sly_cookies)}")
    for c in sly_cookies:
        print(f"      {c['name']}: {c['value'][:40]}... (domain={c['domain']}, expires={c.get('expires',0)})")

    # Check if this is an authenticated session by looking at cookie size/pattern
    for c in sly_cookies:
        if c["name"] == "seriesly_session":
            val = c["value"]
            print(f"\n    seriesly_session: {len(val)} chars")
            print(f"    Valor: {val[:60]}...")

    requests.get(f"http://127.0.0.1:{CDP_PORT}/json/close/{page_id}", timeout=3)

# Cleanup
print("\n[*] Limpiando...")
ws.close()
subprocess.run(["taskkill", "/f", "/im", "chrome.exe"], capture_output=True)
time.sleep(2)
subprocess.run(["cmd", "/c", "rmdir", junction], capture_output=True)

if was_running:
    print("[*] Reabriendo Chrome...")
    subprocess.Popen([chrome_exe, "--restore-last-session"],
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

print("\n[NOTA] La cookie se lee ANTES de navegar a series.ly.")
print("Si la cookie existe pero no funciona, puede que la sesion")
print("haya expirado en el servidor.")
