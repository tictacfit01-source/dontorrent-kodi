# -*- coding: utf-8 -*-
"""El relay FIRMA sus escrituras en mw-sync, y nunca ensena su secreto (dtbl77).

El secreto (MW_KV_FIRMA) lo genera Render y solo vive en su entorno. Aqui:
  1) la firma Ed25519 escrita a mano casa con los vectores OFICIALES del RFC 8032;
  2) sin el secreto no se firma nada (y /catdiag lo dice: inactiva);
  3) con el, firma solo lo que va a mw-sync (a ninguna otra web), y /catdiag
     ensena la clave PUBLICA pero nunca el secreto;
  4) una firma hecha aqui la acepta el worker.js REAL (Node, WebCrypto), y la
     misma con un byte cambiado, no.
Sin red."""
import base64
import json
import os
import subprocess
import sys

os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
os.environ["MW_SIN_KEEPALIVE"] = "1"
os.environ["MW_APRENDIZ"] = "0"
os.environ["MW_SIN_NUBE"] = "1"
os.environ.pop("MW_KV_FIRMA", None)
AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(AQUI, "..", "..", "render_relay"))
import app as A                                          # noqa: E402
import requests                                          # noqa: E402

fallos = 0


def comprueba(nombre, ok, detalle=""):
    global fallos
    print(("  ok   " if ok else "  MAL  ") + nombre + ("" if ok else "  -> " + str(detalle)))
    if not ok:
        fallos += 1


print("\n=== 1) Ed25519 contra los vectores oficiales del RFC 8032 (seccion 7.1) ===")
VECTORES = [
    ("9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60",
     "d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a", "",
     "e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e065224901555fb8821590a33bacc61e39701cf9b46bd25bf5f0595bbe24655141438e7a100b"),
    ("4ccd089b28ff96da9db6c346ec114e0f5b8a319f35aba624da8cf6ed4fb8a6fb",
     "3d4017c3e843895a92b70aa74d1b7ebc9c982ccf2ec4968cc0cd55f12af4660c", "72",
     "92a009a9f0d4cab8720e820b5f642540a2b27b5416503f8fb3762223ebdb69da085ac1e43e15996e458f3613d0f11d8c387b2eaeb4302aeeb00d291612bb0c00"),
    ("c5aa8df43f9f837bedb7442f31dcb7b166d38535076f094b85ce3a2e0b4458f7",
     "fc51cd8e6218a1a38da47ed00230f0580816ed13ba3303ac5deb911548908025", "af82",
     "6291d657deec24024827e69c3abe01a30ce548a284743a445e3680d7db5ac3ac18ff9b538d16f290ae67f760984dc6594a7c15e9716ed28dc027beceea1ec40a"),
]
for i, (sk, pk, msg, sig) in enumerate(VECTORES, 1):
    s = bytes.fromhex(sk)
    comprueba("vector %d: clave publica" % i, A._ed_publica(s).hex() == pk)
    comprueba("vector %d: firma" % i, A._ed_firma(s, bytes.fromhex(msg)).hex() == sig)

ENVIADO = []


class _R(object):
    status_code = 200
    raw = None

    def iter_content(self, *a, **k):
        return iter([b"{}"])

    def close(self):
        pass


viejo = requests.Session.post


def falso(self, url, **k):
    ENVIADO.append((url, dict(k.get("headers") or {}), k.get("data")))
    return _R()


requests.Session.post = falso
SECRETO = "SecretoDePruebaQueNoEsElDeRender_0123456789"
try:
    print("\n=== 2) Escenario 1: relay SIN el secreto ===")
    A._post_con_tope(A._SEMI_SYNC, {"gz": "x"}, 5.0)
    comprueba("no firma (ninguna cabecera de firma)", ENVIADO and "X-MW-Firma" not in ENVIADO[-1][1], ENVIADO[-1:])
    comprueba("...y el cuerpo va igual que antes (JSON)", json.loads(ENVIADO[-1][2]) == {"gz": "x"})
    d = A.app.test_client().get("/catdiag").get_json()
    comprueba("/catdiag dice que NO firma (no da la impresion de estar protegido)",
              d.get("kv_firma", {}).get("activa") is False and not d["kv_firma"].get("publica"), d.get("kv_firma"))

    print("\n=== 3) Con el secreto ===")
    os.environ["MW_KV_FIRMA"] = SECRETO
    for u in (A._SEMI_SYNC, A._ENRIQ_SYNC, A._WFIH_SYNC, A._WFIDX_SYNC):
        A._post_con_tope(u, {"gz": "x"}, 5.0)
        cab = ENVIADO[-1][1]
        comprueba("firma lo que va a mw-sync: " + u.split("workers.dev")[1],
                  cab.get("X-MW-Ts", "").isdigit() and len(base64.b64decode(cab.get("X-MW-Firma", ""))) == 64)
    A._post_con_tope("https://otra.web.example/kv/semillas", {"gz": "x"}, 5.0)
    comprueba("a cualquier otra web, NUNCA", "X-MW-Firma" not in ENVIADO[-1][1], ENVIADO[-1][1])
    A._post_con_tope("https://mw-sync.israeldm93.workers.dev.trampa.example/kv/x", {"gz": "x"}, 5.0)
    comprueba("ni a una que se le parezca", "X-MW-Firma" not in ENVIADO[-1][1], ENVIADO[-1][1])
    r = A.app.test_client().get("/catdiag")
    txt = r.get_data(as_text=True)
    kf = r.get_json().get("kv_firma") or {}
    comprueba("/catdiag ensena la clave PUBLICA y nunca el secreto",
              kf.get("activa") is True and len(base64.b64decode(kf.get("publica", ""))) == 32
              and SECRETO not in txt and A._kv_par()[0].hex() not in txt, kf)

    print("\n=== 4) El worker REAL acepta la firma del relay (Node, WebCrypto) ===")
    A._post_con_tope(A._WFIH_SYNC, {"gz": "QUJD"}, 5.0)
    url, cab, datos = ENVIADO[-1]

    def worker(cuerpo):
        c = json.dumps({"publica": kf["publica"], "ruta": "/kv/wfih", "ts": int(cab["X-MW-Ts"]),
                        "firma": cab["X-MW-Firma"], "cuerpo_b64": base64.b64encode(cuerpo).decode("ascii")})
        out = subprocess.run(["node", os.path.join(AQUI, "kv_firma.js"), "--cruce", c],
                             capture_output=True, text=True, timeout=60)
        try:
            return json.loads(out.stdout.strip().splitlines()[-1])
        except Exception:
            return {"error": out.stdout + out.stderr}
    r1 = worker(datos)
    comprueba("firmado en Python, verificado por el worker: se guarda", r1.get("status") == 200 and r1.get("guardado") == 1, r1)
    r2 = worker(datos.replace(b"QUJD", b"QUJE"))
    comprueba("...y con UN byte del cuerpo cambiado: 401, nada guardado", r2.get("status") == 401 and r2.get("guardado") == 0, r2)
finally:
    requests.Session.post = viejo
    os.environ.pop("MW_KV_FIRMA", None)

print("\n---- VEREDICTO ----")
if fallos:
    print("%d comprobaciones MAL" % fallos)
    sys.exit(1)
print("TODO OK: el relay firma bien (RFC 8032), solo hacia mw-sync, y su secreto no sale")
