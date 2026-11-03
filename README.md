# Plataforma de Tareas Distribuidas

TP2 de Computación II (Universidad de Mendoza, 2026). Un servicio HTTP al que se le
encargan trabajos, que los ejecuta de forma concurrente con asyncio y permite
consultar su estado y resultado.

## 1. Descripción y uso

Se levanta con Docker:

```bash
docker compose up --build
```

La API queda en `http://localhost:8000` (documentación interactiva en `/docs`).

### Tipos de tarea

| Tipo | Naturaleza | Parámetros | Resultado |
|------|-----------|-----------|-----------|
| `esperar` | I/O simulado | `{"segundos": 5}` | texto |
| `descargar` | I/O-bound | `{"urls": ["http://..."]}` | por URL: status, bytes, tiempo |
| `hashear` | CPU-bound | `{"texto": "abc", "iteraciones": 1000000}` | SHA-256 iterado, en hex |

Todas aceptan un `timeout` opcional (en segundos).

### Ejemplos

```bash
# Encolar una tarea (responde de inmediato con el id y estado "pendiente")
curl -X POST localhost:8000/tareas -H 'content-type: application/json' \
  -d '{"tipo": "esperar", "parametros": {"segundos": 5}}'

# Con timeout: si pasa de 2 s queda "fallida"
curl -X POST localhost:8000/tareas -H 'content-type: application/json' \
  -d '{"tipo": "esperar", "parametros": {"segundos": 10}, "timeout": 2}'

# Hash pesado (CPU-bound)
curl -X POST localhost:8000/tareas -H 'content-type: application/json' \
  -d '{"tipo": "hashear", "parametros": {"texto": "hola", "iteraciones": 10000000}}'

# Descargas concurrentes
curl -X POST localhost:8000/tareas -H 'content-type: application/json' \
  -d '{"tipo": "descargar", "parametros": {"urls": ["http://example.com", "http://example.org"]}}'

curl localhost:8000/tareas                    # listar todas
curl "localhost:8000/tareas?estado=ejecutando" # filtrar por estado
curl localhost:8000/tareas/<id>               # estado y resultado de una
curl -X DELETE localhost:8000/tareas/<id>     # cancelar (pendiente o en ejecución)
curl localhost:8000/estadisticas              # totales, tiempos promedio, workers
```

Estados: `pendiente` → `ejecutando` → `completada` | `fallida` | `cancelada`.

### Configuración

Variables de entorno (valores por defecto entre paréntesis):

| Variable | Qué controla |
|----------|--------------|
| `NUM_WORKERS` (4) | Cantidad de workers (corrutinas) que consumen la cola |
| `TAMANO_COLA` (100) | Lugares de la cola; con la cola llena, el `POST` responde 503 |
| `GRACE_APAGADO` (10) | Segundos que se espera a las tareas en curso al apagar |
| `DIRECTORIO_ESTADO` (`.`) | Carpeta donde se escribe `estado_<timestamp>.json` |

### Apagado ordenado

`SIGTERM` / `SIGINT` (por ejemplo `docker compose stop`): se deja de aceptar tareas,
se espera hasta `GRACE_APAGADO` segundos (10 por defecto) a las que están en curso,
las que no terminan se cancelan, y el estado se vuelca a `estado_<timestamp>.json`
(en `./datos` con Docker). Al arrancar, si hay un volcado previo, se informa por log.

## 2. Estructura del repositorio

```
app/
├── main.py          # API FastAPI, lifespan (arranque/apagado ordenado)
├── modelos.py       # Qué es una Tarea (Pydantic): campos, estados, tipos
├── estado.py        # Estado compartido en memoria + estadísticas
├── cola.py          # Pool de workers que consumen la asyncio.Queue
├── tareas.py        # La lógica de cada tipo de tarea (esperar, hashear, descargar)
└── persistencia.py  # Volcado a JSON al apagar y reporte al arrancar
Dockerfile
docker-compose.yml
```

## 3. Arquitectura

