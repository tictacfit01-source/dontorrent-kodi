# DonTorrent para Kodi

Repo privado con el addon **plugin.video.dontorrent** y el repositorio
**repository.dontorrent** que entrega actualizaciones automaticas a cada
Kodi donde lo instales.

## Instalar en un Kodi (5 minutos, una sola vez)

### Opcion 1 - mas sencillo: descarga el zip y listo

1. En el dispositivo (PC, Fire Stick, TV box...) abre esta URL en el
   navegador (descarga directa, no listado):

   https://github.com/tictacfit01-source/dontorrent-kodi/releases/latest/download/repository.dontorrent-1.0.0.zip

2. Abre Kodi -> Ajustes -> Sistema -> Complementos -> activa
   **Origenes desconocidos**.
3. Complementos -> icono de la caja abierta -> **"Instalar desde un
   archivo zip"** -> navega a Descargas -> selecciona
   `repository.dontorrent-1.0.0.zip`.
4. Espera al toast verde "Complemento instalado: DonTorrent Repo (israe)".
5. Mismo menu -> **"Instalar desde repositorio"** -> "DonTorrent Repo
   (israe)" -> Complementos de video -> **DonTorrent** -> Instalar.

A partir de aqui, cada vez que se publique una nueva version, Kodi la
descargara solo (revision automatica cada 24h por defecto, o forzando
con "Buscar actualizaciones" en Ajustes -> Complementos).

### Opcion 2 - via USB

Si el dispositivo no tiene navegador comodo, copia el zip por USB y
sigue los pasos 2-5 desde ahi.

### Opcion 3 - via fuente HTTP (no recomendado)

Tambien funciona anadiendo como fuente
`https://raw.githubusercontent.com/tictacfit01-source/dontorrent-kodi/main/repo/repository.dontorrent/`
pero Kodi no puede listar carpetas en raw.githubusercontent y el
navegador interno se queda en blanco. La opcion 1 es mas fiable.

---

## Estructura del repo

```
plugin.video.dontorrent/   # codigo del addon (lo que se actualiza)
repository.dontorrent/     # addon-repositorio que apunta a /repo
tools/build_repo.py        # genera repo/ desde los dos addons
.github/workflows/         # CI: build automatico + GitHub Release
repo/                      # generado por CI, lo lee Kodi
```

## Subir una nueva version del addon

1. Edita el codigo en `plugin.video.dontorrent/`.
2. **Sube el numero de version** en `plugin.video.dontorrent/addon.xml`
   (atributo `version=`). Si no lo subes, Kodi no actualizara.
3. `git add . && git commit -m "..." && git push`.

CI se encarga de:
- Reconstruir `repo/` y commitearlo.
- Crear/actualizar la GitHub Release `vX.Y.Z` con los dos zips adjuntos.

Cada Kodi con el repositorio instalado vera el nuevo `addons.xml.md5`
en la siguiente comprobacion (max 24h) y aplicara la actualizacion solo.

## Build local (para probar antes de pushear)

```
python tools/build_repo.py
```

Genera `repo/` igual que el workflow. Puedes apuntar un Kodi local a
`file:///C:/.../repo/addons.xml` para probar.
