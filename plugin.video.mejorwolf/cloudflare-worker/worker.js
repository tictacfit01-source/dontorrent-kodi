/**
 * MejorWolf Cloudflare Worker relay.
 *
 * Uso: GET/POST https://mw-relay.<tu-subdominio>.workers.dev/?u=<url-absoluta>
 *
 * Puntos clave:
 *  - Lee el body del upstream con arrayBuffer() -> Cloudflare nos descomprime
 *    gzip/br antes de leer, asi reenviamos bytes planos al cliente.
 *  - Pide Accept-Encoding: identity aguas arriba para evitar compresion.
 *  - Reenvia Cookie (cliente->upstream) y Set-Cookie (upstream->cliente) para
 *    flujos con CSRF/sesion (WolfMax /buscar, Series.ly login).
 *  - Quita content-encoding/transfer-encoding/CSP/HSTS/X-Frame para que el
 *    cliente no intente re-decodificar ni bloquee.
 *  - Match de host por substring: asi sobrevive a rotaciones de TLD
 *    (mejortorrent.eu -> .to -> .es) sin tocar el worker.
 *  - Mantiene cabeceras x-mw-relay-* para diagnostico desde Kodi.
 *  - /pair: Pairing page — usuario entra email+pass en el movil,
 *    Browser Rendering hace login en series.ly (resuelve Turnstile).
 */

// import puppeteer from "@cloudflare/puppeteer";  // No se usa — Turnstile se resuelve en Android WebView

// ===== Supabase config (para guardar cookie de series.ly) =====
const SUPABASE_URL = "https://yddgjpjyldgvuswcsxci.supabase.co";
const SUPABASE_ANON_KEY =
  "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9." +
  "eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InlkZGdqcGp5bGRndnVzd2NzeGNpIiwi" +
  "cm9sZSI6ImFub24iLCJpYXQiOjE3NzgyNTIwMzAsImV4cCI6MjA5MzgyODAzMH0." +
  "bpIkjXUowHhhJKz_HVFkGj1WogD5dpyi_JGL2yLOYl0";

const ALLOWED_HOSTS = [
  "wolfmax4k",
  "mejortorrent",
  "elitetorrent",
  "dontorrent",
  "pelispanda",
  "vivatorrents",
  "1337x",
  "gatonplayseries",
  "enlacito.com",
  "short-info.link",
  "acortador.es",
  "image.tmdb.org",
  "themoviedb.org",
  "api.themoviedb.org",
  "search.brave.com",
  "duckduckgo.com",
  "html.duckduckgo.com",
  "bing.com",
];

const BROWSER_HEADERS = {
  "User-Agent":
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 " +
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
  "Accept":
    "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif," +
    "image/webp,*/*;q=0.8",
  "Accept-Language": "es-ES,es;q=0.9,en;q=0.5",
  "Accept-Encoding": "identity",
  "Cache-Control": "no-cache",
  "Pragma": "no-cache",
  "Upgrade-Insecure-Requests": "1",
};

function hostAllowed(targetUrl) {
  try {
    const h = new URL(targetUrl).hostname.toLowerCase();
    return ALLOWED_HOSTS.some((d) => h.includes(d));
  } catch {
    return false;
  }
}

const SKIP_RESP_HEADERS = new Set([
  "content-encoding",
  "content-length",
  "transfer-encoding",
  "connection",
  "keep-alive",
  "strict-transport-security",
  "x-frame-options",
  "content-security-policy",
  "content-security-policy-report-only",
]);


// ===== SHA-256 puro en JS (para resolver Anubis PoW dentro del Worker) =====
// Necesitamos hash sincrono: crypto.subtle.digest es async y hacer 500K awaits
// seria demasiado lento para el brute-force del PoW.

const _SHA256_K = new Uint32Array([
  0x428a2f98,0x71374491,0xb5c0fbcf,0xe9b5dba5,0x3956c25b,0x59f111f1,0x923f82a4,0xab1c5ed5,
  0xd807aa98,0x12835b01,0x243185be,0x550c7dc3,0x72be5d74,0x80deb1fe,0x9bdc06a7,0xc19bf174,
  0xe49b69c1,0xefbe4786,0x0fc19dc6,0x240ca1cc,0x2de92c6f,0x4a7484aa,0x5cb0a9dc,0x76f988da,
  0x983e5152,0xa831c66d,0xb00327c8,0xbf597fc7,0xc6e00bf3,0xd5a79147,0x06ca6351,0x14292967,
  0x27b70a85,0x2e1b2138,0x4d2c6dfc,0x53380d13,0x650a7354,0x766a0abb,0x81c2c92e,0x92722c85,
  0xa2bfe8a1,0xa81a664b,0xc24b8b70,0xc76c51a3,0xd192e819,0xd6990624,0xf40e3585,0x106aa070,
  0x19a4c116,0x1e376c08,0x2748774c,0x34b0bcb5,0x391c0cb3,0x4ed8aa4a,0x5b9cca4f,0x682e6ff3,
  0x748f82ee,0x78a5636f,0x84c87814,0x8cc70208,0x90befffa,0xa4506ceb,0xbef9a3f7,0xc67178f2
]);

function _rr(v, n) { return ((v >>> n) | (v << (32 - n))) >>> 0; }

function _sha256_digest(bytes) {
  let h0=0x6a09e667, h1=0xbb67ae85, h2=0x3c6ef372, h3=0xa54ff53a;
  let h4=0x510e527f, h5=0x9b05688c, h6=0x1f83d9ab, h7=0x5be0cd19;
  const msgLen = bytes.length;
  const bitLen = msgLen * 8;
  const totalLen = Math.ceil((msgLen + 9) / 64) * 64;
  const padded = new Uint8Array(totalLen);
  padded.set(bytes);
  padded[msgLen] = 0x80;
  const dv = new DataView(padded.buffer);
  // 64-bit big-endian length: high 32 bits = 0 (msg always < 2^32 bits)
  dv.setUint32(totalLen - 4, bitLen, false);
  const W = new Uint32Array(64);
  for (let off = 0; off < totalLen; off += 64) {
    for (let i = 0; i < 16; i++) W[i] = dv.getUint32(off + i * 4, false);
    for (let i = 16; i < 64; i++) {
      const s0 = (_rr(W[i-15],7) ^ _rr(W[i-15],18) ^ (W[i-15]>>>3)) >>> 0;
      const s1 = (_rr(W[i-2],17) ^ _rr(W[i-2],19) ^ (W[i-2]>>>10)) >>> 0;
      W[i] = (W[i-16] + s0 + W[i-7] + s1) >>> 0;
    }
    let a=h0,b=h1,c=h2,d=h3,e=h4,f=h5,g=h6,hh=h7;
    for (let i = 0; i < 64; i++) {
      const S1 = (_rr(e,6)^_rr(e,11)^_rr(e,25))>>>0;
      const ch = ((e&f)^((~e)&g))>>>0;
      const t1 = (hh+S1+ch+_SHA256_K[i]+W[i])>>>0;
      const S0 = (_rr(a,2)^_rr(a,13)^_rr(a,22))>>>0;
      const maj = ((a&b)^(a&c)^(b&c))>>>0;
      const t2 = (S0+maj)>>>0;
      hh=g; g=f; f=e; e=(d+t1)>>>0; d=c; c=b; b=a; a=(t1+t2)>>>0;
    }
    h0=(h0+a)>>>0; h1=(h1+b)>>>0; h2=(h2+c)>>>0; h3=(h3+d)>>>0;
    h4=(h4+e)>>>0; h5=(h5+f)>>>0; h6=(h6+g)>>>0; h7=(h7+hh)>>>0;
  }
  const out = new Uint8Array(32);
  const ov = new DataView(out.buffer);
  ov.setUint32(0,h0); ov.setUint32(4,h1); ov.setUint32(8,h2); ov.setUint32(12,h3);
  ov.setUint32(16,h4); ov.setUint32(20,h5); ov.setUint32(24,h6); ov.setUint32(28,h7);
  return out;
}

function _sha256hex(str) {
  const bytes = new TextEncoder().encode(str);
  const d = _sha256_digest(bytes);
  return Array.from(d).map(b => b.toString(16).padStart(2, '0')).join('');
}

/**
 * Brute-force Anubis PoW usando crypto.subtle.digest (nativo async).
 * El tiempo en crypto.subtle NO cuenta como CPU time del Worker,
 * así que no dispara el error 1102 ni siquiera en el plan free.
 * Procesamos en lotes de BATCH candidatos para reducir awaits.
 */
async function _solveAnubisPow(randomData, difficulty) {
  const fullBytes = Math.floor(difficulty / 2);
  const checkNibble = difficulty % 2 !== 0;
  const enc = new TextEncoder();
  const BATCH = 2000;

  for (let base = 0; ; base += BATCH) {
    // Preparar candidatos
    const buffers = new Array(BATCH);
    for (let i = 0; i < BATCH; i++) {
      buffers[i] = enc.encode(randomData + String(base + i));
    }
    // Hashear todos en paralelo con crypto nativo
    const digests = await Promise.all(
      buffers.map(buf => crypto.subtle.digest("SHA-256", buf))
    );
    // Verificar resultados
    for (let i = 0; i < BATCH; i++) {
      const hash = new Uint8Array(digests[i]);
      let ok = true;
      for (let j = 0; j < fullBytes; j++) {
        if (hash[j] !== 0) { ok = false; break; }
      }
      if (ok && checkNibble && (hash[fullBytes] >> 4) !== 0) ok = false;
      if (ok) {
        const hexHash = Array.from(hash).map(b => b.toString(16).padStart(2, "0")).join("");
        return { hash: hexHash, nonce: base + i };
      }
    }
  }
}

