"""
sync_sly_cookie.py — Sincroniza cookie de Series.ly a Supabase.

Ejecutar en el PC despues de hacer login en series.ly con Chrome.
El addon en el Android TV Box leera la cookie de Supabase automaticamente.

Uso:
    1. Abre Chrome y logea en https://series.ly
    2. Ejecuta: python sync_sly_cookie.py
    3. Listo — el addon en el TV Box usara la sesion automaticamente.

Requisitos:
    pip install requests

Metodo:
    Cierra Chrome, abre una instancia headless temporal con el mismo perfil,
    y usa Chrome DevTools Protocol (CDP) para leer las cookies en texto plano.
    Esto evita la encriptacion App-Bound (v20) de Chrome 127+.
    Luego cierra la instancia headless y reabre Chrome normalmente.
"""

import os
import sys
import json
import socket
import struct
import subprocess
import time
import base64
import requests

# --- Supabase config (misma que el addon) ---
SUPABASE_URL = "https://yddgjpjyldgvuswcsxci.supabase.co"
SUPABASE_ANON_KEY = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
    "eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InlkZGdqcGp5bGRndnVzd2NzeGNpIiwi"
    "cm9sZSI6ImFub24iLCJpYXQiOjE3NzgyNTIwMzAsImV4cCI6MjA5MzgyODAzMH0."
    "bpIkjXUowHhhJKz_HVFkGj1WogD5dpyi_JGL2yLOYl0"
)

CDP_PORT = 9222


# ===================================================================
# Mini WebSocket client (solo stdlib, sin dependencias externas)
# ===================================================================

def _ws_connect(host, port, path):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(15)
    s.connect((host, port))
    key_b64 = base64.b64encode(os.urandom(16)).decode()
    handshake = (
        f"GET {path} HTTP/1.1\r\n"
        f"Host: {host}:{port}\r\n"
        f"Upgrade: websocket\r\n"
        f"Connection: Upgrade\r\n"
        f"Sec-WebSocket-Key: {key_b64}\r\n"
        f"Sec-WebSocket-Version: 13\r\n"
        f"\r\n"
    )
    s.sendall(handshake.encode())
    resp = b""
    while b"\r\n\r\n" not in resp:
        chunk = s.recv(4096)
        if not chunk:
            raise ConnectionError("WS handshake: connection closed")
        resp += chunk
    if b"101" not in resp.split(b"\r\n")[0]:
        raise ConnectionError(f"WS rejected: {resp[:200]}")
    return s


def _ws_send(sock, data):
    payload = data.encode("utf-8")
    frame = bytearray([0x81])
    mask = os.urandom(4)
    length = len(payload)
    if length < 126:
        frame.append(0x80 | length)
    elif length < 65536:
        frame.append(0x80 | 126)
        frame.extend(struct.pack(">H", length))
    else:
        frame.append(0x80 | 127)
        frame.extend(struct.pack(">Q", length))
    frame.extend(mask)
    frame.extend(bytearray(b ^ mask[i % 4] for i, b in enumerate(payload)))
    sock.sendall(bytes(frame))


def _ws_recv(sock):
    def _rx(sk, n):
        buf = b""
        while len(buf) < n:
            chunk = sk.recv(n - len(buf))
            if not chunk:
                raise ConnectionError("WS: connection closed")
            buf += chunk
        return buf

    hdr = _rx(sock, 2)
    masked = bool(hdr[1] & 0x80)
    length = hdr[1] & 0x7F
    if length == 126:
        length = struct.unpack(">H", _rx(sock, 2))[0]
    elif length == 127:
        length = struct.unpack(">Q", _rx(sock, 8))[0]
    mk = _rx(sock, 4) if masked else None
    data = _rx(sock, length)
    if masked:
        data = bytearray(b ^ mk[i % 4] for i, b in enumerate(data))
    return data.decode("utf-8", errors="replace")


_msg_id = 0


