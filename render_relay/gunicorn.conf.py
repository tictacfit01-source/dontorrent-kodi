# Configuracion de gunicorn. gunicorn la lee AUTOMATICAMENTE al arrancar desde
# este directorio (render_relay/ = Root Directory del servicio), asi que aplica
# aunque el "Start Command" del panel de Render no incluya estos flags (los
# argumentos de la linea de comandos solo sobreescriben lo que mencionan; como
# el comando NO fija -k ni --threads, mandan estos valores).
#
# Por que: el mando se congelaba. Con workers SINCRONOS solo se atienden 2
# peticiones a la vez; una busqueda (DonTorrent PoW 14-38s + Elite/Wolf/Divx)
# ocupaba ambos y el sondeo /kb/poll del box quedaba en cola (hasta ~37-60s
# medidos). El trabajo es de ESPERA DE RED (I/O) -> los hilos lo resuelven: las
# peticiones rapidas del mando cogen un hilo libre al instante.
#
# 2 PROCESOS (uno libre para el mando mientras el otro resuelve el PoW de
# DonTorrent, que es CPU/GIL) x 6 HILOS = 12 peticiones simultaneas.
#
# 13-sep: eran 4 hilos (8 a la vez) y se QUEDO SIN NINGUNO. Cada busqueda de la
# web lanza CINCO peticiones (catsearch + catetbox x3 + catdxsearch) que pueden
# durar 26s; dos busquedas solapadas (una persona que cambia de idea, o dos
# personas a la vez) ocupan las 8 y el relay deja de contestar a TODO -- ni
# /ping ni /kb/poll: parece caido con el servicio perfectamente vivo. El front
# ya aborta las de la busqueda anterior (SREQ/sreqAbort en app.py), y aqui va el
# margen para cuando de verdad haya varias personas. Es trabajo de ESPERA de
# red: los hilos de mas casi no cuestan memoria (el limite del plan free son
# 512 MB y los dos workers rondan los 300).
worker_class = "gthread"
workers = 2
threads = 6
timeout = 120
graceful_timeout = 30
keepalive = 5
