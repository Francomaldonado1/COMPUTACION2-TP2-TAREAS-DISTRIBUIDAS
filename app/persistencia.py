"""
persistencia.py — Volcado del estado a JSON al apagar, y reporte al arrancar.

El estado vive en memoria; esto es lo único que sobrevive a un reinicio.
"""

import json
import logging
import os
from collections import Counter
from datetime import datetime
from pathlib import Path

from app import estado as registro

# Usamos el logger de uvicorn para que los mensajes salgan junto a los suyos.
log = logging.getLogger("uvicorn.error")

# Carpeta de los volcados. Configurable para poder montarla como volumen en Docker.
DIRECTORIO = Path(os.getenv("DIRECTORIO_ESTADO", "."))


def volcar_estado() -> Path:
    """Escribe todas las tareas en estado_<timestamp>.json y devuelve la ruta."""
    ahora = datetime.now()
    ruta = DIRECTORIO / f"estado_{ahora:%Y%m%d_%H%M%S}.json"
    datos = {
        "volcado_en": ahora.isoformat(),
        "tareas": [t.model_dump(mode="json") for t in registro.listar_tareas()],
    }
    ruta.write_text(json.dumps(datos, ensure_ascii=False, indent=2), encoding="utf-8")
    return ruta


def reportar_volcado_previo() -> None:
    """Si hay un volcado de una ejecución anterior, lo informa por log."""
    archivos = sorted(DIRECTORIO.glob("estado_*.json"))
    if not archivos:
        log.info("No hay volcado previo de estado")
        return
    ultimo = archivos[-1]
    try:
        datos = json.loads(ultimo.read_text(encoding="utf-8"))
        conteo = Counter(t["estado"] for t in datos["tareas"])
        log.info(
            "Volcado previo encontrado: %s (%d tareas: %s)",
            ultimo.name, len(datos["tareas"]), dict(conteo),
        )
    except (OSError, ValueError, KeyError) as e:
        log.warning("Volcado previo ilegible (%s): %s", ultimo.name, e)
