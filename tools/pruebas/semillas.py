# -*- coding: utf-8 -*-
"""Semillas en la cuadricula (dtbl36): todo sin red.

Medido el 22-09-2026, tras 27 h sin desplegar: CERO semillas en las tres
pestanas del Inicio (163 tarjetas). Esto vigila las piezas del arreglo:
  1) el conteo por UDP en LOTE (un tracker de mentira en 127.0.0.1);
  2) /seedsknown ensena lo ultimo que se sabe (hasta un dia), dice que pelis
     de DonTorrent son RAR y pide refrescar lo viejo;
  3) el aprendiz: que elige, que se salta, y que el PoW de DonTorrent va
     espaciado y solo a cajas que NO estan reproduciendo;
  4) la copia en la nube: valida lo que baja y nunca pisa lo que ya sabe;
  5) el camino directo muerto de /dtpacked no se reintenta en media hora.

Escribe en el /tmp del relay (C:\\tmp en Windows): guarda y restaura lo que
hubiera.
"""
import base64
import gzip
import json
import os
import shutil
import socket
import struct
import sys
import threading
import time

os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
os.environ["MW_SIN_KEEPALIVE"] = "1"     # nada de red de fondo en la prueba
os.environ["MW_APRENDIZ"] = "0"      # nada de hilos de fondo en la prueba
os.environ["MW_SIN_NUBE"] = "1"      # ni bajar la copia de verdad al importar
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "..", "render_relay"))
import app as A                                          # noqa: E402

# Pase lo que pase en la prueba, NADA puede llegar a la copia de produccion.
A._SEMI_SYNC = "http://127.0.0.1:9/kv/semillas"
A._WFIDX_SYNC = "http://127.0.0.1:9/wfidx"

fallos = 0


def comprueba(nombre, ok, detalle=""):
    global fallos
    print(("  ok   " if ok else "  MAL  ") + nombre + ("" if ok else "  -> " + str(detalle)))
    if not ok:
        fallos += 1


FICHEROS = [A._DTPACKED_FILE, A._DXIH_FILE, A._SEEDS_FILE, A._KB_STATUS_FILE,
            A._KB_NOW_FILE, A._APR_FILE]
os.makedirs("/tmp", exist_ok=True)
copia = {}
for f in FICHEROS:
    if os.path.exists(f):
        copia[f] = f + ".prueba_bak"
        shutil.copy(f, copia[f])


def escribe(f, d):
    with open(f, "w", encoding="utf-8") as fh:
        json.dump(d, fh)


