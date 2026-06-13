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
# DonTorrent, que es CPU/GIL) x 4 HILOS = 8 peticiones simultaneas.
worker_class = "gthread"
workers = 2
threads = 4
timeout = 120
graceful_timeout = 30
keepalive = 5
