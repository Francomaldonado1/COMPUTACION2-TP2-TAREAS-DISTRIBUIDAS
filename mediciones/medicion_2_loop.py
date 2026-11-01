"""
Medición 2 — El event loop bloqueado.

Levanta la API dos veces (puerto 8001): primero con el cálculo CPU-bound
ejecutado DIRECTO en el event loop (SIN_EXECUTOR=1), después con run_in_executor.
En cada una lanza un 'hashear' pesado y, mientras corre, mide cuánto tarda en
responder un GET.

Uso (desde la raíz del repo):
    ./venv/bin/python mediciones/medicion_2_loop.py
    ./venv/bin/python mediciones/medicion_2_loop.py --iteraciones 20000000
"""

import argparse
import asyncio
import os
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx

RAIZ = Path(__file__).resolve().parent.parent
PUERTO = 8001
BASE = f"http://127.0.0.1:{PUERTO}"


async def esperar_servidor(cliente: httpx.AsyncClient) -> None:
    for _ in range(50):
        try:
            await cliente.get(f"{BASE}/estadisticas")
            return
        except httpx.HTTPError:
            await asyncio.sleep(0.2)
    raise RuntimeError("el servidor no arrancó")


async def medir(sin_executor: bool, iteraciones: int) -> list[float]:
    """Devuelve las latencias (s) de los GET hechos mientras corre el hash."""
    with tempfile.TemporaryDirectory() as tmp:
        env = {
            **os.environ,
            "SIN_EXECUTOR": "1" if sin_executor else "0",
            "DIRECTORIO_ESTADO": tmp,  # que el volcado no ensucie el repo
        }
        servidor = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "app.main:app", "--port", str(PUERTO)],
            cwd=RAIZ, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        try:
            async with httpx.AsyncClient(timeout=120) as cliente:
                await esperar_servidor(cliente)
                r = await cliente.post(f"{BASE}/tareas", json={
                    "tipo": "hashear",
                    "parametros": {"texto": "x", "iteraciones": iteraciones},
                })
                tarea_id = r.json()["id"]
                await asyncio.sleep(0.5)  # que el hash ya esté corriendo

                latencias = []
                while True:
                    t0 = time.perf_counter()
                    r = await cliente.get(f"{BASE}/tareas/{tarea_id}")
                    latencias.append(time.perf_counter() - t0)
                    if r.json()["estado"] in ("completada", "fallida"):
                        return latencias
                    await asyncio.sleep(0.1)
        finally:
            servidor.send_signal(signal.SIGINT)
            servidor.wait(timeout=30)


async def main(iteraciones: int) -> None:
    print(f"Hash de {iteraciones:,} iteraciones; GET /tareas/<id> durante su ejecución\n")
    for sin_executor in (True, False):
        lat = await medir(sin_executor, iteraciones)
        nombre = "SIN executor (en el event loop)" if sin_executor else "CON run_in_executor"
        print(f"{nombre}")
        print(f"  GETs medidos:        {len(lat)}")
        print(f"  latencia máxima:     {max(lat) * 1000:9.1f} ms")
        print(f"  latencia promedio:   {sum(lat) / len(lat) * 1000:9.1f} ms\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--iteraciones", type=int, default=10_000_000)
    asyncio.run(main(parser.parse_args().iteraciones))
