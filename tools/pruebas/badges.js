// Los badges (RAR, calidad, semillas) tienen que acabar en SU tarjeta.
//
// Se piden por una pelicula y llegan segundos despues; para entonces esa
// posicion de la cuadricula puede tener OTRA, porque la busqueda va fundiendo
// versiones segun contestan las fuentes. Asi es como aparecio un "RAR" de
// DivxTotal pegado a la X-Men de DonTorrent, que no lo es.
//
//   node tools/pruebas/badges.js
const fs = require('fs');
const path = require('path');

const APP = process.argv[2] || path.join(__dirname, '..', '..', 'render_relay', 'app.py');
const src = fs.readFileSync(APP, 'utf8');

function saca(desde, hasta) {
  const i = src.indexOf(desde);
  if (i < 0) {
    console.log('Ese app.py no tiene el bloque de los badges por identidad');
    console.log('(" ' + desde + ' "). Si es una version anterior a dtbl32, es');
    console.log('normal: ahi los badges se pintaban por POSICION, que es justo');
    console.log('el fallo que esto vigila.');
    process.exit(2);
  }
  const j = src.indexOf(hasta, i + desde.length);
  return src.slice(i, j < 0 ? undefined : j);
}

// --- DOM de mentira: tarjetas con data-i y un hueco para los badges ---------
function creaTarjeta(i) {
  const badges = [];
  const tl = {
    querySelector: (sel) => badges.find(b => sel.indexOf(b.className.split(' ')[0]) >= 0) || null,
    appendChild: (b) => badges.push(b),
    insertBefore: (b) => badges.push(b),
    firstChild: null,
  };
  return { _i: i, badges, querySelector: (sel) => (sel === '.tl' ? tl : null) };
}
const tarjetas = [];
const grid = {
  querySelector: (sel) => {
    const m = /data-i="(\d+)"/.exec(sel);
    return m ? (tarjetas[+m[1]] || null) : null;
  },
};
const el = { querySelector: (sel) => (sel === '.grid' ? grid : null) };
const LISTS = { buscar: [] };
const document = { createElement: () => ({ className: '', textContent: '' }) };

// Solo el bloque de los badges: de itemKey hasta justo antes del corazon.
eval(saca('// QUIEN es un item', 'function heartSVG'));

let fallos = 0;
const comprueba = (n, c, d) => {
  if (c) console.log('  ok   ' + n);
  else { console.log('  MAL  ' + n + (d ? '  -> ' + d : '')); fallos++; }
};

const peli = (src_, id, t) => ({ source: src_, content_id: id, title: t, kind: 'movie' });

console.log('\n=== El caso real: se pide el RAR de una de DivxTotal y, mientras,');
console.log('    la tarjeta pasa a ser la de DonTorrent (gana por calidad) ===');
LISTS.buscar = [peli('dx', 'dx1', 'X-Men'), peli('dt', 'dt9', 'Otra')];
tarjetas.length = 0; tarjetas.push(creaTarjeta(0), creaTarjeta(1));
const job = { el, list: 'buscar', i: 0, ik: itemKey(LISTS.buscar[0]) };
// ...la fusion sustituye la posicion 0 por la version de DonTorrent
LISTS.buscar[0] = peli('dt', 'dt1', 'X-Men');
rarBadge(job);
comprueba('el RAR NO se pega a la pelicula que no lo es',
  tarjetas[0].badges.length === 0,
  'le pusieron ' + JSON.stringify(tarjetas[0].badges.map(b => b.textContent)));

console.log('\n=== Si el item sigue ahi, el badge SI se pinta ===');
LISTS.buscar = [peli('dx', 'dx1', 'X-Men')];
tarjetas.length = 0; tarjetas.push(creaTarjeta(0));
rarBadge({ el, list: 'buscar', i: 0, ik: itemKey(LISTS.buscar[0]) });
comprueba('el RAR llega a su tarjeta', tarjetas[0].badges.length === 1);

console.log('\n=== Si el item se MOVIO de sitio, el badge lo sigue ===');
LISTS.buscar = [peli('dt', 'otra', 'Otra'), peli('dx', 'dx1', 'X-Men')];
tarjetas.length = 0; tarjetas.push(creaTarjeta(0), creaTarjeta(1));
rarBadge({ el, list: 'buscar', i: 0, ik: itemKey(peli('dx', 'dx1', 'X-Men')) });
comprueba('va a la tarjeta 1, no a la 0',
  tarjetas[1].badges.length === 1 && tarjetas[0].badges.length === 0);

console.log('\n=== Y lo mismo con la calidad ===');
LISTS.buscar = [peli('dx', 'dx1', 'X-Men')];
tarjetas.length = 0; tarjetas.push(creaTarjeta(0));
LISTS.buscar[0] = peli('dt', 'dt1', 'X-Men');
qualBadge({ el, list: 'buscar', i: 0, ik: itemKey(peli('dx', 'dx1', 'X-Men')) }, '4K');
comprueba('una calidad ajena no se estampa', tarjetas[0].badges.length === 0);

console.log('\n---- VEREDICTO ----');
if (fallos) { console.log(fallos + ' MAL'); process.exit(1); }
console.log('TODO OK: cada badge acaba en su tarjeta');
