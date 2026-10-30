"""
estado.py — El "estado global" del sistema.

Como asyncio corre en un ÚNICO hilo, un dict normal es completamente seguro:
no hay race conditions porque el event loop nunca interrumpe a mitad de una
operación sobre el dict (solo cambia de corrutina en los puntos de await).

Guardamos dos cosas separadas:
  - tareas: los datos serializables (lo que devuelven los GET)
  - _asyncio_tasks: los objetos asyncio.Task (solo para poder cancelar)
    No van en el modelo Pydantic porque no son serializables a JSON.
"""

from typing import Optional
import asyncio

from app.modelos import EstadoTarea, Tarea, TipoTarea


# Diccionario principal: id → Tarea
tareas: dict[str, Tarea] = {}

# Diccionario interno: id → asyncio.Task (para cancelación)
# El cliente nunca ve esto; es solo para que el DELETE pueda hacer task.cancel()
_asyncio_tasks: dict[str, asyncio.Task] = {}


def registrar_tarea(tarea: Tarea) -> None:
    """Agrega una tarea nueva al estado (la crea como pendiente)."""
    tareas[tarea.id] = tarea


def obtener_tarea(tarea_id: str) -> Optional[Tarea]:
    """Devuelve una tarea por su id, o None si no existe."""
    return tareas.get(tarea_id)


def listar_tareas(estado: Optional[str] = None) -> list[Tarea]:
    """Devuelve todas las tareas, con filtro opcional por estado."""
    todas = list(tareas.values())
    if estado:
        todas = [t for t in todas if t.estado == estado]
    return todas


def registrar_asyncio_task(tarea_id: str, task: asyncio.Task) -> None:
    """Guarda el asyncio.Task asociado a una tarea (para poder cancelarla)."""
    _asyncio_tasks[tarea_id] = task


def obtener_asyncio_task(tarea_id: str) -> Optional[asyncio.Task]:
    """Devuelve el asyncio.Task para cancelación, o None si ya terminó."""
    return _asyncio_tasks.get(tarea_id)


def limpiar_asyncio_task(tarea_id: str) -> None:
    """Elimina el asyncio.Task del registro cuando la tarea ya terminó."""
    _asyncio_tasks.pop(tarea_id, None)


def tareas_en_curso() -> list[asyncio.Task]:
    """Los asyncio.Task que todavía no terminaron (para el apagado ordenado)."""
    return [t for t in _asyncio_tasks.values() if not t.done()]


def calcular_estadisticas(workers_totales: int) -> dict:
    """Totales por estado, tiempos promedio de las completadas y workers ocupados."""
    por_estado = {e.value: 0 for e in EstadoTarea}
    duraciones: dict[str, list[float]] = {}
    for t in tareas.values():
        estado = EstadoTarea(t.estado).value
        por_estado[estado] += 1
        if estado == EstadoTarea.COMPLETADA.value:
            segundos = (t.finalizada_en - t.iniciada_en).total_seconds()
            duraciones.setdefault(TipoTarea(t.tipo).value, []).append(segundos)

    todas = [d for lista in duraciones.values() for d in lista]
    return {
        "total": len(tareas),
        "por_estado": por_estado,
        "tiempo_promedio_s": round(sum(todas) / len(todas), 3) if todas else None,
        "tiempo_promedio_por_tipo_s": {
            tipo: round(sum(l) / len(l), 3) for tipo, l in duraciones.items()
        },
        "workers_ocupados": por_estado[EstadoTarea.EJECUTANDO.value],
        "workers_totales": workers_totales,
    }
