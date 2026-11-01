"""
Medición 1 — Concurrencia I/O: N descargas secuenciales contra N concurrentes.

Por defecto levanta un servidor local donde cada URL tarda 1 segundo, así el
resultado no depende de internet. Con --url se mide contra una URL real.

Uso (desde la raíz del repo):
    ./venv/bin/python mediciones/medicion_1_io.py
    ./venv/bin/python mediciones/medicion_1_io.py --url https://httpbin.org/delay/1
"""

import argparse
import asyncio
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx

PUERTO = 9100


class Lento(BaseHTTPRequestHandler):
    def do_GET(self):
        time.sleep(1)  # es un servidor con threads: acá sí se puede bloquear
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"x" * 1000)

    def log_message(self, *args):
        pass


class Servidor(ThreadingHTTPServer):
    # Por defecto el backlog de socketserver es 5: con 20 conexiones simultáneas,
    # las que sobran se reintentan al segundo y la medición sale inflada.
    request_queue_size = 64


async def secuencial(cliente: httpx.AsyncClient, urls: list[str]) -> None:
    for u in urls:
        await cliente.get(u)  # no pide la siguiente hasta terminar esta


async def concurrente(cliente: httpx.AsyncClient, urls: list[str]) -> None:
    await asyncio.gather(*(cliente.get(u) for u in urls))  # todas a la vez


async def main(n: int, url: str | None) -> None:
    if url is None:
        servidor = Servidor(("127.0.0.1", PUERTO), Lento)
        threading.Thread(target=servidor.serve_forever, daemon=True).start()
        url = f"http://127.0.0.1:{PUERTO}/"
    urls = [url] * n

    # max_connections alto: que el límite de conexiones de httpx no distorsione
    limites = httpx.Limits(max_connections=n)
    async with httpx.AsyncClient(timeout=60, limits=limites) as cliente:
        t0 = time.perf_counter()
        await secuencial(cliente, urls)
        t_sec = time.perf_counter() - t0

        t0 = time.perf_counter()
        await concurrente(cliente, urls)
        t_con = time.perf_counter() - t0

    print(f"{n} descargas")
    print(f"  secuencial (for + await): {t_sec:6.2f} s")
    print(f"  concurrente (gather):     {t_con:6.2f} s")
    print(f"  speedup:                  {t_sec / t_con:6.1f}x")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("-n", type=int, default=20)
    parser.add_argument("--url", default=None)
    args = parser.parse_args()
    asyncio.run(main(args.n, args.url))
