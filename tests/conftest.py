"""
Fixtures de los tests.

La app se levanta COMPLETA (con su lifespan: cola y workers) dentro de cada test y
se le habla por HTTP en memoria, sin abrir ningún puerto.
"""

import asyncio
import os

# Un solo worker: así es fácil dejar tareas 'pendientes' (el worker está ocupado).
# Tiene que fijarse ANTES de importar la app, que lee la variable al importarse.
os.environ["NUM_WORKERS"] = "1"

import httpx
import pytest_asyncio

from app import estado as registro
from app import main, persistencia


@pytest_asyncio.fixture
async def cliente(tmp_path, monkeypatch):
    monkeypatch.setattr(persistencia, "DIRECTORIO", tmp_path)  # el volcado va a un tmp
    monkeypatch.setattr(main, "GRACE_APAGADO", 0.2)            # que el apagado no tarde
    registro.tareas.clear()
    registro._asyncio_tasks.clear()

    # ASGITransport NO ejecuta el lifespan: lo corremos nosotros a mano
    async with main.app.router.lifespan_context(main.app):
        transporte = httpx.ASGITransport(app=main.app)
        async with httpx.AsyncClient(transport=transporte, base_url="http://test") as c:
            yield c


async def crear(cliente: httpx.AsyncClient, tipo: str, parametros: dict, **extra) -> str:
    """POST /tareas y devuelve el id."""
    r = await cliente.post("/tareas", json={"tipo": tipo, "parametros": parametros, **extra})
    assert r.status_code == 201
    return r.json()["id"]


async def esperar_estado(cliente: httpx.AsyncClient, id: str, estado: str, limite: float = 3):
    """Consulta la tarea hasta que llegue al estado pedido (o falla el test)."""
    async def sondear():
        while True:
            t = (await cliente.get(f"/tareas/{id}")).json()
            if t["estado"] == estado:
                return t
            await asyncio.sleep(0.02)
    return await asyncio.wait_for(sondear(), timeout=limite)
