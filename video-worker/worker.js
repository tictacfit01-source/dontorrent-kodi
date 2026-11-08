// mw-video: el streaming de Cinubo en el propio móvil (dtbl127, 08-11-2026).
//
// El navegador no puede reproducir directamente lo que abre la tele:
//  - Lulustream da 403 a cualquier trozo sin "Referer: luluvdo.com", y un
//    navegador no puede poner ese Referer.
//  - Vidara sí deja (CORS *), pero su /api/stream no deja LEER la respuesta desde
//    otra web, así que el móvil no puede sacar el enlace él solo; y directo da
//    1,6 Mbit/s por conexión (el vídeo pide ~3).
// Este worker saca la emisión de la página del servidor y la sirve con las
// cabeceras de cada uno y CORS abierto. Medido el 08-11 desde casa: Vidara
// 14-35 Mbit/s y Lulustream 56-72 con UNA conexión. Los tokens llevan la IP de
// quien los pidió, pero ninguno la comprueba (probado: sacado aquí, abierto en casa).
//
// Reglas:
//  - NUNCA se reenvía un Range a la familia Vidara: su nginx guarda en caché la
//    primera respuesta de cada trozo, aunque sea un pedazo, y se la da a todos
//    (The Gentlemen 1x03, 03-11). Ver memoria "streaming-limite-por-conexion".
//  - /p solo sirve URLs firmadas por /r (no es un proxy abierto): la firma cubre
//    las cabeceras, el dominio y la caducidad. Una HMAC por petición.
//  - Nunca un cuerpo vacío: los errores salen en JSON con su motivo.

const UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0 Safari/537.36";
const CORS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Expose-Headers": "Content-Length,Content-Range,X-MW-Err",
};
const VIGENCIA_S = 12 * 3600;            // un capítulo largo con pausas
const VIDARA = /(^|\.)vidar+a+\.[a-z]{2,6}$/;
const LULU = /(^|\.)lulu[a-z]*\.[a-z]{2,6}$/;
const FIRE = /(^|\.)(firestream|frstream)\.[a-z]{2,6}$/;
// los que sirven los trozos de Vidara (97bf1.com, s1q2105.com...): sin Range
const SIN_RANGE = new Set();

