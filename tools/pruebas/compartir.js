// Android "Compartir -> MejorWolf" (dtbl78), sin Android: se cargan el SERVICE
// WORKER real (_SW_JS) y el bloque real de la pagina (==== COMPARTIR ====) de
// app.py, comparten la MISMA cache de mentira, y se comprueba que:
//  1) el archivo que recibe el service worker llega ENTERO a la pagina (byte a
//     byte, con su nombre y su sha256) y se borra de la cache al recogerlo;
//  2) sin preguntar SOLO va a la tele del contexto si la certeza es "exacta",
//     sigue siendo la elegida, esta conectada y no reproduce otra cosa; si no,
//     se pregunta nombrando la tele; si no casa, no se manda;
//  3) si no llega, llega a medias, alterado, enorme o viejo: error VISIBLE con
//     el selector a mano y nada subido;
//  4) lo huerfano se limpia sin tocar lo que otra apertura esta recogiendo;
//  5) nada dice "reproduciendo" hasta que Kodi lo confirma (/kb/now), y el mando
//     solo se abre si es de la tele de destino;
//  6) otra reproduccion (sendPlay real) anula el contexto, y sendPlay sigue
//     como siempre.
// Todo con reloj de mentira: ningun temporizador real.
"use strict";
const fs = require("fs");
const path = require("path");
const vm = require("vm");
const nodeCrypto = require("crypto");

let fallos = 0;
function comprueba(nombre, ok, detalle) {
  console.log((ok ? "  ok   " : "  MAL  ") + nombre + (ok ? "" : "  -> " + JSON.stringify(detalle)));
  if (!ok) fallos++;
}
const APP = fs.readFileSync(path.join(__dirname, "..", "..", "render_relay", "app.py"), "utf8");
const MAXB = String(Number(/_COMPARTIR_MAX = (\d+) \* 1024 \* 1024/.exec(APP)[1]) * 1024 * 1024);
const MAX = Number(MAXB);
const tramo = (desde, hasta) => { const a = APP.indexOf(desde); return APP.slice(a, APP.indexOf(hasta, a)); };
const SW = tramo('_SW_JS = """', '""".replace("__MW_MAXB__"').slice(12).replace(/__MW_MAXB__/g, MAXB);
const BLOQUE = tramo("// ==== COMPARTIR (dtbl78) ====", "// ==== FIN COMPARTIR ====").replace(/__MW_MAXB__/g, MAXB);
const WFSUBE = tramo("function wfSube(inp)", "// ==== COMPARTIR (dtbl78) ====");
const WFSUBEZIP = tramo("function wfSubeZip(inp)", "function fuenteCaidaDlg(");
const SENDPLAY = tramo("function sendPlay(ref,cdF,alOk)", "// DonTorrent CAIDO (lo dice su propia web");
const sha256 = (b) => nodeCrypto.createHash("sha256").update(b).digest("hex");

// --- Una CacheStorage de mentira, compartida (como en el movil) ---------------
function cacheStorage(op) {
  const o = op || {};
  const cajas = new Map();
  return {
    _cajas: cajas,
    async open(n) {
      if (!cajas.has(n)) cajas.set(n, new Map());
      const m = cajas.get(n);
      return {
        async put(k, r) { m.set(String(k), { cuerpo: Buffer.from(await r.arrayBuffer()), cab: Object.fromEntries(r.headers) }); },
        async match(k) {
          const e = m.get(String(k));
          if (!e) return undefined;
          if (o.rompeBlob && k === "/__compartido") return { headers: new Headers(e.cab), blob: async () => { throw new Error("roto"); } };
          return new Response(e.cuerpo, { headers: e.cab });
        },
        async delete(k) { return m.delete(String(k)); },
      };
    },
    async keys() { return [...cajas.keys()]; },
    async delete(n) { return cajas.delete(n); },
  };
}

// --- El service worker real ---------------------------------------------------
function cargaSW(caches, op) {
  const o = op || {};
  const oyentes = {};
  // su reloj, de mentira tambien: comparte con la pagina en T0
  const Reloj = class extends Date { constructor(...a) { if (a.length) super(...a); else super(o.ahora || T0); } static now() { return o.ahora || T0; } };
  const ctx = vm.createContext({ caches, Response, Request, URL, FormData, Blob, console, Promise, JSON, String, Date: Reloj,
    Array, Uint8Array, crypto: o.sinCrypto ? undefined : nodeCrypto.webcrypto,
    fetch: async () => new Response("red") });
  ctx.self = { location: { href: "https://mw.test/sw.js" }, addEventListener: (t, f) => (oyentes[t] = oyentes[t] || []).push(f),
               navigator: { userAgent: "Mozilla/5.0 (Linux; Android 16; Pixel 8 Pro) Chrome/153.0.0.0 Mobile Safari/537.36" },
               skipWaiting() {}, clients: { claim() {} } };
  ctx.addEventListener = ctx.self.addEventListener;
  vm.runInContext(SW, ctx);
  return async function comparte(fd) {
    const req = new Request("https://mw.test/compartir", { method: "POST", body: fd });
    let resp = null;
    for (const f of oyentes.fetch || []) {
      f({ request: req, respondWith: (p) => { resp = p; } });
      if (resp) break;
    }
    return resp ? await resp : null;
  };
}

