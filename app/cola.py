"""
cola.py — El pool de workers que consume la asyncio.Queue.

Cada worker es una corrutina que vive en un loop infinito:
saca una tarea de la cola, la ejecuta y actualiza su estado.
"""

import asyncio
from datetime import datetime

from app import estado as registro
from app.modelos import EstadoTarea, Tarea
from app.tareas import EJECUTORES


async def ejecutar(tarea: Tarea) -> None:
    """Corre una tarea y deja su estado final (completada, fallida o cancelada)."""
    if tarea.estado == EstadoTarea.CANCELADA:
        # Se canceló entre que el worker la sacó de la cola y que arrancó.
        return
    tarea.estado = EstadoTarea.EJECUTANDO
    tarea.iniciada_en = datetime.now()
    try:
        funcion = EJECUTORES[tarea.tipo]
        tarea.resultado = await funcion(tarea.parametros)
        tarea.estado = EstadoTarea.COMPLETADA
    except asyncio.CancelledError:
        # CancelledError NO es Exception: hay que capturarla explícitamente.
        # Dejamos el estado final y la RELANZAMOS para que la cancelación termine bien.
        tarea.estado = EstadoTarea.CANCELADA
        tarea.error = "Cancelada"
        raise
    except Exception as e:
        tarea.estado = EstadoTarea.FALLIDA
        tarea.error = f"{type(e).__name__}: {e}"
    finally:
        tarea.finalizada_en = datetime.now()


async def worker(nombre: str, cola: asyncio.Queue) -> None:
    """Consume tareas de la cola para siempre (hasta que lo cancelen)."""
    while True:
        tarea = await cola.get()
        try:
            if tarea.estado == EstadoTarea.CANCELADA:
                continue  # la cancelaron mientras esperaba en la cola
            # La tarea corre en su propio asyncio.Task para poder cancelarla
            # desde afuera (DELETE) sin matar al worker.
            interna = asyncio.create_task(ejecutar(tarea))
            registro.registrar_asyncio_task(tarea.id, interna)
            try:
                # wait() no relanza la cancelación de 'interna': el worker sigue vivo.
                # Si cancelan al WORKER (apagado), la CancelledError sí sale de acá.
                await asyncio.wait({interna})
            finally:
                if not interna.done():
                    interna.cancel()
                    await asyncio.wait({interna})
                registro.limpiar_asyncio_task(tarea.id)
        finally:
            cola.task_done()


def iniciar_workers(cantidad: int, cola: asyncio.Queue) -> list[asyncio.Task]:
    """Lanza N workers como asyncio.Tasks y devuelve sus handles."""
    return [
        asyncio.create_task(worker(f"worker-{i}", cola))
        for i in range(cantidad)
    ]