const enc = new TextEncoder();
let _clave = null;
async function clave(env) {
  if (!_clave) {
    if (!env.CLAVE) throw new Error("falta CLAVE");
    _clave = await crypto.subtle.importKey("raw", enc.encode(env.CLAVE), { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
  }
  return _clave;
}
const b64u = b => btoa(String.fromCharCode(...new Uint8Array(b))).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
const b64uTxt = s => btoa(unescape(encodeURIComponent(s))).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
const txtB64u = s => decodeURIComponent(escape(atob(s.replace(/-/g, "+").replace(/_/g, "/"))));

// "a.b.ejemplo.com" -> "ejemplo.com" (basta para los servidores de vídeo)
function dominio(host) {
  const p = host.toLowerCase().split(".");
  return p.length <= 2 ? p.join(".") : p.slice(-2).join(".");
}

async function firma(env, datos) {
  const cuerpo = b64uTxt(JSON.stringify(datos));
  const s = await crypto.subtle.sign("HMAC", await clave(env), enc.encode(cuerpo));
  return cuerpo + "." + b64u(s).slice(0, 22);
}
async function comprueba(env, tok) {
  const [cuerpo, sig] = String(tok || "").split(".");
  if (!cuerpo || !sig) return null;
  const s = await crypto.subtle.sign("HMAC", await clave(env), enc.encode(cuerpo));
  if (b64u(s).slice(0, 22) !== sig) return null;
  let d;
  try { d = JSON.parse(txtB64u(cuerpo)); } catch (e) { return null; }
  if (!d || !(d.x > Date.now() / 1000)) return null;
  return d;
}

// ---- Sacar la emisión de la página de cada servidor (como cinubo.py) --------
function b36(n, a) {
  const d = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ";
  let s = "";
  do { s = d[n % a] + s; n = Math.floor(n / a); } while (n > 0);
  return s;
}
function desempaqueta(src) {
  const m = /\}\('(.*)',\s*(\d+),\s*(\d+),\s*'(.*?)'\.split\('\|'\)/s.exec(src || "");
  if (!m) return "";
  const p = m[1].replace(/\\'/g, "'"), a = +m[2], c = +m[3], k = m[4].split("|");
  const tab = {};
  for (let i = 0; i < c; i++) { const cl = b36(i, a); tab[cl] = k[i] || cl; }
  return p.replace(/\b\w+\b/g, w => (Object.prototype.hasOwnProperty.call(tab, w) ? tab[w] : w));
}
const origen = u => { const x = new URL(u); return x.protocol + "//" + x.host; };
class NoSale extends Error {}

async function pagina(url) {
  const r = await fetch(url, { headers: { "User-Agent": UA, Referer: "https://cinubo.com/" }, redirect: "follow" });
  if (r.status !== 200) throw new NoSale("http " + r.status);
  return { txt: await r.text(), url: r.url };
}

async function resolver(emb) {
  const u = new URL(emb), host = u.hostname.toLowerCase();
  if (VIDARA.test(host)) {
    const o = origen(emb), cod = u.pathname.split("/").filter(Boolean).pop() || "";
    if (!/^[A-Za-z0-9]{6,32}$/.test(cod)) throw new NoSale("código raro");
    const r = await fetch(o + "/api/stream", {
      method: "POST",
      headers: { "User-Agent": UA, "Content-Type": "application/json", Referer: emb, Origin: o },
      body: JSON.stringify({ filecode: cod, device: "web", codecs: {} }),
    });
    if (r.status !== 200) throw new NoSale("http " + r.status);
    const su = String(((await r.json()) || {}).streaming_url || "");
    if (!su.startsWith("https://")) throw new NoSale("sin streaming_url");
    SIN_RANGE.add(dominio(new URL(su).hostname));
    return { m3u8: su, ref: o + "/", org: o, nr: 1 };
  }
  if (FIRE.test(host)) {
    const p = await pagina(emb);
    const m = /id="token-blob"[^>]*>([^<]+)/.exec(p.txt);
    if (!m) throw new NoSale("sin token");
    const o = origen(p.url), vid = new URL(p.url).pathname.replace(/\/+$/, "").split("/").pop();
    if (!/^[0-9A-Za-z_-]{4,40}$/.test(vid)) throw new NoSale("id raro");
    const j = await fetch(o + "/api/videos/" + vid + "/resolve", {
      method: "POST",
      headers: { "User-Agent": UA, "Content-Type": "application/json", Referer: p.url, Origin: o },
      body: JSON.stringify({ blob: m[1].trim() }),
    });
    if (j.status !== 200) throw new NoSale("http " + j.status);
    let su = String(((await j.json()) || {}).signedVideoUrl || "");
    if (su.startsWith("/")) su = o + su;
    if (!su.startsWith("https://")) throw new NoSale("sin signedVideoUrl");
    return { m3u8: su, ref: "", org: "" };
  }
  // Lulustream, Vidhide, Streamwish...: el reproductor empaquetado (P.A.C.K.E.R)
  const p = await pagina(emb);
  const claro = p.txt + " " + desempaqueta(p.txt), o = origen(p.url);
  const ml = /var\s*links\s*=\s*(\{[^;]{10,3000}\})/.exec(claro);
  let su = "";
  if (ml) {
    try {
      const ls = JSON.parse(ml[1]);
      for (const k of ["hls4", "hls3", "hls2"]) {
        let v = String(ls[k] || "");
        if (v.startsWith("/")) v = o + v;
        if (v.startsWith("https://")) { su = v; break; }
      }
    } catch (e) { /* sigue con el m3u8 suelto */ }
  }
  if (!su) {
    const mm = /https:\/\/[^"'\s<>]+?\.m3u8[^"'\s<>]*/.exec(claro);
    if (!mm) throw new NoSale("sin m3u8");
    su = mm[0];
  }
  // Lulustream pide Referer y da 403 si lleva Origin (cinubo.py: con_origin=False)
  return { m3u8: su, ref: o + "/", org: "" };
}

// ---- Servir la emisión ------------------------------------------------------
async function enlace(env, self, u, base) {
  const d = { r: base.r, o: base.o, nr: base.nr, h: dominio(new URL(u).hostname), x: base.x };
  return self + "/p?k=" + (await firma(env, d)) + "&u=" + encodeURIComponent(u);
}

async function reescribe(env, self, txt, u, base) {
  // una firma por dominio (casi siempre uno solo): pocas HMAC aunque haya 400 trozos
  const toks = {};
  const de = async abs => {
    const h = dominio(new URL(abs).hostname);
    if (!toks[h]) toks[h] = await firma(env, { r: base.r, o: base.o, nr: base.nr, h, x: base.x });
    return self + "/p?k=" + toks[h] + "&u=" + encodeURIComponent(abs);
  };
  const out = [];
  for (const l of txt.split("\n")) {
    const t = l.trim();
    if (!t) { out.push(l); continue; }
    if (t.startsWith("#")) {
      const m = /URI="([^"]+)"/.exec(l);
      out.push(m ? l.replace(m[0], 'URI="' + (await de(new URL(m[1], u).href)) + '"') : l);
      continue;
    }
    out.push(await de(new URL(t, u).href));
  }
  return out.join("\n");
}

const err = (motivo, st = 502) =>
  new Response(JSON.stringify({ ok: false, error: motivo }), {
    status: st, headers: { ...CORS, "Content-Type": "application/json", "X-MW-Err": motivo },
  });

async function sirve(req, env, self, u, d) {
  const h = { "User-Agent": UA };
  if (d.r) h.Referer = d.r;
  if (d.o) h.Origin = d.o;
  const dom = dominio(new URL(u).hostname);
  const rg = req.headers.get("Range");
  if (rg && !d.nr && !SIN_RANGE.has(dom)) h.Range = rg;
  const r = await fetch(u, { headers: h });
  const ct = r.headers.get("Content-Type") || "";
  const esLista = /\.m3u8$/i.test(new URL(u).pathname) || /mpegurl/i.test(ct);
  if (esLista && r.status === 200) {
    const txt = await r.text();
    if (!txt.startsWith("#EXTM3U")) return err("lista rara");
    return new Response(await reescribe(env, self, txt, u, d), {
      headers: { ...CORS, "Content-Type": "application/vnd.apple.mpegurl", "Cache-Control": "no-store" },
    });
  }
  if (r.status >= 400) return err("servidor " + r.status, r.status);
  const hh = new Headers(CORS);
  for (const k of ["Content-Type", "Content-Length", "Content-Range", "Accept-Ranges", "Cache-Control"]) {
    const v = r.headers.get(k);
    if (v) hh.set(k, v);
  }
  return new Response(r.body, { status: r.status, headers: hh });
}

export default {
  async fetch(req, env) {
    const url = new URL(req.url), self = url.origin;
    if (req.method === "OPTIONS") {
      return new Response(null, { headers: { ...CORS, "Access-Control-Allow-Headers": "Range", "Access-Control-Allow-Methods": "GET" } });
    }
    if (req.method !== "GET") return err("método", 405);
    try {
      if (url.pathname === "/r") {
        // la página del servidor que da el relay (/cbembed) -> la emisión, firmada
        const emb = url.searchParams.get("u") || "";
        if (!/^https:\/\/[a-z0-9.-]+\/[^\s]{4,200}$/i.test(emb)) return err("enlace", 400);
        const x = await resolver(emb);
        const base = { r: x.ref, o: x.org, nr: x.nr ? 1 : 0, x: Math.floor(Date.now() / 1000) + VIGENCIA_S };
        return new Response(JSON.stringify({ ok: true, play: await enlace(env, self, x.m3u8, base) }), {
          headers: { ...CORS, "Content-Type": "application/json", "Cache-Control": "no-store" },
        });
      }
      if (url.pathname === "/p") {
        const u = url.searchParams.get("u") || "";
        const d = await comprueba(env, url.searchParams.get("k"));
        if (!d) return err("firma", 403);
        if (!u.startsWith("https://") || dominio(new URL(u).hostname) !== d.h) return err("dominio", 403);
        return await sirve(req, env, self, u, d);
      }
      if (url.pathname === "/" || url.pathname === "/ping") {
        return new Response(JSON.stringify({ ok: true, s: "mw-video" }), { headers: { ...CORS, "Content-Type": "application/json" } });
      }
      return err("ruta", 404);
    } catch (e) {
      return err(e instanceof NoSale ? e.message : "fallo: " + String((e && e.message) || e).slice(0, 80));
    }
  },
};