// --- La pagina: el bloque real con lo de alrededor de mentira ------------------
// `now`: lo que contesta /kb/now, por orden. Cada elemento es lo que suena (np)
// con la tele conectada, o {__kb: respuesta entera}. Vacio: nada suena, conectada.
function pagina(caches, opciones) {
  const o = opciones || {};
  const R = { toasts: [], dialogos: [], subidas: [], envios: [], elige: 0, now: o.now || [], quitado: false, mando: 0, diags: [], kbsend: [] };
  let ahora = o.t0 || 1.8e12;
  const ls = new Map(Object.entries(o.ls || {}));
  R.ls = ls;
  const ctx = vm.createContext({
    console, Promise, URL, URLSearchParams, FormData, Blob, Response, JSON, Math, String, Number, Buffer, Object, Array, Uint8Array,
    navigator: { userAgent: "Mozilla/5.0 (Linux; Android 16; Pixel 8 Pro) Chrome/153.0.0.0 Mobile Safari/537.36" },
    crypto: nodeCrypto.webcrypto,
    location: { search: o.search || "" },
    history: { replaceState: () => { R.quitado = true; } },
    localStorage: { getItem: (k) => (ls.has(k) ? ls.get(k) : null), setItem: (k, v) => ls.set(k, String(v)),
                    removeItem: (k) => ls.delete(k) },
    code: { value: o.codeActual || "" },
    devName: (c) => ({ "111111": "Comedor", "222222": "Dormitorio" })[c] || "",
    toast: (t) => R.toasts.push(t),
    mwConfirm: (titulo, texto, etiqueta, acepta) => R.dialogos.push({ titulo, texto, etiqueta, acepta }),
    mwBack: () => {}, avisaCodigo: () => R.toasts.push("SIN_CODIGO"),
    _tituloBase: (t) => String(t || "").replace(/\s+\d{1,2}x\d{1,3}\b.*$/i, ""),
    $: (id) => (id === "wfdlg" ? { classList: { contains: () => false } } : null),
    wfElige: () => { R.elige++; }, openRemote: () => { R.mando++; }, pollNow: () => {}, pintaCaidas: () => {},
    histSnap: () => null, histPush: () => {}, closeSheet: () => {}, closeOv: () => {}, dtCaidaDlg: () => {},
    WFPIDE: o.wfpide || null,
    sendPlay: (ref, cd, alOk) => { R.envios.push({ ref, cd }); Promise.resolve().then(() => alOk(o.send === undefined ? { ok: true, tele: true } : o.send, o.send === null ? { error: "x" } : undefined)); return true; },
    fetch: async (url, op) => {
      if (url === "/wfsube") {
        const campos = {};
        for (const [k, v] of op.body.entries()) {
          campos[k] = typeof v === "string" ? v : { nombre: v.name, bytes: Buffer.from(await v.arrayBuffer()) };
        }
        R.subidas.push(campos);
        // como el servidor: sin huella, o con otra, no se acepta
        if (!campos.sha || !campos.f || campos.sha !== sha256(campos.f.bytes)) {
          return new Response(JSON.stringify({ ok: false, error: "El archivo ha llegado alterado: no se ha guardado nada." }), { status: 400 });
        }
        const r = o.wfsube || { st: 200, d: { ok: true, tipo: "torrent", nombre: "Insidious [4k]", link: "magnet:?xt=urn:btih:aa", certeza: "exacta" } };
        return new Response(JSON.stringify(r.d), { status: r.st });
      }
      if (url === "/compartir/diag") { R.diags.push(JSON.parse(op.body)); return new Response("{}"); }
      if (url.indexOf("/kb/now?code=") === 0) {
        if (o.kbFalla) throw new Error("sin red");
        const x = R.now.length ? R.now.shift() : null;
        return new Response(JSON.stringify(x && x.__kb ? x.__kb : (x ? { np: x } : { np: null, tele: true })));
      }
      if (url === "/kb/send") { R.kbsend.push(JSON.parse(op.body)); return new Response(JSON.stringify(o.kbsend || { ok: true, tele: true })); }
      throw new Error("red no esperada: " + url);
    },
    setTimeout: (f, ms) => { ahora += ms || 0; return setImmediate(f); },
    Date: class extends Date { constructor(...a) { if (a.length) super(...a); else super(ahora); } static now() { return ahora; } },
  });
  ctx.window = { caches, crypto: o.sinCrypto ? undefined : ctx.crypto };
  ctx.caches = caches;
  vm.runInContext("var WFESP=null,WFZIP=null,ULTCAIDAS=null,lastPlayTs=0;" + WFSUBE + WFSUBEZIP + BLOQUE, ctx);
  if (o.sendPlayReal) vm.runInContext(SENDPLAY, ctx);
  R.ctx = ctx;
  R.espera = async (n) => { for (let i = 0; i < (n || 400); i++) await new Promise((r) => setImmediate(r)); };
  R.mueve = (ms) => { ahora += ms; };
  return R;
}