/**
 * Brute-force para el PoW de descarga de DonTorrent (dificultad baja, ~4K iter).
 * Dificultad 3 → ~4K iteraciones, trivial. Usa crypto.subtle por seguridad.
 */
async function _solveDtDownloadPow(challenge, difficulty) {
  const target = "0".repeat(difficulty);
  const enc = new TextEncoder();
  const BATCH = 500;
  for (let base = 0; ; base += BATCH) {
    const buffers = new Array(BATCH);
    for (let i = 0; i < BATCH; i++) {
      buffers[i] = enc.encode(challenge + String(base + i));
    }
    const digests = await Promise.all(
      buffers.map(buf => crypto.subtle.digest("SHA-256", buf))
    );
    for (let i = 0; i < BATCH; i++) {
      const hex = Array.from(new Uint8Array(digests[i]))
        .map(b => b.toString(16).padStart(2, "0")).join("");
      if (hex.startsWith(target)) return base + i;
    }
  }
}

/** Extrae cookies de Set-Cookie headers de una Response. */
function _collectCookies(response) {
  const cookies = {};
  const raw =
    typeof response.headers.getSetCookie === "function"
      ? response.headers.getSetCookie()
      : response.headers.get("set-cookie")
        ? [response.headers.get("set-cookie")]
        : [];
  for (const sc of raw) {
    const m = sc.match(/^([^=;\s]+)=([^;]*)/);
    if (m && m[1] && m[2] && !sc.includes("Max-Age=0")) cookies[m[1]] = m[2];
  }
  return cookies;
}

function _cookieStr(cookies) {
  return Object.entries(cookies).map(([k, v]) => `${k}=${v}`).join("; ");
}

/**
 * GET a la raiz del dominio DonTorrent. Si hay Anubis, lo resuelve.
 * Devuelve {cookies, solved, elapsed, error?}.
 * Todo en UNA invocacion del Worker -> misma IP de salida.
 */
async function _solveAnubisIfNeeded(domain) {
  const baseUrl = `https://${domain}`;
  const r = await fetch(baseUrl + "/", {
    method: "GET",
    headers: { ...BROWSER_HEADERS },
    redirect: "follow",
  });
  const html = await r.text();
  const initCookies = _collectCookies(r);

  if (!html.includes("anubis_challenge")) {
    return { cookies: initCookies, solved: false, msg: "no Anubis" };
  }

  // Parsear challenge JSON
  const m = html.match(
    /<script\s+id="anubis_challenge"\s+type="application\/json">\s*([\s\S]*?)\s*<\/script>/
  );
  if (!m) return { cookies: initCookies, solved: false, error: "no challenge script" };

  let cd;
  try { cd = JSON.parse(m[1]); }
  catch { return { cookies: initCookies, solved: false, error: "bad JSON" }; }

  const rules = cd.rules || {};
  const ch = cd.challenge || {};
  const randomData = ch.randomData || "";
  const difficulty = rules.difficulty || ch.difficulty || 5;
  const challengeId = ch.id || "";

  if (!randomData || !challengeId) {
    return { cookies: initCookies, solved: false, error: "missing fields" };
  }

  // Resolver PoW (async — crypto.subtle no cuenta como CPU time)
  const t0 = Date.now();
  const { hash, nonce } = await _solveAnubisPow(randomData, difficulty);
  const elapsed = Date.now() - t0;

  // Enviar pass-challenge (MISMO Worker invocation -> misma IP)
  const passUrl =
    `${baseUrl}/.within.website/x/cmd/anubis/api/pass-challenge` +
    `?id=${encodeURIComponent(challengeId)}` +
    `&response=${hash}&nonce=${nonce}&redir=/&elapsedTime=${elapsed}`;

  const passHeaders = { ...BROWSER_HEADERS };
  if (Object.keys(initCookies).length) {
    passHeaders["Cookie"] = _cookieStr(initCookies);
  }

  const passResp = await fetch(passUrl, {
    method: "GET",
    headers: passHeaders,
    redirect: "manual",
  });

  const passCookies = _collectCookies(passResp);
  const allCookies = { ...initCookies, ...passCookies };

  return {
    cookies: allCookies,
    solved: Object.keys(passCookies).length > 0,
    elapsed, nonce, difficulty,
  };
}


// ===== /dtsearch (POST JSON) =====
//
// El Worker resuelve el PoW Y hace POST /buscar en UNA sola invocacion.
// Esto garantiza la misma IP de salida para todo el flujo Anubis.
//
// Body JSON: {domain, q}
// Respuesta: HTML de la pagina de resultados (text/html).
async function dtSearch(body) {
  // Proxy ligero: reenvía POST /buscar a DonTorrent sin resolver
  // Anubis (el PoW supera el límite de CPU del plan gratuito).
  //
  // Funciona cuando Workers de CF no reciben challenge Anubis
  // (IPs de Cloudflare suelen estar en whitelist).
  // Si Anubis aparece, devuelve el HTML de challenge para que el
  // addon lo detecte y use la estrategia DoH.
  //
  // El addon puede enviar cookies Anubis pre-resueltas en el body:
  //   { domain, q, cookies: { "browser-pow-auth": "..." } }
  const { domain, q, cookies } = body;
  const baseUrl = `https://${domain}`;

  try {
    const searchHeaders = {
      ...BROWSER_HEADERS,
      "Content-Type": "application/x-www-form-urlencoded",
      Origin: baseUrl,
      Referer: baseUrl + "/",
    };

    // Usar cookies pre-resueltas si las tenemos
    if (cookies && typeof cookies === "object" && Object.keys(cookies).length) {
      searchHeaders.Cookie = _cookieStr(cookies);
    }

    const searchResp = await fetch(baseUrl + "/buscar", {
      method: "POST",
      headers: searchHeaders,
      body: `valor=${encodeURIComponent(q)}&Buscar=Buscar`,
      redirect: "follow",
    });

    const html = await searchResp.text();

    const out = new Headers();
    out.set("content-type", "text/html; charset=utf-8");
    out.set("access-control-allow-origin", "*");
    out.set("x-mw-dt-search-status", String(searchResp.status));
    out.set("x-mw-dt-html-len", String(html.length));
    out.set("x-mw-dt-anubis", html.includes("anubis_challenge") ? "1" : "0");
    return new Response(html, { status: searchResp.status, headers: out });

  } catch (err) {
    return jsonResp({ error: err && err.message ? err.message : String(err) }, 502);
  }
}


// ===== /dtpow (POST JSON) =====
//
// Resuelve el PoW de DESCARGA de DonTorrent. El addon envia su solucion
// Anubis ya resuelta. El Worker hace pass-challenge + generate + solve +
// validate en una sola invocacion.
//
// Body: {domain, content_id, tabla, challenge_id, hash, nonce, elapsed, init_cookies}
async function dtPow(body) {
  const { domain, content_id, tabla, challenge_id, hash, nonce, elapsed, init_cookies } = body;
  const DL_DIFFICULTY = 3;
  const baseUrl = `https://${domain}`;
  const apiUrl = `${baseUrl}/api_validate_pow.php`;

  try {
    let authCookies = {};

    // 1) Pass-challenge si tenemos solucion Anubis
    if (challenge_id && hash && init_cookies && Object.keys(init_cookies).length) {
      const passUrl =
        `${baseUrl}/.within.website/x/cmd/anubis/api/pass-challenge` +
        `?id=${encodeURIComponent(challenge_id)}` +
        `&response=${hash}&nonce=${nonce}&redir=/&elapsedTime=${elapsed || 0}`;

      const passResp = await fetch(passUrl, {
        method: "GET",
        headers: { ...BROWSER_HEADERS, Cookie: _cookieStr(init_cookies) },
        redirect: "manual",
      });
      const passCk = _collectCookies(passResp);
      authCookies = { ...init_cookies, ...passCk };
    }

    // Si no auth, probar sin Anubis
    if (!authCookies["browser-pow-auth"]) {
      const probeResp = await fetch(baseUrl + "/", {
        method: "GET", headers: BROWSER_HEADERS, redirect: "follow",
      });
      const probeHtml = await probeResp.text();
      const probeCk = _collectCookies(probeResp);
      if (!probeHtml.includes("anubis_challenge")) {
        authCookies = probeCk;
      } else {
        const cm = probeHtml.match(
          /<script\s+id="anubis_challenge"\s+type="application\/json">\s*([\s\S]*?)\s*<\/script>/
        );
        return jsonResp({
          error: "anubis_active",
          challenge: cm ? JSON.parse(cm[1]) : null,
          init_cookies: probeCk,
        }, 200);
      }
    }

    const apiHeaders = {
      ...BROWSER_HEADERS,
      "Content-Type": "application/json",
      Accept: "application/json,*/*;q=0.8",
    };
    if (Object.keys(authCookies).length) {
      apiHeaders.Cookie = _cookieStr(authCookies);
    }

    // 2) POST generate
    const genResp = await fetch(apiUrl, {
      method: "POST", headers: apiHeaders,
      body: JSON.stringify({ action: "generate", content_id: Number(content_id), tabla }),
      redirect: "follow",
    });
    const genText = await genResp.text();
    let genData;
    try { genData = JSON.parse(genText); }
    catch {
      return jsonResp({ error: "generate: no-JSON", status: genResp.status, preview: genText.substring(0, 300) }, 502);
    }
    if (!genData.success || !genData.challenge) {
      return jsonResp({ error: genData.error || "generate: sin challenge", genData }, 502);
    }

    // 3) Resolver PoW descarga (dificultad 3 = ~4K iter, trivial, < 1ms CPU)
    const dlTarget = "0".repeat(DL_DIFFICULTY);
    let dlNonce = 0;
    while (true) {
      if (_sha256hex(genData.challenge + String(dlNonce)).startsWith(dlTarget)) break;
      dlNonce++;
    }

    // 4) POST validate
    const valResp = await fetch(apiUrl, {
      method: "POST", headers: apiHeaders,
      body: JSON.stringify({ action: "validate", challenge: genData.challenge, nonce: dlNonce }),
      redirect: "follow",
    });
    const valText = await valResp.text();
    let valData;
    try { valData = JSON.parse(valText); }
    catch {
      return jsonResp({ error: "validate: no-JSON", preview: valText.substring(0, 300) }, 502);
    }
    if (valData.status === "captcha_required") {
      return jsonResp({ error: "captcha_required" }, 429);
    }
    if (!valData.success || !valData.download_url) {
      return jsonResp({ error: valData.error || "validate: sin download_url", valData }, 502);
    }

    let url = valData.download_url;
    if (url.startsWith("//")) url = "https:" + url;
    else if (url.startsWith("/")) url = `${baseUrl}${url}`;

    return jsonResp({ success: true, download_url: url });

  } catch (err) {
    return jsonResp({ error: err && err.message ? err.message : String(err) }, 502);
  }
}


