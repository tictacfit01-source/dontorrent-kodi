// La extension de Chrome "MejorWolf · WolfTorrent -> tele" (dtbl76), sin Chrome:
// se carga su background.js REAL en una caja de arena con un chrome.* y un
// fetch de mentira, y se le hacen pasar descargas como las de verdad.
"use strict";
const fs = require("fs");
const path = require("path");
const vm = require("vm");
const crypto = require("crypto");

const DIR = path.join(__dirname, "..", "extension-wolf");
let fallos = 0;
function comprueba(nombre, ok, detalle) {
  console.log((ok ? "  ok   " : "  MAL  ") + nombre + (ok ? "" : "  -> " + JSON.stringify(detalle)));
  if (!ok) fallos++;
}

const L = require(path.join(DIR, "logica.js"));

console.log("\n=== 1) Que descargas son de WolfTorrent y estan bien ===");
const T = "https://wolftorrent.com/torrents/series/Ted_Lasso_1_5_HDTV_720p.torrent";
const buena = { id: 7, state: "complete", url: T, finalUrl: T, filename: "C:\\Users\\x\\Downloads\\Ted.torrent",
                referrer: "https://wolftorrent.com/descarga/serie/2s42pf", totalBytes: 900, bytesReceived: 900,
                exists: true, mime: "application/x-bittorrent" };
comprueba("un .torrent de wolftorrent.com, completo: si", L.esDeWolf(buena) && L.descargaValida(buena));
comprueba("de otra web: no", !L.esDeWolf(Object.assign({}, buena, { url: "https://evil.example/a.torrent",
                                                                     finalUrl: "https://evil.example/a.torrent" })));
comprueba("un .exe de wolftorrent.com: no", !L.esDeWolf(Object.assign({}, buena, {
  filename: "x.exe", url: "https://wolftorrent.com/x.exe", finalUrl: "https://wolftorrent.com/x.exe" })));
comprueba("a medias, con error, borrada o una pagina HTML: no",
  !L.descargaValida(Object.assign({}, buena, { bytesReceived: 100 }))
  && !L.descargaValida(Object.assign({}, buena, { error: "NETWORK_FAILED" }))
  && !L.descargaValida(Object.assign({}, buena, { exists: false }))
  && !L.descargaValida(Object.assign({}, buena, { mime: "text/html" }))
  && !L.descargaValida(Object.assign({}, buena, { state: "in_progress" })));

function benc(x) {
  if (typeof x === "number") return Buffer.from("i" + x + "e");
  if (typeof x === "string") x = Buffer.from(x);
  if (Buffer.isBuffer(x)) return Buffer.concat([Buffer.from(x.length + ":"), x]);
  if (Array.isArray(x)) return Buffer.concat([Buffer.from("l"), ...x.map(benc), Buffer.from("e")]);
  const ks = Object.keys(x).sort();
  return Buffer.concat([Buffer.from("d"), ...ks.map(k => Buffer.concat([benc(k), benc(x[k])])), Buffer.from("e")]);
}
const TOR = benc({ announce: "udp://tracker.opentrackr.org:1337/announce",
                   info: { name: "Ted Lasso [HDTV 720p][Cap.105]", length: 1406669373, "piece length": 2097152,
                           pieces: Buffer.alloc(20) } });
