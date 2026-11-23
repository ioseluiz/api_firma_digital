"""Visor: copia temporal del PDF original en un contenedor privado de Blob, servida con una URL SAS.

El control PDF de Power Apps no abre URLs autenticadas de SharePoint. F01 manda aquí el original,
se guarda en el contenedor `visor` y se devuelve una URL firmada de solo lectura que el control sí
puede cargar. Cuando la solicitud termina, F02 borra el blob; la política de ciclo de vida del
contenedor borra lo que quede.

Firma del SAS: la cuenta tiene `allowSharedKeyAccess = false`, así que no hay clave de cuenta. Se usa
una **clave de delegación de usuario** obtenida con la identidad administrada de la Function, cuyo
tope en Azure es de 7 días: `VISOR_DIAS` se recorta a ese máximo.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional, Protocol
from urllib.parse import quote, urlsplit

from .errores import VisorNoDisponible, VisorNoEncontrado

log = logging.getLogger("ini.firma.visor")

MAX_DIAS = 7  # tope de Azure para la clave de delegación de usuario
TOKEN_LARGO = 32


def token_para(codigo: str, secreto: str) -> str:
    """Token estable por código: HMAC-SHA256(secreto, código) recortado. Hace que la URL corta no sea adivinable."""
    return hmac.new(secreto.encode("utf-8"), codigo.strip().encode("utf-8"), hashlib.sha256).hexdigest()[:TOKEN_LARGO]


def token_valido(codigo: str, token: str, secreto: str) -> bool:
    return bool(secreto) and hmac.compare_digest(token_para(codigo, secreto), (token or "").strip().lower())


def url_corta(base_api: str, codigo: str, secreto: str) -> str:
    """`https://host/api/v1/visor/<codigo>/abrir/<token>`: cabe en una columna de 255 y redirige a un SAS fresco."""
    return f"{base_api.rstrip('/')}/visor/{quote(codigo, safe='')}/abrir/{token_para(codigo, secreto)}"


_NO_VALIDO = re.compile(r"[^A-Za-z0-9_.-]+")


def nombre_blob(codigo: str) -> str:
    """`FIRMA-2026-000012` -> `FIRMA-2026-000012.pdf`; cualquier otro carácter se vuelve guion."""
    limpio = _NO_VALIDO.sub("-", codigo.strip()).strip("-.")
    if not limpio:
        raise VisorNoEncontrado("Código vacío o inválido para el visor.")
    return f"{limpio}.pdf"


@dataclass(frozen=True)
class ResultadoVisor:
    url: str
    vence_utc: datetime
    blob: str
    bytes: int
    dias: int


class AlmacenVisor(Protocol):
    nombre: str

    def publicar(self, codigo: str, pdf: bytes) -> ResultadoVisor: ...

    def renovar(self, codigo: str) -> ResultadoVisor: ...

    def leer(self, codigo: str) -> bytes: ...

    def borrar(self, codigo: str) -> bool: ...


class AlmacenVisorNinguno:
    """Sin visor configurado (desarrollo local sin Azure): el API responde 503 en /visor."""

    nombre = "ninguno"

    def publicar(self, codigo: str, pdf: bytes) -> ResultadoVisor:
        raise VisorNoDisponible("El visor no está configurado en este entorno (VISOR_ORIGEN=ninguno).")

    def renovar(self, codigo: str) -> ResultadoVisor:
        raise VisorNoDisponible("El visor no está configurado en este entorno (VISOR_ORIGEN=ninguno).")

    def leer(self, codigo: str) -> bytes:
        raise VisorNoDisponible("El visor no está configurado en este entorno (VISOR_ORIGEN=ninguno).")

    def borrar(self, codigo: str) -> bool:
        raise VisorNoDisponible("El visor no está configurado en este entorno (VISOR_ORIGEN=ninguno).")


class AlmacenVisorBlob:
    nombre = "blob"

    def __init__(self, cuenta_url: str, contenedor: str, dias: int, cliente=None) -> None:
        if cliente is None:
            # Importación perezosa: en pruebas se inyecta un doble y no hace falta el SDK cargado.
            from azure.identity import DefaultAzureCredential
            from azure.storage.blob import BlobServiceClient

            cliente = BlobServiceClient(cuenta_url, credential=DefaultAzureCredential())
        self._cliente = cliente
        self._cuenta_url = cuenta_url.rstrip("/")
        self._cuenta = urlsplit(self._cuenta_url).hostname.split(".")[0]
        self._contenedor = contenedor
        if dias > MAX_DIAS:
            log.warning("VISOR_DIAS=%d supera el tope de %d días de la clave de delegación; se usa %d", dias, MAX_DIAS, MAX_DIAS)
        self._dias = max(1, min(int(dias), MAX_DIAS))

    def publicar(self, codigo: str, pdf: bytes) -> ResultadoVisor:
        from azure.storage.blob import ContentSettings

        blob = nombre_blob(codigo)
        bc = self._cliente.get_blob_client(self._contenedor, blob)
        bc.upload_blob(
            pdf,
            overwrite=True,
            content_settings=ContentSettings(
                content_type="application/pdf",
                content_disposition=f'inline; filename="{blob}"',
            ),
        )
        return self._firmar(blob, len(pdf))

    def renovar(self, codigo: str) -> ResultadoVisor:
        blob = nombre_blob(codigo)
        bc = self._cliente.get_blob_client(self._contenedor, blob)
        if not bc.exists():
            raise VisorNoEncontrado(f"No hay visor publicado para {codigo!r}.")
        tamano = int(getattr(bc.get_blob_properties(), "size", 0) or 0)
        return self._firmar(blob, tamano)

    def leer(self, codigo: str) -> bytes:
        """Los bytes del original, para servirlos directamente desde la ruta anónima del visor."""
        from azure.core.exceptions import ResourceNotFoundError

        bc = self._cliente.get_blob_client(self._contenedor, nombre_blob(codigo))
        try:
            return bc.download_blob().readall()
        except ResourceNotFoundError:
            raise VisorNoEncontrado(f"No hay visor publicado para {codigo!r}.") from None

    def borrar(self, codigo: str) -> bool:
        from azure.core.exceptions import ResourceNotFoundError

        blob = nombre_blob(codigo)
        bc = self._cliente.get_blob_client(self._contenedor, blob)
        try:
            bc.delete_blob()
        except ResourceNotFoundError:
            return False
        return True

    def _firmar(self, blob: str, tamano: int) -> ResultadoVisor:
        from azure.storage.blob import BlobSasPermissions, generate_blob_sas

        ahora = datetime.now(timezone.utc).replace(microsecond=0)
        inicio = ahora - timedelta(minutes=5)  # desfase de reloj entre servicios
        vence = ahora + timedelta(days=self._dias)
        clave = self._cliente.get_user_delegation_key(inicio, vence)
        sas = generate_blob_sas(
            account_name=self._cuenta,
            container_name=self._contenedor,
            blob_name=blob,
            user_delegation_key=clave,
            permission=BlobSasPermissions(read=True),
            expiry=vence,
            start=inicio,
            protocol="https",
            content_type="application/pdf",
            content_disposition=f'inline; filename="{blob}"',
        )
        url = f"{self._cuenta_url}/{self._contenedor}/{quote(blob)}?{sas}"
        return ResultadoVisor(url=url, vence_utc=vence, blob=blob, bytes=tamano, dias=self._dias)


def almacen_desde_entorno() -> AlmacenVisor:
    origen = os.getenv("VISOR_ORIGEN", "ninguno").strip().lower()
    if origen == "ninguno":
        return AlmacenVisorNinguno()
    if origen == "blob":
        cuenta_url = os.getenv("FIRMAS_STORAGE_URL", "").strip()
        if not cuenta_url:
            raise RuntimeError("VISOR_ORIGEN=blob exige FIRMAS_STORAGE_URL.")
        contenedor = os.getenv("VISOR_CONTENEDOR", "visor").strip() or "visor"
        dias = int(os.getenv("VISOR_DIAS", str(MAX_DIAS)))
        return AlmacenVisorBlob(cuenta_url, contenedor, dias)
    raise RuntimeError(f"VISOR_ORIGEN desconocido: {origen!r} (valores: blob, ninguno)")
