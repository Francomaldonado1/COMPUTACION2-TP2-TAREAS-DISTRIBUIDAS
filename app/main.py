"""
main.py — La API HTTP (FastAPI) y el arranque/apagado del sistema.
"""

import asyncio
from datetime import datetime
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, HTTPException

from app import estado as registro
from app.cola import iniciar_workers
from app.modelos import EstadoTarea, Tarea, TareaRequest

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


@app.delete("/tareas/{tarea_id}")
async def cancelar_tarea(tarea_id: str) -> Tarea:
    tarea = registro.obtener_tarea(tarea_id)
    if tarea is None:
        raise HTTPException(status_code=404, detail="Tarea no encontrada")

    if tarea.estado == EstadoTarea.PENDIENTE:
        # Sigue en la cola: la marcamos y el worker la va a saltear al sacarla.
        tarea.estado = EstadoTarea.CANCELADA
        tarea.error = "Cancelada"
        tarea.finalizada_en = datetime.now()
    elif tarea.estado == EstadoTarea.EJECUTANDO:
        interna = registro.obtener_asyncio_task(tarea_id)
        if interna is not None:
            interna.cancel()
            # Esperamos a que termine de cancelarse: así respondemos con el estado real.
            await asyncio.wait({interna})
    else:
        raise HTTPException(
            status_code=409, detail=f"La tarea ya terminó ({EstadoTarea(tarea.estado).value})"
        )
    return tarea