def _cdp(sock, method, params=None, timeout_sec=30):
    """Envia un comando CDP y espera la respuesta, ignorando eventos."""
    global _msg_id
    _msg_id += 1
    current_id = _msg_id
    msg = {"id": current_id, "method": method}
    if params:
        msg["params"] = params
    _ws_send(sock, json.dumps(msg))
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        try:
            raw = _ws_recv(sock)
        except socket.timeout:
            continue
        r = json.loads(raw)
        if "id" not in r:
            continue  # evento, ignorar
        if r["id"] == current_id:
            if "error" in r:
                raise RuntimeError(f"CDP: {r['error']}")
            return r.get("result", {})
    raise TimeoutError(f"CDP timeout: {method}")


# ===================================================================
# Chrome management
# ===================================================================

def _find_chrome_exe():
    for env_var in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
        base = os.environ.get(env_var, "")
        if base:
            p = os.path.join(base, "Google", "Chrome", "Application", "chrome.exe")
            if os.path.exists(p):
                return p
    return None


def _is_chrome_running():
    try:
        r = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq chrome.exe", "/NH"],
            capture_output=True, text=True, timeout=5)
        return "chrome.exe" in r.stdout.lower()
    except Exception:
        return False


def _kill_chrome(force=False):
    args = ["taskkill", "/im", "chrome.exe"]
    if force:
        args.insert(1, "/f")
    subprocess.run(args, capture_output=True, timeout=10)


def _close_chrome_gracefully():
    """Cierra Chrome suavemente para que guarde cookies en disco.

    Primero intenta cerrar las ventanas (WM_CLOSE), espera a que
    Chrome guarde estado, y solo usa force kill como ultimo recurso.
    """
    import ctypes

    # 1) Intentar cierre suave via taskkill SIN /f
    subprocess.run(["taskkill", "/im", "chrome.exe"],
                   capture_output=True, timeout=10)

    # Esperar hasta 8 seg a que cierre solo
    for i in range(16):
        time.sleep(0.5)
        if not _is_chrome_running():
            print("    [OK] Chrome cerrado suavemente (cookies guardadas)")
            return
    print("    [*] Chrome no cerro suavemente, forzando...")

    # 2) Forzar como fallback
    _kill_chrome(force=True)
    for _ in range(10):
        time.sleep(0.5)
        if not _is_chrome_running():
            break


def _wait_cdp(timeout=15):
    end = time.time() + timeout
    while time.time() < end:
        try:
            r = requests.get(f"http://127.0.0.1:{CDP_PORT}/json/version",
                             timeout=2)
            if r.status_code == 200:
                return True
        except Exception:
            pass
        time.sleep(0.5)
    return False


# ===================================================================
# Cookie extraction via CDP (headless Chrome)
# ===================================================================

