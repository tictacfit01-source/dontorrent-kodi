# MejorWolf Relay (Cloudflare Worker)

Pequeño puente HTTPS que permite al addon llegar a MejorTorrent y WolfMax4k
cuando el ISP del usuario bloquea los dominios. El dominio `*.workers.dev`
no esta bloqueado por ningun ISP español: si lo bloqueasen, romperian medio
internet.

## Despliegue (5 minutos, una sola vez)

1. Crea cuenta gratis en <https://dash.cloudflare.com> si no tienes.
2. Menu izquierdo → **Workers & Pages** → **Create application** → **Create Worker**.
3. Nombre: `mw-relay` (o el que quieras).
4. Pulsa **Deploy** sin tocar nada (despliega el Hello World inicial).
5. Pulsa **Edit code**.
6. Borra todo el contenido del editor.
7. Pega el contenido de [`worker.js`](worker.js).
8. Pulsa **Save and Deploy**.
9. Te dara una URL del estilo `https://mw-relay.TU-SUBDOMINIO.workers.dev`.
10. Copiala.

## Configurar el addon

Opcion A (para tu uso): en Kodi, **Ajustes → Proxy** y pega la URL.

Opcion B (para que tu hermano no toque nada): edita
`resources/settings.xml` antes de empaquetar y pon esa URL como `default`
del setting `proxy_url`. Empaqueta el zip y pasalo. Listo.

## Limites

- 100.000 peticiones/dia (plan gratuito). Sobra para uso familiar.
- 10 ms de CPU por peticion (este worker es trivial, usa <1 ms).
- Si lo superas, Cloudflare te avisa por email; puedes pasarte a Workers
  Paid ($5/mes) para 10M peticiones, pero para 5 personas no llegaras nunca.

## Seguridad

- El worker solo proxea las dominios listados en `ALLOWED_HOSTS`. Cualquier
  otro destino devuelve 403. Asi nadie puede usar tu URL como proxy abierto
  para spam o cosas peores.
- No registra peticiones (Cloudflare guarda contadores agregados, no URLs).
- Toda la comunicacion es HTTPS extremo a extremo.
