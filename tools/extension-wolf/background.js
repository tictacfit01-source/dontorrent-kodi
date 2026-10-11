// MejorWolf · WolfTorrent -> tele (Manifest V3, service worker)
//
// La verificacion de WolfTorrent la pasa TU navegador, contigo delante: esta
// extension no abre, ni pulsa, ni resuelve nada en su web. Actua solo DESPUES:
// cuando una descarga de WolfTorrent termina bien (un .torrent o el ZIP del
// dia), se la da a MejorWolf (/wfsube) con la pagina de la que salio, que es lo
// que la ata a SU pelicula o capitulo. Si no se puede entregar, queda en cola y
// se reintenta sin volver a descargarla. Y por la mañana avisa del ZIP.
importScripts("logica.js");
var L = self.MWLogica;
var ZIP_PAGINA = "https://wolftorrent.com/torrents-del-dia";

// --- Donde viven los archivos pendientes: IndexedDB (no cabe en storage) -------
var Almacen = self.MWAlmacen || (function () {
  var abierta = null;
  function db() {
    if (!abierta) {
      abierta = new Promise(function (ok, ko) {
        var r = indexedDB.open("mejorwolf", 1);
        r.onupgradeneeded = function () { r.result.createObjectStore("archivos"); };
        r.onsuccess = function () { ok(r.result); };
        r.onerror = function () { ko(r.error); };
      });
    }
    return abierta;
  }
  function op(modo, fn) {
    return db().then(function (d) {
      return new Promise(function (ok, ko) {
        var tx = d.transaction("archivos", modo);
        var req = fn(tx.objectStore("archivos"));
        tx.oncomplete = function () { ok(req ? req.result : undefined); };
        tx.onerror = function () { ko(tx.error); };
      });
    });
  }
  return {
    guarda: function (id, b) { return op("readwrite", function (s) { return s.put(b, id); }); },
    lee: function (id) { return op("readonly", function (s) { return s.get(id); }); },
    borra: function (id) { return op("readwrite", function (s) { return s.delete(id); }); }
  };
})();

// --- La cola (estado de cada envio), en storage.local; un cambio a la vez -------
var cerrojo = Promise.resolve();
function conCola(fn) {
  var p = cerrojo.then(function () {
    return chrome.storage.local.get({ cola: {} }).then(function (s) {
      var r = fn(s.cola);
      var claves = Object.keys(s.cola);
      if (claves.length > 300) {          // lo viejo y terminado se olvida
        claves.sort(function (a, b) { return s.cola[a].ts - s.cola[b].ts; });
        claves.slice(0, claves.length - 300).forEach(function (k) {
          if (s.cola[k].estado !== "pendiente") delete s.cola[k];
        });
      }
      return chrome.storage.local.set({ cola: s.cola }).then(function () { return r; });
    });
  });
  cerrojo = p.catch(function () {});
  return p;
}

function avisa(titulo, texto) {
  try {
    chrome.notifications.create("", { type: "basic", iconUrl: "icono.png", title: titulo, message: texto });
  } catch (e) { console.warn("[MejorWolf] aviso:", e); }
}

async function pintaIcono() {
  var s = await chrome.storage.local.get({ cola: {} });
  var r = L.resumen(s.cola);
  var malos = r.fallido + r.falta_codigo;
  var n = r.pendiente + malos;
  await chrome.action.setBadgeText({ text: n ? String(n) : "" });
  await chrome.action.setBadgeBackgroundColor({ color: malos ? "#d93025" : "#1a73e8" });
}

async function huella(b, alg) {
  var h = await crypto.subtle.digest(alg || "SHA-1", b);
  return Array.from(new Uint8Array(h)).map(function (x) { return ("0" + x.toString(16)).slice(-2); }).join("");
}

// El archivo que tu navegador acaba de bajar, otra vez desde la misma URL y con
// tu misma sesion: una extension no puede leer la carpeta de Descargas.
async function recoge(item) {
  var r = await fetch(item.finalUrl || item.url, { credentials: "include" });
  if (!r.ok) throw new Error("WolfTorrent contestó " + r.status + " al recoger el archivo.");
  var b = new Uint8Array(await r.arrayBuffer());
  if (b.length > L.MAX) throw new Error("El archivo es demasiado grande.");
  var tipo = L.tipoDe(b);
  if (!tipo) throw new Error("Lo descargado no es un .torrent ni un ZIP completo (¿una página guardada?).");
  return { bytes: b, tipo: tipo };
}

