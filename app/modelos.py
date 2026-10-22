"""
modelos.py — Define la estructura de datos de una Tarea.

Usamos Pydantic (que ya viene con FastAPI) para definir los modelos.
Pydantic valida automáticamente los tipos y serializa a JSON.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


class EstadoTarea(str, Enum):
    """Estados posibles del ciclo de vida de una tarea."""
    PENDIENTE  = "pendiente"
    EJECUTANDO = "ejecutando"
    COMPLETADA = "completada"
    FALLIDA    = "fallida"
    CANCELADA  = "cancelada"


class TipoTarea(str, Enum):
    """Tipos de tarea que el sistema sabe ejecutar."""
    ESPERAR   = "esperar"
    DESCARGAR = "descargar"
    HASHEAR   = "hashear"


class TareaRequest(BaseModel):
    """
    Lo que el cliente manda en el POST /tareas.
    'parametros' es un dict flexible porque cada tipo de tarea
    necesita datos distintos:
      - esperar:   {"segundos": 5}
      - descargar: {"urls": ["http://...", ...]}
      - hashear:   {"texto": "abc", "iteraciones": 100000}
    """
    tipo:       TipoTarea
    parametros: dict[str, Any]
    timeout:    Optional[float] = None   # segundos; None = sin límite


class Tarea(BaseModel):
    """
    Representación interna de una tarea.
    Esta es la que vive en el diccionario de estado y se devuelve en los GET.
    """
    id:           str            = Field(default_factory=lambda: str(uuid.uuid4()))
    tipo:         TipoTarea
    estado:       EstadoTarea    = EstadoTarea.PENDIENTE
    parametros:   dict[str, Any]
    resultado:    Optional[Any]  = None   # lo que devuelve la tarea al completarse
    error:        Optional[str]  = None   # motivo si falla o se cancela
    timeout:      Optional[float]= None

    # Timestamps para medir tiempos (los necesitamos para GET /estadisticas)
    creada_en:    datetime       = Field(default_factory=datetime.now)
    iniciada_en:  Optional[datetime] = None
    finalizada_en:Optional[datetime] = None

    class Config:
        # Permite que el modelo conviva con tipos no-Pydantic en el futuro
        use_enum_values = True
