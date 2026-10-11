var ICONO = { enviado: "✅", pendiente: "⏳", recogiendo: "⏳", fallido: "❌",
              duplicado: "↩️", falta_codigo: "🔑" };
var NOMBRE = { enviado: "enviados", pendiente: "pendientes", fallido: "fallidos",
               falta_codigo: "sin código", duplicado: "repetidos" };

function pinta(cola) {
  var ids = Object.keys(cola).sort(function (a, b) { return cola[b].ts - cola[a].ts; });
  var cuenta = {};
  ids.forEach(function (k) { cuenta[cola[k].estado] = (cuenta[cola[k].estado] || 0) + 1; });
  document.getElementById("cuenta").textContent = Object.keys(NOMBRE)
    .filter(function (k) { return cuenta[k]; })
    .map(function (k) { return cuenta[k] + " " + NOMBRE[k]; }).join(" · ") || "Nada todavía";
  var ul = document.getElementById("lista");
  ul.textContent = "";
  if (!ids.length) {
    var v = document.createElement("li");
    v.className = "vacio";
    v.textContent = "Cuando bajes un .torrent o el ZIP del día de WolfTorrent, aparecerá aquí.";
    ul.appendChild(v);
    return;
  }
  ids.slice(0, 30).forEach(function (k) {
    var e = cola[k];
    var li = document.createElement("li");
    var n = document.createElement("div");
    n.className = "n";
    n.textContent = (ICONO[e.estado] || "•") + " " + e.nombre;
    var t = document.createElement("div");
    t.className = "t";
    t.textContent = e.texto || (e.estado === "pendiente" ? "En cola…" : "");
    li.appendChild(n);
    li.appendChild(t);
    ul.appendChild(li);
  });
}

function refresca() {
  chrome.runtime.sendMessage({ op: "estado" }, function (r) { pinta((r && r.cola) || {}); });
}

document.getElementById("reintentar").addEventListener("click", function () {
  this.disabled = true;
  var b = this;
  chrome.runtime.sendMessage({ op: "reintentar" }, function () { b.disabled = false; refresca(); });
});
document.getElementById("limpiar").addEventListener("click", function () {
  chrome.runtime.sendMessage({ op: "limpiar" }, refresca);
});
document.getElementById("opciones").addEventListener("click", function () {
  chrome.runtime.openOptionsPage();
});
refresca();