def get_cookie_via_cdp(domain, cookie_name):
    """Extrae una cookie de Chrome usando CDP con una instancia headless temporal.

    Flujo:
      1. Cierra Chrome (si esta abierto).
      2. Abre Chrome headless con --remote-debugging-port y mismo perfil.
      3. Navega a series.ly para cargar las cookies del perfil.
      4. Lee las cookies via CDP.
      5. Cierra headless y reabre Chrome normalmente.
    """
    chrome_exe = _find_chrome_exe()
    if not chrome_exe:
        print("[!] Chrome no encontrado en el sistema")
        return None

    chrome_was_running = _is_chrome_running()
    user_data_dir = os.path.join(
        os.environ.get("LOCALAPPDATA", ""), "Google", "Chrome", "User Data"
    )

    # --- Paso 1: cerrar Chrome SUAVEMENTE para que guarde cookies ---
    if chrome_was_running:
        print("    [*] Cerrando Chrome suavemente...")
        print("        (Chrome guardara las cookies y restaurara pestanyas al reabrir)")
        _close_chrome_gracefully()
        time.sleep(1)  # margen extra para liberar locks de fichero

    # --- Paso 2: crear junction point para el perfil ---
    # Chrome 127+ bloquea --remote-debugging-port cuando se usa el
    # directorio de perfil por defecto. Un junction point (enlace de
    # directorio NTFS) apunta a la misma carpeta pero con ruta diferente,
    # lo que evita la comprobacion de Chrome.
    import tempfile
    junction_dir = os.path.join(tempfile.gettempdir(), "chrome_debug_profile")
    if os.path.exists(junction_dir):
        # Eliminar junction anterior (rmdir no borra el contenido real)
        subprocess.run(["cmd", "/c", "rmdir", junction_dir],
                       capture_output=True, timeout=5)
    subprocess.run(
        ["cmd", "/c", "mklink", "/J", junction_dir, user_data_dir],
        capture_output=True, timeout=5)

    # --- Paso 3: abrir headless Chrome con CDP ---
    print(f"    [*] Abriendo Chrome headless (debug port {CDP_PORT})...")
    headless_proc = subprocess.Popen(
        [chrome_exe,
         "--headless=new",
         f"--remote-debugging-port={CDP_PORT}",
         "--remote-allow-origins=*",
         "--disable-gpu",
         "--no-first-run",
         f"--user-data-dir={junction_dir}"],
    )

    if not _wait_cdp(timeout=20):
        print("[!] Chrome headless no respondio en 20 segundos")
        _kill_chrome(force=True)
        _cleanup_junction(junction_dir)
        if chrome_was_running:
            _restart_chrome_normal(chrome_exe)
        return None

    print("    [OK] CDP disponible")

    # --- Paso 3: conectar y leer cookies ---
    cookie_value = None
    try:
        cookie_value = _extract_cookie_cdp(domain, cookie_name)
    except Exception as e:
        print(f"[!] Error extrayendo cookie: {e}")

    # --- Paso 5: cerrar headless, limpiar junction, reabrir Chrome ---
    print("    [*] Cerrando Chrome headless...")
    _kill_chrome(force=True)
    time.sleep(2)
    _cleanup_junction(junction_dir)

    if chrome_was_running:
        _restart_chrome_normal(chrome_exe)

    return cookie_value


def _extract_cookie_cdp(domain, cookie_name):
    """Conecta al CDP headless, navega a la web, y lee las cookies."""
    # Crear un target (pagina)
    new_tab = requests.put(
        f"http://127.0.0.1:{CDP_PORT}/json/new?about:blank",
        timeout=5
    ).json()
    page_ws_url = new_tab.get("webSocketDebuggerUrl", "")
    page_id = new_tab.get("id", "")

    if not page_ws_url:
        raise RuntimeError("No se pudo crear target en CDP")

    # Conectar al websocket de la pagina
    ws_path = "/" + page_ws_url.split("/", 3)[-1]
    ws = _ws_connect("127.0.0.1", CDP_PORT, ws_path)

    try:
        # Habilitar dominios
        _cdp(ws, "Network.enable")
        _cdp(ws, "Page.enable")

        # Navegar a series.ly para que Chrome cargue las cookies del perfil
        print(f"    [*] Navegando a https://{domain}...")
        _cdp(ws, "Page.navigate", {"url": f"https://{domain}"})
        time.sleep(4)

        # Leer cookies
        result = _cdp(ws, "Network.getCookies",
                       {"urls": [f"https://{domain}",
                                 f"https://www.{domain}"]})
        cookies = result.get("cookies", [])
        print(f"    [*] {len(cookies)} cookies para {domain}")

        for c in cookies:
            if c.get("name") == cookie_name:
                val = c.get("value", "")
                print(f"    [OK] Cookie '{cookie_name}' encontrada "
                      f"({len(val)} chars)")
                return val

        # Si no se encontro, probar Network.getAllCookies
        try:
            result2 = _cdp(ws, "Network.getAllCookies")
            for c in result2.get("cookies", []):
                if (c.get("name") == cookie_name and
                        domain in c.get("domain", "")):
                    val = c.get("value", "")
                    print(f"    [OK] Cookie encontrada en getAllCookies "
                          f"({len(val)} chars)")
                    return val
        except Exception:
            pass

        print(f"    [!] Cookie '{cookie_name}' no encontrada en {domain}")
        return None

    finally:
        try:
            ws.close()
        except Exception:
            pass
        try:
            requests.get(
                f"http://127.0.0.1:{CDP_PORT}/json/close/{page_id}",
                timeout=3)
        except Exception:
            pass