const TOR = Buffer.concat([Buffer.from("d8:announce1:x4:infod6:lengthi9e4:name3:abce"), Buffer.alloc(300, 7), Buffer.from("e")]);
const T0 = 1.8e12;
const CTX_OK = { url: "https://wolfmax4k.com/pelicula/r2xz6p", t: "Insidious: Fuera del más allá", q: "4K",
                 sz: "18,88 GB", code: "111111", ts: T0 - 60e3 };
const LS_OK = { mw_wfpend: JSON.stringify(CTX_OK) };
const torrentFD = (b, nombre) => { const fd = new FormData(); fd.append("f", new Blob([b || TOR], { type: "application/x-bittorrent" }), nombre || "a.torrent"); return fd; };
const NADA = { __kb: { np: null, tele: true } };
const APAGADA = { __kb: { np: null, tele: false } };

(async function () {
  console.log("\n=== 1) Del service worker a la pagina, sin perder el archivo ===");
  let caches = cacheStorage();
  let r = await cargaSW(caches, {})(torrentFD(TOR, "Insidious-(4K)-(WolfTorrent).torrent"));
  comprueba("el service worker contesta 303 a la app con ?compartido=1",
    r && r.status === 303 && r.headers.get("location") === "https://mw.test/?compartido=1", r && [r.status, r.headers.get("location")]);
  const caja = caches._cajas.get("mw-compartido");
  comprueba("...y el archivo queda en la cache del movil (no en el servidor), con su hora",
    caja && caja.has("/__compartido") && caja.get("/__compartido").cab["x-ts"]);
  let P = pagina(caches, { search: "?compartido=1", ls: LS_OK, codeActual: "111111", t0: T0,
                           now: [NADA, NADA, null, { title: "Insidious [4k]" }] });
  await P.ctx.wfRecoge();
  await P.espera();
  const s = P.subidas[0] || {};
  comprueba("la pagina lo recoge ENTERO: mismos bytes y mismo nombre", s.f && Buffer.compare(s.f.bytes, TOR) === 0
    && s.f.nombre === "Insidious-(4K)-(WolfTorrent).torrent", s.f && { n: s.f.nombre, b: s.f.bytes.length });
  comprueba("...y lo sube con SU sha256 (la que el servidor recalcula)", s.sha === sha256(TOR), s.sha);
  comprueba("...con su contexto: ficha, titulo, calidad, tamaño y la tele de entonces",
    s.url === CTX_OK.url && s.t === CTX_OK.t && s.q === "4K" && s.sz === "18,88 GB" && s.code === "111111", s);
  comprueba("...y se borra de la cache al recogerlo, y la direccion queda limpia",
    !caja.has("/__compartido") && !caja.has("/__compartido_diag") && P.quitado);
  comprueba("exacta + la misma tele elegida + conectada + libre: a la tele sin preguntar",
    P.envios.length === 1 && P.envios[0].cd === "111111" && P.dialogos.length === 0, [P.envios, P.dialogos]);
  const antesDeKodi = P.toasts.slice(0, P.toasts.findIndex((t) => /reproduciendo/.test(t)));
  comprueba("no dice 'reproduciendo' hasta que Kodi lo confirma; antes, 'esperando a que Kodi empiece'",
    antesDeKodi.length && !antesDeKodi.some((t) => /reproduciendo|▶/.test(t)) && antesDeKodi.some((t) => /Esperando a que Kodi/.test(t)), P.toasts);
  comprueba("...y cuando Kodi lo confirma, lo dice con lo que de verdad reproduce, y abre el mando de ESA tele",
    P.toasts.some((t) => /▶ Comedor está reproduciendo «Insidious \[4k\]»/.test(t)) && P.mando === 1, [P.toasts, P.mando]);
  comprueba("el contexto se borra al terminar", P.ctx.wfCtx() === null);
  const dg = P.diags.find((d) => d.via === "compartir") || {};
  comprueba("diagnostico: Chrome 153, el service worker recibio el archivo (sus bytes) y llego ENTERO a la pagina",
    dg.chrome === "153" && dg.sw_recibido === true && dg.sw_bytes === TOR.length && dg.pag_bytes === TOR.length
    && dg.integro === true && dg.resultado === "recogido", dg);
  comprueba("...lleva el codigo SOLO para autenticarse, y nada del titulo, del archivo ni de su huella",
    P.diags.every((d) => d.code === "111111") && !JSON.stringify(P.diags).match(/Insidious|announce|222222/)
    && !JSON.stringify(P.diags).includes(sha256(TOR)), P.diags);

  console.log("\n=== 2) La tele de destino, sin equivocos ===");
  P = pagina(cacheStorage(), { codeActual: "222222", now: [NADA, NADA, null, { title: "Insidious [4k]" }] });
  await P.ctx.wfEntrega(new Blob([TOR]), "a.torrent", CTX_OK);
  await P.espera();
  comprueba("si ahora hay OTRA tele elegida: pregunta '¿Lo pongo en Comedor?' diciendo cual hay elegida, y no manda nada",
    P.envios.length === 0 && P.dialogos.length === 1 && /¿Lo pongo en Comedor\?/.test(P.dialogos[0].titulo)
    && /ahora tienes elegida Dormitorio/.test(P.dialogos[0].texto), P.dialogos);
  P.dialogos[0].acepta();
  await P.espera();
  comprueba("...al aceptar, a Comedor; y el mando NO se abre (manejaria Dormitorio)",
    P.envios.length === 1 && P.envios[0].cd === "111111" && P.toasts.some((t) => /▶ Comedor está reproduciendo/.test(t))
    && P.mando === 0, [P.envios, P.toasts, P.mando]);
  P = pagina(cacheStorage(), { codeActual: "111111", now: [{ title: "Otra cosa" }, { title: "Otra cosa" }, { title: "Otra cosa" }, { title: "Insidious [4k]" }] });
  await P.ctx.wfEntrega(new Blob([TOR]), "a.torrent", CTX_OK);
  await P.espera();
  comprueba("si esa tele esta reproduciendo otra cosa: pregunta (no se corta nada por sorpresa)",
    P.envios.length === 0 && P.dialogos.length === 1 && /está reproduciendo «Otra cosa»/.test(P.dialogos[0].texto), P.dialogos);
  P.dialogos[0].acepta();
  await P.espera();
  comprueba("...y al aceptar, 'reproduciendo' solo cuando suena LO NUEVO (no lo que ya sonaba)",
    P.envios.length === 1 && !P.toasts.some((t) => /reproduciendo «Otra cosa»/.test(t))
    && P.toasts.some((t) => /reproduciendo «Insidious \[4k\]»/.test(t)), P.toasts);
  P = pagina(cacheStorage(), { codeActual: "111111", now: [APAGADA, APAGADA] });
  await P.ctx.wfEntrega(new Blob([TOR]), "a.torrent", CTX_OK);
  await P.espera();
  comprueba("si esa tele no esta conectada: pregunta, diciendolo",
    P.envios.length === 0 && P.dialogos.length === 1 && /Comedor no está conectada/.test(P.dialogos[0].texto), P.dialogos);
  P.dialogos[0].acepta();
  await P.espera();
  comprueba("...y si al aceptar sigue sin estar: NO manda nada y vuelve a preguntar (Reintentar)",
    P.envios.length === 0 && P.dialogos.length === 2 && /no está conectada/.test(P.dialogos[1].titulo)
    && P.dialogos[1].etiqueta === "Reintentar" && /No he mandado nada/.test(P.dialogos[1].texto), P.dialogos);
  P = pagina(cacheStorage(), { codeActual: "111111", kbFalla: true });
  await P.ctx.wfEntrega(new Blob([TOR]), "a.torrent", CTX_OK);
  await P.espera();
  comprueba("si no se puede saber como esta la tele: pregunta, no manda",
    P.envios.length === 0 && P.dialogos.length === 1 && /¿Lo pongo en Comedor\?/.test(P.dialogos[0].titulo), P.dialogos);

  console.log("\n=== 3) Con duda se pregunta; si no casa, no se manda ===");
  P = pagina(cacheStorage(), { codeActual: "111111", wfsube: { st: 200, d: { ok: true, tipo: "torrent", nombre: "Insidious [4k]", link: "magnet:?x", certeza: "titulo" } },
                               now: [NADA, NADA, { title: "Insidious [4k]" }] });
  await P.ctx.wfEntrega(new Blob([TOR]), "a.torrent", CTX_OK);
  await P.espera();
  comprueba("solo por titulo (sin tamaño): pregunta '¿Lo pongo en Comedor?' y NO manda nada aun",
    P.dialogos.length === 1 && /¿Lo pongo en Comedor\?/.test(P.dialogos[0].titulo) && P.envios.length === 0, P.dialogos);
  P.dialogos[0].acepta();
  await P.espera();
  comprueba("...al aceptar, a esa tele", P.envios.length === 1 && P.envios[0].cd === "111111", P.envios);
  P = pagina(cacheStorage(), { wfsube: { st: 409, d: { ok: false, error: "Ese .torrent no parece de «Insidious»: es «Spider-Man»." } } });
  await P.ctx.wfEntrega(new Blob([TOR]), "a.torrent", CTX_OK);
  await P.espera();
  comprueba("no casa (409): lo dice y NO lo manda a la tele",
    P.envios.length === 0 && P.dialogos.length === 1 && /No es «Insidious: Fuera del más allá»/.test(P.dialogos[0].titulo)
    && /No lo mando a la tele/.test(P.dialogos[0].texto), P.dialogos);
  P = pagina(cacheStorage(), { codeActual: "222222", wfsube: { st: 200, d: { ok: true, tipo: "torrent", nombre: "Algo", link: "magnet:?y", certeza: "sin_ficha" } } });
  await P.ctx.wfEntrega(new Blob([TOR]), "a.torrent", null);
  await P.espera();
  comprueba("sin contexto (no se sabe de que titulo es): pregunta, y no manda nada sin un si",
    P.dialogos.length === 1 && /No sé de qué título es/.test(P.dialogos[0].texto) && P.envios.length === 0, P.dialogos);

  console.log("\n=== 4) Un contexto caducado, roto o ajeno nunca manda solo ===");
  const malos = {
    "de hace mas de 30 min": JSON.stringify(Object.assign({}, CTX_OK, { ts: T0 - 31 * 60e3 })),
    "del futuro": JSON.stringify(Object.assign({}, CTX_OK, { ts: T0 + 5 * 60e3 })),
    "JSON roto": "{url:",
    "de otra web": JSON.stringify(Object.assign({}, CTX_OK, { url: "https://evil.example/pelicula/r2xz6p" })),
    "con un codigo raro": JSON.stringify(Object.assign({}, CTX_OK, { code: "11111a" })),
    "que no es un objeto": "42",
  };
  for (const [nombre, valor] of Object.entries(malos)) {
    caches = cacheStorage();
    await cargaSW(caches, {})(torrentFD());
    P = pagina(caches, { search: "?compartido=1", ls: { mw_wfpend: valor }, codeActual: "111111", t0: T0 });
    await P.ctx.wfRecoge();
    await P.espera();
    comprueba("contexto " + nombre + ": no vale, se borra, y lo compartido PREGUNTA (nada enviado solo)",
      P.envios.length === 0 && P.dialogos.length === 1 && /No sé de qué título es/.test(P.dialogos[0].texto)
      && !P.ls.has("mw_wfpend") && P.subidas.length === 1 && !("url" in P.subidas[0]), [P.dialogos, P.envios]);
  }
  P = pagina(cacheStorage(), { ls: LS_OK, codeActual: "111111", sendPlayReal: true });
  P.ctx.sendPlay({ a: "dt", c: "12345", tb: "peliculas", t: "Otra peli" });
  await P.espera();
  comprueba("otra reproduccion (sendPlay de siempre) anula el contexto pendiente", !P.ls.has("mw_wfpend") && P.ctx.wfCtx() === null);
  comprueba("...y sendPlay sigue igual: la misma orden, '▶ En la tele' y el mando abierto",
    P.kbsend.length === 1 && JSON.stringify(P.kbsend[0]) === JSON.stringify({ code: "111111", cmd: "play_ref", a: "dt", t: "Otra peli", c: "12345", tb: "peliculas" })
    && P.toasts.includes("▶ En la tele") && P.mando === 1, [P.kbsend, P.toasts]);
  P = pagina(cacheStorage(), { ls: LS_OK, codeActual: "111111", sendPlayReal: true, kbsend: { ok: true, tele: false } });
  P.ctx.sendPlay({ a: "pl", u: "magnet:?xt=urn:btih:bb", t: "X" });
  await P.espera();
  comprueba("...tele apagada por la via de siempre: su aviso de siempre",
    P.toasts.some((t) => /Tu tele no está conectada/.test(t)) && P.kbsend[0].u === "magnet:?xt=urn:btih:bb", P.toasts);

  console.log("\n=== 5) Si no llega bien: un error que se ve, el selector a mano y nada subido ===");
  P = pagina(cacheStorage(), { search: "?compartido=1", ls: LS_OK });
  let res = await P.ctx.wfRecoge();
  comprueba("?compartido=1 pero la cache vacia: error visible, nada subido ni enviado",
    res === "fallo" && P.dialogos.length === 1 && /No he podido recibir el archivo/.test(P.dialogos[0].titulo)
    && P.subidas.length === 0 && P.envios.length === 0, P.dialogos);
  P.dialogos[0].acepta();
  comprueba("...y su boton abre el selector de Descargas", P.dialogos[0].etiqueta === "Elegir el archivo" && P.elige === 1);
  for (const q of ["sinsw", "0"]) {
    P = pagina(cacheStorage(), { search: "?compartido=" + q });
    res = await P.ctx.wfRecoge();
    comprueba("?compartido=" + q + " (no paso por el service worker / sin archivo): error visible con selector",
      res === "fallo" && P.dialogos.length === 1 && P.dialogos[0].etiqueta === "Elegir el archivo");
  }
  r = await cargaSW(caches = cacheStorage(), {})(new FormData());
  comprueba("compartir sin archivo: el service worker manda ?compartido=0 (que acaba en ese error)",
    r && r.headers.get("location") === "https://mw.test/?compartido=0");
  caches = cacheStorage();
  let fd = new FormData();
  fd.append("title", "Insidious.torrent");
  fd.append("text", "content://downloads/1234");        // lo que mandaria un Chrome que pierde el archivo
  r = await cargaSW(caches, {})(fd);
  P = pagina(caches, { search: "?compartido=0", ls: LS_OK, codeActual: "111111" });
  res = await P.ctx.wfRecoge();
  const dp = P.diags[0] || {};
  comprueba("un Chrome que manda el formulario SIN el archivo: error visible, nada subido, y queda dicho cual y que trajo",
    r.headers.get("location") === "https://mw.test/?compartido=0" && res === "fallo" && P.subidas.length === 0
    && P.dialogos.length === 1 && dp.resultado === "sw_sin_archivo" && dp.sw_recibido === false && dp.chrome === "153"
    && JSON.stringify(dp.campos) === JSON.stringify(["title", "text"]), dp);
  const comparteYToca = async (toca, opSW, opCache) => {
    const c = cacheStorage(opCache);
    await cargaSW(c, opSW || {})(torrentFD());
    const cj = c._cajas.get("mw-compartido");
    if (toca) toca(cj);
    const p = pagina(c, { search: "?compartido=1", ls: LS_OK, codeActual: "111111" });
    const rr = await p.ctx.wfRecoge();
    await p.espera();
    return { P: p, res: rr, cj };
  };
  let x = await comparteYToca((cj) => { cj.set("/__compartido", Object.assign({}, cj.get("/__compartido"), { cuerpo: TOR.subarray(0, 100) })); });
  comprueba("llega INCOMPLETO a la pagina: error visible con los bytes, nada subido",
    x.res === "fallo" && x.P.subidas.length === 0 && /incompleto \(100 de/.test(x.P.dialogos[0] && x.P.dialogos[0].texto)
    && (x.P.diags[0] || {}).integro === false && (x.P.diags[0] || {}).resultado === "incompleto", [x.P.dialogos, x.P.diags]);
  x = await comparteYToca((cj) => { const b = Buffer.from(TOR); b[50] ^= 1; cj.set("/__compartido", Object.assign({}, cj.get("/__compartido"), { cuerpo: b })); });
  comprueba("llega del MISMO tamaño pero alterado: no coincide la huella -> error, nada subido",
    x.res === "fallo" && x.P.subidas.length === 0 && (x.P.diags[0] || {}).resultado === "alterado" && x.P.dialogos.length === 1, [x.P.dialogos, x.P.diags]);
  x = await comparteYToca(null, { sinCrypto: true });
  comprueba("si el service worker no pudo calcular la huella: no se da por bueno, nada subido",
    x.res === "fallo" && x.P.subidas.length === 0 && (x.P.diags[0] || {}).resultado === "alterado", x.P.diags);
  P = pagina(cacheStorage(), { codeActual: "111111", sinCrypto: true });
  await P.ctx.wfEntrega(new Blob([TOR]), "a.torrent", CTX_OK);
  await P.espera();
  comprueba("si la pagina no tiene la API criptografica: se bloquea la subida",
    P.subidas.length === 0 && P.dialogos.length === 1 && /No se ha podido comprobar el archivo/.test(P.dialogos[0].texto), P.dialogos);
  P = pagina(cacheStorage(), { wfsube: { st: 400, d: { ok: false, error: "Eso no es un archivo .torrent." } } });
  await P.ctx.wfEntrega(new Blob([Buffer.from("<html>")]), "x.torrent", CTX_OK);
  await P.espera();
  comprueba("un archivo que no es un .torrent: error visible con selector, nada enviado",
    P.envios.length === 0 && P.dialogos.length === 1 && /no es un archivo \.torrent/.test(P.dialogos[0].texto));

  console.log("\n=== 6) El tope de tamaño, antes de leer ===");
  caches = cacheStorage();
  r = await cargaSW(caches, {})(torrentFD(Buffer.alloc(MAX + 1, 1), "video.mp4"));
  let cj = caches._cajas.get("mw-compartido");
  const dsw = JSON.parse(cj.get("/__compartido_diag").cuerpo.toString());
  comprueba("service worker: algo de mas de 15 MB no se guarda; ?compartido=0 y queda dicho que era enorme",
    r.headers.get("location") === "https://mw.test/?compartido=0" && !cj.has("/__compartido") && dsw.grande === true
    && dsw.bytes === MAX + 1 && dsw.recibido === false, dsw);
  P = pagina(caches, { search: "?compartido=0", ls: LS_OK, codeActual: "111111" });
  res = await P.ctx.wfRecoge();
  comprueba("...y la pagina lo dice ('demasiado grande') con el selector, sin subir nada",
    res === "fallo" && P.subidas.length === 0 && /demasiado grande/.test(P.dialogos[0] && P.dialogos[0].texto)
    && (P.diags[0] || {}).resultado === "demasiado_grande" && cj.size === 0, [P.dialogos, P.diags]);
  let leido = 0;
  const enorme = { size: MAX + 1, name: "video.mp4", arrayBuffer: async () => { leido++; return new ArrayBuffer(8); } };
  P = pagina(cacheStorage(), { codeActual: "111111" });
  P.ctx.wfSube({ files: [enorme] });
  await P.espera();
  comprueba("selector ('Ya lo tengo'): lo enorme se rechaza SIN leerlo y sin subirlo",
    leido === 0 && P.subidas.length === 0 && /demasiado grande/.test(P.dialogos[0] && P.dialogos[0].texto), P.dialogos);
  P = pagina(cacheStorage(), { codeActual: "111111" });
  P.ctx.wfSubeZip({ files: [enorme] });
  await P.espera();
  comprueba("ZIP del dia: igual, sin leerlo ni subirlo", leido === 0 && P.subidas.length === 0
    && P.toasts.some((t) => /demasiado grande/.test(t)), P.toasts);

  console.log("\n=== 7) Limpieza: lo huerfano fuera, lo que se esta recogiendo no se toca ===");
  caches = cacheStorage();
  await cargaSW(caches, {})(torrentFD());
  cj = caches._cajas.get("mw-compartido");
  P = pagina(caches, { search: "", t0: T0 + 60e3 });              // abierta normal 1 min despues
  comprueba("abrir la app normal no hace nada de esto", (await P.ctx.wfRecoge()) === "nada" && !P.dialogos.length && !P.subidas.length);
  comprueba("...y NO borra lo compartido hace 1 min (puede estar recogiendolo otra apertura)",
    cj.has("/__compartido") && cj.has("/__compartido_diag"));
  P = pagina(caches, { search: "", t0: T0 + 11 * 60e3 });         // y 11 min despues
  await P.ctx.wfRecoge();
  comprueba("lo que lleva mas de 10 min sin recoger (la app se cerro): fuera, archivo y diagnostico", cj.size === 0);
  caches = cacheStorage();
  await cargaSW(caches, {})(torrentFD());
  P = pagina(caches, { search: "?compartido=1", ls: LS_OK, codeActual: "111111", t0: T0 + 11 * 60e3 });
  res = await P.ctx.wfRecoge();
  comprueba("si al abrir con ?compartido=1 lo guardado es de hace rato: no se usa, se borra y se dice",
    res === "fallo" && P.subidas.length === 0 && (P.diags[0] || {}).resultado === "caducado"
    && caches._cajas.get("mw-compartido").size === 0, [P.dialogos, P.diags]);
  caches = cacheStorage();
  await cargaSW(caches, {})(torrentFD(TOR, "primero.torrent"));
  await cargaSW(caches, {})(new FormData());                                 // otro compartir, sin archivo
  comprueba("un compartir nuevo borra el archivo del anterior: nunca se empareja uno viejo con un diagnostico nuevo",
    !caches._cajas.get("mw-compartido").has("/__compartido"));
  caches = cacheStorage();
  await cargaSW(caches, {})(torrentFD());
  const A1 = pagina(caches, { search: "?compartido=1", ls: LS_OK, codeActual: "111111", now: [NADA, NADA, { title: "Insidious [4k]" }] });
  const A2 = pagina(caches, { search: "?compartido=1", ls: LS_OK, codeActual: "111111", now: [NADA, NADA, { title: "Insidious [4k]" }] });
  const [r1, r2] = await Promise.all([A1.ctx.wfRecoge(), A2.ctx.wfRecoge()]);
  await A1.espera(); await A2.espera();
  comprueba("dos aperturas a la vez: una lo recoge, la otra no hace nada (ni error falso, ni doble envio)",
    [r1, r2].sort().join() === "otra,recogido" && A1.subidas.length + A2.subidas.length === 1
    && A1.envios.length + A2.envios.length === 1 && A1.dialogos.length + A2.dialogos.length === 0, [r1, r2]);
  x = await comparteYToca(null, {}, { rompeBlob: true });
  comprueba("si falla la lectura a medias: error visible y no se queda nada en el movil",
    x.res === "fallo" && x.P.subidas.length === 0 && x.cj.size === 0 && (x.P.diags[0] || {}).resultado === "error_lectura", x.P.diags);

  console.log("\n=== 8) Nunca 'reproduciendo' sin confirmacion de Kodi ===");
  P = pagina(cacheStorage(), { codeActual: "111111", send: { ok: true, tele: false } });
  await P.ctx.wfEntrega(new Blob([TOR]), "a.torrent", CTX_OK);
  await P.espera();
  comprueba("la tele se desconecta justo al mandar: lo dice, y que aun no se reproduce nada",
    P.toasts.some((t) => /no está conectada.*Aún no se reproduce nada/.test(t)) && !P.toasts.some((t) => /reproduciendo/.test(t)), P.toasts);
  P = pagina(cacheStorage(), { codeActual: "111111" });
  await P.ctx.wfEntrega(new Blob([TOR]), "a.torrent", CTX_OK);
  await P.espera(6000);                 // 2 min de reloj de mentira, a 3 s por vuelta
  comprueba("Kodi no confirma en 2 min: lo dice tal cual, sin inventarse que va",
    P.toasts.some((t) => /aún no ha confirmado/.test(t)) && !P.toasts.some((t) => /reproduciendo/.test(t)), P.toasts);
  P = pagina(cacheStorage(), { codeActual: "111111", send: null });
  await P.ctx.wfEntrega(new Blob([TOR]), "a.torrent", CTX_OK);
  await P.espera();
  comprueba("si el servidor no acepta la orden: ni 'pedido' ni 'reproduciendo'",
    P.envios.length === 1 && !P.toasts.some((t) => /Pedido a|reproduciendo/.test(t)), P.toasts);

  console.log("\n=== 9) 'Ya lo tengo' y el ZIP del dia van con su huella ===");
  P = pagina(cacheStorage(), { codeActual: "111111", wfpide: { url: CTX_OK.url, t: CTX_OK.t, q: "4K", sz: "18,88 GB" }, now: [NADA, NADA, { title: "X" }] });
  P.ctx.wfSube({ files: [Object.assign(new Blob([TOR]), { name: "elegido.torrent" })] });
  await P.espera();
  comprueba("el selector sube con el mismo contexto, su huella, y decide igual",
    P.subidas.length === 1 && P.subidas[0].url === CTX_OK.url && P.subidas[0].sz === "18,88 GB"
    && P.subidas[0].sha === sha256(TOR) && P.envios.length === 1, P.subidas);
  P = pagina(cacheStorage(), { codeActual: "111111", wfsube: { st: 200, d: { ok: true, tipo: "zip", n: 3, dia: "2026-10-10", emparejados: 2 } } });
  const ZIP = Buffer.from("PK\x03\x04 zip de mentira");
  P.ctx.wfSubeZip({ files: [Object.assign(new Blob([ZIP]), { name: "dia.zip" })] });
  await P.espera();
  comprueba("el ZIP del dia sube con su sha256 y se guarda como siempre",
    P.subidas.length === 1 && P.subidas[0].sha === sha256(ZIP) && P.toasts.some((t) => /3 torrents de WolfMax guardados/.test(t)), [P.subidas, P.toasts]);

  console.log("\n---- VEREDICTO ----");
  if (fallos) { console.log(fallos + " comprobaciones MAL"); process.exit(1); }
  console.log("TODO OK: lo compartido llega entero, va a SU tele solo con certeza, y nada se da por hecho");
})().catch((e) => { console.log("EXCEPCION", e); process.exit(1); });
