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
  return { innerHTML: '', textContent: '', appendChild(f) { if ('src' in f) MARCOS.push(f); }, clientWidth: 390, clientHeight: 600, classList: { add: (c) => cls.add(c), remove: (c) => cls.delete(c), contains: (c) => cls.has(c) },
           querySelector: (q) => (q === 'iframe' ? MARCOS[MARCOS.length - 1] : ({})) };
}
const DOM = {};
const $ = (id) => DOM[id] || (DOM[id] = Elem());
const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const code = { value: '111111' };
const TOASTS = [], ENVIOS = [], PIDE = [];
let RESP = {};
const toast = (t) => TOASTS.push(t);
const avisaCodigo = () => {};
const MARCOS = [], ATRAS = [];
const mwBack = (id) => { ATRAS.push(id); $(id).classList.remove('on'); };
const mwOpen = () => {};
const sendPlay = (ref) => { ENVIOS.push(ref); return true; };
const _tituloBase = (t) => t;
function fetch(u) {
  PIDE.push(u);
  // (dtbl151) un servidor que la compañía bloquea: la petición falla (DNS)
  if ((globalThis.BLOQ || []).some((b) => u.indexOf(b) >= 0)) return Promise.reject(new TypeError('Failed to fetch'));
  const r = RESP[decodeURIComponent(((u.split('r=')[1] || '').split('&')[0]))] || { ok: false, error: 'no_disponible' };
  return Promise.resolve({ json: () => Promise.resolve(r) });
}
// el tope de 9 s de cpvAlcanza (¿llega el móvil al servidor?) no salta en las pruebas
const setTimeout2 = (f, ms) => (ms === 9000 ? 0 : f());
const CTX = new Function('$', 'esc', 'code', 'toast', 'avisaCodigo', 'mwBack', 'mwOpen', 'sendPlay', '_tituloBase', 'fetch', 'setTimeout',
  codigo + '\nreturn {cbPinta, cbPlay, cbGemelos, cbFunde, esPack, set: function(c){CBSEL=c}};')(
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
comprueba('cada subida de los que la tele abre sola; los demás sin repetidos; sin Powvideo (captcha, dtbl151): 5 botones para 7 enlaces',
  botones.length === 5 && botones.join() === 'cbPlay(0),cbPlay(1),cbPlay(2),cbPlay(3),cbPlay(6)', botones);
comprueba('las dos subidas de Vidara 1080p, numeradas (dtbl120)',
  /Vidara <i>1080p · 1<\/i>/.test(h) && /Vidara <i>1080p · 2<\/i>/.test(h), h);
comprueba('una sola subida: sin número', /Lulustream <i>1080p<\/i>/.test(h) && /Lulustream <i>720p<\/i>/.test(h), h);
comprueba('los que la tele abre sola, arriba y fuera del plegable',
  h.indexOf('Vidara') < h.indexOf('<details') && h.indexOf('Lulustream') < h.indexOf('<details'), h);
comprueba('el resto, plegado y avisado', /<details class="cbmas"><summary>Más servidores \(pueden no abrir en la tele\)/.test(h)
  && h.indexOf('Vidmoly') > h.indexOf('<details'));
CTX.set({ k: 'k', el: 'cbd-l', t: 'X', items: items.filter((x) => x.srv === 'POWVIDEO' || x.srv === 'VIDMOLY') });
CTX.cbPinta('cbd-l');
let hp = $('cbd-l').innerHTML;
comprueba('si no hay ninguno seguro, los otros sin plegar', !/<details/.test(hp) && /Vidmoly/.test(hp), hp);
comprueba('(dtbl151) en la tele, Powvideo: se dice que pide captcha y se ofrece verlo Aquí, sin botón suyo',
  /Powvideo pide captcha: en la tele no se puede/.test(hp) && /cbPonDonde\('aqui'/.test(hp) && !/cbPlay\([45]\)/.test(hp), hp);

console.log('\n=== 2d) Aquí, con captcha (dtbl151) ===');
globalThis.localStorage = { getItem: () => 'aqui', setItem: () => {} };
CTX.set({ k: 'k', el: 'cbd-l', t: 'Ted Lasso 1x10', items });
CTX.cbPinta('cbd-l');
hp = $('cbd-l').innerHTML;
comprueba('con servidores sin captcha: los de captcha plegados y avisados; Vidmoly ni sale',
  hp.indexOf('Vidara') < hp.indexOf('<details') && /Más servidores \(piden captcha\)/.test(hp) && /No soy un robot/.test(hp)
  && /Powvideo <i>720p · 1<\/i>/.test(hp) && /Powvideo <i>720p · 2<\/i>/.test(hp) && !/Vidmoly/.test(hp), hp);
const capItems = [{ r: 'p1', srv: 'POWVIDEO', q: 'HD 720p' }, { r: 's1', srv: 'STREAMPLAY', q: 'SD HDTV' }, { r: 'x1', srv: 'NETU', q: 'HD 720p' }];
CTX.set({ k: 'serie:456:8x2', el: 'cbd-l', t: 'Los Simpson 8x02', items: capItems });
CTX.cbPinta('cbd-l');
hp = $('cbd-l').innerHTML;
comprueba('solo con captcha: a la vista, avisados, y ya no "ningún servidor"',
  !/<details/.test(hp) && /No soy un robot/.test(hp) && /cbPlay\(0\)/.test(hp) && /cbPlay\(1\)/.test(hp) && !/cbPlay\(2\)/.test(hp)
  && !/ningún servidor/.test(hp), hp);
globalThis.localStorage = undefined;

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

console.log('\n=== 2c) Packs de temporada (Héroes, dtbl148) ===');
const H = { tmdb_id: 1639, _cbt: [{ s: 1, e: Array.from({ length: 23 }, (_, i) => i + 1) }, { s: 4, e: [1, 2, 3] }] };
const torH = [{ season: 1, episode: 1, label: '1x01 - 1x23' }, { season: 4, episode: 1, label: '4x01' }, { season: 4, episode: 2, label: '4x02' }, { season: 4, episode: 3, label: '4x03' }];
const fH = CTX.cbFunde(torH, H), nH = fH.filter((e) => e.cb).map((e) => e.label);
comprueba('el pack 1x01 - 1x23 no tapa: salen sus 23 capítulos de streaming', nH.filter((l) => l.startsWith('1x')).length === 23, nH);
comprueba('T4 completa en sueltos: nada añadido', !nH.some((l) => l.startsWith('4x')), nH);
comprueba('el pack sigue ahí, delante', fH[0] === torH[0]);
comprueba('esPack: «1x01 - 1x23» sí; «1x05 al 1x08» y «1x01 al 1x03» no; «4x01» no',
  CTX.esPack(torH[0]) && !CTX.esPack({ label: '1x05 al 1x08' }) && !CTX.esPack({ label: '1x01 al 1x03' }) && !CTX.esPack({ label: '4x01' }));

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
  RESP = { v1: { ok: false, error: 'espera', s: 40 } };
  CTX.cbPlay(0);
  for (let k = 0; k < 12; k++) await new Promise((r) => setImmediate(r));
  comprueba('Cinubo ocupado: lo dice, espera y reintenta solo (3 veces, sin pasar a la gemela)',
    PIDE.length === 4 && PIDE.every((u) => /r=v1&?/.test(u) || /r=v1$/.test(u)) && ENVIOS.length === 0
    && /mucha gente: lo pongo en la tele en 40 s/.test(TOASTS.join(' '))
    && /saturado ahora mismo/.test(TOASTS.join(' ')), { PIDE, TOASTS });
  PIDE.length = 0; ENVIOS.length = 0; TOASTS.length = 0;
  let veces = 0;
  RESP = {};
  Object.defineProperty(RESP, 'v1', { get: () => (++veces === 1 ? { ok: false, error: 'espera', s: 5 } : { ok: true, u: 'https://vidara.to/e/YA' }), enumerable: true });
  CTX.cbPlay(0);
  for (let k = 0; k < 12; k++) await new Promise((r) => setImmediate(r));
  comprueba('...y en cuanto hay hueco, a la tele', PIDE.length === 2 && ENVIOS.length === 1 && ENVIOS[0].u === 'https://vidara.to/e/YA', { PIDE, ENVIOS });
  PIDE.length = 0; TOASTS.length = 0;
  RESP = {};
  CTX.cbPlay(6);
  await new Promise((r) => setImmediate(r)); await new Promise((r) => setImmediate(r));
  for (let k = 0; k < 14; k++) await new Promise((r) => setImmediate(r));
  comprueba('si falla: prueba SOLA los servidores que la tele abre (como mucho 2 más, sin ráfagas) y luego lo dice',
    PIDE.length === 3 && /pruebo con Vidara/.test(TOASTS.join(' ')) && /elige otro servidor/.test(TOASTS[TOASTS.length - 1]), { PIDE, TOASTS });
  PIDE.length = 0; TOASTS.length = 0; ENVIOS.length = 0;
  RESP = { v1: { ok: false, error: 'muerto' }, v2: { ok: true, u: 'https://vidara.to/e/VIVO' } };
  CTX.cbPlay(0);
  for (let k = 0; k < 8; k++) await new Promise((r) => setImmediate(r));
  comprueba('enlace muerto: lo dice y pasa al siguiente, que va a la tele',
    /Ese enlace ya no existe: pruebo con Vidara/.test(TOASTS.join(' ')) && ENVIOS.length === 1 && ENVIOS[0].u === 'https://vidara.to/e/VIVO', { TOASTS, ENVIOS });

  console.log('\n=== 3b) Aquí, con captcha: el marco cerrado (dtbl151) ===');
  globalThis.localStorage = { getItem: () => 'aqui', setItem: () => {} };
  globalThis.history = { length: 5 };
  globalThis.document = { hidden: false, body: Elem(), createElement: (tag) => {
    const a = {}; const o = { setAttribute: (k, v) => { a[k] = v; }, attrs: a, style: {} }; if (tag === 'iframe') o.src = ''; return o; } };
  globalThis.navigator = {};
  const espera = async (n) => { for (let k = 0; k < n; k++) await new Promise((r) => setImmediate(r)); };
  PIDE.length = 0; MARCOS.length = 0; ATRAS.length = 0;
  RESP = { p1: { ok: true, u: 'https://powvideo.net/embed-abc.html' } };
  CTX.set({ k: 'serie:456:8x2', el: 'cbd-l', t: 'Los Simpson 8x02', items: capItems });
  CTX.cbPlay(0);
  await espera(10);
  let fr = MARCOS[0];
  comprueba('pide el servidor a Cinubo, comprueba que el móvil llega y abre su página en un marco',
    /\/cbembed\?r=p1/.test(PIDE[0]) && PIDE[1] === 'https://powvideo.net/embed-abc.html' && fr && fr.src === 'https://powvideo.net/embed-abc.html'
    && $('cpv').classList.contains('on'), { PIDE, fr });
  comprueba('marco CERRADO: sin ventanas ni salir de la web (sin anuncios)',
    fr && fr.attrs.sandbox === 'allow-scripts allow-same-origin allow-forms' && !/popups|top-navigation/.test(fr.attrs.sandbox || ''), fr && fr.attrs);
  comprueba('dice qué hacer: tocar el vídeo y marcar el captcha', /No soy un robot/.test($('cpv-h').innerHTML));
  fr.onload();
  comprueba('cargada la página: fuera el "Conectando"', $('cpv').classList.contains('listo'));
  PIDE.length = 0; fr.onload(); await espera(2);
  comprueba('resuelto (2ª carga): cuenta en /uso y dice cómo darle a play',
    PIDE.some((u) => u === '/vp/latido') && /play/.test($('cpv-h').innerHTML), { PIDE });
  fr.onload();
  comprueba('"atrás" dentro del marco (3ª carga): se cierra el panel', ATRAS[ATRAS.length - 1] === 'cpv', ATRAS);
  // bloqueado por la compañía -> el siguiente con captcha; todos -> lo explica
  PIDE.length = 0; MARCOS.length = 0;
  RESP = { p1: { ok: true, u: 'https://powvideo.net/embed-abc.html' }, s1: { ok: true, u: 'https://streamplay.to/embed-xyz.html' } };
  globalThis.BLOQ = ['powvideo.net'];
  CTX.cbPlay(0);
  await espera(16);
  comprueba('Powvideo bloqueado: lo dice y prueba Streamplay solo',
    MARCOS.length === 1 && MARCOS[0].src === 'https://streamplay.to/embed-xyz.html' && PIDE.some((u) => /r=s1/.test(u)), { PIDE, MARCOS: MARCOS.map((m) => m.src) });
  PIDE.length = 0; MARCOS.length = 0;
  globalThis.BLOQ = ['powvideo.net', 'streamplay.to'];
  CTX.cbPlay(0);
  await espera(16);
  comprueba('todos bloqueados: ningún marco y se explica (datos del móvil / DNS privado)',
    MARCOS.length === 0 && $('cpv').classList.contains('err') && /datos del móvil/.test($('cpv-msg').innerHTML), $('cpv-msg').innerHTML);
  globalThis.BLOQ = [];

  console.log('\n=== 3c) Pantalla completa: Powvideo con la forma de la pantalla (dtbl152) ===');
  globalThis.screen = { width: 412, height: 915 };
  PIDE.length = 0; MARCOS.length = 0;
  RESP = { p1: { ok: true, u: 'https://powvideo.net/embed-lfv5faats8vq.html' }, s1: { ok: true, u: 'https://streamplay.to/embed-560jlt11f3tn.html' } };
  CTX.cbPlay(0);
  await espera(10);
  fr = MARCOS[0];
  comprueba('Powvideo se pide ya en horizontal con el tamaño de la pantalla (915x412)',
    fr && fr.src === 'https://powvideo.net/embed-lfv5faats8vq-915x412.html' && PIDE.indexOf('https://powvideo.net/embed-lfv5faats8vq-915x412.html') >= 0, { src: fr && fr.src, PIDE });
  fr.onload();
  comprueba('durante el captcha el marco va a su tamaño natural', !fr.style.transform && !fr.style.width, fr.style);
  fr.onload();
  comprueba('con el vídeo: escalado para que encaje entero (390 de ancho -> x0,4262) y el botón de pantalla completa a la vista',
    fr.style.width === '915px' && fr.style.height === '412px' && fr.style.transform === 'scale(0.4262)' && $('cpv').classList.contains('vid'), fr.style);
  PIDE.length = 0; MARCOS.length = 0;
  globalThis.BLOQ = ['powvideo.net'];
  CTX.cbPlay(0);
  await espera(16);
  comprueba('Streamplay (no admite el tamaño en la dirección): tal cual, y se avisa de su botón',
    MARCOS.length === 1 && MARCOS[0].src === 'https://streamplay.to/embed-560jlt11f3tn.html', MARCOS.map((m) => m.src));
  MARCOS[0].onload(); MARCOS[0].onload();
  comprueba('...a su tamaño natural y con el aviso', !MARCOS[0].style.transform && /la del reproductor/.test($('cpv-h').innerHTML), $('cpv-h').innerHTML);
  globalThis.BLOQ = []; globalThis.localStorage = undefined;

  console.log('\n---- VEREDICTO ----');
  if (fallos) { console.log(fallos + ' comprobaciones MAL'); process.exit(1); }
  console.log('TODO OK: el cuadro se ve, la lista es corta y clara, y elegir un servidor llega a la tele');
})();
