// Fusion de tarjetas: el corazon del motor, probado con el codigo REAL.
//
// Saca las funciones de fusion de dentro de _CAT_PAGE (en render_relay/app.py)
// y las ejecuta con un DOM de mentira. Aqui se decide que tarjeta se queda con
// el sitio cuando la misma pelicula viene de varias fuentes, que se guarda como
// "Tambien en", y que pasa con los capitulos de una serie: si esto se rompe, el
// dueno ve tarjetas repetidas, series vacias o su 4K escondido.
//
//   node tools/pruebas/fusion.js
const fs = require('fs');
const path = require('path');

// Se puede apuntar a otro app.py para comparar con una version anterior:
//   node tools/pruebas/fusion.js /ruta/app.py
const APP = process.argv[2] || path.join(__dirname, '..', '..', 'render_relay', 'app.py');
const src = fs.readFileSync(APP, 'utf8');

// --- extraer las funciones que nos interesan, tal cual estan en el fichero ---
function saca(desde, hasta) {
  const i = src.indexOf(desde);
  if (i < 0) throw new Error('no encuentro: ' + desde);
  const j = src.indexOf(hasta, i + desde.length);
  if (j < 0) throw new Error('no encuentro el final de: ' + desde);
  return src.slice(i, j);
}

const codigo = [
  saca('function mergeResults(', '\nfunction srcScore'),
  saca('var QRANK=', '\nvar SRANK='),
  saca('var SRANK=', '\nfunction srcScore'),
  saca('function srcScore(', '\nfunction mergeEps'),
  saca('function mergeEps(', '\nfunction upgrade'),
  saca('function upgrade(', '\n// Guarda la version que PIERDE'),
  saca('function altKey(', '\nfunction altMejor'),
  saca('function altMejor(', '\n// `activa` es la tarjeta'),
  saca('function addAlt(', '\nfunction repaintCard'),
].join('\n');

// --- DOM de mentira: aqui solo nos importa la LISTA, no lo que se pinta ---
const LISTS = { inicio: [], buscar: [], lista: [] };
const g = { querySelector: () => null };
function appendGrid() {}
function renderGrid() {}
function repaintCard() {}
function pintaFuentes() {}

eval(codigo);

let fallos = 0;
function comprueba(nombre, condicion, detalle) {
  if (condicion) { console.log('  ok   ' + nombre); }
  else { console.log('  MAL  ' + nombre + (detalle ? '  -> ' + detalle : '')); fallos++; }
}

const peli = (src_, q, extra) => Object.assign(
  { title: 'Carrera de bestias', kind: 'movie', source: src_, quality: q,
    content_id: src_ + ':' + q }, extra || {});

console.log('\n=== 1) La misma peli DOS VECES EN EL MISMO LOTE (4K y DVDRip de DonTorrent) ===');
LISTS.inicio = [];
mergeResults('inicio', g, [peli('dt', '4K'), peli('dt', 'DVDRIP')], 1);
comprueba('queda UNA sola tarjeta', LISTS.inicio.length === 1, LISTS.inicio.length + ' tarjetas');
comprueba('se queda la de 4K', LISTS.inicio[0] && LISTS.inicio[0].quality === '4K');
comprueba('la otra version sigue elegible en "Tambien en"',
  !!(LISTS.inicio[0] && LISTS.inicio[0].alts && LISTS.inicio[0].alts.length === 1),
  JSON.stringify((LISTS.inicio[0] || {}).alts));

console.log('\n=== 2) Lo mismo pero en DOS LOTES (como llega de dos fuentes) ===');
LISTS.inicio = [];
mergeResults('inicio', g, [peli('dt', '1080p')], 1);
mergeResults('inicio', g, [peli('wf', '4K')], 1);
comprueba('una sola tarjeta', LISTS.inicio.length === 1);
comprueba('gana el 4K de WolfMax (la calidad manda)',
  LISTS.inicio[0].source === 'wf' && LISTS.inicio[0].quality === '4K',
  LISTS.inicio[0].source + ' ' + LISTS.inicio[0].quality);