def lee(f):
    try:
        with open(f, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return {}


IH = ["%040x" % (0xabc000 + i) for i in range(200)]


# ---------------------------------------------------------------------------
def tracker_falso(seeders, mal_tid=False):
    """Un tracker UDP de BEP-15 de mentira: connect + scrape de varios hashes."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("127.0.0.1", 0))
    s.settimeout(0.2)
    para = threading.Event()
    cid = 0x1234ABCD

    def corre():
        while not para.is_set():
            try:
                data, addr = s.recvfrom(4096)
            except socket.timeout:
                continue
            except OSError:
                return
            if len(data) == 16:
                pid, action, tid = struct.unpack(">QII", data)
                if pid == 0x41727101980 and action == 0:
                    s.sendto(struct.pack(">IIQ", 0, tid, cid), addr)
            elif len(data) > 16:
                c, action, tid = struct.unpack(">QII", data[:16])
                if action == 2 and c == cid:
                    n = (len(data) - 16) // 20
                    hs = [data[16 + 20 * i:36 + 20 * i] for i in range(n)]
                    cuerpo = b"".join(struct.pack(">III", seeders.get(h.hex(), 0), 1, 2)
                                      for h in hs)
                    s.sendto(struct.pack(">II", 2, tid ^ (1 if mal_tid else 0)) + cuerpo,
                             addr)
    threading.Thread(target=corre, daemon=True).start()
    return s.getsockname()[1], para, s


try:
    print("\n=== 1) Conteo UDP en LOTE (BEP-15) ===")
    cuentas = {h: (i * 7) % 50 for i, h in enumerate(IH[:150])}
    p1, para1, s1 = tracker_falso(cuentas)
    r = A._udp_scrape_many("127.0.0.1", p1, [bytes.fromhex(h) for h in IH[:150]])
    comprueba("150 hashes en tres paquetes (70+70+10): todos contestados",
              len(r) == 150, len(r))
    comprueba("y cada uno con SU numero",
              all(r[bytes.fromhex(h)] == cuentas[h] for h in IH[:150]))
    p2, para2, s2 = tracker_falso(cuentas, mal_tid=True)
    r2 = A._udp_scrape_many("127.0.0.1", p2, [bytes.fromhex(IH[0])])
    comprueba("una respuesta que no es la nuestra (otro transaction id) se ignora",
              r2 == {}, r2)
    t0 = time.time()
    muerto = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    muerto.bind(("127.0.0.1", 0))
    pm = muerto.getsockname()[1]           # nadie contesta aqui
    r3 = A._udp_scrape_many("127.0.0.1", pm, [bytes.fromhex(IH[0])], timeout=0.8)
    comprueba("un tracker mudo devuelve vacio y dentro de su tope",
              r3 == {} and time.time() - t0 < 2.5, (r3, round(time.time() - t0, 1)))
    otras = {h: 99 for h in IH[:3]}
    p4, para4, s4 = tracker_falso(otras)
    viejo = A._SEED_TRACKERS
    A._SEED_TRACKERS = (("127.0.0.1", p1), ("127.0.0.1", p4), ("127.0.0.1", pm))
    try:
        c = A._seed_counts_many(IH[:5] + ["corto", None])
    finally:
        A._SEED_TRACKERS = viejo
    comprueba("_seed_counts_many se queda con el MAXIMO entre trackers",
              c.get(IH[0]) == 99 and c.get(IH[3]) == cuentas[IH[3]], c)
    comprueba("y descarta lo que no es un infohash", len(c) == 5, len(c))
    for p in (para1, para2, para4):
        p.set()

    print("\n=== 2) /seedsknown: lo ultimo que se sabe, el RAR y el refresco ===")
    now = time.time()
    escribe(A._DTPACKED_FILE, {
        "peliculas:1": {"p": True, "q": "1080p", "ih": IH[10], "ts": now - 100,
                        "s": 7, "sts": now - 3600},          # conteo de hace 1 h
        "peliculas:2": {"p": False, "ih": IH[11], "ts": now,
                        "s": 3, "sts": now - 30 * 3600},     # de hace 30 h
        "peliculas:3": {"p": True, "ts": now},               # RAR sin infohash
        "peliculas:4": {"p": False, "ih": IH[12], "ts": now - 40 * 86400},  # caducado
    })
    escribe(A._DXIH_FILE, {"https://wolfmax4k.com/movie/1": {"ih": IH[13], "ts": now}})
    escribe(A._SEEDS_FILE, {IH[13]: {"s": 12, "ts": now - 60},
                            IH[11]: {"s": 5, "ts": now - 7200}})   # mas nuevo que el de DT
    pedidos = []
    viejo_ref = A._seeds_refresca
    A._seeds_refresca = lambda ihs: pedidos.append(list(ihs))
    try:
        cli = A.app.test_client()
        d = cli.post("/seedsknown", json={"k": [
            "dt:peliculas:1", "dt:peliculas:2", "dt:peliculas:3", "dt:peliculas:4",
            "u:https://wolfmax4k.com/movie/1", "u:https://nada/x"]}).get_json()
    finally:
        A._seeds_refresca = viejo_ref
    s = d.get("s") or {}
    comprueba("un conteo de hace 1 h SE ENSENA (antes, a los 45 min, desaparecia)",
              s.get("dt:peliculas:1") == 7, s)
    comprueba("de dos conteos del mismo .torrent, el MAS NUEVO",
              s.get("dt:peliculas:2") == 5, s)
    comprueba("el de WolfMax, por su infohash", s.get("u:https://wolfmax4k.com/movie/1") == 12, s)
    comprueba("lo caducado (40 dias) y lo que no se sabe no salen",
              "dt:peliculas:4" not in s and "u:https://nada/x" not in s, s)
    comprueba("RAR: las dos de DonTorrent que lo son, y solo esas",
              sorted(d.get("r") or []) == ["dt:peliculas:1", "dt:peliculas:3"], d.get("r"))
    comprueba("se pide refrescar lo viejo (y no lo fresco)",
              len(pedidos) == 1 and sorted(pedidos[0]) == sorted([IH[10], IH[11]]),
              pedidos)

    print("\n=== 3) El aprendiz: que elige ===")
    now = time.time()
    escribe(A._DTPACKED_FILE, {
        "peliculas:10": {"p": False, "ih": IH[20], "ts": now, "s": 4, "sts": now - 60},
        "peliculas:11": {"p": False, "ih": IH[21], "ts": now, "s": 4, "sts": now - 7200},
    })
    escribe(A._DXIH_FILE, {"https://divxtotal.foo/peliculas/b/": {"ih": IH[22], "ts": now}})
    escribe(A._SEEDS_FILE, {IH[22]: {"s": 1, "ts": now - 7200}})
    A._APR_NEG.clear()
    A._APR_NEG["peliculas:13"] = (now - 10, 1200)
    items = [
        {"kind": "movie", "source": "dt", "content_id": "10", "tabla": "peliculas"},  # sabida
        {"kind": "movie", "source": "dt", "content_id": "11", "tabla": "peliculas"},  # conteo viejo
        {"kind": "movie", "source": "dt", "content_id": "12", "tabla": "peliculas"},  # pendiente
        {"kind": "movie", "source": "dt", "content_id": "12", "tabla": "peliculas"},  # repetida
        {"kind": "movie", "source": "dt", "content_id": "13", "tabla": "peliculas"},  # fallo reciente
        {"kind": "movie", "source": "wf", "url": "https://wolfmax4k.com/movie/9"},   # pendiente
        {"kind": "movie", "source": "dx", "url": "https://divxtotal.foo/peliculas/b/"},  # viejo
        {"kind": "movie", "source": "et", "url": "https://elitetorrent.com/x"},      # no se aprende
        {"kind": "movie", "source": "dt", "content_id": "abc"},                     # id raro
    ]
    pend, viejos = A._apr_pendientes(items, now)
    comprueba("pendientes: la DT que no se sabe y la de WolfMax, sin repetir",
              [(s_, k) for s_, k, _ in pend] ==
              [("dt", "peliculas:12"), ("wf", "https://wolfmax4k.com/movie/9")],
              [(s_, k) for s_, k, _ in pend])
    comprueba("los conteos viejos se refrescan (DT y DivxTotal), los frescos no",
              sorted(viejos) == sorted([IH[21], IH[22]]), viejos)

    print("\n=== 4) El aprendiz: como trabaja ===")
    escribe(A._KB_STATUS_FILE, {"111111": {"ts": now, "v": "2.9.72"},
                                "222222": {"ts": now, "v": "2.9.72"},
                                "333333": {"ts": now, "v": "2.9.72"},
                                "444444": {"ts": now - 600, "v": "2.9.72"}})
    escribe(A._KB_NOW_FILE, {"222222": {"np": {"title": "Silo 1x01"}, "ts": now}})
    libres = A._cajas_libres()
    comprueba("solo cajas vivas y que NO estan reproduciendo",
              sorted(libres) == ["111111", "333333"], libres)
    hechos = []
    orig = {n: getattr(A, n) for n in ("_apr_items_inicio", "_apr_dt", "_apr_wf",
                                       "_apr_dx", "_semillas_nube_sube",
                                       "_seed_counts_many", "_mem_cgroup_mb")}
    A._apr_items_inicio = lambda: [
        {"kind": "movie", "source": "dt", "content_id": "30", "tabla": "peliculas", "title": "A"},
        {"kind": "movie", "source": "dt", "content_id": "31", "tabla": "peliculas", "title": "B"},
        {"kind": "movie", "source": "dx", "url": "https://divxtotal.foo/peliculas/c/", "title": "C"}]
    A._apr_dt = lambda k: hechos.append(("dt", k)) or {"ih": IH[30], "p": True, "q": "4K"}
    A._apr_wf = lambda u: hechos.append(("wf", u)) or {"ih": ""}
    A._apr_dx = lambda u: hechos.append(("dx", u)) or {"timeout": True}
    A._semillas_nube_sube = lambda forzar=False: 0
    A._seed_counts_many = lambda ihs: {h: 8 for h in ihs}
    A._mem_cgroup_mb = lambda: 150.0
    A._APR_T_DT[0] = 0.0
    A._APR_NEG.clear()
    try:
        escribe(A._DTPACKED_FILE, {})
        escribe(A._DXIH_FILE, {})
        p_1 = A._apr_ronda()
        p_2 = A._apr_ronda()
        p_3 = A._apr_ronda()
    finally:
        for n, f in orig.items():
            setattr(A, n, f)
    comprueba("UN trabajo por vuelta; el PoW de DonTorrent no se repite en 2 min",
              hechos == [("dt", "peliculas:30"), ("dx", "https://divxtotal.foo/peliculas/c/")],
              hechos)
    comprueba("y cuando solo queda DonTorrent y no le toca, espera (sin trabajar)",
              p_1 == A._APR_PAUSA and p_2 == A._APR_PAUSA and p_3 == 60.0, (p_1, p_2, p_3))
    dtp = lee(A._DTPACKED_FILE)
    comprueba("lo aprendido queda guardado: infohash, RAR, calidad y conteo",
              (dtp.get("peliculas:30") or {}).get("ih") == IH[30]
              and dtp["peliculas:30"].get("p") is True
              and dtp["peliculas:30"].get("q") == "4K"
              and dtp["peliculas:30"].get("s") == 8, dtp.get("peliculas:30"))
    neg = A._APR_NEG.get("https://divxtotal.foo/peliculas/c/")
    comprueba("un 'no contesto a tiempo' se reintenta a los 20 min, no a las 6 h",
              bool(neg) and neg[1] == 1200, neg)

    print("\n=== 5) La copia en la nube ===")
    now = time.time()
    escribe(A._DTPACKED_FILE, {"peliculas:50": {"ih": IH[50], "ts": now, "p": False, "q": ""}})
    escribe(A._DXIH_FILE, {})
    remoto = {"v": 1,
              "dtp": {"peliculas:50": {"ih": IH[99], "ts": now},        # ya lo sabemos
                      "peliculas:51": {"ih": IH[51], "ts": now - 5, "p": True,
                                       "q": "1080p", "s": 9, "sts": now - 5},
                      "peliculas:52": {"ih": "no-es-un-hash", "ts": now},
                      "../../etc": {"ih": IH[53], "ts": now}},
              "dih": {"https://wolfmax4k.com/movie/7": {"ih": IH[54], "ts": now},
                      "javascript:alert(1)": {"ih": IH[55], "ts": now},
                      "https://x/y": {"ih": IH[56], "ts": "ayer"}}}
    gz = base64.b64encode(gzip.compress(json.dumps(remoto).encode())).decode()

    viejo_get, viejo_post = A._get_con_tope, A._post_con_tope
    A._get_con_tope = lambda url, tope, **k: (json.dumps({"ok": True, "gz": gz}), 200)
    try:
        n = A._semillas_nube_baja()
    finally:
        A._get_con_tope = viejo_get
    dtp, dih = lee(A._DTPACKED_FILE), lee(A._DXIH_FILE)
    comprueba("baja lo que falta (2 buenas) y nada mas", n == 2, n)
    comprueba("nunca pisa lo que ya sabia", dtp["peliculas:50"]["ih"] == IH[50])
    comprueba("con su RAR y su ultimo conteo",
              dtp.get("peliculas:51", {}).get("p") is True
              and dtp["peliculas:51"].get("s") == 9, dtp.get("peliculas:51"))
    comprueba("y descarta lo que no es valido (hash roto, claves raras, fechas raras)",
              "peliculas:52" not in dtp and "../../etc" not in dtp
              and list(dih) == ["https://wolfmax4k.com/movie/7"], (list(dtp), list(dih)))
    enviado = []
    # arriba hay algo que aqui NO esta (p.ej. porque la recuperacion del
    # arranque fallo): la subida tiene que conservarlo, no pisarlo
    arriba = {"v": 1, "dtp": {"peliculas:60": {"ih": IH[60], "ts": now - 50}},
              "dih": {}}
    gz2 = base64.b64encode(gzip.compress(json.dumps(arriba).encode())).decode()
    A._post_con_tope = lambda url, cuerpo, tope: enviado.append(cuerpo) or 200
    viejo_render = A._EN_RENDER
    try:
        A._EN_RENDER = False
        A._SEMI_NUBE["subida_ts"] = 0.0
        comprueba("un relay de PRUEBAS nunca sube (pisaria la copia buena)",
                  A._semillas_nube_sube() == 0 and not enviado, enviado)
        A._EN_RENDER = True
        A._SEMI_NUBE["firma"] = None
        A._get_con_tope = lambda url, tope, **k: (None, 503)
        n_mal = A._semillas_nube_sube()
        comprueba("si no se puede LEER lo de arriba, no se sube nada",
                  n_mal == 0 and not enviado, enviado)
        A._SEMI_NUBE["subida_ts"] = 0.0
        A._get_con_tope = lambda url, tope, **k: (json.dumps({"ok": True, "gz": gz2}), 200)
        n_sub = A._semillas_nube_sube()
        otra = A._semillas_nube_sube()
        # una subida "en marcha" (colgada) no deja empezar otra
        A._SEMI_NUBE["subida_ts"] = 0.0
        A._SEMI_NUBE["firma"] = None
        A._SEMI_NUBE["vuelo"] = time.time() - 30
        n_vuelo = A._semillas_nube_sube()
        vuelo_intacto = A._SEMI_NUBE["vuelo"] > 0
        A._SEMI_NUBE["vuelo"] = 0.0
    finally:
        A._post_con_tope = viejo_post
        A._get_con_tope = viejo_get
        A._EN_RENDER = viejo_render
    dat = json.loads(gzip.decompress(base64.b64decode(enviado[0]["gz"])))
    comprueba("sube la UNION: lo de aqui (3) mas lo que solo estaba arriba (1)",
              n_sub == 4 and set(dat["dtp"]) == {"peliculas:50", "peliculas:51",
                                                 "peliculas:60"}
              and set(dat["dih"]) == {"https://wolfmax4k.com/movie/7"}, dat)
    comprueba("y lo que solo estaba arriba se recupera tambien aqui",
              (lee(A._DTPACKED_FILE).get("peliculas:60") or {}).get("ih") == IH[60])
    comprueba("no vuelve a subir antes de 10 min", otra == 0 and len(enviado) == 1)
    comprueba("con otra subida en marcha no empieza una segunda (ni le quita la marca)",
              n_vuelo == 0 and len(enviado) == 1 and vuelo_intacto, (n_vuelo, len(enviado)))

    print("\n=== 5b) Contra un servidor que GOTEA (lo que hace Cloudflare a ratos) ===")

    def servidor(modo):
        """'gotea': la linea de estado y luego un byte cada 0,3 s, para siempre
        (el timeout de requests es ENTRE bytes: nunca salta). 'bien': un 200."""
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(("127.0.0.1", 0))
        srv.listen(8)
        srv.settimeout(0.3)
        para = threading.Event()

        def atiende(c):
            try:
                c.settimeout(5)
                c.recv(65536)
                if modo == "bien":
                    cuerpo = b'{"ok": true}'
                    c.sendall(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
                              b"Content-Length: " + str(len(cuerpo)).encode()
                              + b"\r\nConnection: close\r\n\r\n" + cuerpo)
                else:
                    c.sendall(b"HTTP/1.1 200 OK\r\n")
                    while not para.is_set():
                        c.sendall(b"X")
                        time.sleep(0.3)
            except Exception:
                pass
            finally:
                try:
                    c.close()
                except Exception:
                    pass

        def corre():
            while not para.is_set():
                try:
                    c, _ = srv.accept()
                except socket.timeout:
                    continue
                except OSError:
                    return
                threading.Thread(target=atiende, args=(c,), daemon=True).start()
        threading.Thread(target=corre, daemon=True).start()
        return "http://127.0.0.1:%d/kv/semillas" % srv.getsockname()[1], para

    url_gotea, para_g = servidor("gotea")
    url_bien, para_b = servidor("bien")
    t0 = time.time()
    st = A._post_con_tope(url_gotea, {"gz": "x"}, 2.0)
    dur_post = time.time() - t0
    comprueba("POST contra el goteo: se corta en su tope (2 s) y no se queda colgado",
              st == 0 and dur_post < 4.0, (st, round(dur_post, 1)))
    t0 = time.time()
    txt, st2 = A._get_con_tope(url_gotea, 2.0)
    dur_get = time.time() - t0
    comprueba("GET contra el goteo: igual", txt is None and dur_get < 4.0,
              (st2, round(dur_get, 1)))
    comprueba("y contra un servidor normal, el POST contesta 200",
              A._post_con_tope(url_bien, {"gz": "x"}, 5.0) == 200)

    # Y por HTTPS, que es como va TODO en produccion. Al envolver en TLS, Python
    # "desengancha" el socket crudo: la primera version del vigia lo cortaba y
    # no pasaba nada (medido: seguia colgado a los 12 s). El certificado de la
    # prueba se genera aqui con openssl y se tira: una clave en el repo haria
    # saltar el aviso de secretos de GitHub.
    import shutil as _sh
    import ssl as _ssl
    import subprocess as _sp
    import tempfile as _tf
    _openssl = _sh.which("openssl")
    if not _openssl:
        print("  (sin openssl: me salto el caso HTTPS)")
    else:
        _dir = _tf.mkdtemp(prefix="mw_tls_")
        _crt, _key = os.path.join(_dir, "c.pem"), os.path.join(_dir, "k.pem")
        _env = dict(os.environ, MSYS_NO_PATHCONV="1")
        _sp.run([_openssl, "req", "-x509", "-newkey", "rsa:2048", "-nodes",
                 "-keyout", _key, "-out", _crt, "-days", "1",
                 "-subj", "/CN=127.0.0.1", "-addext", "subjectAltName=IP:127.0.0.1"],
                capture_output=True, env=_env)
        _ctx = _ssl.SSLContext(_ssl.PROTOCOL_TLS_SERVER)
        _ctx.load_cert_chain(_crt, _key)
        _srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        _srv.bind(("127.0.0.1", 0))
        _srv.listen(4)
        _srv.settimeout(0.3)

        def _tls_atiende(c):
            try:
                s = _ctx.wrap_socket(c, server_side=True)
                s.recv(65536)
                s.sendall(b"HTTP/1.1 200 OK\r\n")
                while not para_g.is_set():
                    s.sendall(b"X")                 # cada byte, su registro TLS
                    time.sleep(0.3)
            except Exception:
                pass
            finally:
                try:
                    c.close()
                except Exception:
                    pass

        def _tls_acepta():
            while not para_g.is_set():
                try:
                    c, _ = _srv.accept()
                except socket.timeout:
                    continue
                except OSError:
                    return
                threading.Thread(target=_tls_atiende, args=(c,), daemon=True).start()
        threading.Thread(target=_tls_acepta, daemon=True).start()
        _url_tls = "https://127.0.0.1:%d/kv/semillas" % _srv.getsockname()[1]
        _viejo_ca = os.environ.get("REQUESTS_CA_BUNDLE")
        os.environ["REQUESTS_CA_BUNDLE"] = _crt
        try:
            t0 = time.time()
            st_tls = A._post_con_tope(_url_tls, {"gz": "x"}, 2.0)
            dur_tls = time.time() - t0
            t0 = time.time()
            txt_tls, _ = A._get_con_tope(_url_tls, 2.0)
            dur_tls2 = time.time() - t0
        finally:
            if _viejo_ca is None:
                os.environ.pop("REQUESTS_CA_BUNDLE", None)
            else:
                os.environ["REQUESTS_CA_BUNDLE"] = _viejo_ca
            _sh.rmtree(_dir, ignore_errors=True)
        comprueba("HTTPS: goteo DESPUES del handshake, el POST se corta en su tope",
                  st_tls == 0 and dur_tls < 4.0, (st_tls, round(dur_tls, 1)))
        comprueba("HTTPS: y el GET igual", txt_tls is None and dur_tls2 < 4.0,
                  round(dur_tls2, 1))
    # la subida del indice de WolfMax: se hacia DENTRO de las peticiones
    viejo_idx, viejo_url = dict(A._WFIDX), A._WFIDX_SYNC
    A._WFIDX.clear()
    A._WFIDX.update({"https://wolfmax4k.com/movie/1": {"t": "Prueba", "k": "movie", "q": ""}})
    A._WFIDX_SYNC = url_gotea
    A._WFIDX_SUBE_VUELO[0] = 0.0
    try:
        t0 = time.time()
        r1 = A._wfidx_nube_sube(forzar=True)
        dur_idx = time.time() - t0
        r2 = A._wfidx_nube_sube(forzar=True)
        comprueba("la subida del indice ya no hace esperar a la peticion (segundo plano)",
                  r1 == 1 and dur_idx < 0.5, (r1, round(dur_idx, 2)))
        comprueba("y con una en marcha (colgada) no se lanza otra", r2 == 0, r2)
        fin_espera = time.time() + 30
        while A._WFIDX_SUBE_VUELO[0] and time.time() < fin_espera:
            time.sleep(0.2)
        comprueba("la colgada se corta sola en su tope (20 s) y libera el turno",
                  A._WFIDX_SUBE_VUELO[0] == 0.0, A._WFIDX_SUBE_VUELO[0])
    finally:
        A._WFIDX.clear()
        A._WFIDX.update(viejo_idx)
        A._WFIDX_SYNC = viejo_url
        para_g.set()
        para_b.set()

    print("\n=== 6) /dtpacked: el camino directo muerto no se reintenta ===")
    llamadas = []
    viejo_dl = A._dt_download_url
    A._dt_download_url = lambda d, c, t: llamadas.append(c) or None
    try:
        A._DTDIR.update({"ok": 0.0, "fallo": 0.0, "saltados": 0})
        u1 = A._dt_url_directa("", "1", "peliculas")
        u2 = A._dt_url_directa("", "2", "peliculas")
        A._DTDIR["fallo"] = time.time() - 1801
        u3 = A._dt_url_directa("", "3", "peliculas")
    finally:
        A._dt_download_url = viejo_dl
    comprueba("tras un fallo, la siguiente va directa a la caja",
              llamadas == ["1", "3"] and A._DTDIR["saltados"] == 1, (llamadas, A._DTDIR))
    comprueba("y pasada media hora se vuelve a probar (por si Render ya no esta baneado)",
              "3" in llamadas)
finally:
    for f in FICHEROS:
        try:
            if f in copia:
                shutil.move(copia[f], f)
            elif os.path.exists(f):
                os.remove(f)
        except Exception:
            pass

print("\n---- VEREDICTO ----")
if fallos:
    print("%d comprobaciones MAL" % fallos)
    sys.exit(1)
print("TODO OK: las semillas se saben, se refrescan y se guardan sin molestar a nadie")
