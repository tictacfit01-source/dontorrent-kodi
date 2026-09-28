// Mis listas: un titulo guardado se reconoce aunque la tarjeta cambie de
// fuente principal (dtbl54), con el codigo REAL de _CAT_PAGE.
//
// La tarjeta de una busqueda cambia de principal cuando llega una version
// mejor (DonTorrent 720p -> WolfMax 4K), y en la ficha al tocar otro chip.
// El guardado se reconocia solo por `tipo:content_id` de la principal: Ted
// Lasso guardada desde DonTorrent salia como NO guardada en cuanto WolfMax
// ganaba la tarjeta, y se podia guardar dos veces.
//
//   node tools/pruebas/listas.js
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

let fallos = 0;
function comprueba(nombre, ok, detalle) {
  console.log((ok ? '  ok   ' : '  MAL  ') + nombre + (ok ? '' : '  -> ' + JSON.stringify(detalle)));
  if (!ok) fallos++;
}

// --- lo que la pagina tiene alrededor --------------------------------------
const almacen = {};
const localStorage = {
  getItem: (k) => (k in almacen ? almacen[k] : null),
  setItem: (k, v) => { almacen[k] = String(v); },
};
var favs = [];
const BORRADOS = {};
function saveFavs() {}
function syncSubirPronto() {}
function mlPushSoon() {}
function delMarca(t, k) { BORRADOS[k] = 1; }
function delOlvida(t, k) { delete BORRADOS[k]; }

eval(saca('var LST=[];', 'var _mlPushT=null;'));

const dt = { kind: 'serie', source: 'dt', quality: '720p', content_id: 'dt:ted', title: 'Ted Lasso' };
const wf4k = { kind: 'serie', source: 'wf', quality: '4K', content_id: 'https://wolfmax4k.com/serie/zryh84', title: 'Ted Lasso' };
// la tarjeta despues de fundir: gana WolfMax 4K y DonTorrent pasa a "Tambien en"
const tarjeta = Object.assign({}, wf4k, { alts: [Object.assign({}, dt)] });
const otra = { kind: 'serie', source: 'dt', quality: '1080p', content_id: 'dt:silo', title: 'Silo' };

console.log('\n=== 1) Guardada desde DonTorrent, la tarjeta pasa a WolfMax ===');
favAdd(dt, 'def');
comprueba('se guarda', favs.length === 1 && isFav(dt));
comprueba('la tarjeta que ahora gana WolfMax 4K sigue saliendo como guardada', isFav(tarjeta));
comprueba('y en su lista', favEnLista(tarjeta, 'def'));
favAdd(tarjeta, 'def');
comprueba('guardarla otra vez NO la duplica', favs.length === 1, favs.map(fk));
comprueba('otro titulo no se confunde', !isFav(otra));

console.log('\n=== 2) Al reves: guardada con WolfMax 4K, se ve la de DonTorrent sola ===');
favs = [];
favAdd(tarjeta, 'def');
comprueba('la tarjeta de DonTorrent sola (sin "Tambien en") la reconoce', isFav(dt));
const chip = Object.assign({}, dt, { alts: [Object.assign({}, wf4k)] });   // tocar el chip DonTorrent en la ficha
comprueba('y la ficha tras tocar el chip DonTorrent tambien', isFav(chip));

console.log('\n=== 3) Quitar ===');
favQuitarTodo(dt);
comprueba('quitarla desde la version DonTorrent la quita', favs.length === 0 && !isFav(tarjeta));
comprueba('...dejando la marca de borrado de la guardada (para el resto de moviles)',
  BORRADOS[fk(tarjeta)] === 1, BORRADOS);

console.log('\n=== 4) Guardada dos veces (antes de dtbl54) ===');
favs = [favCopia(dt, ['def']), favCopia(tarjeta, ['def'])];
comprueba('parte de dos', favs.length === 2);
LST.push({ id: 'l2', n: 'Pendiente' });
favAdd(tarjeta, 'l2');
comprueba('moverla a otra lista deja UNA', favs.length === 1, favs.map(fk));
comprueba('en la lista nueva', favEnLista(dt, 'l2') && !favEnLista(dt, 'def'));
comprueba('y la copia sobrante queda marcada como borrada', BORRADOS[fk(dt)] === 1, BORRADOS);
favs = [favCopia(dt, ['def']), favCopia(tarjeta, ['def'])];
favQuitar(dt, 'def');
comprueba('quitarla de su lista quita las dos copias', favs.length === 0, favs.map(fk));
favs = [favCopia(dt, ['def']), favCopia(tarjeta, ['def'])];
favQuitarTodo(tarjeta);
comprueba('"Quitar de mis listas" quita las dos copias', favs.length === 0, favs.map(fk));

