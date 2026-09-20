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