const TOR2 = benc({ announce: "x", info: { name: "Otra", length: 123456789, "piece length": 1, pieces: Buffer.alloc(20) } });
const ZIP = (function () {        // un ZIP de verdad (sin comprimir) con un .torrent dentro
  const nom = Buffer.from("a.torrent");
  const crc = (function (b) { let c, t = [], k; for (let n = 0; n < 256; n++) { c = n; for (k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1; t[n] = c >>> 0; }
    c = 0xffffffff; for (const x of b) c = t[(c ^ x) & 0xff] ^ (c >>> 8); return (c ^ 0xffffffff) >>> 0; })(TOR);
  const lh = Buffer.alloc(30); lh.writeUInt32LE(0x04034b50, 0); lh.writeUInt16LE(20, 4); lh.writeUInt32LE(crc, 14);
  lh.writeUInt32LE(TOR.length, 18); lh.writeUInt32LE(TOR.length, 22); lh.writeUInt16LE(nom.length, 26);
  const cd = Buffer.alloc(46); cd.writeUInt32LE(0x02014b50, 0); cd.writeUInt16LE(20, 4); cd.writeUInt16LE(20, 6);
  cd.writeUInt32LE(crc, 16); cd.writeUInt32LE(TOR.length, 20); cd.writeUInt32LE(TOR.length, 24); cd.writeUInt16LE(nom.length, 28);
  const off = lh.length + nom.length + TOR.length;
  const eo = Buffer.alloc(22); eo.writeUInt32LE(0x06054b50, 0); eo.writeUInt16LE(1, 8); eo.writeUInt16LE(1, 10);
  eo.writeUInt32LE(cd.length + nom.length, 12); eo.writeUInt32LE(off, 16);
  return Buffer.concat([lh, nom, TOR, cd, nom, eo]);
})();
const HTML = Buffer.from("<!doctype html><html><head><title>Un momento…</title></head><body>verificando " + "x".repeat(200));
comprueba("por dentro: .torrent, ZIP, y una pagina HTML guardada con otro nombre no es nada",
  L.tipoDe(TOR) === "torrent" && L.tipoDe(ZIP) === "zip" && L.tipoDe(HTML) === ""
  && L.tipoDe(Buffer.concat([Buffer.from("d"), Buffer.alloc(100)])) === ""
  && L.tipoDe(ZIP.subarray(0, ZIP.length - 22)) === "", [L.tipoDe(TOR), L.tipoDe(ZIP)]);
comprueba("que hacer con cada respuesta del servidor",
  L.clasifica(200, { ok: true, tipo: "torrent", nombre: "x" }).estado === "enviado"
  && L.clasifica(0, null).estado === "pendiente" && L.clasifica(503, null).estado === "pendiente"
  && L.clasifica(429, { ok: false }).estado === "pendiente"
  && L.clasifica(403, { ok: false, reintentar: true }).estado === "pendiente"
  && L.clasifica(409, { ok: false, error: "no casa" }).estado === "fallido"
  && L.clasifica(401, { ok: false }).estado === "falta_codigo");
comprueba("reintentos: 1, 5, 15, 60 y 180 min, y luego se para (nada de bucles a ciegas)",
  L.proximo(1, 0) === 60e3 && L.proximo(5, 0) === 180 * 60e3 && L.proximo(6, 0) === null);
const d8 = new Date(2026, 9, 11, 8, 0), d6 = new Date(2026, 9, 11, 6, 30);
comprueba("el ZIP de ayer esta pendiente desde las 07:00 si lo ultimo es de antes",
  L.zipPendiente("2026-10-09", d8) && !L.zipPendiente("2026-10-10", d8) && !L.zipPendiente("2026-10-09", d6)
  && L.zipPendiente("", d8));

console.log("\n=== 2) El background.js real, con un Chrome de mentira ===");
let AHORA = new Date(2026, 9, 11, 8, 0).getTime();
class FakeDate extends Date {
  constructor(...a) { if (a.length) super(...a); else super(AHORA); }
  static now() { return AHORA; }
}
function almacen() { const d = {}; return {
  get(def) { const o = {}; for (const k of Object.keys(def)) o[k] = k in d ? JSON.parse(JSON.stringify(d[k])) : def[k]; return Promise.resolve(o); },
  set(o) { for (const k of Object.keys(o)) d[k] = JSON.parse(JSON.stringify(o[k])); return Promise.resolve(); },
  _d: d }; }
const oyentes = {};
function evento(n) { return { addListener(f) { (oyentes[n] = oyentes[n] || []).push(f); } }; }
const AVISOS = [], POSTS = [], BADGE = { text: "" }, PESTANAS = [];
let RELAY_RESP = [];              // lo que contesta /wfsube, en orden
let CATDIAG_ZIP = {};
const ARCHIVOS = {};              // url -> bytes que devuelve WolfTorrent al recogerlo
const DESCARGAS = {};             // id -> DownloadItem
const memoria = {};
const chrome = {
  storage: { sync: almacen(), local: almacen(), session: almacen() },
  downloads: { onChanged: evento("descarga"), search: q => Promise.resolve(DESCARGAS[q.id] ? [DESCARGAS[q.id]] : []) },
  alarms: { create: () => Promise.resolve(), onAlarm: evento("alarma") },
  notifications: { create: (id, o) => { AVISOS.push(Object.assign({ id }, o)); }, onClicked: evento("clic"), clear() {} },
  runtime: { onInstalled: evento("instala"), onStartup: evento("arranca"), onMessage: evento("mensaje") },
  action: { setBadgeText: o => { BADGE.text = o.text; return Promise.resolve(); },
            setBadgeBackgroundColor: o => { BADGE.color = o.color; return Promise.resolve(); } },
  tabs: { create: o => { PESTANAS.push(o.url); } },
};
async function fetchFalso(url, op) {
  op = op || {};
  if (url.indexOf("https://wolftorrent.com/") === 0) {
    const b = ARCHIVOS[url];
    if (!b) return { ok: false, status: 404, arrayBuffer: async () => new ArrayBuffer(0) };
    return { ok: true, status: 200, arrayBuffer: async () => b.buffer.slice(b.byteOffset, b.byteOffset + b.length) };
  }
  if (url === L.RELAY + "/wfsube") {
    const campos = {};
    for (const [k, v] of op.body.entries()) campos[k] = typeof v === "string" ? v : { nombre: v.name, bytes: v.size };
    POSTS.push(campos);
    const r = RELAY_RESP.length ? RELAY_RESP.shift() : { st: 200, js: { ok: true, tipo: "torrent", nombre: "x" } };
    if (r.st === 0) throw new Error("sin red");
    return { ok: r.st < 300, status: r.st, json: async () => r.js };
  }
  if (url === L.RELAY + "/catdiag") return { ok: true, status: 200, json: async () => ({ wfih: { zip: CATDIAG_ZIP } }) };
  throw new Error("destino NO autorizado: " + url);
}
const caja = vm.createContext({
  chrome, fetch: fetchFalso, FormData, Blob, URL, Uint8Array, ArrayBuffer, Promise, console, Date: FakeDate,
  crypto: { subtle: crypto.webcrypto.subtle }, setTimeout, clearTimeout,
  MWAlmacen: { guarda: async (id, b) => { memoria[id] = b; }, lee: async id => memoria[id], borra: async id => { delete memoria[id]; } },
});
caja.self = caja;
caja.importScripts = f => vm.runInContext(fs.readFileSync(path.join(DIR, f), "utf8"), caja, { filename: f });
vm.runInContext(fs.readFileSync(path.join(DIR, "background.js"), "utf8"), caja, { filename: "background.js" });
const X = caja.MWExt;
const cola = () => chrome.storage.local._d.cola || {};

(async function () {
  await chrome.storage.sync.set({ code: "123456" });
  ARCHIVOS[T] = TOR;
  DESCARGAS[7] = buena;
  let r = await X.nuevaDescarga(buena);
  comprueba("termina un .torrent de WolfTorrent: va a MejorWolf con el codigo, la pagina de origen y el archivo",
    r === "enviado" && POSTS.length === 1 && POSTS[0].code === "123456"
    && POSTS[0].ref === "https://wolftorrent.com/descarga/serie/2s42pf" && POSTS[0].f.bytes === TOR.length
    && cola().d7.estado === "enviado" && BADGE.text === "" && !memoria.d7, { r, POSTS, c: cola() });
  r = await X.nuevaDescarga(buena);
  comprueba("el mismo aviso de Chrome otra vez: no se envia dos veces", r === "repetida" && POSTS.length === 1, r);
  const otra = Object.assign({}, buena, { id: 8, filename: "Ted (1).torrent" });
  r = await X.nuevaDescarga(otra);
  comprueba("el MISMO archivo bajado otra vez: 'repetido', sin enviarlo", r === "duplicado" && POSTS.length === 1, r);

  const H = "https://wolftorrent.com/torrents/peliculas/Trampa.torrent";
  ARCHIVOS[H] = HTML;
  r = await X.nuevaDescarga(Object.assign({}, buena, { id: 9, url: H, finalUrl: H, filename: "Trampa.torrent" }));
  comprueba("una pagina HTML guardada como .torrent: fallida, dicha, y nada enviado",
    r === "fallido" && POSTS.length === 1 && /no es un \.torrent/.test(cola().d9.texto), cola().d9);
  r = await X.nuevaDescarga(Object.assign({}, buena, { id: 10, url: "https://otra.web/a.torrent", finalUrl: "https://otra.web/a.torrent" }));
  comprueba("una descarga de otra web: ni se mira", r === "ajena" && !cola().d10, r);
  r = await X.nuevaDescarga(Object.assign({}, buena, { id: 11, bytesReceived: 5 }));
  comprueba("una descarga a medias: ni se mira", r === "incompleta" && !cola().d11, r);

  console.log("\n=== 3) Si no se puede entregar: en cola y reintento, sin duplicar ===");
  const T2 = "https://wolftorrent.com/torrents/series/Otra.torrent";
  ARCHIVOS[T2] = TOR2;
  RELAY_RESP = [{ st: 403, js: { ok: false, reintentar: true, error: "MejorWolf no conoce ese código" } }];
  r = await X.nuevaDescarga(Object.assign({}, buena, { id: 12, url: T2, finalUrl: T2, filename: "Otra.torrent" }));
  comprueba("la tele aun no se ha conectado (403): pendiente, con su archivo guardado y el icono lo cuenta (con el fallido de antes: 2)",
    r === "pendiente" && memoria.d12 && BADGE.text === "2" && cola().d12.prox === AHORA + 60e3, { r, b: BADGE, c: cola().d12 });
  await X.reintenta(false);
  comprueba("antes de su hora no se reintenta", POSTS.length === 2, POSTS.length);
  AHORA += 61e3;
  delete ARCHIVOS[T2];                          // aunque WolfTorrent ya no lo diera
  r = await X.reintenta(false);
  comprueba("a su hora se reintenta con el archivo GUARDADO (sin volver a WolfTorrent) y sale",
    r[0] === "enviado" && POSTS.length === 3 && !memoria.d12 && BADGE.text === "1", { r, n: POSTS.length });

  RELAY_RESP = [{ st: 409, js: { ok: false, error: "Ese .torrent no casa con su ficha" } }];
  const T3 = "https://wolftorrent.com/torrents/peliculas/No.torrent";
  ARCHIVOS[T3] = benc({ announce: "y", info: { name: "No", length: 5, "piece length": 1, pieces: Buffer.alloc(20) } });
  r = await X.nuevaDescarga(Object.assign({}, buena, { id: 13, url: T3, finalUrl: T3, filename: "No.torrent" }));
  comprueba("si MejorWolf dice que no casa (409): fallido, con el motivo, y no insiste",
    r === "fallido" && /no casa/.test(cola().d13.texto) && BADGE.color === "#d93025" && !memoria.d13, cola().d13);

  await chrome.storage.sync.set({ code: "" });
  const T4 = "https://wolftorrent.com/torrents/peliculas/Sin.torrent";
  ARCHIVOS[T4] = benc({ announce: "z", info: { name: "Sin", length: 9, "piece length": 1, pieces: Buffer.alloc(20) } });
  const n0 = POSTS.length;
  r = await X.nuevaDescarga(Object.assign({}, buena, { id: 14, url: T4, finalUrl: T4, filename: "Sin.torrent" }));
  comprueba("sin el codigo de la tele: ni se intenta, y lo dice", r === "falta_codigo" && POSTS.length === n0, r);
  await chrome.storage.sync.set({ code: "123456" });
  r = await X.reintenta(true);
  comprueba("al ponerlo (opciones -> reintentar), sale", r.indexOf("enviado") >= 0 && cola().d14.estado === "enviado", r);

  console.log("\n=== 4) El ZIP del dia y el aviso de la mañana ===");
  const Z = "https://wolftorrent.com/descarga/archivo/abcdefghijklmnopqrstuv/WolfTorrent-2026-10-10.zip";
  ARCHIVOS[Z] = ZIP;
  RELAY_RESP = [{ st: 200, js: { ok: true, tipo: "zip", n: 93, dia: "2026-10-10", emparejados: 80 } }];
  r = await X.nuevaDescarga(Object.assign({}, buena, { id: 20, url: Z, finalUrl: Z, filename: "WolfTorrent-2026-10-10.zip",
                                                        mime: "application/zip", referrer: "https://wolftorrent.com/torrents-del-dia" }));
  comprueba("el ZIP: entregado entero y apuntado el dia", r === "enviado" && POSTS[POSTS.length - 1].f.bytes === ZIP.length
    && chrome.storage.sync._d.ultZip === "2026-10-10" && /93 torrents/.test(AVISOS[AVISOS.length - 1].message), r);
  comprueba("con el de ayer ya subido, no hay aviso", (await X.revisaZip(new FakeDate())) === "al dia");
  AHORA += 864e5;                               // dia siguiente, 08:00
  CATDIAG_ZIP = { dia: "2026-10-10" };
  const nav = AVISOS.length;
  r = await X.revisaZip(new FakeDate());
  comprueba("al dia siguiente, a las 8: aviso (una vez) que abre su pagina al pulsarlo",
    r === "avisado" && AVISOS.length === nav + 1 && AVISOS[nav].id === "zip-dia", r);
  oyentes.clic[0]("zip-dia");
  comprueba("...y al pulsarlo se abre la pagina del ZIP, nada mas", PESTANAS[0] === "https://wolftorrent.com/torrents-del-dia", PESTANAS);
  comprueba("no vuelve a avisar ese dia", (await X.revisaZip(new FakeDate())) === "ya avisado");
  AHORA += 864e5;
  CATDIAG_ZIP = { dia: AHORA ? L.iso(new Date(AHORA - 864e5)) : "" };
  comprueba("si otra persona de la familia ya lo subio, no avisa", (await X.revisaZip(new FakeDate())) === "al dia");

  console.log("\n=== 5) Permisos minimos y un solo destino ===");
  const man = JSON.parse(fs.readFileSync(path.join(DIR, "manifest.json"), "utf8"));
  comprueba("Manifest V3 con downloads, notifications, alarms y storage; nada mas",
    man.manifest_version === 3 && JSON.stringify(man.permissions.slice().sort()) ===
    JSON.stringify(["alarms", "downloads", "notifications", "storage"]), man.permissions);
  comprueba("solo dos webs: WolfTorrent (recoger lo bajado) y MejorWolf (entregarlo)",
    JSON.stringify(man.host_permissions) === JSON.stringify(["https://wolftorrent.com/*", L.RELAY + "/*"]), man.host_permissions);
  const src = fs.readFileSync(path.join(DIR, "background.js"), "utf8") + fs.readFileSync(path.join(DIR, "logica.js"), "utf8");
  const urls = (src.match(/https:\/\/[a-z0-9.-]+/gi) || []).map(u => u.toLowerCase());
  comprueba("en el codigo no hay ningun otro destino",
    urls.every(u => u === "https://wolftorrent.com" || u === L.RELAY), [...new Set(urls)]);
  comprueba("y no hay nada que abra, pulse o rellene la web de WolfTorrent (sin content scripts ni scripting)",
    !man.content_scripts && !/chrome\.scripting|executeScript/.test(src));

  console.log("\n---- VEREDICTO ----");
  if (fallos) { console.log(fallos + " comprobaciones MAL"); process.exit(1); }
  console.log("TODO OK: la extension recoge lo que bajas de WolfTorrent y lo entrega a MejorWolf, y nada mas");
})().catch(e => { console.log("EXCEPCION", e); process.exit(1); });