```
   Cliente HTTP
        |
        v
  ┌──────────────────┐  put_nowait  ┌──────────────────┐
  │   API (FastAPI)  │─────────────>│  asyncio.Queue   │
  │  POST /tareas    │              └────────┬─────────┘
  │  GET  /tareas    │                       │ get()
  │  DELETE ...      │                       v
  └────────┬─────────┘              ┌──────────────────┐
           │ lee                    │  4 workers       │
           v                        │  (corrutinas)    │
  ┌──────────────────┐   escribe    │  ├─ I/O: await   │
  │ estado (dict)    │<─────────────┤  └─ CPU: executor│──> ProcessPoolExecutor
  └──────────────────┘              └──────────────────┘
```

Flujo de una tarea:

1. `POST /tareas` crea la `Tarea` (estado `pendiente`), la guarda en el dict de estado y
   la encola. Responde enseguida.
2. Un worker libre saca la tarea de la cola y la ejecuta en su propio `asyncio.Task`
   (guardado en `_asyncio_tasks` para poder cancelarlo).
3. Según el tipo: `esperar` y `descargar` se ejecutan con `await`; `hashear` se delega a
   un `ProcessPoolExecutor` con `run_in_executor`.
4. El worker deja el estado final (`completada`, `fallida` o `cancelada`) en el dict, y
   los `GET` lo leen de ahí.

## 4. Decisiones de diseño

- **Estado en un dict simple, sin locks.** La API y los workers corren en el mismo hilo
  (el event loop) y comparten memoria. El event loop solo cambia de corrutina en los
  `await`, así que ninguna operación sin `await` se interrumpe a mitad de camino: por
  ejemplo, entre el `put_nowait` y el `registrar_tarea` del `POST` no hay ninguno, y
  ningún worker puede sacar la tarea de la cola antes de que esté registrada. Con
  threads, el cambio podría ocurrir entre cualquier par de instrucciones y haría falta
  un `Lock`. Acá solo habría un problema si se pusiera un `await` en medio de una
  sección crítica. Contraste con el TP1, donde había procesos separados y hacía falta
  `Manager`/`mmap`.
- **Dos diccionarios en `estado.py`.** `tareas` guarda datos serializables (lo que ven
  los `GET` y lo que va al JSON). `_asyncio_tasks` guarda los `asyncio.Task` en vivo,
  solo para poder cancelarlos: un `Task` envuelve una corrutina en ejecución y no se
  puede escribir como JSON.
- **CPU-bound fuera del event loop.** `hashear` corre en un `ProcessPoolExecutor`. Si se
  ejecutara dentro de la corrutina, congelaría el event loop y toda la API. Se eligen
  procesos y no threads por el GIL. La medición 2 lo muestra: durante un hash de 10 M de iteraciones, un `GET`
  tarda 4640 ms sin executor y 3 ms con él.
- **Cancelación.** Hay dos casos. Si la tarea está `pendiente` (todavía en la cola),
  `DELETE` solo la marca `cancelada` y el worker la saltea al sacarla: no hay nada
  corriendo ni recursos abiertos. Si está `ejecutando`, `DELETE` llama a `Task.cancel()`,
  que lanza `CancelledError` en el `await` donde estaba pausada. Ese error sube por la
  corrutina: los `async with` y `finally` liberan los recursos (por ejemplo, el cliente
  `httpx` de `descargar` cierra sus conexiones), y al llegar a `ejecutar()` se captura
  para dejar el estado en `cancelada` y se relanza para que la cancelación termine bien.
  `DELETE` espera con `asyncio.wait` a que esto ocurra y responde con el estado real.
  Cada tarea corre en su propio `asyncio.Task` para poder cancelarla sin matar al worker.
- **Excepciones en tareas.** `ejecutar()` captura cualquier `Exception` y deja la tarea
  `fallida` con el motivo, así que el worker sigue vivo y atiende la siguiente. Si aun así
  escapara una excepción del bucle del worker, esa corrutina moriría sin ruido (Python
  solo avisa "Task exception was never retrieved") y el pool quedaría con un worker menos,
  sin reemplazo; está anotado en las limitaciones.
- **Cola con tamaño máximo** (100) y `put_nowait`: si se llena, la API responde 503 en
  vez de dejar colgado al cliente (backpressure).
- **Timeouts con `asyncio.wait_for`.** Cada tarea corre dentro de `wait_for(..., timeout)`;
  al vencer, cancela la corrutina y la tarea queda `fallida` con el motivo. Con
  `timeout=None` no hay límite.
