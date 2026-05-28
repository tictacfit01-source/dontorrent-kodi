"""
Login de Series.ly via Android WebView — 100% automatico, 100% gratis.

Truco: WebView.loadDataWithBaseURL("https://series.ly", html, ...)
hace que Turnstile piense que esta en series.ly -> token valido -> login OK.
"""

import time

try:
    import xbmc
    import xbmcgui
    import xbmcaddon
    _KODI = True
except ImportError:
    _KODI = False

# Detectar Android + pyjnius
_ANDROID = False
_ANDROID_ERR = ""
try:
    from jnius import autoclass, PythonJavaClass, java_method  # type: ignore
    _ANDROID = True
except ImportError as e:
    _ANDROID_ERR = f"jnius no disponible: {e}"
except Exception as e:
    _ANDROID_ERR = f"jnius error: {e}"


def _log(msg):
    if _KODI:
        xbmc.log(f"[MejorWolf/WebView] {msg}", xbmc.LOGINFO)


def _notify(msg, error=False):
    if _KODI:
        xbmcgui.Dialog().notification(
            "Series.ly", msg[:80],
            xbmcgui.NOTIFICATION_ERROR if error else xbmcgui.NOTIFICATION_INFO,
            5000,
        )


def is_available():
    return _ANDROID and _KODI


def get_debug_info():
    info = f"KODI={_KODI}, ANDROID={_ANDROID}"
    if _ANDROID_ERR:
        info += f", ERR={_ANDROID_ERR}"
    import platform
    info += f", OS={platform.system()}"
    try:
        import subprocess
        r = subprocess.run(["getprop", "ro.build.version.release"],
                           capture_output=True, text=True, timeout=3)
        if r.returncode == 0 and r.stdout.strip():
            info += f", AndroidVer={r.stdout.strip()}"
    except Exception:
        pass
    return info


def _get_proxy_base():
    if not _KODI:
        return "https://mw-relay.israeldm93.workers.dev"
    a = xbmcaddon.Addon()
    raw = (a.getSetting("proxy_url") or "").strip().rstrip("/")
    return raw or "https://mw-relay.israeldm93.workers.dev"


def _get_credentials():
    if not _KODI:
        return "", ""
    a = xbmcaddon.Addon()
    return (
        (a.getSetting("seriesly_email") or "").strip(),
        (a.getSetting("seriesly_password") or "").strip(),
    )


def _js_escape(s):
    if not s:
        return ""
    return (s.replace("\\", "\\\\")
             .replace("'", "\\'")
             .replace('"', '\\"')
             .replace("\n", "\\n")
             .replace("\r", "\\r"))


def _build_turnstile_html(email, password, proxy_base):
    return f"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Series.ly Login</title>