def _cleanup_junction(junction_dir):
    """Elimina el junction point temporal (no borra datos reales)."""
    try:
        if os.path.exists(junction_dir):
            subprocess.run(["cmd", "/c", "rmdir", junction_dir],
                           capture_output=True, timeout=5)
    except Exception:
        pass


def _restart_chrome_normal(chrome_exe):
    """Reabre Chrome normalmente (restaura pestanyas)."""
    print("    [*] Reabriendo Chrome normalmente...")
    subprocess.Popen(
        [chrome_exe, "--restore-last-session"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


# ===================================================================
# Supabase upload
# ===================================================================

def push_to_supabase(cookie_value):
    """Sube la cookie a Supabase mw_config (key=seriesly_cookie).

    Intenta PATCH primero (si la fila ya existe), luego INSERT.
    El RLS permite PATCH (update) con anon key pero bloquea INSERT.
    """
    headers = {
        "apikey": SUPABASE_ANON_KEY,
        "Authorization": f"Bearer {SUPABASE_ANON_KEY}",
        "Content-Type": "application/json",
    }
    url = f"{SUPABASE_URL}/rest/v1/mw_config"

    # --- Intento 1: PATCH (actualizar fila existente) ---
    url_patch = f"{url}?key=eq.seriesly_cookie"
    r = requests.patch(
        url_patch, headers={**headers, "Prefer": "return=minimal"},
        json={"value": {"cookie": cookie_value}},
        timeout=10,
    )
    if r.status_code in (200, 204):
        print("[OK] Cookie actualizada en Supabase")
        return True

    # --- Intento 2: POST / UPSERT (crear fila nueva) ---
    r2 = requests.post(
        url, headers={**headers, "Prefer": "resolution=merge-duplicates"},
        json={"key": "seriesly_cookie", "value": {"cookie": cookie_value}},
        timeout=10,
    )
    if r2.status_code in (200, 201, 204):
        print("[OK] Cookie creada en Supabase")
        return True

    # --- Fallback: mostrar para copiar manualmente ---
    print(f"\n[ATENCION] No se pudo guardar en Supabase (HTTP {r2.status_code})")
    print("  Pega este valor en los ajustes del addon Kodi directamente:")
    print("  Ajustes > Series.ly > Cookie de sesion")
    print(f"\n  Cookie: {cookie_value}")
    print()
    print("  (O creala manualmente en Supabase: tabla mw_config,")
    print("   key=seriesly_cookie)")
    return False


# ===================================================================
# Main
# ===================================================================

def main():
    print("=" * 60)
    print("  Sincronizar cookie Series.ly -> Supabase")
    print("  (para usar en el addon MejorWolf del Android TV)")
    print("=" * 60)
    print()

    if sys.platform != "win32":
        print("[!] Este script solo funciona en Windows")
        print("    Alternativa: abre series.ly en Chrome, copia la cookie")
        print("    'seriesly_session' manualmente y pegala aqui:")
        cookie = input("\n> Cookie seriesly_session: ").strip()
        if not cookie:
            print("Cancelado.")
            return
    else:
        print("[*] Extrayendo cookie de Chrome via DevTools Protocol...")
        print("    Chrome se cerrara y reabrira automaticamente.")
        print()

        cookie = get_cookie_via_cdp("series.ly", "seriesly_session")

        if not cookie:
            print("\n[!] No se pudo leer la cookie automaticamente.")
            print("    Alternativa manual:")
            print("    1. Abre Chrome > series.ly (logueado)")
            print("    2. Pulsa F12 > Application > Cookies > series.ly")
            print("    3. Copia el valor de 'seriesly_session'")
            cookie = input("\n> Pega la cookie aqui: ").strip()
            if not cookie:
                print("Cancelado.")
                return

    print(f"\n[*] Cookie obtenida ({len(cookie)} chars)")
    print(f"    Inicio: {cookie[:40]}...")
    print()

    print("[*] Subiendo a Supabase...")
    ok = push_to_supabase(cookie)

    if ok:
        print("\n[LISTO] El addon en el Android TV Box usara esta sesion")
        print("        automaticamente la proxima vez que abras Series.ly.")

    print()
    input("Pulsa Enter para salir...")


if __name__ == "__main__":
    main()
