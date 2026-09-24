# Pruebas rápidas del motor

Tres comprobaciones que corren **en local y sin tocar la red**. Cada una tardó
horas de diagnóstico en su día; pasarlas antes de desplegar cuesta un minuto.

```bash
cd "C:/Users/israe/Desktop/Projects/Nueva App Kodi"
rm -rf render_relay/__pycache__
PYTHONDONTWRITEBYTECODE=1 python -u tools/pruebas/busqueda.py
PYTHONDONTWRITEBYTECODE=1 python -u tools/pruebas/temporadas.py
PYTHONDONTWRITEBYTECODE=1 python -u tools/pruebas/hilos_enrich.py
```

(El `rm -rf __pycache__` y el `PYTHONDONTWRITEBYTECODE=1` no son adorno: cambiar
una cadena por otra del mismo tamaño puede dejar el `.pyc` viejo por bueno y
acabas probando el código de antes.)

**`fusion.js`** (Node) — el corazón del motor: qué tarjeta se queda con el sitio
cuando la misma película viene de varias fuentes, qué se guarda en "También en"
y qué pasa con los capítulos. Ejecuta el **código real** de la web, extraído de
`_CAT_PAGE`. Admite una ruta para comparar con otra versión:
`node tools/pruebas/fusion.js /ruta/a/otro/app.py`.

**`caratulas.py`** — que no se tire una carátula que ya teníamos al deduplicar.

**`anio_titulo.py`** (22-09-2026) — el año y el título original entre paréntesis
en el relay. WolfMax titula `"Poli malo (Bad Man) (2025)"` y DonTorrent
`"Poli malo"`: salían dos tarjetas de la misma película (4-5 parejas por pestaña
del Inicio). Vigila también lo que **no** se debe juntar: `"Suspiria (1977)"`
contra la de 2018, y `"Dune (Parte Dos)"` sin año contra `"Dune"`. La mitad de
la web está en `fusion.js` (casos 12-15).

**`semillas.py`** (22-09-2026) — las semillas en la cuadrícula: el conteo UDP en
lote contra un tracker de mentira en `127.0.0.1`, `/seedsknown` enseñando lo
último que se sabe y el RAR de DonTorrent, el aprendiz (qué elige, que el PoW de
DonTorrent va espaciado y solo a cajas que **no** están reproduciendo) y la
copia en la nube (valida lo que baja, nunca pisa lo que ya sabe y un relay de
pruebas **nunca** sube). Escribe en `C:\tmp` y lo deja como estaba.

**`variantes.py`** (22-09-2026) — cómo se reescribe la búsqueda para el
buscador literal de DonTorrent, incluido `"xmen"` → `"x-men"` preguntando a
TMDB (simulado, sin red).

**`pagina.py`** (23-09-2026) — que **todo el JavaScript de las páginas
compile** (`node --check` sobre cada `<script>` de `_CAT_PAGE` y `_KB_PAGE`,
tal cual las sirve el relay) y que `/`, `/cat` y `/kb` se sirvan. La web entera
es un solo script: un paréntesis de más y se queda en blanco para todos, con
el relay contestando 200. **Pasarla SIEMPRE que se toque `_CAT_PAGE`.**

**`caida.py`** + **`caida.js`** (23-09-2026) — DonTorrent caído de verdad (su
503 "La web volverá enseguida"). La de Python: que solo cuenta como caída lo
que DonTorrent dice sin dudas, el estado compartido y su caducidad, que
`/catdetail` contesta al momento (o corta la espera en cuanto se entera: 0,8 s
en vez de 24), que `/kb/send` reproduce por magnet si ya sabemos la huella y si
no lo dice sin mandar nada a la tele, y que búsqueda, Inicio y aprendiz no
mandan trabajo inútil a las cajas. La de Node: lo que ve la persona (el
cuadro, el aviso de la ficha y del Inicio, el chip "caído" de la búsqueda y
qué se busca en "otras fuentes"), con el código real de la web.

**`dominio_dt.py`** (23-09-2026, addon 2.9.74) — el `resolve_domain` de la
caja con DonTorrent caído: su página de mantenimiento corta la búsqueda al
primer dominio (antes probaba los 14, más de un minuto por intento), el fallo
se recuerda 3 min también en disco, un acierto rápido ya no espera al sondeo
más lento, y la tele dice "DonTorrent está caído" al fallar un play. Con Kodi
de mentira y perfil temporal; ni Telegram, ni Supabase, ni DoH de verdad.

**`memoria.py`** (23-09-2026) — el vigilante de memoria del relay: una poda
que no suelta nada no se repite cada 8 s (el 23-09 hubo 626 en 1,6 h, 0 MB,
vaciando cada vez las cachés buenas), el relevo no depende de esa pausa, y
`/catmem` enseña el montón de glibc (`malloc`) y, con `?tipos=1`, los objetos
vivos. Lo de glibc solo existe en Linux: en el PC se comprueba que no rompe
nada y en Linux lo informa el workflow `relay-check`. Desde dtbl45, el
**censo** (`/catmem?censo=1`): cuánto pesa lo vivo por tipo y por la global de
la que cuelga, contando lo que el gc no sigue (una caché de textos en tuplas
no se veía contando objetos). Se prueba con una caché así de 4 MB: tiene que
salir con su nombre, y nunca claves ni contenidos (hay códigos de caja).
tracemalloc se descartó midiendo: frena de 17 a 30 veces el trabajo típico.
Sección 8 (dtbl46): **`/catmem?quien=Tipo`**, la cadena de quién retiene a
los objetos vivos de un tipo (los más viejos), hacia arriba hasta una global,
un hilo en marcha (su función, su variable y su hilo), una clase o nadie. Se
prueba con las cuatro formas de retener: una global, un hilo, una excepción
guardada (hay que atravesar el marco muerto y su traceback hasta quien la
guarda) y un ciclo suelto. Nació el 24-09: 95 SSLContext vivos con una sola
Session, +76 MB en C que el censo de objetos no podía ver.