<style>
* {{ margin:0; padding:0; box-sizing:border-box; }}
body {{
  background: #0d1117; color: #e6edf3;
  font-family: -apple-system, sans-serif;
  display: flex; align-items: center; justify-content: center;
  min-height: 100vh; padding: 20px;
}}
.c {{ text-align: center; max-width: 400px; width: 100%; }}
h2 {{ color: #58a6ff; margin-bottom: 8px; }}
.s {{ color: #8b949e; margin: 16px 0; min-height: 24px; }}
.sp {{
  display: inline-block; width: 20px; height: 20px;
  border: 3px solid #30363d; border-top-color: #58a6ff;
  border-radius: 50%; animation: spin 0.8s linear infinite;
  vertical-align: middle; margin-right: 8px;
}}
@keyframes spin {{ to {{ transform: rotate(360deg); }} }}
.ok {{ color: #3fb950; font-size: 1.3em; }}
.er {{ color: #f85149; }}
#tb {{ display: flex; justify-content: center; margin: 20px 0; }}
</style>
</head>
<body>
<div class="c">
  <h2>Series.ly</h2>
  <p id="s" class="s"><span class="sp"></span> Resolviendo verificacion...</p>
  <div id="tb"></div>
</div>
<script src="https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit" async></script>
<script>
var P='{proxy_base}', E='{_js_escape(email)}', W='{_js_escape(password)}';
var tok='', sent=false;

function init() {{
  if(typeof turnstile==='undefined'){{ setTimeout(init,300); return; }}
  turnstile.render('#tb', {{
    sitekey:'0x4AAAAAACa-o4Kq858TtUP6',
    callback:function(t){{
      tok=t;
      document.getElementById('s').innerHTML='<span class="sp"></span> Iniciando sesion...';
      doLogin();
    }},
    'error-callback':function(c){{
      document.getElementById('s').innerHTML='<span class="er">Error verificacion ('+c+')</span>';
      setTimeout(function(){{ if(typeof turnstile!=='undefined') turnstile.reset(); }},3000);
    }},
    theme:'dark'
  }});
}}
setTimeout(init,500);

function doLogin(){{
  if(sent) return; sent=true;
  fetch(P+'/pair/login',{{
    method:'POST',
    headers:{{'Content-Type':'application/json'}},
    body:JSON.stringify({{ email:E, password:W, turnstileToken:tok }})
  }})
  .then(function(r){{ return r.json(); }})
  .then(function(d){{
    if(d.success){{
      document.getElementById('s').innerHTML='<span class="ok">&#10004; Sesion iniciada</span>';
      window.MW_LOGIN_OK=true;
    }} else {{
      document.getElementById('s').innerHTML='<span class="er">'+(d.error||'Error')+'</span>';
      sent=false; tok='';
      setTimeout(function(){{ if(typeof turnstile!=='undefined') turnstile.reset(); }},2000);
    }}
  }})
  .catch(function(e){{
    document.getElementById('s').innerHTML='<span class="er">Error: '+e.message+'</span>';
    sent=false;
  }});
}}
</script>
</body>
</html>"""


# ==========================================================================
# Android WebView — clases Java definidas DENTRO de la funcion
# para no petar si pyjnius no esta disponible
# ==========================================================================

_login_result = {"done": False, "ok": False}


def show_webview_login():
    """Abre WebView Android, resuelve Turnstile, login automatico."""
    if not is_available():
        _log("WebView no disponible")
        return False

    # Clases Java definidas AQUI DENTRO (no a nivel de modulo)
    # para que el import del modulo no falle si pyjnius no esta
    class Runnable(PythonJavaClass):
        __javainterfaces__ = ["java/lang/Runnable"]
        __javacontext__ = "app"

        def __init__(self, fn):
            super().__init__()
            self._fn = fn

        @java_method("()V")
        def run(self):
            try:
                self._fn()
            except Exception as e:
                _log(f"Runnable error: {e}")

    class JSCallback(PythonJavaClass):
        __javainterfaces__ = ["android/webkit/ValueCallback"]
        __javacontext__ = "app"

        @java_method("(Ljava/lang/Object;)V")
        def onReceiveValue(self, value):
            val = str(value).strip('"')
            if val == "yes":
                _log("MW_LOGIN_OK detectado!")
                _login_result["done"] = True
                _login_result["ok"] = True

    # 1. Credenciales
    email, password = _get_credentials()
    proxy_base = _get_proxy_base()

    if not email or not password:
        _log("Sin credenciales")
        _notify("Configura email y contrasena primero", error=True)
        return False

    # 2. HTML
    html_content = _build_turnstile_html(email, password, proxy_base)
    _log(f"HTML: {len(html_content)} bytes")

    # 3. WebView
    global _login_result
    _login_result = {"done": False, "ok": False}

    wv_ref = [None]
    dlg_ref = [None]

    try:
        Activity = autoclass("org.xbmc.kodi.Main")
        activity = Activity.mActivity

        WebView = autoclass("android.webkit.WebView")
        CookieManager = autoclass("android.webkit.CookieManager")
        LinearLayout = autoclass("android.widget.LinearLayout")
        ViewGroupLP = autoclass("android.view.ViewGroup$LayoutParams")
        Builder = autoclass("android.app.AlertDialog$Builder")

        def create_wv():
            try:
                cm = CookieManager.getInstance()
                cm.setAcceptCookie(True)

                layout = LinearLayout(activity)
                layout.setOrientation(LinearLayout.VERTICAL)

                wv = WebView(activity)
                lp = ViewGroupLP(ViewGroupLP.MATCH_PARENT, ViewGroupLP.MATCH_PARENT)
                wv.setLayoutParams(lp)

                ws = wv.getSettings()
                ws.setJavaScriptEnabled(True)
                ws.setDomStorageEnabled(True)
                ws.setMixedContentMode(0)
                ws.setUserAgentString(
                    "Mozilla/5.0 (Linux; Android 12; TV) "
                    "AppleWebKit/537.36 Chrome/124.0 Safari/537.36"
                )

                layout.addView(wv)

                b = Builder(activity)
                b.setView(layout)
                b.setCancelable(True)
                dialog = b.create()
                dialog.show()

                w = dialog.getWindow()
                if w:
                    w.setLayout(ViewGroupLP.MATCH_PARENT, ViewGroupLP.MATCH_PARENT)

                wv_ref[0] = wv
                dlg_ref[0] = dialog

                wv.loadDataWithBaseURL(
                    "https://series.ly/ingresar",
                    html_content, "text/html", "UTF-8", None,
                )
                _log("WebView creado OK")

            except Exception as e:
                _log(f"Error creando WebView: {e}")
                _login_result["done"] = True

        activity.runOnUiThread(Runnable(create_wv))

    except Exception as e:
        _log(f"Error Activity: {e}")
        _notify(f"Error Android: {e}", error=True)
        return False

    # 4. Esperar resultado
    timeout = 180
    elapsed = 0

    while not _login_result["done"] and elapsed < timeout:
        xbmc.sleep(2000)
        elapsed += 2

        # Comprobar JS cada 4s
        if elapsed % 4 == 0 and wv_ref[0]:
            try:
                def check():
                    try:
                        wv_ref[0].evaluateJavascript(
                            "window.MW_LOGIN_OK===true?'yes':'no'",
                            JSCallback(),
                        )
                    except Exception:
                        pass
                Activity = autoclass("org.xbmc.kodi.Main")
                Activity.mActivity.runOnUiThread(Runnable(check))
            except Exception:
                pass

        # Verificar Supabase cada 15s
        if elapsed % 15 == 0 and elapsed >= 10:
            try:
                from . import supabase_sync as sb
                cookie = sb.get_seriesly_cookie()
                if cookie and len(cookie) > 50:
                    _log("Cookie en Supabase!")
                    _login_result["done"] = True
                    _login_result["ok"] = True
            except Exception:
                pass

    # 5. Cerrar
    try:
        if dlg_ref[0]:
            Activity = autoclass("org.xbmc.kodi.Main")
            Activity.mActivity.runOnUiThread(
                Runnable(lambda: dlg_ref[0].dismiss())
            )
    except Exception:
        pass

    if _login_result["ok"]:
        _notify("Sesion iniciada correctamente")
        return True

    if elapsed >= timeout:
        _notify("Tiempo agotado", error=True)

    return False
