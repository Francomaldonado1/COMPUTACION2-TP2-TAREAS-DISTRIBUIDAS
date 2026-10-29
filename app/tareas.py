"""
tareas.py — La lógica real de cada tipo de tarea.

Cada función recibe el dict 'parametros' de la tarea y devuelve el resultado.
Son corrutinas: los workers las ejecutan con await.
"""

import asyncio
import hashlib
import time
from concurrent.futures import ProcessPoolExecutor
from typing import Any

import httpx

# Pool de procesos para las tareas CPU-bound. Se crea una sola vez al arrancar
# (ver main.py) y se reutiliza: crear un pool por pedido sería carísimo.
_pool: ProcessPoolExecutor | None = None


def iniciar_pool(max_workers: int) -> None:
    global _pool
    _pool = ProcessPoolExecutor(max_workers=max_workers)


def cerrar_pool() -> None:
    if _pool is not None:
        _pool.shutdown(wait=False, cancel_futures=True)


async def esperar(parametros: dict[str, Any]) -> str:
    """I/O simulado: duerme N segundos SIN bloquear el event loop."""
    segundos = parametros["segundos"]
    await asyncio.sleep(segundos)
    return f"esperé {segundos} segundos"


def _hash_iterado(texto: str, iteraciones: int) -> str:
    """CPU puro: SHA-256 aplicado N veces. Va a nivel módulo para que se pueda
    enviar a otro proceso (pickle no puede serializar funciones locales)."""
    h = texto.encode()
    for _ in range(iteraciones):
        h = hashlib.sha256(h).digest()
    return h.hex()


async def hashear(parametros: dict[str, Any]) -> str:
    """CPU-bound: se delega a un proceso aparte para NO bloquear el event loop."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(
        _pool, _hash_iterado, parametros["texto"], parametros["iteraciones"]
    )


async def _descargar_una(cliente: httpx.AsyncClient, url: str) -> dict[str, Any]:
    """Descarga una URL y devuelve código de estado, tamaño y tiempo.
    Si falla, devuelve el error en vez de romper las demás descargas."""
    inicio = time.perf_counter()
    try:
        respuesta = await cliente.get(url)
        return {
            "url": url,
            "status": respuesta.status_code,
            "bytes": len(respuesta.content),
            "tiempo_s": round(time.perf_counter() - inicio, 3),
        }
    except httpx.HTTPError as e:
        return {
            "url": url,
            "error": f"{type(e).__name__}: {e}",
            "tiempo_s": round(time.perf_counter() - inicio, 3),
        }


async def descargar(parametros: dict[str, Any]) -> list[dict[str, Any]]:
    """I/O-bound: todas las URLs a la vez. Tarda lo que la más lenta, no la suma."""
    urls = parametros["urls"]
    if not isinstance(urls, list) or not urls:
        raise ValueError("'urls' debe ser una lista no vacía")
    # 'async with' cierra las conexiones aunque nos cancelen a mitad de camino.
    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as cliente:
        return await asyncio.gather(*(_descargar_una(cliente, u) for u in urls))


# Despacho: tipo de tarea -> función que la ejecuta.
EJECUTORES = {
    "esperar": esperar,
    "hashear": hashear,
    "descargar": descargar,
}
