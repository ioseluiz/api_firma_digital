"""De dónde salen las imágenes de firma.

- `local`: una carpeta (desarrollo y pruebas). NUNCA se versiona.
- `blob`:  contenedor privado de Azure Blob Storage, leído con la identidad administrada de la
           Function (producción). Nadie más tiene acceso de lectura.

Se cachean en memoria unos minutos: cambian una vez cada varios años.
"""

from __future__ import annotations

import os
import re
import time
from pathlib import Path
from typing import Optional, Protocol

from .errores import ImagenNoEncontrada

_ID_VALIDO = re.compile(r"^[A-Za-z0-9_.-]+$")
_TTL_CACHE_S = 600


def _validar_id(imagen_id: str) -> str:
    if not imagen_id or not _ID_VALIDO.match(imagen_id) or ".." in imagen_id:
        raise ImagenNoEncontrada(f"Identificador de imagen inválido: {imagen_id!r}")
    return imagen_id


class OrigenImagenes(Protocol):
    nombre: str

    def obtener(self, imagen_id: str) -> bytes: ...


class _Cache:
    def __init__(self) -> None:
        self._datos: dict[str, tuple[float, bytes]] = {}

    def get(self, k: str) -> Optional[bytes]:
        v = self._datos.get(k)
        if v and time.monotonic() - v[0] < _TTL_CACHE_S:
            return v[1]
        return None

    def put(self, k: str, b: bytes) -> None:
        self._datos[k] = (time.monotonic(), b)


class OrigenLocal:
    nombre = "local"

    def __init__(self, carpeta: "str | os.PathLike[str]") -> None:
        self.carpeta = Path(carpeta).resolve()
        self._cache = _Cache()

    def obtener(self, imagen_id: str) -> bytes:
        imagen_id = _validar_id(imagen_id)
        b = self._cache.get(imagen_id)
        if b is not None:
            return b
        ruta = (self.carpeta / imagen_id).resolve()
        if ruta.parent != self.carpeta or not ruta.is_file():
            raise ImagenNoEncontrada(f"No existe la imagen de firma {imagen_id!r}.")
        b = ruta.read_bytes()
        self._cache.put(imagen_id, b)
        return b


class OrigenBlob:
    nombre = "blob"

    def __init__(self, cuenta_url: str, contenedor: str) -> None:
        # Importación perezosa: en pruebas locales no hace falta el SDK de Azure cargado.
        from azure.identity import DefaultAzureCredential
        from azure.storage.blob import ContainerClient

        self._contenedor = ContainerClient(cuenta_url, contenedor, credential=DefaultAzureCredential())
        self._cache = _Cache()

    def obtener(self, imagen_id: str) -> bytes:
        from azure.core.exceptions import ResourceNotFoundError

        imagen_id = _validar_id(imagen_id)
        b = self._cache.get(imagen_id)
        if b is not None:
            return b
        try:
            b = self._contenedor.download_blob(imagen_id).readall()
        except ResourceNotFoundError as e:
            raise ImagenNoEncontrada(f"No existe la imagen de firma {imagen_id!r} en el contenedor.") from e
        self._cache.put(imagen_id, b)
        return b


def origen_desde_entorno() -> OrigenImagenes:
    """Lee FIRMAS_ORIGEN (local|blob) y las variables que cada origen necesita."""
    tipo = os.getenv("FIRMAS_ORIGEN", "local").strip().lower()
    if tipo == "blob":
        url = os.environ["FIRMAS_STORAGE_URL"]          # https://<cuenta>.blob.core.windows.net
        contenedor = os.getenv("FIRMAS_CONTENEDOR", "firmas")
        return OrigenBlob(url, contenedor)
    if tipo == "local":
        return OrigenLocal(os.getenv("FIRMAS_CARPETA", "firmas_local"))
    raise RuntimeError(f"FIRMAS_ORIGEN desconocido: {tipo!r} (use 'local' o 'blob')")