console.log('\n=== 5) Lo que NO debe casar ===');
favs = [];
favAdd(Object.assign({}, otra, { alts: [{ source: 'et', quality: '720p' }] }), 'def');
const sinId = { kind: 'serie', source: 'dt', content_id: 'dt:andor', title: 'Andor', alts: [{ source: 'et', quality: '720p' }] };
comprueba('dos versiones sin content_id no hacen iguales dos titulos distintos', !isFav(sinId));
comprueba('una peli y una serie con el mismo id no son la misma',
  !isFav({ kind: 'movie', content_id: 'dt:silo' }));
comprueba('el guardado de siempre (exacto) sigue funcionando', isFav(otra) && favBuscar(otra).content_id === 'dt:silo');
// una fusion sin año colo el WolfMax de la Dune de 1984 entre las versiones de la de 2021
const wf84 = { kind: 'movie', source: 'wf', quality: '1080p', content_id: 'https://wolfmax4k.com/pelicula/d84', title: 'Dune', year: '1984' };
const dune21 = { kind: 'movie', source: 'dt', quality: '4K', content_id: 'dt:dune', title: 'Dune', year: '2021', alts: [Object.assign({}, wf84, { year: '' })] };
favs = [];
favAdd(dune21, 'def');
favAdd(wf84, 'l2');
comprueba('con año distinto son dos titulos: guardar la de 1984 no se lleva la de 2021',
  favs.length === 2 && favEnLista(dune21, 'def') && favEnLista(wf84, 'l2'), favs.map(fk));
favQuitarTodo(wf84);
comprueba('...ni quitarla', favs.length === 1 && isFav(dune21), favs.map(fk));

console.log('\n=== 6) Lo que no cambia: TMDB y titulo+año ===');
// la tarjeta de una serie de WolfMax lleva el enlace de su ULTIMO capitulo
favs = [];
favAdd({ kind: 'serie', source: 'wf', quality: '4K', title: 'Ted Lasso', year: '2020',
  content_id: 'https://wolfmax4k.com/serie/episodio/aaa111' }, 'def');
const semanaQueViene = { kind: 'serie', source: 'wf', quality: '4K', title: 'Ted Lasso', year: '2020',
  content_id: 'https://wolfmax4k.com/serie/episodio/bbb222' };
comprueba('una serie de WolfMax sigue guardada cuando sale un capitulo nuevo', isFav(semanaQueViene));
comprueba('con tildes y signos distintos tambien',
  isFav({ kind: 'serie', source: 'dt', title: 'TED LASSO!', year: 2020, content_id: 'dt:x' }));
comprueba('otra serie con el mismo nombre y otro año, no',
  !isFav({ kind: 'serie', source: 'dt', title: 'Ted Lasso', year: '2031', content_id: 'dt:y' }));
comprueba('sin año no se casa por el nombre',
  !isFav({ kind: 'serie', source: 'dt', title: 'Ted Lasso', content_id: 'dt:z' }));
comprueba('una peli con el mismo nombre y año que la serie, no',
  !isFav({ kind: 'movie', source: 'dt', title: 'Ted Lasso', year: '2020', content_id: 'dt:w' }));
favs = [];
favAdd({ kind: 'movie', source: 'dt', quality: '1080p', title: 'Dune: Parte Dos', year: '2024', tmdb_id: 693134, content_id: 'dt:d2' }, 'def');
comprueba('por su ficha de TMDB aunque cada fuente la titule a su manera',
  isFav({ kind: 'movie', source: 'wf', quality: '4K', title: 'Dune Parte 2 (Dune: Part Two)', year: '2024', tmdb_id: 693134, content_id: 'https://wolfmax4k.com/pelicula/q9' }));

console.log('\n---- VEREDICTO ----');
if (fallos) { console.log(fallos + ' comprobaciones MAL'); process.exit(1); }
console.log('TODO OK: un guardado se reconoce por cualquiera de sus versiones');
