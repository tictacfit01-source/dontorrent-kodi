chrome.storage.sync.get({ code: "", aviso: true }, function (s) {
  document.getElementById("code").value = s.code;
  document.getElementById("aviso").checked = !!s.aviso;
});

document.getElementById("code").addEventListener("input", function () {
  this.value = this.value.replace(/\D/g, "").slice(0, 6);
});

document.getElementById("guardar").addEventListener("click", function () {
  var code = document.getElementById("code").value.trim();
  var est = document.getElementById("estado");
  if (!/^\d{6}$/.test(code)) {
    est.style.color = "#b00";
    est.textContent = "El código son 6 cifras (el mismo que pones en la web de MejorWolf).";
    return;
  }
  chrome.storage.sync.set({ code: code, aviso: document.getElementById("aviso").checked }, function () {
    est.style.color = "#2a7a2a";
    est.textContent = "Guardado.";
    // lo que esperaba por el codigo, ahora sale
    chrome.runtime.sendMessage({ op: "reintentar" });
  });
});