comprueba('DonTorrent queda como alternativa',
  (LISTS.inicio[0].alts || []).some(a => a.source === 'dt'));

console.log('\n=== 3) El 4K de WolfMax NO puede quedar escondido ===');
[['dt', '1080p'], ['dt', 'BluRay'], ['dt', 'HDTV'], ['dx', '1080p'], ['et', '720p']].forEach(([s, q]) => {
  LISTS.inicio = [];
  mergeResults('inicio', g, [peli(s, q)], 1);
  mergeResults('inicio', g, [peli('wf', '4K')], 1);
  const gana = LISTS.inicio[0];
  comprueba('4K de WolfMax gana a ' + s + ' ' + q,
    gana.source === 'wf', 'gano ' + gana.source + ' ' + gana.quality);
});

console.log('\n=== 4) La ganadora SIN caratula se queda la de la perdedora ===');
LISTS.inicio = [];
mergeResults('inicio', g, [peli('dt', '1080p', { poster: 'POSTER', rating: 7.7 })], 1);
mergeResults('inicio', g, [peli('wf', '4K', { poster: null })], 1);
comprueba('la tarjeta 4K hereda la caratula', LISTS.inicio[0].poster === 'POSTER',
  String(LISTS.inicio[0].poster));
comprueba('y la nota', LISTS.inicio[0].rating === 7.7);

console.log('\n=== 5) Y al reves: la que LLEGA y pierde le presta su caratula ===');
LISTS.inicio = [];
mergeResults('inicio', g, [peli('wf', '4K', { poster: null })], 1);
mergeResults('inicio', g, [peli('dt', '720p', { poster: 'POSTER2', year: null })], 1);
comprueba('la tarjeta que manda ya no esta en gris', LISTS.inicio[0].poster === 'POSTER2',
  String(LISTS.inicio[0].poster));

console.log('\n=== 6) Remakes: mismo titulo, AÑOS distintos -> dos tarjetas ===');
LISTS.inicio = [];
mergeResults('inicio', g, [
  { title: 'Suspiria', kind: 'movie', source: 'dt', quality: '1080p', year: '1977', content_id: 'a' },
  { title: 'Suspiria', kind: 'movie', source: 'dt', quality: '1080p', year: '2018', content_id: 'b' }], 1);
comprueba('salen las DOS peliculas', LISTS.inicio.length === 2, LISTS.inicio.length + '');

console.log('\n=== 7) Series: los capitulos de dos fuentes se SUMAN ===');
LISTS.inicio = [];
const capsA = [{ label: '1x01', season: 1, episode: 1 }, { label: '1x02', season: 1, episode: 2 }];
const capsB = [{ label: '1x02', season: 1, episode: 2 }, { label: '1x03', season: 1, episode: 3 }];
mergeResults('inicio', g, [{ title: 'Silo', kind: 'serie', source: 'wf', quality: '4K', content_id: 's1', eps: capsA }], 1);
mergeResults('inicio', g, [{ title: 'Silo', kind: 'serie', source: 'dt', quality: '1080p', content_id: 's2', eps: capsB }], 1);
const eps = LISTS.inicio[0].eps || [];
comprueba('una sola tarjeta de serie', LISTS.inicio.length === 1);
comprueba('con los TRES capitulos, sin repetir', eps.length === 3,
  eps.map(e => e.label).join(','));

console.log('\n=== 8) Nada de perder tarjetas por el camino ===');
LISTS.inicio = [];
const lote = [];
for (let i = 0; i < 20; i++) lote.push({ title: 'Peli ' + i, kind: 'movie', source: 'dt', content_id: 'p' + i });
mergeResults('inicio', g, lote, 1);
comprueba('entran las 20', LISTS.inicio.length === 20, LISTS.inicio.length + '');
mergeResults('inicio', g, lote, 1);   // el mismo lote otra vez
comprueba('repetir el lote no duplica', LISTS.inicio.length === 20, LISTS.inicio.length + '');

console.log('\n---- VEREDICTO ----');
if (fallos) { console.log(fallos + ' comprobaciones MAL'); process.exit(1); }
console.log('TODO OK: la fusion no pierde nada y gana la mejor version');
