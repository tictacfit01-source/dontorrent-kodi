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

console.log('\n=== 9) La misma peli escrita de tres formas (el caso de EliteTorrent) ===');
LISTS.inicio = [];
mergeResults('inicio', g, [
  { title: 'X-Men - Dias del futuro pasado', kind: 'movie', source: 'et', content_id: 'e1' },
  { title: 'X-Men: Días del futuro pasado', kind: 'movie', source: 'et', content_id: 'e2' },
  { title: 'X Men dias del futuro pasado', kind: 'movie', source: 'dt', content_id: 'd1' }], 1);
comprueba('las tres se funden en UNA', LISTS.inicio.length === 1,
  LISTS.inicio.length + ' tarjetas: ' + LISTS.inicio.map(x => x.title).join(' | '));

console.log('\n=== 10) Pero el orden de las palabras SI distingue ===');
LISTS.inicio = [];
mergeResults('inicio', g, [
  { title: 'X-Men Dias del futuro pasado', kind: 'movie', source: 'et', content_id: 'f1' },
  { title: 'X-Men Dias del pasado futuro', kind: 'movie', source: 'et', content_id: 'f2' }], 1);
comprueba('son dos titulos distintos, no se inventan fusiones',
  LISTS.inicio.length === 2, LISTS.inicio.length + '');

console.log('\n=== 11) Y los remakes siguen separados por el año ===');
LISTS.inicio = [];
mergeResults('inicio', g, [
  { title: 'Suspiria', kind: 'movie', source: 'dt', year: '1977', content_id: 'g1' },
  { title: 'Suspiria!', kind: 'movie', source: 'dt', year: '2018', content_id: 'g2' }], 1);
comprueba('dos peliculas, dos tarjetas', LISTS.inicio.length === 2,
  LISTS.inicio.length + '');

// --- 22-09-2026: el año y el titulo original ENTRE PARENTESIS ---------------
// WolfMax (y a veces DivxTotal) titulan "Poli malo (Bad Man) (2025)" y
// DonTorrent "Poli malo". Con el mismo año y el mismo tmdb_id salian DOS
// tarjetas de la misma pelicula: medido en el Inicio real, 4-5 parejas en
// Estrenos y otras tantas en Cine.
console.log('\n=== 12) El año pegado al titulo (WolfMax) no separa la misma peli ===');
LISTS.inicio = [];
mergeResults('inicio', g, [
  { title: 'Gail Daughtry y el vale por un rollo VIP (2026)', kind: 'movie', source: 'wf', quality: 'Bluray', year: '2026', content_id: 'https://wolfmax4k.com/movie/270683' },
  { title: 'Gail Daughtry y el vale por un rollo VIP', kind: 'movie', source: 'dt', quality: 'BluRay', year: '2026', content_id: '31016' }], 1);
comprueba('una sola tarjeta', LISTS.inicio.length === 1,
  LISTS.inicio.length + ' tarjetas: ' + LISTS.inicio.map(x => x.title + ' [' + x.source + ']').join(' | '));
comprueba('y la otra version sigue en "Tambien en"',
  !!(LISTS.inicio[0] && (LISTS.inicio[0].alts || []).length === 1));

console.log('\n=== 13) El titulo ORIGINAL entre parentesis tampoco ===');
LISTS.inicio = [];
mergeResults('inicio', g, [
  { title: 'Poli malo', kind: 'movie', source: 'dt', quality: 'DVDRIP', year: '2025', content_id: '31014' }], 1);
mergeResults('inicio', g, [
  { title: 'Poli malo (Bad Man) (2025)', kind: 'movie', source: 'wf', quality: 'Bluray', year: '2025', content_id: 'https://wolfmax4k.com/movie/1' },
  { title: 'Cuatro historias de deseo 3 (Lust Stories 3) (2026)', kind: 'movie', source: 'wf', quality: 'Bluray', year: '2026', content_id: 'https://wolfmax4k.com/movie/2' }], 1);
mergeResults('inicio', g, [
  { title: 'Cuatro historias de deseo 3', kind: 'movie', source: 'dt', quality: 'DVDRIP', year: '2026', content_id: '31020' }], 1);
comprueba('dos peliculas, dos tarjetas (no cuatro)', LISTS.inicio.length === 2,
  LISTS.inicio.length + ' tarjetas: ' + LISTS.inicio.map(x => x.title + ' [' + x.source + ']').join(' | '));
comprueba('gana el BluRay de WolfMax al DVDRip',
  LISTS.inicio.every(x => x.source === 'wf'), LISTS.inicio.map(x => x.source).join(','));

console.log('\n=== 14) La misma de WolfMax con y sin el año en el titulo ===');
LISTS.inicio = [];
mergeResults('inicio', g, [
  { title: 'Adolescencia Sexo y Muerte En Campamento Miasma', kind: 'movie', source: 'wf', quality: 'Bluray', year: '2026', content_id: 'https://wolfmax4k.com/movie/3' },
  { title: 'Adolescencia Sexo y Muerte En Campamento Miasma (2026)', kind: 'movie', source: 'wf', quality: 'Bluray', year: '2026', content_id: 'https://wolfmax4k.com/movie/4' }], 1);
comprueba('una sola tarjeta', LISTS.inicio.length === 1, LISTS.inicio.length + '');

console.log('\n=== 15) Lo que NO se puede juntar ===');
LISTS.inicio = [];
mergeResults('inicio', g, [
  { title: 'Suspiria', kind: 'movie', source: 'dt', year: '2018', content_id: 'h1' },
  { title: 'Suspiria (1977)', kind: 'movie', source: 'wf', content_id: 'h2' }], 1);
comprueba('el año del titulo separa los remakes (1977 y 2018)', LISTS.inicio.length === 2,
  LISTS.inicio.length + '');
LISTS.inicio = [];
mergeResults('inicio', g, [
  { title: 'Dune', kind: 'movie', source: 'dt', year: '2021', content_id: 'i1' },
  { title: 'Dune (Parte Dos)', kind: 'movie', source: 'et', content_id: 'i2' }], 1);
comprueba('un parentesis SIN año conocido no se quita (Dune y Dune Parte Dos)',
  LISTS.inicio.length === 2, LISTS.inicio.length + '');
LISTS.inicio = [];
mergeResults('inicio', g, [
  { title: '1917 (2019)', kind: 'movie', source: 'wf', content_id: 'j1' },
  { title: '1917', kind: 'movie', source: 'dt', year: '2019', content_id: 'j2' }], 1);
comprueba('un titulo que ES un numero ("1917") sigue funcionando', LISTS.inicio.length === 1,
  LISTS.inicio.length + '');

console.log('\n---- VEREDICTO ----');
if (fallos) { console.log(fallos + ' comprobaciones MAL'); process.exit(1); }
console.log('TODO OK: la fusion no pierde nada y gana la mejor version');
