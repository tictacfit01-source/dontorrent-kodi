@echo off
echo ============================================
echo   MejorWolf - Desplegar Cloudflare Worker
echo ============================================
echo.
echo Este script despliega el worker actualizado
echo que permite DonTorrent y Series.ly a traves
echo del proxy.
echo.
echo Se abrira el navegador para autenticarte
echo en Cloudflare. Haz clic en "Allow".
echo.
pause

cd /d "%~dp0"

echo.
echo [1/2] Iniciando sesion en Cloudflare...
call npx wrangler login
if errorlevel 1 (
    echo.
    echo ERROR: No se pudo iniciar sesion.
    echo Asegurate de hacer clic en "Allow" en el navegador.
    pause
    exit /b 1
)

echo.
echo [2/2] Desplegando worker...
call npx wrangler deploy
if errorlevel 1 (
    echo.
    echo ERROR: No se pudo desplegar el worker.
    pause
    exit /b 1
)

echo.
echo ============================================
echo   Worker desplegado correctamente!
echo   DonTorrent y Series.ly ahora funcionan.
echo ============================================
echo.
pause