// ===== Endpoint especializado: /wfsearch?q=... =====
//
// El buscador real de WolfMax (/mvc/controllers/data.find.php) devuelve
// {"response":true,"data":{"message":"Denied"}} si el POST llega desde una
// IP distinta a la que obtuvo el token en el GET /. Como Cloudflare rota
// egress entre requests, si el cliente hace GET+POST separados fallan los
// dos. Solucion: hacer ambos subrequests dentro de la MISMA invocacion del
// worker — dentro de una isolate, Cloudflare mantiene la conexion y el
// egress es mas estable.
async function wfSearch(query) {
  const base = "https://www.wolfmax4k.com";
  const baseHeaders = {
    "User-Agent": BROWSER_HEADERS["User-Agent"],
    "Accept-Language": "es-ES,es;q=0.9,en;q=0.5",
    "Accept-Encoding": "identity",
  };

  // 1) GET home -> token + PHPSESSID
  const r0 = await fetch(base + "/", {
    method: "GET",
    headers: {
      ...baseHeaders,
      "Accept": BROWSER_HEADERS["Accept"],
      "Upgrade-Insecure-Requests": "1",
    },
    redirect: "follow",
  });
  const home = await r0.text();
  const tokMatch = home.match(/name=["']?token["']?\s+value=["']([^"']+)/i);
  const token = tokMatch ? tokMatch[1] : "";

  // Reunir cookies del Set-Cookie del home
  const setCookies =
    typeof r0.headers.getSetCookie === "function"
      ? r0.headers.getSetCookie()
      : (r0.headers.get("set-cookie") ? [r0.headers.get("set-cookie")] : []);
  const cookiePairs = [];
  for (const sc of setCookies) {
    const m = sc.match(/^([^=;\s]+)=([^;]*)/);
    if (m) cookiePairs.push(m[1] + "=" + m[2]);
  }
  const cookie = cookiePairs.join("; ");

  if (!token) {
    return new Response(
      JSON.stringify({ error: "no token", homeBytes: home.length }),
      { status: 502, headers: { "content-type": "application/json" } }
    );
  }

  // 2) POST AJAX multipart — asi es como lo hace el JS del propio sitio.
  const boundary = "----wfmw" + Math.random().toString(36).slice(2);
  const CRLF = "\r\n";
  const body =
    `--${boundary}${CRLF}Content-Disposition: form-data; name="_ACTION"${CRLF}${CRLF}buscar${CRLF}` +
    `--${boundary}${CRLF}Content-Disposition: form-data; name="token"${CRLF}${CRLF}${token}${CRLF}` +
    `--${boundary}${CRLF}Content-Disposition: form-data; name="q"${CRLF}${CRLF}${query}${CRLF}` +
    `--${boundary}--${CRLF}`;

  const r1 = await fetch(base + "/mvc/controllers/data.find.php", {
    method: "POST",
    headers: {
      ...baseHeaders,
      "Accept": "application/json, text/javascript, */*; q=0.01",
      "X-Requested-With": "XMLHttpRequest",
      "Origin": base,
      "Referer": base + "/",
      "Cookie": cookie,
      "Content-Type": `multipart/form-data; boundary=${boundary}`,
    },
    body,
    redirect: "follow",
  });
  const buf = await r1.arrayBuffer();

  const out = new Headers();
  out.set("content-type", r1.headers.get("content-type") || "application/json");
  out.set("access-control-allow-origin", "*");
  out.set("x-mw-wf-token", token ? "ok" : "miss");
  out.set("x-mw-wf-home-bytes", String(home.length));
  out.set("x-mw-wf-ajax-status", String(r1.status));
  return new Response(buf, { status: r1.status, headers: out });
}

// ===== Guardar cookie seriesly_session en Supabase =====
async function saveCookieToSupabase(cookieValue) {
  const url = `${SUPABASE_URL}/rest/v1/mw_config?key=eq.seriesly_cookie`;
  try {
    const resp = await fetch(url, {
      method: "PATCH",
      headers: {
        apikey: SUPABASE_ANON_KEY,
        Authorization: `Bearer ${SUPABASE_ANON_KEY}`,
        "Content-Type": "application/json",
        Prefer: "return=minimal",
      },
      body: JSON.stringify({ value: { cookie: cookieValue } }),
    });
    return resp.ok;
  } catch {
    return false;
  }
}

// ===== Extraer seriesly_session de headers Set-Cookie =====
function extractSessionCookie(response) {
  const setCookies =
    typeof response.headers.getSetCookie === "function"
      ? response.headers.getSetCookie()
      : response.headers.get("set-cookie")
        ? [response.headers.get("set-cookie")]
        : [];
  for (const sc of setCookies) {
    const m = sc.match(/seriesly_session=([^;]+)/);
    if (m && m[1]) return m[1];
  }
  return null;
}

// ===== Pagina HTML de exito tras login =====
function successPage() {
  return `<!DOCTYPE html>
<html lang="es">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>MejorWolf - Login OK</title>
<style>
  body{font-family:system-ui,sans-serif;background:#1a1a2e;color:#fff;
       display:flex;align-items:center;justify-content:center;min-height:100vh;margin:0}
  .box{text-align:center;padding:2rem;background:#16213e;border-radius:16px;max-width:400px}
  .ok{font-size:4rem;margin-bottom:1rem}
  h1{color:#0fb;margin:0 0 1rem}
  p{color:#aab;line-height:1.6}
</style></head>
<body><div class="box">
  <div class="ok">&#10004;</div>
  <h1>Sesion sincronizada</h1>
  <p>Ya puedes volver a Kodi en tu Android TV Box.<br>
     Entra en Series.ly y funcionara automaticamente.</p>
  <p style="color:#556;font-size:0.85rem;margin-top:2rem">Puedes cerrar esta pagina.</p>
</div></body></html>`;
}

// ===== Proxy login de Series.ly =====
// El usuario abre /login en su movil, inicia sesion normalmente,
// y el Worker captura la cookie y la guarda en Supabase.
async function handleLogin(request) {
  const reqUrl = new URL(request.url);
  const workerOrigin = reqUrl.origin;

  // Target: series.ly login page or the POST target
  const SLY = "https://series.ly";

  if (request.method === "GET") {
    // Proxy the login page from series.ly
    const targetPath = reqUrl.pathname.replace(/^\/login\/?/, "/") || "/";
    // Map /login -> /ingresar, /login/anything -> /anything
    let slyUrl = SLY + "/ingresar";
    if (targetPath !== "/" && targetPath !== "/ingresar") {
      slyUrl = SLY + targetPath;
    }

    // Forward cookies from browser
    const fwd = { ...BROWSER_HEADERS };
    const ck = request.headers.get("cookie");
    if (ck) fwd["Cookie"] = ck;

    const upstream = await fetch(slyUrl, {
      method: "GET",
      headers: fwd,
      redirect: "follow",
    });

    let html = await upstream.text();

    // Rewrite absolute URLs in HTML so links/forms go through our proxy
    // series.ly -> workerOrigin/login
    html = html.replace(/https:\/\/series\.ly\//g, workerOrigin + "/login/");
    html = html.replace(/action=["']\/ingresar["']/g, `action="/login/ingresar"`);
    html = html.replace(/action=["']https:\/\/series\.ly\/ingresar["']/g,
                         `action="${workerOrigin}/login/ingresar"`);

    // Rewrite asset URLs to load directly from series.ly (CSS/JS/images)
    html = html.replace(/(href|src)=["']\//g, `$1="${SLY}/`);

    // Check if upstream set a session cookie -> save to Supabase
    const sessionCookie = extractSessionCookie(upstream);

    // Forward Set-Cookie headers
    const respHeaders = new Headers();
    respHeaders.set("content-type", "text/html; charset=utf-8");
    respHeaders.set("access-control-allow-origin", "*");
    const setCookies =
      typeof upstream.headers.getSetCookie === "function"
        ? upstream.headers.getSetCookie()
        : upstream.headers.get("set-cookie")
          ? [upstream.headers.get("set-cookie")]
          : [];
    for (const c of setCookies) respHeaders.append("set-cookie", c);

    return new Response(html, { status: upstream.status, headers: respHeaders });
  }

  // POST: login form submission
  if (request.method === "POST") {
    const targetPath = reqUrl.pathname.replace(/^\/login/, "") || "/ingresar";
    const slyUrl = SLY + targetPath;

    const fwd = { ...BROWSER_HEADERS };
    const ck = request.headers.get("cookie");
    if (ck) fwd["Cookie"] = ck;
    const ct = request.headers.get("content-type");
    if (ct) fwd["Content-Type"] = ct;
    fwd["Origin"] = SLY;
    fwd["Referer"] = SLY + "/ingresar";

    const body = await request.arrayBuffer();
    const upstream = await fetch(slyUrl, {
      method: "POST",
      headers: fwd,
      body,
      redirect: "manual",  // Don't follow redirect - we need to check cookies
    });

    // Check for session cookie in response
    const sessionCookie = extractSessionCookie(upstream);
    if (sessionCookie) {
      // Login succeeded! Save to Supabase
      const saved = await saveCookieToSupabase(sessionCookie);
      // Show success page
      const respHeaders = new Headers();
      respHeaders.set("content-type", "text/html; charset=utf-8");
      // Forward cookies so browser keeps the session
      const setCookies =
        typeof upstream.headers.getSetCookie === "function"
          ? upstream.headers.getSetCookie()
          : upstream.headers.get("set-cookie")
            ? [upstream.headers.get("set-cookie")]
            : [];
      for (const c of setCookies) respHeaders.append("set-cookie", c);
      return new Response(successPage(), { status: 200, headers: respHeaders });
    }

    // Login failed or redirect - follow the redirect through our proxy
    if (upstream.status >= 300 && upstream.status < 400) {
      const location = upstream.headers.get("location") || "";
      let redirectTo = workerOrigin + "/login/ingresar";
      if (location) {
        // Rewrite redirect to go through our proxy
        redirectTo = location.replace(SLY, workerOrigin + "/login");
        if (!redirectTo.startsWith("http")) {
          redirectTo = workerOrigin + "/login" + redirectTo;
        }
      }
      const respHeaders = new Headers();
      respHeaders.set("location", redirectTo);
      const setCookies =
        typeof upstream.headers.getSetCookie === "function"
          ? upstream.headers.getSetCookie()
          : upstream.headers.get("set-cookie")
            ? [upstream.headers.get("set-cookie")]
            : [];
      for (const c of setCookies) respHeaders.append("set-cookie", c);
      return new Response(null, { status: 302, headers: respHeaders });
    }

    // Non-redirect response (validation error etc) - proxy back
    let html = await upstream.text();
    html = html.replace(/https:\/\/series\.ly\//g, workerOrigin + "/login/");
    html = html.replace(/action=["']\/ingresar["']/g, `action="/login/ingresar"`);
    html = html.replace(/(href|src)=["']\//g, `$1="${SLY}/`);

    const respHeaders = new Headers();
    respHeaders.set("content-type", "text/html; charset=utf-8");
    const setCookies =
      typeof upstream.headers.getSetCookie === "function"
        ? upstream.headers.getSetCookie()
        : upstream.headers.get("set-cookie")
          ? [upstream.headers.get("set-cookie")]
          : [];
    for (const c of setCookies) respHeaders.append("set-cookie", c);

    return new Response(html, { status: upstream.status, headers: respHeaders });
  }

  return new Response("Method not allowed", { status: 405 });
}

// ===== Proxy generico para assets de series.ly bajo /login/ =====
async function proxyLoginAsset(request) {
  const reqUrl = new URL(request.url);
  const path = reqUrl.pathname.replace(/^\/login/, "");
  const slyUrl = "https://series.ly" + path + reqUrl.search;

  const fwd = { ...BROWSER_HEADERS };
  const ck = request.headers.get("cookie");
  if (ck) fwd["Cookie"] = ck;

  const upstream = await fetch(slyUrl, {
    method: "GET",
    headers: fwd,
    redirect: "follow",
  });

  const buf = await upstream.arrayBuffer();

  // Check for session cookie in ANY response (login might redirect here)
  const sessionCookie = extractSessionCookie(upstream);
  if (sessionCookie) {
    await saveCookieToSupabase(sessionCookie);
  }

  const respHeaders = new Headers();
  const ct = upstream.headers.get("content-type");
  if (ct) respHeaders.set("content-type", ct);
  respHeaders.set("access-control-allow-origin", "*");

  const setCookies =
    typeof upstream.headers.getSetCookie === "function"
      ? upstream.headers.getSetCookie()
      : upstream.headers.get("set-cookie")
        ? [upstream.headers.get("set-cookie")]
        : [];
  for (const c of setCookies) respHeaders.append("set-cookie", c);

  return new Response(buf, { status: upstream.status, headers: respHeaders });
}


// ===== Pagina HTML de pairing =====
function pairPage() {
  return `<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>MejorWolf - Series.ly</title>
<style>
  *{box-sizing:border-box;margin:0;padding:0}
  body{font-family:system-ui,-apple-system,sans-serif;background:#0f0f1a;color:#e0e0e0;
       display:flex;align-items:center;justify-content:center;min-height:100vh;padding:1rem}
  .card{background:#1a1a2e;border-radius:16px;padding:2rem;width:90%;max-width:440px;
        box-shadow:0 8px 32px rgba(0,0,0,.4)}
  .logo{text-align:center;font-size:1.6rem;font-weight:700;color:#0fb;margin-bottom:.3rem}
  .sub{text-align:center;color:#889;font-size:.85rem;margin-bottom:1.5rem}
  label{display:block;font-size:.85rem;color:#aab;margin-bottom:.3rem;margin-top:1rem}
  input[type=text]{width:100%;padding:.7rem .9rem;
        border:1px solid #333;border-radius:8px;background:#12122a;color:#fff;
        font-size:1rem;outline:none}
  input:focus{border-color:#0fb}
  .btn{width:100%;padding:.8rem;border:none;border-radius:8px;font-size:1rem;font-weight:600;
       cursor:pointer;margin-top:1rem;transition:all .2s}
  .btn-primary{background:#0fb;color:#111}
  .btn-primary:hover{background:#0da}
  .btn-primary:disabled{background:#334;color:#667;cursor:wait}
  .status{text-align:center;margin-top:1rem;min-height:2.5rem;font-size:.9rem;line-height:1.4}
  .status.ok{color:#0fb}
  .status.err{color:#f55}
  .status.loading{color:#aab}
  .spinner{display:inline-block;width:18px;height:18px;border:2px solid #556;
           border-top-color:#0fb;border-radius:50%;animation:spin .8s linear infinite;
           vertical-align:middle;margin-right:.5rem}
  @keyframes spin{to{transform:rotate(360deg)}}
  .info-box{background:#12122a;border:1px solid #2a2a4e;border-radius:8px;padding:1rem;
            margin-top:1rem;font-size:.85rem;color:#99b;line-height:1.6}
  .info-box b{color:#0fb}
  .success-screen{display:none;text-align:center;padding:2rem 0}
  .success-screen h2{color:#0fb;font-size:1.3rem;margin-bottom:.5rem}
  .success-screen p{color:#99b;font-size:.9rem}
  .note{text-align:center;color:#556;font-size:.75rem;margin-top:1.5rem;line-height:1.5}
</style>
</head>
<body>
<div class="card">
  <div class="logo">MejorWolf</div>
  <div class="sub">Series.ly para Kodi</div>

  <div id="mainSection">
    <div class="info-box">
      <b>&#9432; Informacion</b><br><br>
      El login de Series.ly se hace <b>automaticamente</b> desde tu Android TV Box.<br><br>
      Solo tienes que poner tu email y contrase&ntilde;a en Kodi
      (Ajustes del addon) y el login se hace solo.<br><br>
      Si ves esta pagina es porque el login automatico no funciono.
      Dimelo para que pueda arreglarlo.
    </div>

    <div style="margin-top:1.5rem">
      <label for="manualCookie">&#128273; Pegar cookie manualmente (avanzado)</label>
      <input type="text" id="manualCookie" placeholder="eyJpdiI6Ik..." style="font-size:.85rem">
      <button type="button" class="btn btn-primary" id="cookieBtn"
              style="font-size:.85rem;padding:.6rem;margin-top:.5rem"
              onclick="saveCookie()">Guardar cookie</button>
    </div>

    <div id="status" class="status"></div>

    <div class="note">
      Esta opcion es solo para usuarios avanzados.<br>
      Normalmente no necesitas hacer nada aqui.
    </div>
  </div>

  <div id="successScreen" class="success-screen">
    <h2>&#10004; Cookie guardada</h2>
    <p>Vuelve a Kodi y entra en Series.ly.<br>Tu sesion ya esta activa.</p>
    <p style="color:#556;font-size:.8rem;margin-top:1.5rem">Puedes cerrar esta pagina.</p>
  </div>
</div>
<script>
const status = document.getElementById('status');

async function saveCookie() {
  const cookie = document.getElementById('manualCookie').value.trim();
  if (!cookie) return;
  const cookieBtn = document.getElementById('cookieBtn');
  cookieBtn.disabled = true;
  cookieBtn.textContent = 'Guardando...';
  status.className = 'status loading';
  status.innerHTML = '<span class="spinner"></span> Guardando cookie...';
  try {
    const resp = await fetch('/pair/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ cookie }),
    });
    const data = await resp.json();
    if (data.success) {
      document.getElementById('mainSection').style.display = 'none';
      document.getElementById('successScreen').style.display = 'block';
    } else {
      status.className = 'status err';
      status.textContent = data.error || 'Error guardando cookie.';
      cookieBtn.disabled = false;
      cookieBtn.textContent = 'Guardar cookie';
    }
  } catch (err) {
    status.className = 'status err';
    status.textContent = 'Error: ' + err.message;
    cookieBtn.disabled = false;
    cookieBtn.textContent = 'Guardar cookie';
  }
}
</script>
</body>
</html>`;
}


// ===== Resolver Turnstile via CapSolver =====
const CAPSOLVER_API = "https://api.capsolver.com";
const TURNSTILE_SITEKEY = "0x4AAAAAACa-o4Kq858TtUP6";
const SLY_LOGIN_URL = "https://series.ly/ingresar";

async function solveTurnstile(apiKey) {
  // 1. Create task
  const createResp = await fetch(CAPSOLVER_API + "/createTask", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      clientKey: apiKey,
      task: {
        type: "AntiTurnstileTaskProxyLess",
        websiteURL: SLY_LOGIN_URL,
        websiteKey: TURNSTILE_SITEKEY,
      },
    }),
  });
  const createData = await createResp.json();
  if (createData.errorId !== 0) {
    throw new Error("CapSolver createTask: " + (createData.errorDescription || "unknown"));
  }
  const taskId = createData.taskId;

  // 2. Poll for result (max 120s)
  for (let i = 0; i < 40; i++) {
    await sleep(3000);
    const resultResp = await fetch(CAPSOLVER_API + "/getTaskResult", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ clientKey: apiKey, taskId }),
    });
    const resultData = await resultResp.json();
    if (resultData.status === "ready") {
      return resultData.solution.token;
    }
    if (resultData.errorId !== 0) {
      throw new Error("CapSolver result: " + (resultData.errorDescription || "unknown"));
    }
  }
  throw new Error("CapSolver timeout (120s)");
}


// ===== Login via CapSolver Turnstile + POST directo =====
async function handlePairLogin(request, env) {
  let body;
  try {
    body = await request.json();
  } catch {
    return jsonResp({ error: "JSON invalido" }, 400);
  }

  const { email, password, cookie: manualCookie, capsolverKey: clientCapKey } = body;

  // ===== Mode 1: Manual cookie paste =====
  if (manualCookie) {
    const saved = await saveCookieToSupabase(manualCookie.trim());
    return jsonResp({
      success: true,
      saved,
      message: "Cookie guardada. Vuelve a Kodi.",
    });
  }

  // ===== Mode 2: Automated login =====
  if (!email || !password) {
    return jsonResp({ error: "Email y contrasena requeridos" }, 400);
  }

  // Accept a pre-solved Turnstile token (from Android WebView)
  // or solve via CapSolver if key is configured (from env OR from client)
  const preToken = body.turnstileToken || "";

  try {
    // Step 1: Get CSRF token + session cookies from series.ly
    const csrfResp = await fetch("https://series.ly/sanctum/csrf-cookie", {
      headers: BROWSER_HEADERS,
      redirect: "follow",
    });
    // Collect cookies
    const cookieJar = {};
    const csrfSetCookies =
      typeof csrfResp.headers.getSetCookie === "function"
        ? csrfResp.headers.getSetCookie()
        : csrfResp.headers.get("set-cookie")
          ? [csrfResp.headers.get("set-cookie")]
          : [];
    for (const sc of csrfSetCookies) {
      const m = sc.match(/^([^=;\s]+)=([^;]*)/);
      if (m) cookieJar[m[1]] = m[2];
    }

    // Get the login page for CSRF token
    const pageResp = await fetch(SLY_LOGIN_URL, {
      headers: {
        ...BROWSER_HEADERS,
        Cookie: Object.entries(cookieJar).map(([k, v]) => `${k}=${v}`).join("; "),
      },
      redirect: "follow",
    });
    const pageHtml = await pageResp.text();
    // Collect more cookies
    const pageSetCookies =
      typeof pageResp.headers.getSetCookie === "function"
        ? pageResp.headers.getSetCookie()
        : pageResp.headers.get("set-cookie")
          ? [pageResp.headers.get("set-cookie")]
          : [];
    for (const sc of pageSetCookies) {
      const m = sc.match(/^([^=;\s]+)=([^;]*)/);
      if (m) cookieJar[m[1]] = m[2];
    }

    // Extract CSRF token
    const csrfMatch = pageHtml.match(/name="_token"\s+value="([^"]+)"/);
    const csrfToken = csrfMatch ? csrfMatch[1] : "";
    if (!csrfToken) {
      return jsonResp({ error: "No se pudo obtener CSRF token de series.ly" }, 502);
    }

    // Step 2: Get Turnstile token
    // Series.ly validates Turnstile server-side.
    // Options: pre-solved token (Android WebView) > CapSolver > intento sin token
    let turnstileToken = preToken || "";
    if (!turnstileToken) {
      const capKey = clientCapKey || env.CAPSOLVER_KEY || "";
      if (capKey) {
        try {
          turnstileToken = await solveTurnstile(capKey);
        } catch (err) {
          // CapSolver fallo — intentar sin Turnstile
          turnstileToken = "";
        }
      }
      // Si no hay token, intentar login sin Turnstile (puede funcionar con form POST)
    }

    // Step 3: POST login to series.ly
    // Decode XSRF-TOKEN cookie for the X-XSRF-TOKEN header (Laravel requirement)
    const xsrfRaw = cookieJar["XSRF-TOKEN"] || "";
    const xsrfDecoded = xsrfRaw ? decodeURIComponent(xsrfRaw) : "";

    const formData = new URLSearchParams({
      _token: csrfToken,
      email,
      password,
      remember: "1",
      "cf-turnstile-response": turnstileToken,
    });

    const postHeaders = {
      ...BROWSER_HEADERS,
      "Content-Type": "application/x-www-form-urlencoded",
      Cookie: Object.entries(cookieJar).map(([k, v]) => `${k}=${v}`).join("; "),
      Origin: "https://series.ly",
      Referer: SLY_LOGIN_URL,
    };
    // Laravel checks X-XSRF-TOKEN header as alternative CSRF validation
    if (xsrfDecoded) {
      postHeaders["X-XSRF-TOKEN"] = xsrfDecoded;
    }

    const loginResp = await fetch(SLY_LOGIN_URL, {
      method: "POST",
      headers: postHeaders,
      body: formData.toString(),
      redirect: "manual",
    });

    // Check for session cookie in response
    const loginSetCookies =
      typeof loginResp.headers.getSetCookie === "function"
        ? loginResp.headers.getSetCookie()
        : loginResp.headers.get("set-cookie")
          ? [loginResp.headers.get("set-cookie")]
          : [];

    let sessionCookie = null;
    for (const sc of loginSetCookies) {
      const m = sc.match(/seriesly_session=([^;]+)/);
      if (m && m[1]) {
        sessionCookie = m[1];
        break;
      }
    }

    // Check login result
    const loginStatus = loginResp.status;
    const location = loginResp.headers.get("location") || "";

    if (loginStatus === 302 && !location.includes("/ingresar")) {
      // Redirect away from login page = success
      if (sessionCookie) {
        const saved = await saveCookieToSupabase(sessionCookie);
        return jsonResp({
          success: true,
          saved,
          message: "Sesion sincronizada correctamente",
        });
      }
      // Login seems OK but no cookie? Follow redirect to get cookie
      const followResp = await fetch(location.startsWith("http") ? location : "https://series.ly" + location, {
        headers: {
          ...BROWSER_HEADERS,
          Cookie: Object.entries(cookieJar).map(([k, v]) => `${k}=${v}`).join("; "),
        },
        redirect: "manual",
      });
      const followSetCookies =
        typeof followResp.headers.getSetCookie === "function"
          ? followResp.headers.getSetCookie()
          : followResp.headers.get("set-cookie")
            ? [followResp.headers.get("set-cookie")]
            : [];
      for (const sc of followSetCookies) {
        const m = sc.match(/seriesly_session=([^;]+)/);
        if (m && m[1]) {
          sessionCookie = m[1];
          break;
        }
      }
      if (sessionCookie) {
        const saved = await saveCookieToSupabase(sessionCookie);
        return jsonResp({ success: true, saved, message: "Sesion sincronizada" });
      }
      return jsonResp({ error: "Login exitoso pero no se capturo la cookie" }, 500);
    }

    // Login failed — check for errors
    if (loginStatus === 422 || loginStatus === 200) {
      // Read response body for error messages
      const respText = await loginResp.text();
      // 422 sin Turnstile = captcha rechazado, NO credenciales incorrectas
      let errorMsg = turnstileToken
        ? "Credenciales incorrectas o captcha expirado"
        : "Captcha no resuelto (Turnstile). Usa el login automatico desde Kodi en tu Android TV.";
      let debugInfo = { httpStatus: loginStatus, bodyLen: respText.length, hadTurnstile: !!turnstileToken };

      // Try JSON parse (422 returns JSON)
      try {
        const errJson = JSON.parse(respText);
        debugInfo.jsonParsed = true;
        debugInfo.message = errJson.message;
        debugInfo.errors = errJson.errors;
        if (errJson.message) errorMsg = errJson.message;
        if (errJson.errors) {
          const errList = Object.values(errJson.errors).flat();
          if (errList.length) errorMsg = errList.join("; ");
        }
      } catch {
        debugInfo.jsonParsed = false;
        debugInfo.bodySnippet = respText.substring(0, 500);
        // HTML response — extract error from <li> tags
        const liMatch = respText.match(/<li>([^<]+)<\/li>/g);
        if (liMatch) {
          errorMsg = liMatch
            .map((l) => l.replace(/<\/?li>/g, "").trim())
            .filter((t) => t.length > 3)
            .join("; ");
        }
      }
      return jsonResp({ error: errorMsg, debug: debugInfo, hadTurnstile: !!turnstileToken }, 401);
    }

    // Redirect back to login = failed
    if (loginStatus === 302 && location.includes("/ingresar")) {
      return jsonResp({
        error: "Credenciales incorrectas (redirect a login)",
        debug: { httpStatus: 302, location, hadTurnstile: !!turnstileToken },
      }, 401);
    }

    // Any other status — include full debug
    let unexpectedBody = "";
    try { unexpectedBody = await loginResp.text(); } catch {}
    return jsonResp({
      error: `Respuesta inesperada: HTTP ${loginStatus}`,
      debug: {
        httpStatus: loginStatus,
        location,
        hadTurnstile: !!turnstileToken,
        bodySnippet: unexpectedBody.substring(0, 500),
        headers: Object.fromEntries([...loginResp.headers.entries()].slice(0, 10)),
      },
    }, 500);
  } catch (err) {
    return jsonResp({
      error: "Error interno: " + (err && err.message ? err.message : String(err)),
    }, 500);
  }
}

// ===== Debug endpoint: muestra todo el flujo de login paso a paso =====
async function handlePairDebug(request) {
  const steps = [];
  const cookieJar = {};

  try {
    // Step 1: CSRF cookie
    steps.push({ step: "1. GET /sanctum/csrf-cookie", status: "..." });
    const csrfResp = await fetch("https://series.ly/sanctum/csrf-cookie", {
      headers: BROWSER_HEADERS,
      redirect: "follow",
    });
    const csrfSetCookies =
      typeof csrfResp.headers.getSetCookie === "function"
        ? csrfResp.headers.getSetCookie()
        : csrfResp.headers.get("set-cookie")
          ? [csrfResp.headers.get("set-cookie")]
          : [];
    for (const sc of csrfSetCookies) {
      const m = sc.match(/^([^=;\s]+)=([^;]*)/);
      if (m) cookieJar[m[1]] = m[2];
    }
    steps[0] = {
      step: "1. GET /sanctum/csrf-cookie",
      status: csrfResp.status,
      cookies: Object.keys(cookieJar),
      setCookieHeaders: csrfSetCookies.map(c => c.substring(0, 120)),
    };

    // Step 2: Get login page
    steps.push({ step: "2. GET /ingresar", status: "..." });
    const cookieStr = Object.entries(cookieJar).map(([k, v]) => `${k}=${v}`).join("; ");
    const pageResp = await fetch(SLY_LOGIN_URL, {
      headers: { ...BROWSER_HEADERS, Cookie: cookieStr },
      redirect: "follow",
    });
    const pageHtml = await pageResp.text();
    const pageSetCookies =
      typeof pageResp.headers.getSetCookie === "function"
        ? pageResp.headers.getSetCookie()
        : pageResp.headers.get("set-cookie")
          ? [pageResp.headers.get("set-cookie")]
          : [];
    for (const sc of pageSetCookies) {
      const m = sc.match(/^([^=;\s]+)=([^;]*)/);
      if (m) cookieJar[m[1]] = m[2];
    }

    // Extract CSRF token
    const csrfMatch = pageHtml.match(/name="_token"\s+value="([^"]+)"/);
    const csrfToken = csrfMatch ? csrfMatch[1] : "";

    // Check what form fields exist
    const formFields = [];
    const inputMatches = pageHtml.matchAll(/name="([^"]+)"/g);
    for (const im of inputMatches) formFields.push(im[1]);

    // Check for Turnstile sitekey
    const sitekeyMatch = pageHtml.match(/data-sitekey="([^"]+)"/);
    const turnstileInForm = pageHtml.includes("cf-turnstile-response");

    steps[1] = {
      step: "2. GET /ingresar",
      status: pageResp.status,
      htmlLength: pageHtml.length,
      csrfTokenFound: !!csrfToken,
      csrfTokenPrefix: csrfToken ? csrfToken.substring(0, 20) + "..." : "MISSING",
      formFields: [...new Set(formFields)],
      turnstileSitekey: sitekeyMatch ? sitekeyMatch[1] : "none",
      turnstileFieldInForm: turnstileInForm,
      cookies: Object.keys(cookieJar),
      xsrfToken: cookieJar["XSRF-TOKEN"] ? "present (" + cookieJar["XSRF-TOKEN"].length + " chars)" : "MISSING",
    };

    // Step 3: Try POST (with fake credentials for safety)
    if (!csrfToken) {
      steps.push({ step: "3. POST SKIPPED", reason: "No CSRF token" });
    } else {
      steps.push({ step: "3. POST /ingresar (fake creds)", status: "..." });

      // Decode XSRF-TOKEN for header
      const xsrfRaw = cookieJar["XSRF-TOKEN"] || "";
      const xsrfDecoded = decodeURIComponent(xsrfRaw);

      const formData = new URLSearchParams({
        _token: csrfToken,
        email: "debug-test@example.com",
        password: "fake-password-12345",
        remember: "1",
        "cf-turnstile-response": "",
      });

      const loginResp = await fetch(SLY_LOGIN_URL, {
        method: "POST",
        headers: {
          ...BROWSER_HEADERS,
          "Content-Type": "application/x-www-form-urlencoded",
          Cookie: Object.entries(cookieJar).map(([k, v]) => `${k}=${v}`).join("; "),
          Origin: "https://series.ly",
          Referer: SLY_LOGIN_URL,
          "X-XSRF-TOKEN": xsrfDecoded,
        },
        body: formData.toString(),
        redirect: "manual",
      });

      const loginSetCookies =
        typeof loginResp.headers.getSetCookie === "function"
          ? loginResp.headers.getSetCookie()
          : loginResp.headers.get("set-cookie")
            ? [loginResp.headers.get("set-cookie")]
            : [];

      const loginLocation = loginResp.headers.get("location") || "";

      // Try to read body
      let loginBody = "";
      try { loginBody = await loginResp.text(); } catch {}

      // Follow redirect to see error messages
      let redirectErrors = "";
      if (loginResp.status === 302) {
        const redirectUrl = loginLocation.startsWith("http") ? loginLocation : "https://series.ly" + loginLocation;
        // Collect cookies from the POST response
        for (const sc of loginSetCookies) {
          const m = sc.match(/^([^=;\s]+)=([^;]*)/);
          if (m) cookieJar[m[1]] = m[2];
        }
        try {
          const followResp = await fetch(redirectUrl, {
            headers: {
              ...BROWSER_HEADERS,
              Cookie: Object.entries(cookieJar).map(([k, v]) => `${k}=${v}`).join("; "),
            },
            redirect: "follow",
          });
          const followHtml = await followResp.text();
          // Extract error messages from the HTML
          const errorLis = followHtml.match(/<li[^>]*>([^<]+)<\/li>/g);
          if (errorLis) {
            redirectErrors = errorLis.map(l => l.replace(/<\/?li[^>]*>/g, "").trim()).join("; ");
          }
          // Also check for alert/error divs
          const alertMatch = followHtml.match(/class="[^"]*alert[^"]*"[^>]*>([\s\S]*?)<\//);
          if (alertMatch) {
            redirectErrors += " | alert: " + alertMatch[1].replace(/<[^>]+>/g, "").trim().substring(0, 200);
          }
          // Check for "invalid" or "turnstile" mentions in script/body
          const bodyLower = followHtml.toLowerCase();
          const mentions = [];
          if (bodyLower.includes("turnstile")) mentions.push("turnstile mentioned in page");
          if (bodyLower.includes("captcha")) mentions.push("captcha mentioned in page");
          if (bodyLower.includes("invalid")) mentions.push("'invalid' found in page");
          if (bodyLower.includes("estas credenciales")) mentions.push("'estas credenciales' found");
          if (bodyLower.includes("cf-turnstile")) mentions.push("cf-turnstile found in page");
          if (mentions.length) redirectErrors += " | " + mentions.join(", ");
        } catch (e) {
          redirectErrors = "follow error: " + e.message;
        }
      }

      steps[2] = {
        step: "3. POST /ingresar (fake creds, sin Turnstile)",
        status: loginResp.status,
        location: loginLocation,
        bodyLength: loginBody.length,
        bodySnippet: loginBody.substring(0, 300),
        setCookies: loginSetCookies.length,
        redirectErrors: redirectErrors || "none found",
        headersSent: {
          xsrfTokenSent: !!xsrfDecoded,
          cookiesSent: Object.keys(cookieJar).length,
        },
        allResponseHeaders: Object.fromEntries([...loginResp.headers.entries()]),
      };

      // Step 4: AJAX POST — Laravel devuelve 422 JSON con errores exactos
      steps.push({ step: "4. POST AJAX (Accept: application/json)", status: "..." });

      // Re-fetch CSRF (token may be consumed)
      const pageResp2 = await fetch(SLY_LOGIN_URL, {
        headers: { ...BROWSER_HEADERS, Cookie: Object.entries(cookieJar).map(([k, v]) => `${k}=${v}`).join("; ") },
        redirect: "follow",
      });
      const pageHtml2 = await pageResp2.text();
      const pageSetCookies2 =
        typeof pageResp2.headers.getSetCookie === "function"
          ? pageResp2.headers.getSetCookie()
          : pageResp2.headers.get("set-cookie")
            ? [pageResp2.headers.get("set-cookie")]
            : [];
      for (const sc of pageSetCookies2) {
        const m = sc.match(/^([^=;\s]+)=([^;]*)/);
        if (m) cookieJar[m[1]] = m[2];
      }
      const csrfMatch2 = pageHtml2.match(/name="_token"\s+value="([^"]+)"/);
      const csrfToken2 = csrfMatch2 ? csrfMatch2[1] : csrfToken;
      const xsrfRaw2 = cookieJar["XSRF-TOKEN"] || "";
      const xsrfDecoded2 = xsrfRaw2 ? decodeURIComponent(xsrfRaw2) : "";

      // Send as AJAX/JSON request — Laravel checks Accept header
      // and returns 422 JSON instead of 302 redirect
      const ajaxResp = await fetch(SLY_LOGIN_URL, {
        method: "POST",
        headers: {
          "User-Agent": BROWSER_HEADERS["User-Agent"],
          "Accept": "application/json",
          "Content-Type": "application/json",
          "X-Requested-With": "XMLHttpRequest",
          "X-XSRF-TOKEN": xsrfDecoded2,
          Cookie: Object.entries(cookieJar).map(([k, v]) => `${k}=${v}`).join("; "),
          Origin: "https://series.ly",
          Referer: SLY_LOGIN_URL,
        },
        body: JSON.stringify({
          _token: csrfToken2,
          email: "debug-test@example.com",
          password: "fake-password-12345",
          remember: true,
          "cf-turnstile-response": "",
        }),
        redirect: "manual",
      });

      let ajaxBody = "";
      try { ajaxBody = await ajaxResp.text(); } catch {}

      let ajaxParsed = null;
      try { ajaxParsed = JSON.parse(ajaxBody); } catch {}

      steps[3] = {
        step: "4. POST AJAX con cf-turnstile-response vacio",
        status: ajaxResp.status,
        location: ajaxResp.headers.get("location") || "",
        body: ajaxParsed || ajaxBody.substring(0, 500),
        contentType: ajaxResp.headers.get("content-type") || "",
      };

      // Step 5: AJAX POST sin cf-turnstile-response
      steps.push({ step: "5. POST AJAX sin campo cf-turnstile-response", status: "..." });

      // Re-fetch CSRF again
      const pageResp3 = await fetch(SLY_LOGIN_URL, {
        headers: { ...BROWSER_HEADERS, Cookie: Object.entries(cookieJar).map(([k, v]) => `${k}=${v}`).join("; ") },
        redirect: "follow",
      });
      const pageHtml3 = await pageResp3.text();
      const pageSetCookies3 =
        typeof pageResp3.headers.getSetCookie === "function"
          ? pageResp3.headers.getSetCookie()
          : pageResp3.headers.get("set-cookie")
            ? [pageResp3.headers.get("set-cookie")]
            : [];
      for (const sc of pageSetCookies3) {
        const m = sc.match(/^([^=;\s]+)=([^;]*)/);
        if (m) cookieJar[m[1]] = m[2];
      }
      const csrfMatch3 = pageHtml3.match(/name="_token"\s+value="([^"]+)"/);
      const csrfToken3 = csrfMatch3 ? csrfMatch3[1] : csrfToken2;
      const xsrfRaw3 = cookieJar["XSRF-TOKEN"] || "";
      const xsrfDecoded3 = xsrfRaw3 ? decodeURIComponent(xsrfRaw3) : "";

      const ajaxResp2 = await fetch(SLY_LOGIN_URL, {
        method: "POST",
        headers: {
          "User-Agent": BROWSER_HEADERS["User-Agent"],
          "Accept": "application/json",
          "Content-Type": "application/json",
          "X-Requested-With": "XMLHttpRequest",
          "X-XSRF-TOKEN": xsrfDecoded3,
          Cookie: Object.entries(cookieJar).map(([k, v]) => `${k}=${v}`).join("; "),
          Origin: "https://series.ly",
          Referer: SLY_LOGIN_URL,
        },
        body: JSON.stringify({
          _token: csrfToken3,
          email: "debug-test@example.com",
          password: "fake-password-12345",
          remember: true,
        }),
        redirect: "manual",
      });

      let ajaxBody2 = "";
      try { ajaxBody2 = await ajaxResp2.text(); } catch {}

      let ajaxParsed2 = null;
      try { ajaxParsed2 = JSON.parse(ajaxBody2); } catch {}

      steps[4] = {
        step: "5. POST AJAX sin campo cf-turnstile-response",
        status: ajaxResp2.status,
        body: ajaxParsed2 || ajaxBody2.substring(0, 500),
        contentType: ajaxResp2.headers.get("content-type") || "",
      };
    }
    // Step 6: Extract JS files and inline scripts from login page
    steps.push({ step: "6. Login page JavaScript analysis", status: "..." });
    {
      // Find script src URLs
      const scriptSrcs = [];
      const srcMatches = pageHtml.matchAll(/<script[^>]+src="([^"]+)"/g);
      for (const m of srcMatches) scriptSrcs.push(m[1]);

      // Find inline scripts
      const inlineScripts = [];
      const inlineMatches = pageHtml.matchAll(/<script(?:\s[^>]*)?>([^]*?)<\/script>/gi);
      for (const m of inlineMatches) {
        const body = m[1].trim();
        if (body && body.length > 10) {
          inlineScripts.push(body.substring(0, 500));
        }
      }

      // Look for keywords in the full HTML
      const htmlLower = pageHtml.toLowerCase();
      const keywords = {};
      for (const kw of ["turnstile", "captcha", "cf-turnstile", "hcaptcha", "recaptcha",
                         "/api/", "fetch(", "axios", "XMLHttpRequest", "ingresar",
                         "login", "authenticate", "csrf", "sanctum"]) {
        const count = (htmlLower.match(new RegExp(kw.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'), "gi")) || []).length;
        if (count > 0) keywords[kw] = count;
      }

      steps[steps.length - 1] = {
        step: "6. Login page JavaScript analysis",
        externalScripts: scriptSrcs,
        inlineScriptsCount: inlineScripts.length,
        inlineSnippets: inlineScripts.slice(0, 5),
        keywordsInHtml: keywords,
      };

      // Step 6b: Fetch the main JS bundle to see login logic
      const mainJsUrl = scriptSrcs.find(s => s.includes("/build/") || s.includes("/js/app") || s.includes("manifest"));
      if (mainJsUrl) {
        steps.push({ step: "6b. Fetching main JS bundle", status: "..." });
        try {
          const fullUrl = mainJsUrl.startsWith("http") ? mainJsUrl : "https://series.ly" + mainJsUrl;
          const jsResp = await fetch(fullUrl, { headers: BROWSER_HEADERS });
          const jsText = await jsResp.text();

          // Search for login-related patterns
          const patterns = {};
          for (const pat of ["turnstile", "cf-turnstile", "captcha", "/api/",
                             "login", "ingresar", "authenticate", "sanctum",
                             "token", "csrf", "post(", ".post("]) {
            const re = new RegExp(pat.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'), "gi");
            const matches = jsText.match(re);
            if (matches) patterns[pat] = matches.length;
          }

          // Find URLs in the JS
          const urlMatches = jsText.match(/["'](\/api\/[^"']+)["']/g) || [];
          const apiUrls = [...new Set(urlMatches)].slice(0, 20);

          // Find fetch/axios calls near "login" or "ingresar"
          const loginContexts = [];
          const loginIdx = [];
          let idx = 0;
          const jsLower = jsText.toLowerCase();
          while ((idx = jsLower.indexOf("ingresar", idx)) !== -1) {
            loginContexts.push(jsText.substring(Math.max(0, idx - 100), Math.min(jsText.length, idx + 200)));
            idx += 10;
            if (loginContexts.length >= 3) break;
          }
          idx = 0;
          while ((idx = jsLower.indexOf("turnstile", idx)) !== -1) {
            loginContexts.push("TURNSTILE: " + jsText.substring(Math.max(0, idx - 100), Math.min(jsText.length, idx + 200)));
            idx += 10;
            if (loginContexts.length >= 6) break;
          }

          steps[steps.length - 1] = {
            step: "6b. Main JS bundle analysis",
            url: fullUrl,
            sizeKB: Math.round(jsText.length / 1024),
            patterns,
            apiUrls,
            loginContexts: loginContexts.map(c => c.substring(0, 300)),
          };
        } catch (err) {
          steps[steps.length - 1] = {
            step: "6b. Main JS bundle fetch error",
            error: err.message,
          };
        }
      }
    }

    // Step 8: Probe API login endpoints (Sanctum token-based auth)
    // Laravel Sanctum often has /api/login that accepts JSON and
    // doesn't require Turnstile — only the web form does.
    const apiEndpoints = [
      { url: "https://series.ly/api/login", method: "POST" },
      { url: "https://series.ly/api/auth/login", method: "POST" },
      { url: "https://series.ly/api/sanctum/token", method: "POST" },
      { url: "https://series.ly/api/v1/login", method: "POST" },
      { url: "https://series.ly/api/v1/auth/login", method: "POST" },
      { url: "https://series.ly/api/user", method: "GET" },
    ];

    for (let i = 0; i < apiEndpoints.length; i++) {
      const ep = apiEndpoints[i];
      steps.push({ step: `8.${i+1}. ${ep.method} ${ep.url}`, status: "..." });
      try {
        const apiCookieStr = Object.entries(cookieJar).map(([k, v]) => `${k}=${v}`).join("; ");
        const xsrf = cookieJar["XSRF-TOKEN"] ? decodeURIComponent(cookieJar["XSRF-TOKEN"]) : "";
        const apiHeaders = {
          "User-Agent": BROWSER_HEADERS["User-Agent"],
          "Accept": "application/json",
          "Content-Type": "application/json",
          "X-Requested-With": "XMLHttpRequest",
          "Origin": "https://series.ly",
          "Referer": "https://series.ly/ingresar",
          Cookie: apiCookieStr,
        };
        if (xsrf) apiHeaders["X-XSRF-TOKEN"] = xsrf;

        const apiOpts = { method: ep.method, headers: apiHeaders, redirect: "manual" };
        if (ep.method === "POST") {
          apiOpts.body = JSON.stringify({
            email: "probe@example.com",
            password: "probe12345",
            device_name: "kodi-mejorwolf",
          });
        }

        const apiResp = await fetch(ep.url, apiOpts);
        let apiBody = "";
        try { apiBody = await apiResp.text(); } catch {}
        let apiJson = null;
        try { apiJson = JSON.parse(apiBody); } catch {}

        steps[steps.length - 1] = {
          step: `8.${i+1}. ${ep.method} ${ep.url}`,
          status: apiResp.status,
          contentType: apiResp.headers.get("content-type") || "",
          body: apiJson || apiBody.substring(0, 300),
          location: apiResp.headers.get("location") || "",
        };
      } catch (err) {
        steps[steps.length - 1] = {
          step: `8.${i+1}. ${ep.method} ${ep.url}`,
          error: err.message,
        };
      }
    }

  } catch (err) {
    steps.push({ step: "ERROR", message: err.message, stack: err.stack });
  }

  return new Response(JSON.stringify({ debug: true, steps }, null, 2), {
    headers: {
      "content-type": "application/json",
      "access-control-allow-origin": "*",
    },
  });
}

function sleep(ms) {
  return new Promise((r) => setTimeout(r, ms));
}

function jsonResp(data, status = 200) {
  return new Response(JSON.stringify(data), {
    status,
    headers: {
      "content-type": "application/json",
      "access-control-allow-origin": "*",
      "access-control-allow-methods": "POST, OPTIONS",
      "access-control-allow-headers": "Content-Type",
    },
  });
}


export default {
  async fetch(request, env) {
    const reqUrl = new URL(request.url);

    // ===== CORS preflight =====
    if (request.method === "OPTIONS") {
      return new Response(null, {
        headers: {
          "access-control-allow-origin": "*",
          "access-control-allow-methods": "GET, POST, OPTIONS",
          "access-control-allow-headers": "Content-Type",
        },
      });
    }

    // ===== /pair: pagina de pairing + login via Browser Rendering =====
    if (reqUrl.pathname === "/pair" || reqUrl.pathname === "/pair/") {
      return new Response(pairPage(), {
        headers: { "content-type": "text/html; charset=utf-8" },
      });
    }
    if (reqUrl.pathname === "/pair/login" && request.method === "POST") {
      return handlePairLogin(request, env);
    }
    if (reqUrl.pathname === "/pair/debug") {
      return handlePairDebug(request);
    }

    // ===== /login: proxy de login de Series.ly (legacy) =====
    if (reqUrl.pathname === "/login" || reqUrl.pathname === "/login/") {
      return handleLogin(request);
    }
    if (reqUrl.pathname.startsWith("/login/")) {
      // Sub-paths: /login/ingresar, /login/sanctum/csrf-cookie, etc.
      if (reqUrl.pathname === "/login/ingresar") {
        return handleLogin(request);
      }
      return proxyLoginAsset(request);
    }

    // ===== /dtsearch: buscar en DonTorrent (pass-challenge + POST en un solo isolate) =====
    if (reqUrl.pathname === "/dtsearch" && request.method === "POST") {
      try {
        const body = await request.json();
        if (!body.domain || !body.q) {
          return jsonResp({ error: "missing domain or q" }, 400);
        }
        return dtSearch(body);
      } catch (err) {
        return jsonResp({ error: "bad JSON: " + (err && err.message || err) }, 400);
      }
    }

    // ===== /dtpow: resolver PoW de descarga DonTorrent =====
    if (reqUrl.pathname === "/dtpow" && request.method === "POST") {
      try {
        const body = await request.json();
        if (!body.domain || body.content_id == null || !body.tabla) {
          return jsonResp({ error: "missing domain, content_id, or tabla" }, 400);
        }
        return dtPow(body);
      } catch (err) {
        return jsonResp({ error: "bad JSON: " + (err && err.message || err) }, 400);
      }
    }

    // Ruta dedicada para buscador WolfMax (GET+POST en un solo isolate)
    if (reqUrl.pathname === "/wfsearch") {
      const q = reqUrl.searchParams.get("q") || "";
      if (!q) {
        return new Response("missing q", { status: 400 });
      }
      try {
        return await wfSearch(q);
      } catch (e) {
        return new Response(
          "wfsearch error: " + (e && e.message ? e.message : String(e)),
          { status: 502 }
        );
      }
    }

    const target = reqUrl.searchParams.get("u");

    if (!target) {
      return new Response(
        "MejorWolf relay OK. Use ?u=<absolute url>",
        { status: 200, headers: { "content-type": "text/plain" } }
      );
    }
    if (!hostAllowed(target)) {
      return new Response("Host not allowed: " + target, { status: 403 });
    }

    // Headers aguas arriba. Empezamos con valores tipo-navegador y despues
    // dejamos que el cliente sobreescriba selectivamente. Esto es CRITICO
    // para endpoints AJAX como /mvc/controllers/data.find.php de WolfMax,
    // que solo devuelven JSON si ven X-Requested-With: XMLHttpRequest y un
    // Accept que incluya application/json. Si el worker reescribe esos
    // headers, el upstream responde con la plantilla HTML del buscador en
    // lugar del JSON esperado -> 0 resultados.
    const fwdHeaders = { ...BROWSER_HEADERS };
    const cookie = request.headers.get("cookie");
    if (cookie) fwdHeaders["Cookie"] = cookie;
    const ct = request.headers.get("content-type");
    if (ct) fwdHeaders["Content-Type"] = ct;
    const ref = request.headers.get("referer");
    if (ref && hostAllowed(ref)) fwdHeaders["Referer"] = ref;

    // Headers opcionales que el cliente puede enviar y queremos reenviar
    // tal cual. Cualquier otro header entrante se ignora para no filtrar
    // identificadores de Cloudflare.
    const PASSTHROUGH = [
      "x-requested-with",
      "origin",
      "accept",
      "accept-language",
      "x-csrf-token",
      "x-xsrf-token",
    ];
    for (const name of PASSTHROUGH) {
      const v = request.headers.get(name);
      if (v) {
        // Normalizamos al case capitalizado que suelen usar los servidores
        const canonical = name
          .split("-")
          .map((p) => p.charAt(0).toUpperCase() + p.slice(1))
          .join("-");
        fwdHeaders[canonical] = v;
      }
    }

    // &nr=1 -> redirect: "manual" (no seguir redirects, util para diagnostico)
    const noRedirect = reqUrl.searchParams.get("nr") === "1";

    try {
      const init = {
        method: request.method,
        headers: fwdHeaders,
        redirect: noRedirect ? "manual" : "follow",
      };
      if (!["GET", "HEAD"].includes(request.method)) {
        init.body = await request.arrayBuffer();
      }

      const upstream = await fetch(target, init);
      // arrayBuffer() fuerza a CF a descomprimir el body antes de devolverlo.
      const buf = await upstream.arrayBuffer();

      const headers = new Headers();
      for (const [k, v] of upstream.headers.entries()) {
        if (!SKIP_RESP_HEADERS.has(k.toLowerCase())) {
          headers.set(k, v);
        }
      }

      // Set-Cookie llega como header multiple; reenviarlo uno a uno.
      const setCookies =
        typeof upstream.headers.getSetCookie === "function"
          ? upstream.headers.getSetCookie()
          : (upstream.headers.get("set-cookie")
              ? [upstream.headers.get("set-cookie")]
              : []);
      for (const c of setCookies) headers.append("set-cookie", c);

      headers.set("content-length", String(buf.byteLength));
      headers.set("access-control-allow-origin", "*");
      headers.set("x-mw-relay-status", String(upstream.status));
      headers.set("x-mw-relay-final", upstream.url);

      return new Response(buf, {
        status: upstream.status,
        statusText: upstream.statusText,
        headers,
      });
    } catch (err) {
      return new Response(
        "relay error: " + (err && err.message ? err.message : String(err)),
        { status: 502 }
      );
    }
  },
};