- **¿Por qué `asyncio.Queue` y no `queue.Queue`?** `queue.Queue.get()` bloquea el hilo
  entero mientras la cola está vacía. Como los workers son corrutinas y comparten hilo con
  la API, el event loop quedaría congelado: no atendería ningún pedido y las tareas en
  curso tampoco avanzarían. Además sería un deadlock, porque quien pone tareas en la cola
  (el `POST`) vive en ese mismo loop bloqueado. En cambio `asyncio.Queue.get()` es una
  corrutina: con la cola vacía el worker se pausa en un `await` y el loop sigue libre
  para recibir pedidos.
- **`httpx` en modo async para las descargas.** `requests` es síncrono: cada `get()`
  bloquearía el event loop mientras espera la red, y las descargas dejarían de ser
  concurrentes (además la consigna lo prohíbe). `httpx.AsyncClient` hace `await` en la
  espera de red, así que el loop queda libre. Se eligió `httpx` y no `aiohttp` porque su
  API es casi igual a la de `requests`, y porque soporta tanto modo sync como async.
- **Registro después de encolar.** El `POST` primero hace `put_nowait` y recién después
  registra la tarea. Al revés (como estaba al principio), una cola llena dejaba una tarea
  `pendiente` fantasma que nadie ejecutaba; lo descubrió la medición 3.
- **Apagado ordenado en el `lifespan`.** Uvicorn captura `SIGTERM` y `SIGINT` y ejecuta el
  apagado del `lifespan`: se dejan de aceptar tareas, las pendientes no arrancan, se espera
  con límite a las que están en curso, las que no terminan se cancelan y se vuelca el estado.
  En Docker, `stop_grace_period` (20 s) tiene que ser mayor que `GRACE_APAGADO` (10 s), o
  Docker mata el proceso con `SIGKILL` antes de que termine el volcado.

## 5. Mediciones

Se reproducen con `python mediciones/medicion_1_io.py` y
`python mediciones/medicion_2_loop.py` (con el `venv` activado).

### 5.1 Concurrencia I/O

20 descargas de 1 segundo cada una, contra un servidor local:

| Forma | Tiempo |
|-------|--------|
| Secuencial (`for` + `await`) | 20.18 s |
| Concurrente (`asyncio.gather`) | 1.05 s |
| **Speedup** | **19.3x** |

Con `asyncio.gather` las 20 corrutinas se lanzan juntas: cada una manda su pedido y se
pausa en el `await` mientras espera la respuesta, y el event loop aprovecha esa espera
para arrancar las demás. Como las esperas se solapan, el total es lo que tarda la
descarga más lenta (~1 s) y no la suma. Con `for` + `await` no se pide la siguiente hasta
terminar la anterior, así que las esperas se suman (20 s). El speedup (19,3x) no llega a
20x porque la versión concurrente tiene un costo de organización de ~0,05 s (abrir las
conexiones, crear y planificar las corrutinas, procesar las respuestas, todo en un solo
hilo), que pesa un 5% sobre un total de apenas ~1 s.

> Nota: en una primera corrida la versión concurrente tardó 2 s. La causa era el
> servidor de prueba, no el cliente: `socketserver` tiene un backlog de 5 conexiones
> por defecto (`request_queue_size`), y con 20 conexiones simultáneas las que no
> entraban se reintentaban al segundo. Ampliando el backlog se estabilizó en ~1 s.

### 5.2 El event loop bloqueado

Hash SHA-256 de 10.000.000 de iteraciones; se mide cuánto tarda un
`GET /tareas/<id>` hecho mientras corre:

| | GETs medidos | Latencia máxima | Latencia promedio |
|-|--------------|-----------------|-------------------|
| Sin executor (en el event loop) | 1 | 4640.6 ms | 4640.6 ms |
| Con `run_in_executor` | 47 | 37.4 ms | 3.1 ms |

