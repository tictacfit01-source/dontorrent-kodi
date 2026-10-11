/**
 * mw-sync — la copia de seguridad de MejorWolf (listas, historial y Kodis).
 *
 * POR QUE EXISTE: hasta ahora la unica copia real estaba en el movil. El espejo
 * del relay vive en /tmp, que Render BORRA en cada despliegue, y el historial y
 * los Kodis guardados no se respaldaban en ningun sitio: perder el movil (o que
 * Safari purgue los datos del sitio) era perderlo todo.
 *
 * COMO: el mismo patron que la app de Audiolibro, que ya funciona:
 *   1) el movil entra con Google (Google Identity Services) y obtiene un ID token,
 *   2) /session lo verifica UNA vez contra Google y emite un token propio
 *      (JWT HS256, 30 dias) que el resto de llamadas validan aqui, sin red,
 *   3) los datos viven en D1 (SQL). KV no vale: 1000 escrituras/dia y lecturas
 *      con hasta 60 s de retraso (lo aprendimos en Audiolibro).
 *
 * ES UN WORKER APARTE, A PROPOSITO: no se toca mw-relay, que es el proxy del
 * que dependen las cajas para reproducir. Si esto fallara, la web sigue
 * funcionando igual que antes (el login es opcional).
 */

const CORS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "GET,POST,OPTIONS",
  "Access-Control-Allow-Headers": "Content-Type,Authorization",
  "Access-Control-Max-Age": "86400",
};

const json = (obj, status = 200) =>
  new Response(JSON.stringify(obj), {
    status,
    headers: { "Content-Type": "application/json; charset=utf-8", ...CORS },
  });

// ===== JWT propio (HS256) =================================================
const enc = new TextEncoder();
const dec = new TextDecoder();

function b64urlFromBytes(bytes) {
  let bin = "";
  for (const b of bytes) bin += String.fromCharCode(b);
  return btoa(bin).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}
function b64urlToBytes(str) {
  str = String(str).replace(/-/g, "+").replace(/_/g, "/");
  while (str.length % 4) str += "=";
  const bin = atob(str);
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out;
}
const b64urlJson = (o) => b64urlFromBytes(enc.encode(JSON.stringify(o)));

function hmacKey(secret) {
  return crypto.subtle.importKey(
    "raw", enc.encode(secret), { name: "HMAC", hash: "SHA-256" },
    false, ["sign", "verify"]);
}

async function signToken(user, secret) {
  const now = Math.floor(Date.now() / 1000);
  const payload = {
    sub: user.sub, name: user.name || "", email: user.email || "",
    picture: user.picture || "", iat: now, exp: now + 30 * 24 * 3600,
  };
  const data = b64urlJson({ alg: "HS256", typ: "JWT" }) + "." + b64urlJson(payload);
  const sig = await crypto.subtle.sign("HMAC", await hmacKey(secret), enc.encode(data));
  return data + "." + b64urlFromBytes(new Uint8Array(sig));
}

async function verifyToken(token, secret) {
  try {
    const p = String(token || "").split(".");
    if (p.length !== 3) return null;
    const ok = await crypto.subtle.verify(
      "HMAC", await hmacKey(secret), b64urlToBytes(p[2]), enc.encode(p[0] + "." + p[1]));
    if (!ok) return null;
    const payload = JSON.parse(dec.decode(b64urlToBytes(p[1])));
    if (!payload.sub) return null;
    if (payload.exp && payload.exp < Math.floor(Date.now() / 1000)) return null;
    return payload;
  } catch (_) { return null; }
}

async function userOf(request, env) {
  const h = request.headers.get("Authorization") || "";
  if (!h.startsWith("Bearer ")) return null;
  return verifyToken(h.slice(7), env.APP_JWT_SECRET);
}

// ===== Google: verificar el ID token (solo al crear la sesion) ============
// tokeninfo es la via simple y suficiente aqui: se llama UNA vez cada 30 dias
// por dispositivo, no en cada peticion.
async function googleUser(credential, clientId) {
  const r = await fetch(
    "https://oauth2.googleapis.com/tokeninfo?id_token=" + encodeURIComponent(credential),
    { cf: { cacheTtl: 0 } });
  if (!r.ok) return null;
  const d = await r.json().catch(() => null);
  if (!d || !d.sub) return null;
  if (clientId && d.aud !== clientId) return null;         // token de OTRA app
  if (d.iss !== "accounts.google.com" && d.iss !== "https://accounts.google.com") return null;
  if (d.exp && Number(d.exp) < Math.floor(Date.now() / 1000)) return null;
  return { sub: d.sub, name: d.name || "", email: d.email || "", picture: d.picture || "" };
}

