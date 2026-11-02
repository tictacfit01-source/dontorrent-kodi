// Cinubo en la web (dtbl86-89), con el codigo REAL de la pagina.
//
//   1) los cuadros que se abren desde la ficha de una serie quedan ENCIMA de
//      ella (02-11: el de Cinubo se abria por debajo y no se veia);
//   2) la lista: sin repetidos, los que la tele abre sola arriba, el resto
//      plegado y avisado;
//   3) al elegir: si esa subida no da servidor, se prueba su gemela (mismo
//      servidor y calidad); con captcha no se insiste; y a la tele va la orden
//      st con la pagina del servidor.
//
//   node tools/pruebas/cinubo_web.js
const fs = require('fs');
const path = require('path');

const APP = path.join(__dirname, '..', '..', 'render_relay', 'app.py');
const src = fs.readFileSync(APP, 'utf8');
function saca(desde, hasta) {
  const i = src.indexOf(desde);
  if (i < 0) throw new Error('no encuentro: ' + desde);
  const j = src.indexOf(hasta, i + desde.length);
  if (j < 0) throw new Error('no encuentro el final de: ' + desde);
  return src.slice(i, j);
}
let fallos = 0;
function comprueba(n, ok, d) {
  console.log((ok ? '  ok   ' : '  MAL  ') + n + (ok ? '' : '  -> ' + JSON.stringify(d)));
  if (!ok) fallos++;
}

