"""Copia de seguridad VERIFICABLE de las copias de mw-sync (solo lecturas).

Uso: python copia_kv.py <carpeta_destino>            -> guarda y verifica
     python copia_kv.py --compara <carpeta_copia>     -> compara produccion con la copia

Por cada copia guarda la respuesta TAL CUAL (lo que se volveria a enviar para
restaurarla: {"gz": ...}) y un manifiesto con recuento y huella sha256 del
contenido descomprimido y canonico (claves ordenadas)."""
import base64
import gzip
import hashlib
import json
import os
import sys
import time
import urllib.request

BASE = "https://mw-sync.israeldm93.workers.dev"
RUTAS = {"semillas": "/kv/semillas", "enriq": "/kv/enriq", "wfih": "/kv/wfih", "wfidx": "/wfidx"}


def baja(ruta):
    req = urllib.request.Request(BASE + ruta, headers={"User-Agent": "mw-copia"})
    return urllib.request.urlopen(req, timeout=30).read()


def contenido(raw):
    gz = (json.loads(raw.decode("utf-8")) or {}).get("gz")
    if not gz:
        return None
    return json.loads(gzip.decompress(base64.b64decode(gz)).decode("utf-8"))


def recuento(nombre, d):
    if d is None:
        return {}
    if nombre == "semillas":
        return {"dtp": len(d.get("dtp") or {}), "dih": len(d.get("dih") or {})}
    if nombre == "enriq":
        return {"m": len(d.get("m") or {})}
    if nombre == "wfih":
        return {"h": len(d.get("h") or {}), "p": len(d.get("p") or {})}
    if isinstance(d, dict):
        return {"claves": len(d.get("e") or d.get("entries") or d)}
    return {"elementos": len(d)}


def huella(d):
    return hashlib.sha256(json.dumps(d, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def guarda(dest):
    os.makedirs(dest, exist_ok=True)
    man = {"cuando": time.strftime("%Y-%m-%d %H:%M:%S"), "copias": {}}
    for nombre, ruta in RUTAS.items():
        raw = baja(ruta)
        with open(os.path.join(dest, nombre + ".json"), "wb") as f:
            f.write(raw)
        d = contenido(raw)
        man["copias"][nombre] = {"bytes": len(raw), "ts": (json.loads(raw) or {}).get("ts"),
                                 "recuento": recuento(nombre, d), "huella": huella(d) if d is not None else None,
                                 "sha256_crudo": hashlib.sha256(raw).hexdigest()}
    with open(os.path.join(dest, "manifiesto.json"), "w", encoding="utf-8") as f:
        json.dump(man, f, ensure_ascii=False, indent=1)
    return man


def verifica(dest):
    """¿Se puede recuperar? Releer de disco, descomprimir, contar y comparar."""
    man = json.load(open(os.path.join(dest, "manifiesto.json"), encoding="utf-8"))
    ok = True
    for nombre, m in man["copias"].items():
        raw = open(os.path.join(dest, nombre + ".json"), "rb").read()
        d = contenido(raw)
        bien = (hashlib.sha256(raw).hexdigest() == m["sha256_crudo"] and huella(d) == m["huella"]
                and recuento(nombre, d) == m["recuento"] and isinstance((json.loads(raw) or {}).get("gz"), str))
        print("  %-9s %s  recuento=%s" % (nombre, "RECUPERABLE" if bien else "MAL", m["recuento"]))
        ok = ok and bien
    return ok


def compara(dest):
    man = json.load(open(os.path.join(dest, "manifiesto.json"), encoding="utf-8"))
    for nombre, ruta in RUTAS.items():
        antes = contenido(open(os.path.join(dest, nombre + ".json"), "rb").read())
        ahora = contenido(baja(ruta))
        r0, r1 = man["copias"][nombre]["recuento"], recuento(nombre, ahora)
        igual = huella(ahora) == man["copias"][nombre]["huella"]
        perdidas = []
        if nombre in ("semillas", "enriq", "wfih") and antes and ahora:
            for sec in ("dtp", "dih", "m", "h", "p"):
                a, b = antes.get(sec) or {}, ahora.get(sec) or {}
                perdidas += ["%s:%s" % (sec, k) for k in a if k not in b]
        cambiadas = []
        if nombre == "wfih" and antes and ahora:
            cambiadas = [k for k, v in (antes.get("h") or {}).items() if k in (ahora.get("h") or {})
                         and (ahora["h"][k].get("ih") != v.get("ih"))]
        print("  %-9s %s  antes=%s ahora=%s  perdidas=%d  huellas_wf_cambiadas=%d"
              % (nombre, "IDENTICA" if igual else "distinta", r0, r1, len(perdidas), len(cambiadas)))
        if perdidas[:5]:
            print("            ejemplo de perdidas:", perdidas[:5])


if __name__ == "__main__":
    if sys.argv[1] == "--compara":
        compara(sys.argv[2])
    else:
        m = guarda(sys.argv[1])
        print("guardada en", sys.argv[1])
        print("VERIFICACION:", "OK" if verifica(sys.argv[1]) else "FALLO")