// ===== Escrituras de las copias del relay: solo FIRMADAS ===================
// /kv/* y /wfidx los escribe SOLO el relay. Antes cualquiera que leyera este
// repositorio (es publico) podia pisarlos. Ahora cada escritura lleva una firma
// Ed25519 del relay (cabeceras X-MW-Ts y X-MW-Firma) sobre
// "mw-kv-v1\n<segundos>\n<ruta>\n<sha256 del cuerpo>": vale para ESA ruta, ESE
// cuerpo y cinco minutos. Aqui solo se guarda la clave PUBLICA del relay
// (KV_PUBLICA, que no es secreta y no da para firmar nada); el secreto lo genero
// Render y no sale de alli. Sin KV_PUBLICA no entra NINGUNA escritura: mejor
// fallar cerrado que quedar abierto. Leer sigue siendo libre.
function deB64(s) {
  try { return Uint8Array.from(atob(String(s || "")), (c) => c.charCodeAt(0)); }
  catch (e) { return new Uint8Array(0); }
}

async function firmaOk(request, env, ruta, cuerpo) {
  const pub = deB64(env && env.KV_PUBLICA);
  if (pub.length !== 32) return false;
  const ts = parseInt(request.headers.get("X-MW-Ts") || "", 10);
  if (!Number.isFinite(ts) || Math.abs(Date.now() / 1000 - ts) > 300) return false;
  const firma = deB64(request.headers.get("X-MW-Firma"));
  if (firma.length !== 64) return false;
  const h = Array.from(new Uint8Array(await crypto.subtle.digest("SHA-256", cuerpo)))
    .map((b) => b.toString(16).padStart(2, "0")).join("");
  const msg = new TextEncoder().encode("mw-kv-v1\n" + ts + "\n" + ruta + "\n" + h);
  try {
    const k = await crypto.subtle.importKey("raw", pub, { name: "Ed25519" }, false, ["verify"]);
    return await crypto.subtle.verify({ name: "Ed25519" }, k, firma, msg);
  } catch (e) {
    return false;
  }
}

// El cuerpo de una escritura, solo si viene firmada; si no, null.
async function cuerpoFirmado(request, env, ruta) {
  const cuerpo = new Uint8Array(await request.arrayBuffer());
  if (!(await firmaOk(request, env, ruta, cuerpo))) return null;
  try { return JSON.parse(new TextDecoder().decode(cuerpo)); } catch (e) { return {}; }
}

// ===== Almacen ============================================================
async function ensureKV(env) {
  await env.DB.prepare(
    "CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT NOT NULL, ts INTEGER NOT NULL)"
  ).run();
}

async function ensure(env) {
  await env.DB.prepare(
    "CREATE TABLE IF NOT EXISTS users (uid TEXT PRIMARY KEY, data TEXT NOT NULL, ts INTEGER NOT NULL)"
  ).run();
}