// Una descarga terminada: ¿es nuestra? -> a la cola -> intento de envio.
async function nuevaDescarga(item) {
  if (!item || !L.esDeWolf(item)) return "ajena";
  if (!L.descargaValida(item)) return "incompleta";
  var id = "d" + item.id;
  var nueva = await conCola(function (c) {
    if (c[id]) return false;               // onChanged puede repetirse
    c[id] = { id: id, nombre: L.nombreDe(item), ref: item.referrer || "", estado: "recogiendo",
              intentos: 0, ts: Date.now(), texto: "" };
    return true;
  });
  if (!nueva) return "repetida";
  var a;
  try {
    a = await recoge(item);
  } catch (e) {
    await conCola(function (c) { c[id].estado = "fallido"; c[id].texto = String(e.message || e); });
    avisa("MejorWolf: no se pudo enviar", String(e.message || e) + " Súbelo a mano desde la web.");
    await pintaIcono();
    return "fallido";
  }
  var sha = await huella(a.bytes);
  var dup = await conCola(function (c) {
    var otro = Object.keys(c).some(function (k) {
      return k !== id && c[k].sha === sha && (c[k].estado === "enviado" || c[k].estado === "pendiente");
    });
    c[id].sha = sha;
    c[id].tipo = a.tipo;
    c[id].estado = otro ? "duplicado" : "pendiente";
    c[id].texto = otro ? "Ya enviado antes (mismo archivo)." : "";
    c[id].prox = Date.now();
    return otro;
  });
  if (dup) { await pintaIcono(); return "duplicado"; }
  await Almacen.guarda(id, a.bytes);
  return await intenta(id);
}

async function intenta(id) {
  var cfg = await chrome.storage.sync.get({ code: "" });
  var e = (await chrome.storage.local.get({ cola: {} })).cola[id];
  if (!e || e.estado === "enviado" || e.estado === "duplicado") return e && e.estado;
  var b = await Almacen.lee(id);
  if (!b) {
    await conCola(function (c) { c[id].estado = "fallido"; c[id].texto = "Se perdió el archivo: bájalo otra vez."; });
    await pintaIcono();
    return "fallido";
  }
  var st = 0, js = null;
  if (!L.codigoValido(cfg.code)) {
    st = 401;
  } else {
    var fd = new FormData();
    fd.append("code", cfg.code);
    if (e.ref) fd.append("ref", e.ref);
    // el relay la recalcula sobre lo que recibe: si no coincide, no lo guarda
    fd.append("sha", await huella(b, "SHA-256"));
    fd.append("f", new Blob([b], { type: e.tipo === "zip" ? "application/zip" : "application/x-bittorrent" }),
              e.nombre);
    try {
      var r = await fetch(L.RELAY + "/wfsube", { method: "POST", body: fd });
      st = r.status;
      try { js = await r.json(); } catch (x) { js = null; }
    } catch (x) { st = 0; }
  }
  var c1 = L.clasifica(st, js);
  var fin = await conCola(function (c) {
    var it = c[id];
    it.intentos += 1;
    it.texto = c1.texto;
    it.estado = c1.estado;
    if (c1.estado === "pendiente") {
      it.prox = L.proximo(it.intentos, Date.now());
      if (it.prox === null) { it.estado = "fallido"; it.texto = "No se pudo entregar tras varios intentos: " + c1.texto; }
    }
    if (c1.estado === "enviado" && js && js.tipo === "zip" && js.dia) it.dia = js.dia;
    return it.estado;
  });
  if (fin === "enviado" || fin === "fallido") await Almacen.borra(id);
  if (fin === "enviado" && js && js.tipo === "zip" && js.dia) await chrome.storage.sync.set({ ultZip: js.dia });
  if (fin === "enviado") avisa("Enviado a MejorWolf", c1.texto);
  else if (fin === "fallido") avisa("MejorWolf no lo ha guardado", c1.texto);
  else if (fin === "falta_codigo") avisa("Falta el código de tu Kodi", c1.texto);
  await pintaIcono();
  return fin;
}

