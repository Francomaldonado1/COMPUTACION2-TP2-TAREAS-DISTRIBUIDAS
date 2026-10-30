"""
main.py — La API HTTP (FastAPI) y el arranque/apagado del sistema.
"""

import asyncio
import os
from datetime import datetime
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, HTTPException

from app import estado as registro
from app import persistencia, tareas
from app.cola import iniciar_workers
from app.modelos import EstadoTarea, Tarea, TareaRequest

NUM_WORKERS = 4
PROCESOS_CPU = 2
TAMANO_COLA = 100
# Segundos que esperamos a las tareas en curso al apagar, antes de cancelarlas.
GRACE_APAGADO = float(os.getenv("GRACE_APAGADO", "10"))


@asynccontextmanager
async def ciclo_de_vida(app: FastAPI):
    # Arranque: se crea la cola y los workers DENTRO del event loop ya corriendo.
    persistencia.reportar_volcado_previo()
    app.state.aceptando = True
    app.state.cola = asyncio.Queue(maxsize=TAMANO_COLA)
    tareas.iniciar_pool(PROCESOS_CPU)
    app.state.workers = iniciar_workers(NUM_WORKERS, app.state.cola)
    yield
    # Apagado ordenado (uvicorn llega acá con SIGTERM o SIGINT).
    app.state.aceptando = False
    await _apagar(app)


async def _apagar(app: FastAPI) -> None:
    # 1) Las pendientes no arrancan: las sacamos de la cola (quedan 'pendiente').
    while not app.state.cola.empty():
        app.state.cola.get_nowait()
        app.state.cola.task_done()
    # 2) Esperamos, con límite, a las que están ejecutando.
    en_curso = registro.tareas_en_curso()
    if en_curso:
        _, colgadas = await asyncio.wait(en_curso, timeout=GRACE_APAGADO)
        # 3) Las que no terminaron a tiempo se cancelan (quedan 'cancelada').
        for t in colgadas:
            t.cancel()
        if colgadas:
            await asyncio.wait(colgadas)
    # 4) Ya no queda trabajo: cerramos workers y pool.
    for w in app.state.workers:
        w.cancel()
    await asyncio.gather(*app.state.workers, return_exceptions=True)
    tareas.cerrar_pool()
    # 5) Volcamos el estado final a disco.
    ruta = persistencia.volcar_estado()
    persistencia.log.info("Estado volcado en %s", ruta)


app = FastAPI(title="Plataforma de Tareas Distribuidas", lifespan=ciclo_de_vida)


@app.post("/tareas", status_code=201)
async def crear_tarea(pedido: TareaRequest) -> Tarea:
    tarea = Tarea(
        tipo=pedido.tipo,
        parametros=pedido.parametros,
        timeout=pedido.timeout,
    )
    if not app.state.aceptando:
        raise HTTPException(status_code=503, detail="El servidor se está apagando")
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


@app.get("/estadisticas")
async def estadisticas() -> dict:
    return registro.calcular_estadisticas(NUM_WORKERS)


if __name__ == "__main__":
    # La guarda es obligatoria: en macOS/Windows ProcessPoolExecutor re-importa
    # este módulo en cada proceso hijo y sin ella se entra en un bucle.
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