export default {
  async fetch(request, env) {
    if (request.method === "OPTIONS") return new Response(null, { headers: CORS });
    const url = new URL(request.url);
    const path = url.pathname.replace(/\/+$/, "") || "/";

    // Que client_id de Google usa la web (asi se puede cambiar sin tocar el
    // codigo de la pagina, que vive en otro sitio).
    if (path === "/config") {
      return json({ ok: true, client_id: env.GOOGLE_CLIENT_ID || "" });
    }

    if (path === "/" || path === "/ping") {
      return json({ ok: true, service: "mw-sync", ts: Date.now() });
    }

    // --- Entrar con Google -> token propio de 30 dias ---------------------
    if (path === "/session" && request.method === "POST") {
      const body = await request.json().catch(() => ({}));
      const cred = (body && body.credential) || "";
      if (!cred) return json({ ok: false, error: "sin credential" }, 400);
      const u = await googleUser(cred, env.GOOGLE_CLIENT_ID);
      if (!u) return json({ ok: false, error: "credential no valido" }, 401);
      const token = await signToken(u, env.APP_JWT_SECRET);
      return json({ ok: true, token, user: { name: u.name, email: u.email, picture: u.picture } });
    }

    // --- Bajar lo guardado ------------------------------------------------
    if (path === "/data" && request.method === "GET") {
      const u = await userOf(request, env);
      if (!u) return json({ ok: false, error: "sin sesion" }, 401);
      await ensure(env);
      const row = await env.DB.prepare("SELECT data, ts FROM users WHERE uid = ?")
        .bind(u.sub).first();
      if (!row) return json({ ok: true, data: null, ts: 0 });
      let data = null;
      try { data = JSON.parse(row.data); } catch (_) { data = null; }
      return json({ ok: true, data, ts: row.ts });
    }

    // --- Subir (el movil manda YA FUSIONADO: aqui no se decide nada) ------
    if (path === "/data" && request.method === "POST") {
      const u = await userOf(request, env);
      if (!u) return json({ ok: false, error: "sin sesion" }, 401);
      const body = await request.json().catch(() => null);
      if (!body || typeof body !== "object" || !body.data) {
        return json({ ok: false, error: "sin data" }, 400);
      }
      const txt = JSON.stringify(body.data);
      // Tope de seguridad: ~1 MB. Una lista de 600 titulos con sus capitulos
      // recortados anda por 200-300 KB, asi que sobra de largo.
      if (txt.length > 1024 * 1024) return json({ ok: false, error: "demasiado grande" }, 413);
      await ensure(env);
      const ts = Date.now();
      await env.DB.prepare(
        "INSERT INTO users (uid, data, ts) VALUES (?, ?, ?) " +
        "ON CONFLICT(uid) DO UPDATE SET data = excluded.data, ts = excluded.ts"
      ).bind(u.sub, txt, ts).run();
      return json({ ok: true, ts });
    }

    // --- Copia del indice de WolfMax del relay ---------------------------
    // Render borra /tmp en CADA despliegue, asi que el relay perdia el indice
    // entero y WolfMax se quedaba cojo 10-20 minutos hasta que las cajas lo
    // repoblaban. Aqui sobrevive. Llega ya comprimido (gzip+base64): el worker
    // no mira dentro, solo lo guarda. Sin sesion, igual que /wffeed del relay.
    if (path === "/wfidx") {
      await ensureKV(env);
      if (request.method === "GET") {
        const row = await env.DB.prepare("SELECT v, ts FROM kv WHERE k = ?")
          .bind("wfidx").first();
        if (!row) return json({ ok: true, gz: null, ts: 0 });
        return json({ ok: true, gz: row.v, ts: row.ts });
      }
      if (request.method === "POST") {
        const body = await cuerpoFirmado(request, env, path);
        if (body === null) return json({ ok: false, error: "no autorizado" }, 401);
        const gz = body && body.gz;
        if (typeof gz !== "string" || !gz) return json({ ok: false, error: "sin gz" }, 400);
        if (gz.length > 3 * 1024 * 1024) return json({ ok: false, error: "demasiado grande" }, 413);
        const ts = Date.now();
        await env.DB.prepare(
          "INSERT INTO kv (k, v, ts) VALUES (?, ?, ?) " +
          "ON CONFLICT(k) DO UPDATE SET v = excluded.v, ts = excluded.ts"
        ).bind("wfidx", gz, ts).run();
        return json({ ok: true, ts, bytes: gz.length });
      }
    }

    // --- Otras copias del relay, con nombre FIJO --------------------------
    // Lo mismo que /wfidx para lo que tampoco puede vivir en /tmp:
    // "semillas": el infohash de cada pelicula que ya se ha visto (conseguirlo
    // es bajar su .torrent, que es lo que mas banea la IP) y su ultimo conteo;
    // "enriq": la ficha (cartel HD, año, nota, titulo con tildes) que las cajas
    // resolvieron para cada titulo del Inicio -- sin ella, tras cada despliegue
    // el Inicio salia unos minutos sin tildes ni carteles (03-10-2026).
    // "wfih": la huella de cada archivo de WolfMax que ya conocemos (desde el
    // 07-10 pide un captcha en cada descarga: lo que trae una persona o el ZIP
    // del dia vale para siempre, y en /tmp se perderia en cada despliegue).
    // Lista cerrada de nombres: esto no es un almacen para cualquiera.
    const kvm = path.match(/^\/kv\/(semillas|enriq|wfih)$/);
    if (kvm) {
      const clave = "kv:" + kvm[1];
      await ensureKV(env);
      if (request.method === "GET") {
        const row = await env.DB.prepare("SELECT v, ts FROM kv WHERE k = ?")
          .bind(clave).first();
        if (!row) return json({ ok: true, gz: null, ts: 0 });
        return json({ ok: true, gz: row.v, ts: row.ts });
      }
      if (request.method === "POST") {
        const body = await cuerpoFirmado(request, env, path);
        if (body === null) return json({ ok: false, error: "no autorizado" }, 401);
        const gz = body && body.gz;
        if (typeof gz !== "string" || !gz) return json({ ok: false, error: "sin gz" }, 400);
        if (gz.length > 1024 * 1024) return json({ ok: false, error: "demasiado grande" }, 413);
        const ts = Date.now();
        await env.DB.prepare(
          "INSERT INTO kv (k, v, ts) VALUES (?, ?, ?) " +
          "ON CONFLICT(k) DO UPDATE SET v = excluded.v, ts = excluded.ts"
        ).bind(clave, gz, ts).run();
        return json({ ok: true, ts, bytes: gz.length });
      }
    }

    return json({ ok: false, error: "no encontrado" }, 404);
  },
};