Sin executor, el cálculo corre dentro de la corrutina `hashear`, es decir, en el mismo
hilo que el event loop. Como `_hash_iterado` es un `for` de 10 millones de vueltas sin
ningún `await`, el loop no recupera el control durante ~4,6 s, y no puede atender ningún
pedido: el primer `GET` queda esperando todo ese tiempo, y por eso se mide una sola
latencia (4640 ms). Con `run_in_executor`, el cálculo corre en otro proceso del
`ProcessPoolExecutor` y la corrutina del worker queda pausada en el `await`, así que el
event loop sigue libre: cada `GET` responde en ~3 ms y, como el script espera 0,1 s entre
consultas, da unas 47 vueltas mientras dura el hash. Se usa un proceso y no un hilo por el
GIL: dos hilos de un mismo proceso se turnan para ejecutar código Python, mientras que
cada proceso tiene el suyo y puede usar un núcleo propio.

### 5.3 Techo del pool

Se mandan N tareas `esperar` de 1 segundo **de golpe** y se mide cuántas entran, cuánto
tardan en terminar y cuánto demora un `GET` durante la carga. `mediciones/medicion_3_techo.py`
levanta la API con distintas configuraciones (`NUM_WORKERS` y `TAMANO_COLA` son variables
de entorno). Con W workers, N tareas de 1 s deberían tardar `ceil(N / W)` segundos.

| Workers | Cola | N enviadas | Aceptadas | Rechazadas (503) | Total (s) | Esperado (s) | GET máx (ms) |
|--------:|-----:|-----------:|----------:|-----------------:|----------:|-------------:|-------------:|
| 4 | 100 | 4 | 4 | 0 | 1.09 | 1 | 6.4 |
| 4 | 100 | 20 | 20 | 0 | 5.06 | 5 | 7.4 |
| 4 | 100 | 100 | 100 | 0 | 25.14 | 25 | 9.9 |
| 4 | 100 | 150 | 104 | 46 | 26.24 | 26 | 9.6 |
| 50 | 200 | 50 | 50 | 0 | 1.14 | 1 | 4.6 |
| 50 | 200 | 200 | 200 | 0 | 4.21 | 4 | 7.9 |
| 50 | 200 | 400 | 250 | 150 | 5.40 | 5 | 17.8 |
| 1000 | 5000 | 1000 | 1000 | 0 | 3.96 | 1 | 12.1 |
| 1000 | 5000 | 3000 | 3000 | 0 | 10.65 | 3 | 24.0 |
| 1000 | 5000 | 5000 | 5000 | 0 | 18.34 | 5 | 46.4 |

Mientras haya pocos workers, el techo es el pool: con 4 y con 50 workers el tiempo
coincide con `ceil(N / W)`, es decir, se completan W tareas por segundo. Además hay un
tope de tareas aceptadas, igual a las que se están ejecutando más los lugares de la cola
(4 + 100 = 104 de 150; 50 + 200 = 250 de 400). Las que llegan con la cola llena reciben un
503 y no se registran: se prefiere rechazar a acumular tareas sin límite en memoria.

Con muchos workers el techo cambia de lugar: con 1000 workers y 1000 tareas de 1 s el
total fue de ~4 s en vez de 1 s, porque todos los `POST` pasan por un único event loop
(un proceso, un hilo) que atiende unos 300 a 350 pedidos por segundo (2.9 s para 1000
tareas, 17.1 s para 5000). Los 1000 `POST` tardaron 2.9 s en entrar y la última tarea 1 s
más. Agregar workers no ayuda, porque el cuello de botella no está en los workers sino en
la entrada. La latencia del `GET` también sube (6 → 46 ms) porque el loop está más ocupado.

Para superar ese techo habría que correr varios procesos de la API (por ejemplo
`uvicorn --workers N`), cada uno con su propio event loop; pero entonces cada proceso
tendría su propio diccionario de tareas y el estado en memoria ya no se compartiría
(ver limitaciones).

Salvedad: el generador de carga corre en la misma máquina que el servidor y comparten
CPU, así que los valores absolutos serían mejores en un escenario real.

## 6. Conceptos del curso aplicados

