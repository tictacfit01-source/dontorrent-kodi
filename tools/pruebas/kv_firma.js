// Las copias del relay en mw-sync (/kv/*, /wfidx): escribir SOLO con la firma
// del relay (dtbl77). Se carga el worker.js REAL con una D1 de mentira y una
// pareja de claves Ed25519 de PRUEBA (no la de produccion, que nadie conoce).
// Con argumento "--cruce <json>" verifica una firma hecha por el relay en Python
// (lo usa kv_firma.py): la misma comprobacion que hara el worker de verdad.
"use strict";
const fs = require("fs");
const os = require("os");
const path = require("path");
const { pathToFileURL } = require("url");
const subtle = require("crypto").webcrypto.subtle;

let fallos = 0;
function comprueba(nombre, ok, detalle) {
  console.log((ok ? "  ok   " : "  MAL  ") + nombre + (ok ? "" : "  -> " + JSON.stringify(detalle)));
  if (!ok) fallos++;
}

function d1() {
  const kv = new Map();
  return {
    kv,
    prepare(sql) {
      let args = [];
      const st = {
        bind(...a) { args = a; return st; },
        async first() { return kv.has(args[0]) ? { v: kv.get(args[0]).v, ts: kv.get(args[0]).ts } : null; },
        async run() { if (/INSERT INTO kv/.test(sql)) kv.set(args[0], { v: args[1], ts: args[2] }); return {}; },
      };
      return st;
    },
  };
}

async function carga() {
  const tmp = path.join(os.tmpdir(), "mw_worker_" + process.pid + ".mjs");
  fs.copyFileSync(path.join(__dirname, "..", "..", "sync-worker", "worker.js"), tmp);
  const W = (await import(pathToFileURL(tmp).href)).default;
  fs.unlinkSync(tmp);
  return W;
}

const b64 = (u) => Buffer.from(u).toString("base64");
const hex = async (u) => Buffer.from(await subtle.digest("SHA-256", u)).toString("hex");

async function firmaCon(priv, ts, ruta, cuerpo) {
  const msg = Buffer.from("mw-kv-v1\n" + ts + "\n" + ruta + "\n" + (await hex(cuerpo)));
  return b64(new Uint8Array(await subtle.sign({ name: "Ed25519" }, priv, msg)));
}

async function cruce(arg) {
  // {publica, ruta, cuerpo_b64, ts, firma}: lo que manda el relay de Python
  const c = JSON.parse(arg);
  const W = await carga();
  const env = { DB: d1(), KV_PUBLICA: c.publica, GOOGLE_CLIENT_ID: "" };
  const r = await W.fetch(new Request("https://mw-sync.example" + c.ruta, {
    method: "POST", headers: { "Content-Type": "application/json", "X-MW-Ts": String(c.ts), "X-MW-Firma": c.firma },
    body: Buffer.from(c.cuerpo_b64, "base64") }), env);
  console.log(JSON.stringify({ status: r.status, guardado: env.DB.kv.size }));
}

(async function () {
  if (process.argv[2] === "--cruce") return cruce(process.argv[3]);
  const W = await carga();
  const par = await subtle.generateKey({ name: "Ed25519" }, true, ["sign", "verify"]);
  const PUB = b64(new Uint8Array(await subtle.exportKey("raw", par.publicKey)));
  const otro = await subtle.generateKey({ name: "Ed25519" }, true, ["sign", "verify"]);
  const env = { DB: d1(), KV_PUBLICA: PUB, GOOGLE_CLIENT_ID: "" };
  const B = "https://mw-sync.example";
  const CUERPO = Buffer.from(JSON.stringify({ gz: "QUJD" }));
  const ahora = () => Math.floor(Date.now() / 1000);
  const post = (ruta, cab, e, cuerpo) => W.fetch(new Request(B + ruta, { method: "POST",
    headers: Object.assign({ "Content-Type": "application/json" }, cab || {}), body: cuerpo || CUERPO }), e || env);
  const get = (ruta, e) => W.fetch(new Request(B + ruta), e || env);

  console.log("\n=== Escenario 2: worker con la clave publica del relay ===");
  for (const ruta of ["/kv/semillas", "/kv/enriq", "/kv/wfih", "/wfidx"]) {
    const n0 = env.DB.kv.size;
    let r = await post(ruta);
    comprueba(ruta + ": sin firma, 401 y NADA escrito", r.status === 401 && env.DB.kv.size === n0, r.status);
    let ts = ahora();
    r = await post(ruta, { "X-MW-Ts": String(ts), "X-MW-Firma": await firmaCon(otro.privateKey, ts, ruta, CUERPO) });
    comprueba(ruta + ": firmado con OTRA clave, 401", r.status === 401 && env.DB.kv.size === n0, r.status);
    r = await post(ruta, { "X-MW-Ts": String(ts), "X-MW-Firma": await firmaCon(par.privateKey, ts, "/kv/otra", CUERPO) });
    comprueba(ruta + ": firma buena pero de OTRA ruta, 401", r.status === 401, r.status);
    r = await post(ruta, { "X-MW-Ts": String(ts), "X-MW-Firma": await firmaCon(par.privateKey, ts, ruta, CUERPO) },
                   env, Buffer.from(JSON.stringify({ gz: "TRAMPA" })));
    comprueba(ruta + ": firma buena pero el cuerpo CAMBIADO, 401", r.status === 401 && env.DB.kv.size === n0, r.status);
    const viejo = ts - 600;
    r = await post(ruta, { "X-MW-Ts": String(viejo), "X-MW-Firma": await firmaCon(par.privateKey, viejo, ruta, CUERPO) });
    comprueba(ruta + ": una firma de hace 10 min (reenvio), 401", r.status === 401, r.status);
    ts = ahora();
    r = await post(ruta, { "X-MW-Ts": String(ts), "X-MW-Firma": await firmaCon(par.privateKey, ts, ruta, CUERPO) });
    comprueba(ruta + ": firmado por el relay, se guarda", r.status === 200 && env.DB.kv.size === n0 + 1, r.status);
    const g = await (await get(ruta)).json();
    comprueba(ruta + ": y leer sigue siendo libre", g.gz === "QUJD", g);
  }

  console.log("\n=== Escenario 3: worker SIN la clave publica: cerrado, no abierto ===");
  for (const sin of [{ DB: d1(), GOOGLE_CLIENT_ID: "" }, { DB: d1(), KV_PUBLICA: "abc", GOOGLE_CLIENT_ID: "" }]) {
    for (const ruta of ["/kv/semillas", "/kv/enriq", "/kv/wfih", "/wfidx"]) {
      const ts = ahora();
      const r = await post(ruta, { "X-MW-Ts": String(ts), "X-MW-Firma": await firmaCon(par.privateKey, ts, ruta, CUERPO) }, sin);
      comprueba(ruta + ": " + (sin.KV_PUBLICA ? "con una clave publica rota" : "sin clave publica") +
                ", ninguna escritura entra (ni firmada)", r.status === 401 && sin.DB.kv.size === 0, r.status);
    }
  }

  console.log("\n=== Lo demas sigue igual ===");
  let r = await get("/ping");
  comprueba("/ping contesta", r.status === 200, r.status);
  r = await get("/kv/otra");
  comprueba("un nombre que no es de la lista, 404", r.status === 404, r.status);
  console.log("\n---- VEREDICTO ----");
  if (fallos) { console.log(fallos + " comprobaciones MAL"); process.exit(1); }
  console.log("TODO OK: las copias solo se escriben firmadas por el relay, y leer sigue libre");
})().catch((e) => { console.log("EXCEPCION", e); process.exit(1); });
