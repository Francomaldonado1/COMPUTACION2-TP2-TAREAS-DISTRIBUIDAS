# Plataforma de Tareas Distribuidas

TP2 de Computación II (Universidad de Mendoza, 2026). Un servicio HTTP al que se le
encargan trabajos, que los ejecuta de forma concurrente con asyncio y permite
consultar su estado y resultado.

> **Estado del documento:** borrador que se completa a medida que avanza el TP.
> Las secciones marcadas con 🚧 todavía faltan.

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

Primera versión, a reescribir y ampliar con mis palabras 🚧

- **Estado en un dict simple, sin locks.** La API y los workers corren en el mismo hilo
  (el event loop) y comparten memoria. El event loop solo cambia de corrutina en los
  `await`, así que ninguna operación sobre el dict se interrumpe a mitad de camino.
  Contraste con el TP1, donde había procesos separados y hacía falta `Manager`/`mmap`.
- **Dos diccionarios en `estado.py`.** `tareas` guarda datos serializables (lo que ven
  los `GET` y lo que va al JSON). `_asyncio_tasks` guarda los `asyncio.Task` en vivo,
  solo para poder cancelarlos: un `Task` envuelve una corrutina en ejecución y no se
  puede escribir como JSON.
- **CPU-bound fuera del event loop.** `hashear` corre en un `ProcessPoolExecutor`. Si se
  ejecutara dentro de la corrutina, congelaría el event loop y toda la API. Se eligen
  procesos y no threads por el GIL. 🚧 completar con la medición 2.
- **Cancelación.** `DELETE` sobre una tarea pendiente la marca `cancelada` y el worker
  la saltea; sobre una en ejecución llama a `Task.cancel()`, y el `CancelledError`
  se captura para dejar el estado final y se relanza. 🚧 explicar recursos a medio usar.
- **Excepciones en tareas.** `ejecutar()` captura cualquier `Exception` y deja la tarea
  `fallida` con el motivo, así que el worker sigue vivo y atiende la siguiente.
- **Cola con tamaño máximo** (100) y `put_nowait`: si se llena, la API responde 503 en
  vez de dejar colgado al cliente (backpressure).
- 🚧 ¿Por qué `asyncio.Queue` y no `queue.Queue`?

## 5. Mediciones 🚧

Se reproducen con `python mediciones/medicion_1_io.py` y
`python mediciones/medicion_2_loop.py` (con el `venv` activado).

### 5.1 Concurrencia I/O

20 descargas de 1 segundo cada una, contra un servidor local:

| Forma | Tiempo |
|-------|--------|
| Secuencial (`for` + `await`) | 20.18 s |
| Concurrente (`asyncio.gather`) | 1.05 s |
| **Speedup** | **19.3x** |

🚧 Interpretación (con mis palabras): ¿por qué ~1 s y no 20 s? ¿Por qué el speedup no
es exactamente 20x?

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

🚧 Interpretación (con mis palabras): ¿por qué un solo GET medido sin executor?
¿Qué pasa con el event loop durante esos ~4.6 s?

### 5.3 Techo del pool 🚧

Hasta dónde escala antes de degradarse.

## 6. Conceptos del curso aplicados 🚧

## 7. Limitaciones conocidas

- El estado vive en memoria: solo sobrevive a un reinicio como volcado JSON, y las
  tareas no se retoman.
- Una tarea cancelada mientras está pendiente sigue ocupando un lugar en la cola hasta
  que un worker la saca.
- Un `hashear` que ya está calculando no se puede interrumpir: cancelarlo o vencer su
  timeout lo marca `cancelada` / `fallida`, pero el proceso del pool sigue usando CPU
  hasta terminar.

## 8. Lo que aprendí 🚧
