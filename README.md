# DonTorrent para Kodi

Repositorio privado con el addon **plugin.video.dontorrent** y el repo
**repository.dontorrent** que entrega actualizaciones automaticas a cada
Kodi donde lo instales.

## Estructura

```
plugin.video.dontorrent/   # codigo del addon (este es el que se actualiza)
repository.dontorrent/     # addon-repositorio que apunta a /repo
tools/build_repo.py        # genera repo/ a partir de los dos addons
.github/workflows/         # CI: rebuild automatico en cada push
repo/                      # generado por CI, lo lee Kodi
```

## Puesta en marcha (una sola vez)

1. Crea un repositorio en GitHub, por ejemplo `dontorrent-kodi`.
   Puede ser **privado o publico**: Kodi solo necesita acceso a las URL
   `raw.githubusercontent.com`, que funcionan en repos publicos sin
   autenticacion. Si lo quieres privado tendras que generar un token y
   meterlo en la URL del addon-repositorio.
2. Edita `repository.dontorrent/addon.xml` y reemplaza
   `REPLACE_GITHUB_USER` por tu usuario real de GitHub (3 ocurrencias:
   `info`, `checksum`, `datadir`).
3. Sube todo a GitHub:
   ```
   git init
   git add .
   git commit -m "init"
   git branch -M main
   git remote add origin https://github.com/<tu_usuario>/dontorrent-kodi.git
   git push -u origin main
   ```
4. Espera ~1 minuto: el workflow `Build Kodi repo` corre, genera `repo/`
   y hace commit con `addons.xml`, `addons.xml.md5` y los `.zip`.
5. (Opcional) Para forzar un build manual: pestaña **Actions** ->
   `Build Kodi repo` -> *Run workflow*.

## Instalar en cada Kodi

1. Ajustes -> Sistema -> Complementos -> activa **Origenes desconocidos**.
2. Descarga el zip del repositorio, en la URL:
   ```
   https://raw.githubusercontent.com/<tu_usuario>/dontorrent-kodi/main/repo/repository.dontorrent/repository.dontorrent-1.0.0.zip
   ```
   (puedes guardar la URL en un USB, o pegarla directamente desde el
   navegador de archivos de Kodi anadiendo la fuente
   `https://raw.githubusercontent.com/<tu_usuario>/dontorrent-kodi/main/repo/repository.dontorrent/`).
3. En Kodi: Complementos -> caja "Instalar desde un archivo zip" -> elige
   ese zip. Aparecera "DonTorrent Repo" instalado.
4. Complementos -> "Instalar desde repositorio" -> "DonTorrent Repo" ->
   "Complementos de video" -> **DonTorrent** -> Instalar.

A partir de aqui, cada vez que hagas `git push` con cambios al addon,
Kodi detectara la nueva version en su comprobacion automatica (cada 24h
por defecto) y actualizara solo. Para forzar la comprobacion: Ajustes ->
Complementos -> "Buscar actualizaciones".

## Subir una nueva version del addon

1. Edita el codigo en `plugin.video.dontorrent/`.
2. **Sube el numero de version** en `plugin.video.dontorrent/addon.xml`
   (atributo `version=`). Si no lo subes, Kodi no actualizara.
3. `git add . && git commit -m "..." && git push`.
4. CI reconstruye `repo/` automaticamente. Listo.

## Build local (para probar antes de pushear)

```
python tools/build_repo.py
```

Genera `repo/` igual que el workflow. Puedes apuntar un Kodi local a
`file:///ruta/a/repo/addons.xml` para probar.

## Repo privado (opcional)

Si prefieres que el repo de GitHub sea privado:

1. Genera un *Personal Access Token* (Fine-grained) con permiso de
   lectura sobre el repo.
2. En `repository.dontorrent/addon.xml` cambia las URL a la forma:
   ```
   https://<tu_usuario>:<TOKEN>@raw.githubusercontent.com/<tu_usuario>/dontorrent-kodi/main/repo/...
   ```
3. Reconstruye y reinstala el zip del repositorio en cada Kodi.

Aviso: cualquiera con acceso a ese Kodi podra leer el token desde el
addon.xml instalado. Para uso domestico es aceptable; para algo mas
serio, mejor publico.