// Lo que toca reintentar ahora (y, con `todo`, tambien lo fallido o sin codigo).
async function reintenta(todo) {
  var cola = (await chrome.storage.local.get({ cola: {} })).cola;
  var ahora = Date.now();
  var ids = Object.keys(cola).filter(function (k) {
    var e = cola[k];
    if (e.estado === "pendiente") return todo || (e.prox || 0) <= ahora;
    return todo && (e.estado === "falta_codigo" || (e.estado === "fallido" && e.sha));
  });
  var out = [];
  for (var i = 0; i < ids.length; i++) {
    if (todo) await conCola(function (c) { c[ids[i]].estado = "pendiente"; c[ids[i]].intentos = 0; });
    out.push(await intenta(ids[i]));
  }
  return out;
}

chrome.downloads.onChanged.addListener(function (d) {
  if (!d || !d.state || d.state.current !== "complete") return;
  chrome.downloads.search({ id: d.id }).then(function (items) {
    return nuevaDescarga(items && items[0]);
  }).catch(function (e) { console.warn("[MejorWolf] descarga:", e); });
});

// --- El aviso de cada mañana y los reintentos -----------------------------------
// chrome.alarms sobrevive a reinicios; con Chrome CERRADO no corre nada, y al
// abrirlo la alarma vencida salta enseguida: el aviso llega entonces.
async function programa() {
  await chrome.alarms.create("mw", { delayInMinutes: 1, periodInMinutes: 1 });
  await pintaIcono();
}
chrome.runtime.onInstalled.addListener(programa);
chrome.runtime.onStartup.addListener(programa);

async function revisaZip(ahora) {
  var cfg = await chrome.storage.sync.get({ aviso: true, ultZip: "", avisado: "", mirado: 0 });
  if (!cfg.aviso) return "apagado";
  var dia = cfg.ultZip;
  if (!L.zipPendiente(dia, ahora)) return "al dia";
  if (cfg.avisado === L.iso(ahora)) return "ya avisado";
  // ¿Lo ha subido ya otra persona de la familia? Lo sabe MejorWolf (como mucho
  // cada 30 min: esto corre cada minuto por los reintentos).
  if ((ahora.getTime() - cfg.mirado) >= 30 * 60e3) {
    await chrome.storage.sync.set({ mirado: ahora.getTime() });
    try {
      var r = await fetch(L.RELAY + "/catdiag");
      var z = (((await r.json()) || {}).wfih || {}).zip || {};
      if (z.dia && (!dia || z.dia > dia)) {
        dia = z.dia;
        await chrome.storage.sync.set({ ultZip: dia });
      }
    } catch (e) { /* sin red: se decide con lo que se sabe */ }
    if (!L.zipPendiente(dia, ahora)) return "al dia";
  }
  await chrome.storage.sync.set({ avisado: L.iso(ahora) });
  chrome.notifications.create("zip-dia", {
    type: "basic", iconUrl: "icono.png", requireInteraction: true,
    title: "Ya está el ZIP de WolfMax de ayer",
    message: "Pulsa aquí: se abre su página, das a «Descargar» y MejorWolf hace el resto." });
  return "avisado";
}

chrome.alarms.onAlarm.addListener(function (a) {
  if (!a || a.name !== "mw") return;
  reintenta(false).then(function () { return revisaZip(new Date()); })
    .catch(function (e) { console.warn("[MejorWolf] alarma:", e); });
});

chrome.notifications.onClicked.addListener(function (id) {
  if (id === "zip-dia") chrome.tabs.create({ url: ZIP_PAGINA });
  chrome.notifications.clear(id);
});

// La ventanita del icono y las opciones.
chrome.runtime.onMessage.addListener(function (m, _de, responde) {
  if (m && m.op === "estado") {
    chrome.storage.local.get({ cola: {} }).then(function (s) { responde({ cola: s.cola }); });
    return true;
  }
  if (m && m.op === "reintentar") {
    reintenta(true).then(function (r) { responde({ hechos: r }); });
    return true;
  }
  if (m && m.op === "limpiar") {
    conCola(function (c) {
      Object.keys(c).forEach(function (k) { if (c[k].estado !== "pendiente") delete c[k]; });
    }).then(pintaIcono).then(function () { responde({ ok: true }); });
    return true;
  }
  return false;
});

self.MWExt = { nuevaDescarga: nuevaDescarga, intenta: intenta, reintenta: reintenta, revisaZip: revisaZip };
