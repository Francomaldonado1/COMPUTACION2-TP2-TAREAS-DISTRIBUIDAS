"""
Medición 3 — Techo del pool: cuántas tareas concurrentes aguanta el sistema.

Para cada configuración (workers, tamaño de cola) levanta la API (puerto 8002) y le
manda, de golpe, N tareas 'esperar' de 1 segundo. Mide cuántas se aceptan (201) y
cuántas se rechazan (503, cola llena), cuánto tardan en terminar las aceptadas, y
cuánto tarda en responder un GET mientras hay carga.

Con 'esperar' el trabajo de cada tarea es casi nulo (solo dormir), así que lo que
se ve es el límite del DISEÑO: workers, cola y capacidad de la API para recibir
pedidos. Con W workers, N tareas de 1 s deberían tardar ceil(N / W) segundos.

Uso (desde la raíz del repo):
    ./venv/bin/python mediciones/medicion_3_techo.py
"""

import asyncio
import math
import os
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx

RAIZ = Path(__file__).resolve().parent.parent
PUERTO = 8002
BASE = f"http://127.0.0.1:{PUERTO}"

# (workers, tamaño de cola, cantidades de tareas a probar)
CONFIGURACIONES = [
    (4, 100, [4, 20, 100, 150]),
    (50, 200, [50, 200, 400]),
    (1000, 5000, [1000, 3000, 5000]),
]


async def esperar_servidor(cliente: httpx.AsyncClient) -> None:
    for _ in range(50):
        try:
            await cliente.get(f"{BASE}/estadisticas")
            return
        except httpx.HTTPError:
            await asyncio.sleep(0.2)
    raise RuntimeError("el servidor no arrancó")


async def lanzar(cliente: httpx.AsyncClient, n: int) -> tuple[int, int, float]:
    """Manda n POST a la vez. Devuelve (aceptadas, rechazadas, segundos que tardó)."""
    t0 = time.perf_counter()
    respuestas = await asyncio.gather(*(
        cliente.post(f"{BASE}/tareas", json={
            "tipo": "esperar", "parametros": {"segundos": 1},
        })
        for _ in range(n)
    ))
    aceptadas = sum(r.status_code == 201 for r in respuestas)
    return aceptadas, n - aceptadas, time.perf_counter() - t0


async def hasta_vaciar(cliente: httpx.AsyncClient) -> tuple[float, float]:
    """Espera a que no quede nada pendiente ni ejecutando.
    Devuelve (segundos que tardó, latencia máxima de un GET durante la espera)."""
    t0 = time.perf_counter()
    peor = 0.0
    while True:
        t1 = time.perf_counter()
        est = (await cliente.get(f"{BASE}/estadisticas")).json()["por_estado"]
        peor = max(peor, time.perf_counter() - t1)
        if est["pendiente"] + est["ejecutando"] == 0:
            return time.perf_counter() - t0, peor
        await asyncio.sleep(0.2)


async def correr(workers: int, cola: int, ns: list[int]) -> None:
    print(f"\n=== {workers} workers, cola de {cola} ===")
    print(f"{'N':>6} {'aceptadas':>10} {'rechazadas':>11} {'POSTs (s)':>10} "
          f"{'total (s)':>10} {'esperado (s)':>13} {'GET máx (ms)':>13}")
    with tempfile.TemporaryDirectory() as tmp:
        env = {**os.environ, "NUM_WORKERS": str(workers), "TAMANO_COLA": str(cola),
               "DIRECTORIO_ESTADO": tmp}
        servidor = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "app.main:app", "--port", str(PUERTO)],
            cwd=RAIZ, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        try:
            limites = httpx.Limits(max_connections=200)
            async with httpx.AsyncClient(timeout=300, limits=limites) as cliente:
                await esperar_servidor(cliente)
                for n in ns:
                    t0 = time.perf_counter()
                    aceptadas, rechazadas, t_post = await lanzar(cliente, n)
                    _, get_max = await hasta_vaciar(cliente)
                    total = time.perf_counter() - t0
                    esperado = math.ceil(aceptadas / workers)
                    print(f"{n:>6} {aceptadas:>10} {rechazadas:>11} {t_post:>10.2f} "
                          f"{total:>10.2f} {esperado:>13} {get_max * 1000:>13.1f}")
        finally:
            servidor.send_signal(signal.SIGINT)
            servidor.wait(timeout=60)


async def main() -> None:
    for workers, cola, ns in CONFIGURACIONES:
        await correr(workers, cola, ns)


if __name__ == "__main__":
    asyncio.run(main())