**`versiones.py`** (23-09-2026) — las cajas muy desactualizadas: cómo se
comparan versiones (se toleran 5 de retraso, porque al publicar las cajas
tardan un día en actualizarse), que los trabajos PRESTADOS solo van a cajas al
día (una en 2.9.54 no entendía varias operaciones y uno de cada ocho trabajos
caducaba esperándola) y que `/kb/status` le dice a su dueño que la actualice.

**`dt_play.py`** (23-09-2026, addon 2.9.75) — reproducir de DonTorrent cuando
no se puede bajar el `.torrent`. En el Comedor, con DonTorrent caído, la caja
usaba el enlace guardado, enseñaba "Descargando torrent…" y le pasaba a
Elementum la URL de DonTorrent, que tampoco podía: la tele se quedaba SIN
HACER NADA (reproducido igual en el Kodi del PC). Ahora: magnet si el relay
sabe la huella (`/dtmagnet`), y si no, un aviso — nunca silencio.

**`fuentes.py`** (24-09-2026, dtbl40) — WolfMax y EliteTorrent caídos (522) y
la lista COMPLETA de capítulos. El 24-09 seguían caídas tres fuentes y la web
decía "las demás funcionan", enseñaba Ted Lasso con los 15 capítulos del índice
como si fueran todos (son ~35) y reproducir de WolfMax acababa en "¿box
encendido?" a los 18 s. Vigila el detector (solo un 52x decide), qué caídas y
qué vivas se anuncian, la caché de listas completas (se guarda, se SUMA, no
encoge, olvida a los 30 días) y los tres envoltorios: `/catboxeps`,
`/catetbox` y `/catetboxresolve`. La parte de la web (aviso con varias
fuentes, la ficha "de la última vez" o "a medias") está en `caida.js`.
Sección 8 (dtbl45): con la fuente caída y confirmada por una caja hace menos
de 5 min, la búsqueda contesta al momento sin encolar nada (antes: un hilo del
relay 24 s esperando a una caja que volvía con el mismo 522); pasado ese rato,
una búsqueda sí va a la caja, que es la única que puede ver que ha vuelto.

**`salud_cajas.py`** (24-09-2026, addon 2.9.76) — el relay no puede ver si
WolfMax o EliteTorrent están caídos: desde la zona de Render sus webs le ponen
un reto ("Just a moment", 403) antes de intentar nada, incluso por nuestro
proxy (medido en producción). Las cajas, en España, sí ven el 522. Vigila que
`http_session` apunta el código de cada web (también cuando falla) y que la
caja cuenta `caidas`/`vivas` con cada trabajo; la parte del relay (lo aplica en
`/catjob/done`) está en `fuentes.py`, sección 7.

Para pasarlas todas de una vez:

```bash
cd "C:/Users/israe/Desktop/Projects/Nueva App Kodi" && rm -rf render_relay/__pycache__
for t in tools/pruebas/*.py; do PYTHONDONTWRITEBYTECODE=1 python -u "$t" >/dev/null 2>&1 && echo "ok  $t" || echo "MAL $t"; done
for t in tools/pruebas/*.js; do node "$t" >/dev/null 2>&1 && echo "ok  $t" || echo "MAL $t"; done
```

## Qué vigila cada una

**`busqueda.py`** — el filtro de relevancia (`_q_relevant`). 35 casos reales,
entre ellos los que se rompieron de verdad: `"x men"` no puede traer
`"The Gentlemen"`, `"amor"` no puede traer `"El amortiguador"`, pero `"xmen"` sí
tiene que encontrar `"X-Men"` y `"interes"` seguir encontrando `"Interestelar"`.
Si se toca el filtro, esto dice al instante qué se ha roto.

**`temporadas.py`** — la agrupación de series (`_agrupa_temporadas`), con los 14
títulos reales del Inicio. Comprueba que las temporadas se juntan en una
tarjeta, que el título queda limpio (para poder fundirse con la misma serie de
otra fuente), y sobre todo lo que **no** debe tocar: `"Temporada de caza"`,
`"Alerta 24 horas"`, las películas, y dos series distintas que empiezan igual.
También que es idempotente y que no muta lo que hay en la caché.

**`hilos_enrich.py`** — la fuga que tiraba el relay. Simula TMDB colgado (una
sesión que gotea para siempre) y exige tres cosas: que el enriquecimiento
**vuelva dentro de su presupuesto**, que los hilos queden **acotados** (el pool
es único por proceso) y que **ningún item se quede sin carátula** teniendo la de
la fuente. Referencia medida el 20-09-2026, cinco enriquecimientos seguidos:
antes 15,0 s y 56 hilos · después 3,0 s y 12 hilos.

## Lo que estas pruebas NO cubren

Todo lo que necesita red o una caja de verdad: el buscador de DonTorrent y sus
guiones, las semillas, los capítulos por caja. Para eso está el harness de
stubs (`Memory/kodi_stub.py`), que corre el addon real desde la IP de casa —
pero con cuidado: encadenar búsquedas contra DonTorrent lo pone de mal humor y
acabas midiendo tu propia saturación.
