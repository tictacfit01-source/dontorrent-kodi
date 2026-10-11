// Lo que DECIDE la extension, sin chrome ni red: asi se prueba en node
// (tools/pruebas/extension_wolf.js). El navegador la carga con importScripts.
(function (raiz) {
  "use strict";
  // El UNICO destino de los archivos (y el unico host, con wolftorrent.com, que
  // pide el manifest). No es configurable a proposito.
  var RELAY = "https://mw-render-relay-6noq.onrender.com";
  var HOSTS = ["wolftorrent.com"];
  var MAX = 15 * 1024 * 1024;            // el tope de /wfsube en el relay
  var ESPERAS_MIN = [1, 5, 15, 60, 180]; // reintentos; despues, fallido

  function hostDe(u) {
    try { return new URL(u).hostname.toLowerCase().replace(/^www\./, ""); }
    catch (e) { return ""; }
  }

  function nombreDe(item) {
    var n = String((item && item.filename) || "").split(/[\\/]/).pop();
    return n || "wolftorrent";
  }

  // ¿Es de WolfTorrent y un .torrent o su ZIP? (lo demas que se baje, ni se mira)
  function esDeWolf(item) {
    var u = (item && (item.finalUrl || item.url)) || "";
    if (HOSTS.indexOf(hostDe(u)) < 0) return false;
    var ruta = "";
    try { ruta = new URL(u).pathname.toLowerCase(); } catch (e) { return false; }
    return /\.(torrent|zip)$/.test(nombreDe(item).toLowerCase()) || /\.(torrent|zip)$/.test(ruta);
  }

  // ¿Termino BIEN? Completa, sin error, con todos sus bytes, existe en disco y
  // no es una pagina (una verificacion o un error guardados como archivo).
  function descargaValida(item) {
    if (!item || item.state !== "complete" || item.error) return false;
    if (item.exists === false) return false;
    if (item.totalBytes > 0 && item.bytesReceived !== item.totalBytes) return false;
    if (/^text\/html/i.test(String(item.mime || ""))) return false;
    return true;
  }

  function contiene(b, txt, desde) {
    var t = [];
    for (var i = 0; i < txt.length; i++) t.push(txt.charCodeAt(i));
    for (var j = desde || 0; j + t.length <= b.length; j++) {
      var k = 0;
      while (k < t.length && b[j + k] === t[k]) k++;
      if (k === t.length) return true;
    }
    return false;
  }

  // "torrent", "zip" o "" mirando DENTRO: un .torrent es bencode con su "info";
  // un ZIP empieza por PK\x03\x04 y acaba con su directorio (PK\x05\x06).
  function tipoDe(b) {
    if (!b || b.length < 32) return "";
    if (b[0] === 0x50 && b[1] === 0x4b && b[2] === 0x03 && b[3] === 0x04) {
      return contiene(b, "PK\x05\x06", Math.max(0, b.length - 65557)) ? "zip" : "";
    }
    if (b[0] === 0x64 && contiene(b, "4:info")) return "torrent";
    return "";
  }

  function iso(x) {
    return x.getFullYear() + "-" + ("0" + (x.getMonth() + 1)).slice(-2) + "-" + ("0" + x.getDate()).slice(-2);
  }

  function diaES(d) {
    var m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(d || "");
    return m ? m[3] + "/" + m[2] : "ayer";
  }

  // El ZIP oficial sale a las 07:00 con lo del dia ANTERIOR: desde esa hora,
  // si lo ultimo subido es de antes de ayer, esta pendiente.
  function zipPendiente(ultDia, ahora) {
    if (ahora.getHours() < 7) return false;
    var esperado = iso(new Date(ahora.getTime() - 864e5));
    return !ultDia || ultDia < esperado;
  }

  // Que hacer con la respuesta de /wfsube. `st` = codigo HTTP (0: sin red).
  function clasifica(st, r) {
    if (st >= 200 && st < 300 && r && r.ok) return { estado: "enviado", texto: textoOk(r) };
    var err = (r && r.error) || (st ? "MejorWolf contestó " + st + "." : "Sin conexión con MejorWolf.");
    if (st === 401) return { estado: "falta_codigo", texto: "Pon el código de tu Kodi en las opciones de la extensión." };
    if (st === 0 || st === 429 || st >= 500 || (st === 403 && r && r.reintentar)) {
      return { estado: "pendiente", texto: err };
    }
    return { estado: "fallido", texto: err };   // 400, 409, 413...: reintentar no lo arregla
  }

  function textoOk(r) {
    if (r.tipo === "zip") {
      return r.n + " torrents de WolfMax del " + diaES(r.dia) + " guardados: ya se ven en la tele.";
    }
    return (r.nombre || "El .torrent") + (r.ya ? ": ya lo teníamos." : ": guardado, ya se puede ver en la tele.");
  }

  // Cuando toca el siguiente intento, o null si ya no hay mas.
  function proximo(intentos, ahoraMs) {
    if (intentos >= ESPERAS_MIN.length + 1) return null;
    return ahoraMs + ESPERAS_MIN[Math.max(0, intentos - 1)] * 60e3;
  }

  function resumen(cola) {
    var r = { enviado: 0, pendiente: 0, fallido: 0, duplicado: 0, falta_codigo: 0 };
    Object.keys(cola || {}).forEach(function (k) {
      var e = cola[k].estado;
      if (r[e] !== undefined) r[e]++;
    });
    return r;
  }

  function codigoValido(c) { return /^\d{6}$/.test(String(c || "")); }

  var api = { RELAY: RELAY, HOSTS: HOSTS, MAX: MAX, ESPERAS_MIN: ESPERAS_MIN, hostDe: hostDe,
              nombreDe: nombreDe, esDeWolf: esDeWolf, descargaValida: descargaValida, tipoDe: tipoDe,
              iso: iso, diaES: diaES, zipPendiente: zipPendiente, clasifica: clasifica,
              proximo: proximo, resumen: resumen, codigoValido: codigoValido };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else raiz.MWLogica = api;
})(typeof self !== "undefined" ? self : this);
