"""Tests de cancelación, timeouts y limpieza de recursos."""

import asyncio

from app import estado as registro
from tests.conftest import crear, esperar_estado


async def cerrar_servidor(srv: asyncio.AbstractServer) -> None:
    """wait_closed() espera a que se cierren TODAS las conexiones: si una quedó abierta
    (justo el bug que estos tests buscan), se colgaría para siempre. Con límite, el test
    falla rápido y con un mensaje claro en vez de quedarse trabado."""
    srv.close()
    try:
        await asyncio.wait_for(srv.wait_closed(), timeout=1)
    except asyncio.TimeoutError:
        pass


async def test_cancelar_tarea_pendiente(cliente):
    # El único worker queda ocupado con la primera, así que la segunda espera en la cola
    larga = await crear(cliente, "esperar", {"segundos": 30})
    await esperar_estado(cliente, larga, "ejecutando")
    pendiente = await crear(cliente, "esperar", {"segundos": 30})

    r = await cliente.delete(f"/tareas/{pendiente}")

    assert r.status_code == 200
    t = r.json()
    assert t["estado"] == "cancelada"
    assert t["iniciada_en"] is None  # nunca llegó a ejecutarse


async def test_cancelar_tarea_en_ejecucion(cliente):
    id = await crear(cliente, "esperar", {"segundos": 30})
    await esperar_estado(cliente, id, "ejecutando")

    r = await cliente.delete(f"/tareas/{id}")

    t = r.json()
    assert t["estado"] == "cancelada"
    assert t["error"] == "Cancelada"
    assert t["finalizada_en"] is not None
    # Y el registro del asyncio.Task quedó limpio (nada colgado)
    assert registro.obtener_asyncio_task(id) is None


async def test_el_worker_sigue_vivo_despues_de_cancelar(cliente):
    id = await crear(cliente, "esperar", {"segundos": 30})
    await esperar_estado(cliente, id, "ejecutando")
    await cliente.delete(f"/tareas/{id}")

    # Hay un solo worker: si la cancelación lo hubiera matado, esta nunca se ejecutaría
    siguiente = await crear(cliente, "esperar", {"segundos": 0.05})
    t = await esperar_estado(cliente, siguiente, "completada")
    assert t["resultado"] == "esperé 0.05 segundos"


async def test_cancelar_tarea_ya_terminada_da_409(cliente):
    id = await crear(cliente, "esperar", {"segundos": 0.01})
    await esperar_estado(cliente, id, "completada")

    r = await cliente.delete(f"/tareas/{id}")

    assert r.status_code == 409


async def test_cancelar_tarea_inexistente_da_404(cliente):
    r = await cliente.delete("/tareas/no-existe")
    assert r.status_code == 404


async def test_timeout_deja_la_tarea_fallida_con_el_motivo(cliente):
    id = await crear(cliente, "esperar", {"segundos": 30}, timeout=0.1)

    t = await esperar_estado(cliente, id, "fallida")

    assert "Timeout" in t["error"]


async def test_cancelar_una_descarga_cierra_la_conexion(cliente):
    """El caso del profe: cancelar a mitad de una descarga no deja conexiones abiertas."""
    conexion_cerrada = asyncio.Event()

    async def servidor_mudo(reader, writer):
        # Acepta la conexión y nunca responde. read() devuelve b"" recién cuando el
        # cliente cierra su lado: ahí sabemos que la conexión se liberó.
        await reader.read()
        conexion_cerrada.set()
        writer.close()

    servidor = await asyncio.start_server(servidor_mudo, "127.0.0.1", 0)
    puerto = servidor.sockets[0].getsockname()[1]
    try:
        id = await crear(cliente, "descargar", {"urls": [f"http://127.0.0.1:{puerto}/"]})
        await esperar_estado(cliente, id, "ejecutando")
        await asyncio.sleep(0.2)  # dar tiempo a que la descarga abra la conexión

        r = await cliente.delete(f"/tareas/{id}")

        assert r.json()["estado"] == "cancelada"
        await asyncio.wait_for(conexion_cerrada.wait(), timeout=2)
    finally:
        await cerrar_servidor(servidor)


async def test_la_tarea_cancelada_figura_como_cancelada_para_asyncio(cliente, monkeypatch):
    """ejecutar() tiene que RELANZAR CancelledError: si la tragara, el asyncio.Task
    terminaría 'normal' y task.cancelled() sería False."""
    capturadas = []
    original = registro.registrar_asyncio_task

    def espiar(id, task):
        capturadas.append(task)
        original(id, task)

    monkeypatch.setattr(registro, "registrar_asyncio_task", espiar)

    id = await crear(cliente, "esperar", {"segundos": 30})
    await esperar_estado(cliente, id, "ejecutando")
    await cliente.delete(f"/tareas/{id}")

    assert len(capturadas) == 1
    assert capturadas[0].cancelled()


async def test_cancelar_una_descarga_libera_tambien_las_conexiones_ociosas(cliente):
    """Dos URLs: /rapido responde enseguida (su conexión queda abierta, ociosa, en el
    pool del cliente) y /mudo nunca responde. Al cancelar, tienen que cerrarse AMBAS."""
    cerradas = {"rapido": asyncio.Event(), "mudo": asyncio.Event()}

    async def servidor(reader, writer):
        pedido = (await reader.readuntil(b"\r\n\r\n")).decode()
        nombre = "rapido" if "GET /rapido" in pedido else "mudo"
        if nombre == "rapido":
            writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nok")
            await writer.drain()
        await reader.read()           # espera hasta que el cliente cierre su lado
        cerradas[nombre].set()
        writer.close()

    srv = await asyncio.start_server(servidor, "127.0.0.1", 0)
    puerto = srv.sockets[0].getsockname()[1]
    try:
        base = f"http://127.0.0.1:{puerto}"
        id = await crear(cliente, "descargar", {"urls": [f"{base}/rapido", f"{base}/mudo"]})
        await esperar_estado(cliente, id, "ejecutando")
        await asyncio.sleep(0.3)      # /rapido ya respondió; /mudo sigue colgada

        await cliente.delete(f"/tareas/{id}")

        for evento in cerradas.values():
            await asyncio.wait_for(evento.wait(), timeout=2)
    finally:
        await cerrar_servidor(srv)
