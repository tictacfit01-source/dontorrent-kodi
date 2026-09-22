// "Cambiar codigo" en Mis Kodis: el flujo del MOVIL, con el codigo REAL de la
// web (extraido de _CAT_PAGE) y red de mentira.
//
// La regla que vigila: el movil solo da el cambio por hecho cuando la tele
// CONFIRMA con un latido usando el codigo nuevo. Tele apagada, tele con addon
// viejo (no entiende la orden) o relay que rechaza: no se toca NADA aqui.
//
//   node tools/pruebas/codigo.js
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
const codigo = saca('function nuevoCodigo(', '\nfunction pickDev(');

// --- el entorno de la web, de mentira ----------------------------------------
let DEVS, DEL, activo, avisos, red, reloj;
function reinicia(devs, act) {
  DEVS = JSON.parse(JSON.stringify(devs)); DEL = {}; activo = act; avisos = [];
  red = { enviados: [], estado: {}, envio: { ok: true } }; reloj = 0;
}
const loadDevs = () => JSON.parse(JSON.stringify(DEVS));
const saveDevs = (d) => { DEVS = JSON.parse(JSON.stringify(d)); };
const delMarca = (t, k) => { DEL[k] = 'borrado'; };
const delOlvida = (t, k) => { delete DEL[k]; };
const code = { get value() { return activo; } };
const setActiveCode = (c) => { activo = c; };
const renderDevs = () => {};
const refreshDevBtn = () => {};
const syncOn = () => true;
const toast = (t) => avisos.push(t);
const mwConfirm = (t, x, e, si) => si();          // el usuario dice que si
const Date = { now: () => reloj };                 // reloj que avanza con los setTimeout
const cola = [];
const setTimeout = (fn, ms) => { cola.push([fn, ms]); };
const fetch = (url, opt) => {
  const r = (obj) => Promise.resolve({ json: () => Promise.resolve(obj) });
  if (url.startsWith('/kb/status?code=')) {
    const c = url.split('=')[1];
    const e = red.estado[c];
    return r({ connected: typeof e === 'function' ? e() : !!e });
  }
  if (url === '/kb/send') { red.enviados.push(JSON.parse(opt.body)); return r(red.envio); }
  return Promise.reject(new Error('url inesperada ' + url));
};
const crypto = require('crypto').webcrypto;

eval(codigo);

async function corre(maxPasos) {        // deja correr promesas y temporizadores
  for (let i = 0; i < (maxPasos || 200); i++) {
    await new Promise((res) => global.setTimeout(res, 0));
    if (!cola.length) continue;
    const [fn, ms] = cola.shift(); reloj += ms; fn();
  }
}

let fallos = 0;
const comprueba = (n, c, d) => {
  if (c) console.log('  ok   ' + n);
  else { console.log('  MAL  ' + n + (d !== undefined ? '  -> ' + JSON.stringify(d) : '')); fallos++; }
};

(async () => {
  console.log('\n=== 1) El codigo nuevo ===');
  const vistos = new Set();
  let bien = true;
  for (let i = 0; i < 2000; i++) { const c = nuevoCodigo(); if (!/^\d{6}$/.test(c)) bien = false; vistos.add(c); }
  comprueba('siempre 6 cifras (con ceros delante si toca)', bien);
  comprueba('y al azar de verdad (2000 tiradas, casi sin repetir)', vistos.size > 1990, vistos.size);

  const salon = [{ name: 'Salón', code: '372473' }, { name: 'PC', code: '601968' }];

  console.log('\n=== 2) Todo bien: la tele confirma ===');
  reinicia(salon, '372473');
  red.estado['372473'] = true;
  let pregunta = 0;
  // el codigo nuevo "aparece" encendido a la tercera consulta (la tele tarda)
  red.estado.__proto__ = null;
  const nuevoDe = () => (red.enviados[0] || {}).nuevo;
  const origEstado = red.estado;
  red.estado = new Proxy(origEstado, { get: (o, k) => (k === nuevoDe() ? (() => (++pregunta >= 3)) : o[k]) });
  keyDev('372473');
  await corre();
  const nuevo = nuevoDe();
  comprueba('se manda la orden a la tele, con SU codigo actual',
    red.enviados.length === 1 && red.enviados[0].code === '372473' && red.enviados[0].cmd === 'codigo_nuevo'
    && /^\d{6}$/.test(nuevo) && nuevo !== '372473', red.enviados);
  comprueba('Salón queda con el codigo nuevo', DEVS[0].code === nuevo && DEVS[0].name === 'Salón', DEVS);
  comprueba('el PC no se toca', DEVS[1].code === '601968');
  comprueba('era la tele activa: la activa pasa a ser la nueva', activo === nuevo, activo);
  comprueba('el viejo queda como BORRADO (la copia de Google no lo resucita)',
    DEL['372473'] === 'borrado' && !DEL[nuevo], DEL);
  comprueba('y se le dice al usuario cual es', avisos.some((a) => a.indexOf(nuevo) >= 0), avisos);

  console.log('\n=== 3) Tele apagada ===');
  reinicia(salon, '601968');
  keyDev('372473');
  await corre();
  comprueba('ni se intenta: nada enviado, nada cambiado',
    red.enviados.length === 0 && DEVS[0].code === '372473' && !DEL['372473'], { e: red.enviados, DEVS });
  comprueba('y se le dice que la encienda', avisos.some((a) => /Enciende/.test(a)), avisos);

  console.log('\n=== 4) Tele con el addon VIEJO (no entiende la orden) ===');
  reinicia(salon, '372473');
  red.estado['372473'] = true;                      // el nuevo no aparece NUNCA
  keyDev('372473');
  await corre(400);
  comprueba('tras esperar lo suyo, NO se cambia nada',
    DEVS[0].code === '372473' && activo === '372473' && !DEL['372473'], { DEVS, activo, DEL });
  comprueba('ha esperado ~45 s preguntando (no se rinde a la primera)', reloj >= 45000, reloj);
  comprueba('y el aviso explica que hacer si en la tele salio el codigo',
    avisos.some((a) => /no ha confirmado/.test(a)), avisos);

  console.log('\n=== 5) El relay lo rechaza (p.ej. ese codigo ya es de otra tele) ===');
  reinicia(salon, '372473');
  red.estado['372473'] = true;
  red.envio = { ok: false, error: 'ese código ya lo usa otra tele' };
  keyDev('372473');
  await corre();
  comprueba('no se cambia nada y se ensena el motivo',
    DEVS[0].code === '372473' && avisos.some((a) => /otra tele/.test(a)), { DEVS, avisos });

  console.log('\n---- VEREDICTO ----');
  if (fallos) { console.log(fallos + ' comprobaciones MAL'); process.exit(1); }
  console.log('TODO OK: el movil solo cambia el codigo cuando la tele lo confirma');
})();
