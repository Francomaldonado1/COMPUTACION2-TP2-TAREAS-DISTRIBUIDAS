"""
main.py — La API HTTP (FastAPI) y el arranque/apagado del sistema.
"""

import asyncio
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, HTTPException

from app import estado as registro
from app.cola import iniciar_workers
from app.modelos import Tarea, TareaRequest

NUM_WORKERS = 4
TAMANO_COLA = 100


@asynccontextmanager
async def ciclo_de_vida(app: FastAPI):
    # Arranque: se crea la cola y los workers DENTRO del event loop ya corriendo.
    app.state.cola = asyncio.Queue(maxsize=TAMANO_COLA)
    app.state.workers = iniciar_workers(NUM_WORKERS, app.state.cola)
    yield
    # Apagado: cancelamos los workers y esperamos a que terminen.
    for w in app.state.workers:
        w.cancel()
    await asyncio.gather(*app.state.workers, return_exceptions=True)


app = FastAPI(title="Plataforma de Tareas Distribuidas", lifespan=ciclo_de_vida)


@app.post("/tareas", status_code=201)
async def crear_tarea(pedido: TareaRequest) -> Tarea:
    tarea = Tarea(
        tipo=pedido.tipo,
        parametros=pedido.parametros,
        timeout=pedido.timeout,
    )
    registro.registrar_tarea(tarea)
    try:
        # put_nowait: si la cola está llena respondemos 503 en vez de colgar al cliente.
        app.state.cola.put_nowait(tarea)
    except asyncio.QueueFull:
        raise HTTPException(status_code=503, detail="Cola llena, reintentá más tarde")
    return tarea


@app.get("/tareas")
async def listar_tareas(estado: Optional[str] = None) -> list[Tarea]:
    return registro.listar_tareas(estado)


@app.get("/tareas/{tarea_id}")
async def obtener_tarea(tarea_id: str) -> Tarea:
    tarea = registro.obtener_tarea(tarea_id)
    if tarea is None:
        raise HTTPException(status_code=404, detail="Tarea no encontrada")
    return tarea
