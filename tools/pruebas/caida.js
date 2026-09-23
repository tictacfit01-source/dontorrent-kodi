// DonTorrent CAIDO, la parte de la web (dtbl38), con el codigo REAL.
//
// Saca de _CAT_PAGE lo que decide que ve la persona cuando DonTorrent esta
// caido: la respuesta de /kb/send (magnet o aviso), el aviso de la ficha de
// serie, el del Inicio y el chip "caído" de la busqueda. Con un DOM de
// mentira: aqui importa QUE se dice y QUE se hace, no como se pinta.
//
//   node tools/pruebas/caida.js
const fs = require('fs');
const path = require('path');

const APP = process.argv[2] || path.join(__dirname, '..', '..', 'render_relay', 'app.py');
const src = fs.readFileSync(APP, 'utf8');

function saca(desde, hasta) {
  const i = src.indexOf(desde);
  if (i < 0) throw new Error('no encuentro: ' + desde);
  const j = src.indexOf(hasta, i + desde.length);
  if (j < 0) throw new Error('no encuentro el final de: ' + desde);
  return src.slice(i, j);
}

const codigo = [
  saca('function sendPlay(', '\nfunction openSeries('),
  saca('var PROG={', '\nfunction progStop('),
].join('\n');

// --- DOM y entorno de mentira -------------------------------------------------
function Elem() {
  const cls = new Set();
  return {
    innerHTML: '', textContent: '', value: '', style: {},
    classList: {
      add: (c) => cls.add(c), remove: (c) => cls.delete(c),
      contains: (c) => cls.has(c), toggle: (c, on) => { if (on === undefined ? !cls.has(c) : on) cls.add(c); else cls.delete(c); },
    },
  };
}
const DOM = {};
function $(id) { return DOM[id] || (DOM[id] = Elem()); }
const code = { value: '111111' };
const TOASTS = [], DLGS = [], LLAMADAS = [];
function toast(t) { TOASTS.push(t); }
function mwConfirm(titulo, texto, etiqueta, alAceptar) { DLGS.push({ titulo, texto, etiqueta, alAceptar }); }
function avisaCodigo() { LLAMADAS.push('avisaCodigo'); }
function histSnap(ref) { return ref; }
function histPush() { LLAMADAS.push('histPush'); }
function closeSheet() { LLAMADAS.push('closeSheet'); $('sheet').classList.remove('on'); }
function closeOv() { LLAMADAS.push('closeOv'); $('ov').classList.remove('on'); }
function openRemote() { LLAMADAS.push('openRemote'); }
function pollNow() {}
function goView(v) { LLAMADAS.push('goView:' + v); }
function go() { LLAMADAS.push('go:' + $('q').value); }
function esc(s) { return String(s); }
let lastPlayTs = 0;
let _searchSeq = 1;
const LISTS = { buscar: [] };
let RESP = null;
function fetch() { return Promise.resolve({ json: () => Promise.resolve(RESP) }); }
const OVRETRY = { title: 'Fauda' };
const window = { LISTS };

eval(codigo);

let fallos = 0;
function comprueba(nombre, condicion, detalle) {
  if (condicion) { console.log('  ok   ' + nombre); }
  else { console.log('  MAL  ' + nombre + (detalle ? '  -> ' + detalle : '')); fallos++; }
}
const espera = (ms) => new Promise((r) => setTimeout(r, ms));

