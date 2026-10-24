"""
tareas.py — La lógica real de cada tipo de tarea.

Cada función recibe el dict 'parametros' de la tarea y devuelve el resultado.
Son corrutinas: los workers las ejecutan con await.
"""

import asyncio
from typing import Any


async def esperar(parametros: dict[str, Any]) -> str:
    """I/O simulado: duerme N segundos SIN bloquear el event loop."""
    segundos = parametros["segundos"]
    await asyncio.sleep(segundos)
    return f"esperé {segundos} segundos"


# Despacho: tipo de tarea -> función que la ejecuta.
# Cuando agreguemos descargar y hashear, solo hay que sumarlas acá.
EJECUTORES = {
    "esperar": esperar,
}