console.log('\n=== 1) Por encima de la ficha de la serie ===');
const z = (sel) => { const m = new RegExp(sel.replace(/[.#]/g, '\\$&') + '[^{]*\\{[^}]*z-index:(\\d+)').exec(src); return m ? +m[1] : null; };
const zOv = Number(/\.ov\{[^}]*z-index:(\d+)/.exec(src)[1]);
const mz = /#cbdlg,#wfdlg(,#novdlg)?\{z-index:(\d+)\}/.exec(src);
const zDlg = Number(mz[2]);
comprueba('el cuadro de Cinubo y el de WolfMax, encima de .ov', zDlg > zOv, { zDlg, zOv });
comprueba('el de Novedades, también', !!mz[1]);
comprueba('...y debajo de los avisos (mwConfirm)', zDlg < Number(/#mwdlg\{z-index:(\d+)\}/.exec(src)[1]));

const codigo = saca('var CBSEL=null;', '\nfunction seekTo(');
function Elem() {
  const cls = new Set();
  return { innerHTML: '', textContent: '', classList: { add: (c) => cls.add(c), remove: (c) => cls.delete(c), contains: (c) => cls.has(c) },
           querySelector: () => ({}) };
}
const DOM = {};
const $ = (id) => DOM[id] || (DOM[id] = Elem());
const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const code = { value: '111111' };
const TOASTS = [], ENVIOS = [], PIDE = [];
let RESP = {};
const toast = (t) => TOASTS.push(t);
const avisaCodigo = () => {};
const mwBack = (id) => $(id).classList.remove('on');
const mwOpen = () => {};
const sendPlay = (ref) => { ENVIOS.push(ref); return true; };
const _tituloBase = (t) => t;
function fetch(u) {
  PIDE.push(u);
  const r = RESP[decodeURIComponent((u.split('r=')[1] || ''))] || { ok: false, error: 'no_disponible' };
  return Promise.resolve({ json: () => Promise.resolve(r) });
}
const setTimeout2 = (f) => f();
const CTX = new Function('$', 'esc', 'code', 'toast', 'avisaCodigo', 'mwBack', 'mwOpen', 'sendPlay', '_tituloBase', 'fetch', 'setTimeout',
  codigo + '\nreturn {cbPinta, cbPlay, cbGemelos, cbFunde, set: function(c){CBSEL=c}};')(
  $, esc, code, toast, avisaCodigo, mwBack, mwOpen, sendPlay, _tituloBase, fetch, setTimeout2);

console.log('\n=== 2) La lista ===');
const items = [
  { r: 'v1', srv: 'VIDARA', q: 'HD 1080p' }, { r: 'v2', srv: 'VIDARA', q: 'HD 1080p' },
  { r: 'l1', srv: 'LULUSTREAM', q: 'HD 1080p' }, { r: 'l2', srv: 'LULUSTREAM', q: 'HD 720p' },
  { r: 'p1', srv: 'POWVIDEO', q: 'HD 720p' }, { r: 'p2', srv: 'POWVIDEO', q: 'HD 720p' },
  { r: 'x1', srv: 'VIDMOLY', q: 'HD 720p' },
];
CTX.set({ k: 'k', el: 'cbd-l', t: 'Ted Lasso 1x10', items });
CTX.cbPinta('cbd-l');
const h = $('cbd-l').innerHTML;
const botones = (h.match(/cbPlay\(\d+\)/g) || []);
comprueba('sin repetidos: 5 botones para 7 enlaces', botones.length === 5, botones);
comprueba('los que la tele abre sola, arriba y fuera del plegable',
  h.indexOf('Vidara') < h.indexOf('<details') && h.indexOf('Lulustream') < h.indexOf('<details'), h);
comprueba('el resto, plegado y avisado', /<details class="cbmas"><summary>Más servidores \(pueden no abrir en la tele\)/.test(h)
  && h.indexOf('Powvideo') > h.indexOf('<details'));
CTX.set({ k: 'k', el: 'cbd-l', t: 'X', items: items.filter((x) => x.srv === 'POWVIDEO') });
CTX.cbPinta('cbd-l');
comprueba('si no hay ninguno seguro, los otros sin plegar', !/<details/.test($('cbd-l').innerHTML) && /Powvideo/.test($('cbd-l').innerHTML));

console.log('\n=== 2a) En la ficha: si el streaming es mejor, va primero ===');
globalThis.QRANK = { '4k': 5, '1080p': 4, hdtv: 3, '720p': 2 };
const movido = [];
const pb = { parentNode: { insertBefore: (el, ref) => movido.push(ref === pb ? 'antes' : 'despues') } };
DOM.sheet = Object.assign(Elem(), { querySelector: () => pb });
const ficha = (q) => { globalThis.sel = { source: 'dt', quality: q }; movido.length = 0;
  CTX.set({ k: 'k', el: 'sh-cb', t: 'Peli', items: [{ r: 'v1', srv: 'VIDARA', q: 'HD 1080p' }, { r: 'x', srv: 'VOE', q: 'HD 1080p' }] });
  CTX.cbPinta('sh-cb'); return $('sh-cb').innerHTML; };
let hf = ficha('720p');
comprueba('torrent 720p y streaming 1080p: sube por encima del botón, con "Mejor calidad"',
  movido[0] === 'antes' && /Mejor calidad en streaming/.test(hf), { movido, hf: hf.slice(0, 80) });
hf = ficha('4K');
comprueba('torrent 4K: el streaming se queda debajo', movido[0] === 'despues' && !/Mejor calidad/.test(hf), movido);
hf = ficha('1080p');
comprueba('a igual calidad: debajo (el torrent manda)', movido[0] === 'despues', movido);
hf = ficha('');
comprueba('torrent sin calidad conocida: no se presume, debajo', movido[0] === 'despues', movido);

console.log('\n=== 2b) Lo que los torrents aún no tienen (The Bear T5) ===');
const T = [{ s: 1, e: [1, 2, 3, 4, 5, 6, 7, 8] }, { s: 4, e: [1, 2, 3, 4, 5, 6, 7, 8, 9, 10] }, { s: 5, e: [1, 2, 3, 4, 5, 6, 7, 8] }];
const X = { tmdb_id: 136315, _cbt: T };
const tor = [{ season: 1, episode: 1, label: '1x01 al 1x03' }, { season: 1, episode: 4, label: '1x04' },
  { season: 1, episode: 5, label: '1x05 al 1x08' }, { season: 4, episode: 1, label: '4x01' }, { season: 4, episode: 2, label: '4x02 al 4x06' }];
const f = CTX.cbFunde(tor, X);
const nuevos = f.filter((e) => e.cb).map((e) => e.label);
comprueba('T1 completa en torrent (packs incluidos): nada añadido', !nuevos.some((l) => l.startsWith('1x')), nuevos);
comprueba('T4 a medias (hasta el 6): se añaden 4x07-4x10', JSON.stringify(nuevos.filter((l) => l.startsWith('4x'))) === '["4x07","4x08","4x09","4x10"]', nuevos);
comprueba('T5, que no tiene ningún torrent: entera', nuevos.filter((l) => l.startsWith('5x')).length === 8, nuevos);
comprueba('los torrents siguen ahí, igual y delante', f.slice(0, tor.length).every((e, i) => e === tor[i]));
comprueba('cada añadido es de streaming y con su propio id para "visto"',
  f.filter((e) => e.cb).every((e) => e.quality === 'Streaming' && /^cb:136315:\d+x\d+$/.test(e.content_id)));
comprueba('sin temporadas de TMDB: la lista tal cual', CTX.cbFunde(tor, { tmdb_id: 1 }) === tor);
comprueba('serie sin ningún torrent: todo de streaming', CTX.cbFunde([], X).length === 26);

console.log('\n=== 3) Al elegir ===');
(async () => {
  CTX.set({ k: 'k', el: 'cbd-l', t: 'Ted Lasso 1x10', items });
  comprueba('gemelos de Vidara 1080p: la otra subida', JSON.stringify(CTX.cbGemelos({ items }, 0)) === '[1]');
  RESP = { v2: { ok: true, u: 'https://vidara.to/e/OTRA' } };
  CTX.cbPlay(0);
  await new Promise((r) => setImmediate(r)); await new Promise((r) => setImmediate(r)); await new Promise((r) => setImmediate(r));
  comprueba('la primera no da servidor -> prueba la gemela y va a la tele',
    PIDE.length === 2 && ENVIOS.length === 1 && ENVIOS[0].a === 'st' && ENVIOS[0].u === 'https://vidara.to/e/OTRA'
    && ENVIOS[0].t === 'Ted Lasso 1x10', { PIDE, ENVIOS });
  PIDE.length = 0; ENVIOS.length = 0; TOASTS.length = 0;
  RESP = { v1: { ok: false, error: 'captcha' } };
  CTX.cbPlay(0);
  await new Promise((r) => setImmediate(r)); await new Promise((r) => setImmediate(r));
  comprueba('con captcha no se insiste (una sola petición) y se dice', PIDE.length === 1 && ENVIOS.length === 0
    && /verificación/.test(TOASTS.join(' ')), { PIDE, TOASTS });
  PIDE.length = 0; TOASTS.length = 0;
  RESP = {};
  CTX.cbPlay(6);
  await new Promise((r) => setImmediate(r)); await new Promise((r) => setImmediate(r));
  comprueba('sin gemelos y sin servidor: dice cuál falló y que se elija otro',
    PIDE.length === 1 && /Vidmoly no contesta ahora: elige otro servidor/.test(TOASTS.join(' ')), TOASTS);

  console.log('\n---- VEREDICTO ----');
  if (fallos) { console.log(fallos + ' comprobaciones MAL'); process.exit(1); }
  console.log('TODO OK: el cuadro se ve, la lista es corta y clara, y elegir un servidor llega a la tele');
})();