(async () => {
  console.log('\n=== 1) Que se busca en "otras fuentes" ===');
  const casos = [['Fauda 5x03', 'Fauda'], ['Fauda 1x01 al 1x03', 'Fauda'],
    ['Poli malo (2025)', 'Poli malo'], ['Dune (Parte Dos)', 'Dune (Parte Dos)'],
    ['Ted Lasso', 'Ted Lasso'], ['', ''], [null, '']];
  for (const [t, esperado] of casos) {
    const got = _tituloBase(t);
    comprueba(JSON.stringify(t) + ' -> ' + JSON.stringify(esperado), got === esperado, got);
  }

  console.log('\n=== 2) /kb/send dice "dt_caida" ===');
  RESP = { ok: false, error: 'dt_caida', dt_caida: { desde: 1 } };
  TOASTS.length = 0; DLGS.length = 0; LLAMADAS.length = 0;
  sendPlay({ a: 'dt', c: '1', tb: 'series', t: 'Fauda 5x03' });
  await espera(20);
  comprueba('sale el cuadro de DonTorrent caido (nada de "Error: ...")',
    DLGS.length === 1 && /DonTorrent está caído/.test(DLGS[0].titulo)
    && !TOASTS.some((t) => /^Error/.test(t)), JSON.stringify(TOASTS));
  comprueba('...dice que no es su tele y ofrece buscar en otras fuentes',
    DLGS.length && /no de tu tele/.test(DLGS[0].texto) && DLGS[0].etiqueta === 'Buscar en otras fuentes');
  comprueba('...y la tele NO se da por enviada (ni historial ni mando)',
    !LLAMADAS.includes('histPush') && !LLAMADAS.includes('openRemote'), LLAMADAS.join(','));
  $('sheet').classList.add('on');
  LLAMADAS.length = 0;
  DLGS[0].alAceptar();
  await espera(420);
  comprueba('"Buscar en otras fuentes": cierra la ficha y busca la SERIE, no el capitulo',
    LLAMADAS.includes('closeSheet') && LLAMADAS.includes('goView:buscar')
    && LLAMADAS.includes('go:Fauda'), LLAMADAS.join(','));

  console.log('\n=== 3) /kb/send lo manda por magnet ===');
  RESP = { ok: true, via: 'magnet' };
  TOASTS.length = 0; LLAMADAS.length = 0;
  sendPlay({ a: 'dt', c: '1', tb: 'peliculas', t: 'Una peli' });
  await espera(20);
  comprueba('se dice que va por la red torrent',
    TOASTS.some((t) => /En la tele/.test(t) && /red torrent/.test(t)), JSON.stringify(TOASTS));
  comprueba('...y es un envio normal (historial y mando)',
    LLAMADAS.includes('histPush') && LLAMADAS.includes('openRemote'), LLAMADAS.join(','));
  RESP = { ok: true };
  TOASTS.length = 0;
  sendPlay({ a: 'dt', c: '1', tb: 'peliculas', t: 'Una peli' });
  await espera(20);
  comprueba('sin via: el "▶ En la tele" de siempre', TOASTS.includes('▶ En la tele'), JSON.stringify(TOASTS));
  RESP = { ok: false, error: 'enlace inválido' };
  TOASTS.length = 0; DLGS.length = 0;
  sendPlay({ a: 'pl', u: 'x', t: 'Otra' });
  await espera(20);
  comprueba('otros errores, como siempre', TOASTS.includes('Error: enlace inválido') && !DLGS.length,
    JSON.stringify(TOASTS));

  console.log('\n=== 4) Los avisos ===');
  const h0 = dtCaidaHTML('Fauda', 0), h1 = dtCaidaHTML('Fauda', 2);
  comprueba('ficha de serie: lo que pasa, de quien es y el boton',
    /DonTorrent está caído ahora mismo/.test(h0) && /no de tu tele/.test(h0)
    && /Buscar en otras fuentes/.test(h0) && /Reintentar/.test(h0) && !/aquí arriba/.test(h0));
  comprueba('...y si la serie esta en otra fuente, se señala', /aquí arriba/.test(h1));
  comprueba('...nada de "enciende tu Kodi"', !/enciende tu Kodi/.test(h0 + h1));
  pintaDtAviso({ desde: 1 });
  comprueba('Inicio: el aviso sale', $('dt-aviso').classList.contains('on')
    && /DonTorrent está caído/.test($('dt-aviso').innerHTML));
  pintaDtAviso(undefined);
  comprueba('...y se va cuando vuelve', !$('dt-aviso').classList.contains('on')
    && $('dt-aviso').innerHTML === '');

  console.log('\n=== 5) El chip de la busqueda ===');
  PROG = { t0: Date.now(), st: { dt: 4, et: 1, dx: 2, wf: 0 }, n: { dt: 1, et: 3, dx: 0, wf: 0 }, tick: null, seq: 1 };
  progPaint();
  const hp = $('buscar-prog').innerHTML;
  comprueba('DonTorrent: "caído", con su estilo', /class="srcp-f dt on down"/.test(hp)
    && /DonTorrent <b>caído<\/b>/.test(hp), hp.slice(0, 300));
  comprueba('...y cuenta como terminada (no se queda "buscando")', /Buscando en 1 fuente/.test(hp), hp.slice(0, 200));

  console.log('\n---- VEREDICTO ----');
  if (fallos) { console.log(fallos + ' comprobaciones MAL'); process.exit(1); }
  console.log('TODO OK: con DonTorrent caido, la web dice la verdad y ofrece salida');
})();