| Dónde en el código | Concepto | Clase |
|--------------------|----------|-------|
| Los workers (`cola.py`) son corrutinas en un solo hilo; `await` cede el control al loop | Event loop, corrutinas, `async`/`await` | 18 |
| Uvicorn atiende muchas conexiones en un hilo, con `epoll` por debajo | I/O multiplexing | 17 |
| Endpoints, validación con Pydantic, códigos 201/404/409/503 | HTTP y FastAPI | 19 |
| `descargar`: `httpx` async + `gather`; 20 descargas en 1 s en vez de 20 s | Asyncio en red, clientes concurrentes | 20 |
| `wait_for` para timeouts; `Task.cancel()` + `CancelledError` para cancelar | Timeouts y cancelación | 20 |
| El pool de workers consume una `asyncio.Queue` compartida | Productor-consumidor, igual que la clase 11 pero con corrutinas | 21 (y 11) |
| El dict `tareas` se comparte sin `Lock`: el loop solo cambia de corrutina en los `await` | Por qué asyncio no necesita locks | 20 |
| `hashear` con `run_in_executor` sobre un `ProcessPoolExecutor` único | Trabajo CPU-bound fuera del loop; threads vs procesos (GIL) | 21 y 22 |
| El pool de procesos se crea una sola vez al arrancar | Un pool por pedido sería un error clásico | 22 |
| `lifespan` y `async with` | Context managers (asíncronos) | Bloque 0 |
| `SIGTERM`/`SIGINT` provocan el volcado a JSON | Señales | 6 |
| `Dockerfile`, `docker-compose.yml`, volumen para los volcados | Docker | 1 y 2 |
| El backlog de 5 conexiones del servidor de prueba inflaba la medición 1 | `listen()` y `request_queue_size` | 13 y 16 |

## 7. Limitaciones conocidas

- El estado vive en memoria: solo sobrevive a un reinicio como volcado JSON, y las
  tareas no se retoman.
- Una tarea cancelada mientras está pendiente sigue ocupando un lugar en la cola hasta
  que un worker la saca.
- Un `hashear` que ya está calculando no se puede interrumpir: cancelarlo o vencer su
  timeout lo marca `cancelada` / `fallida`, pero el proceso del pool sigue usando CPU
  hasta terminar.
- Un solo proceso y un solo event loop atienden todo el HTTP: la medición 3 muestra un
  techo de ~300 a 350 pedidos por segundo. Para escalar más habría que correr varias
  instancias detrás de un balanceador (y entonces el estado en memoria ya no alcanza).
- Si una excepción escapara del bucle de un worker, ese worker moriría y no se reemplaza
  (el pool queda con uno menos). Hoy `ejecutar()` captura todo `Exception`, así que no
  debería pasar, pero no hay un supervisor que lo repare.
- El estado crece sin límite: las tareas terminadas nunca se borran de memoria.
- `NUM_WORKERS` y `PROCESOS_CPU` son fijos al arrancar; no se ajustan en caliente.
- `hashear` con `SIN_EXECUTOR=1` existe solo para la medición 2 y bloquea el loop a propósito.

## 8. Lo que aprendí

En el TP1 usé threads y necesité locks para proteger los datos compartidos, porque el
sistema operativo puede cambiar de hilo entre cualquier par de instrucciones. Con asyncio
entendí que el cambio de corrutina solo ocurre en un `await`, así que un tramo de código
sin `await` es indivisible y no hacen falta locks. También aprendí que asyncio no sirve
para todo: un cálculo pesado sin `await` congela el event loop (en la medición 2 un `GET`
tardó 4,6 s). Y descubrí el GIL: dentro de un mismo proceso solo un hilo ejecuta código
Python a la vez, por eso para el trabajo CPU-bound no sirve un hilo y hace falta un proceso
aparte, que tiene su propio GIL.

Aprendí a desconfiar de las mediciones hasta entender de dónde sale el número. En la
medición 1 la versión con `gather` tardó 2 s en vez de 1 s, y la causa era el backlog de 5
conexiones del servidor de prueba, no mi código. También entendí mejor la diferencia entre
`gather` y un `for` con `await`: ninguno bloquea el loop, pero el `for` espera a terminar
cada descarga antes de pedir la siguiente. En la medición 3 vi que con muchos workers el
cuello de botella se mueve a la API, porque todos los `POST` pasan por un único event loop.

Un bug real que encontré gracias a medir: si la cola estaba llena, el `POST` registraba la
tarea antes de encolarla y quedaba una tarea fantasma `pendiente` que nadie ejecutaba; lo
arreglé encolando primero y registrando después. Y una cosa más chica: `asyncio` es parte
de la biblioteca estándar y vive en el Python del sistema, no en el `venv`, que solo guarda
lo que instalo con `pip`.
