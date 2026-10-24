"""
cola.py — El pool de workers que consume la asyncio.Queue.

Cada worker es una corrutina que vive en un loop infinito:
saca una tarea de la cola, la ejecuta y actualiza su estado.
"""

import asyncio
from datetime import datetime

from app.modelos import EstadoTarea, Tarea
from app.tareas import EJECUTORES


async def ejecutar(tarea: Tarea) -> None:
    """Corre una tarea y deja su estado final (completada o fallida)."""
    tarea.estado = EstadoTarea.EJECUTANDO
    tarea.iniciada_en = datetime.now()
    try:
        funcion = EJECUTORES[tarea.tipo]
        tarea.resultado = await funcion(tarea.parametros)
        tarea.estado = EstadoTarea.COMPLETADA
    except Exception as e:
        # Exception NO incluye CancelledError: la cancelación la manejamos aparte.
        tarea.estado = EstadoTarea.FALLIDA
        tarea.error = f"{type(e).__name__}: {e}"
    finally:
        tarea.finalizada_en = datetime.now()


async def worker(nombre: str, cola: asyncio.Queue) -> None:
    """Consume tareas de la cola para siempre (hasta que lo cancelen)."""
    while True:
        tarea = await cola.get()
        try:
            await ejecutar(tarea)
        finally:
            cola.task_done()


def iniciar_workers(cantidad: int, cola: asyncio.Queue) -> list[asyncio.Task]:
    """Lanza N workers como asyncio.Tasks y devuelve sus handles."""
    return [
        asyncio.create_task(worker(f"worker-{i}", cola))
        for i in range(cantidad)
    ]
