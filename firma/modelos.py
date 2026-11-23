"""Contrato JSON de la API (pydantic v2). Es lo que Power Automate manda y recibe."""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator


class Firmante(BaseModel):
    nombre: str = Field(default="", max_length=120, description="Solo se usa si sello.texto es true: 'Ing. Juan Pérez'")
    cargo: str = Field(default="", max_length=160)
    email: str = Field(default="", max_length=160)
    imagen_id: str = Field(
        min_length=1,
        max_length=80,
        pattern=r"^[A-Za-z0-9_.-]+$",
        description="Nombre del archivo de firma en el origen (p. ej. 'gerente.png'). Sin rutas.",
    )


class Posicion(BaseModel):
    pagina: int = Field(default=0, ge=0, description="0 = última página; n = página n (base 1)")
    x_mm: float = Field(default=120.0, ge=0, description="Desde el borde izquierdo, en mm")
    y_mm: float = Field(default=40.0, ge=0, description="Desde el borde inferior hasta la base de la imagen, en mm")
    ancho_mm: float = Field(default=50.0, gt=0, le=200)
    ancla_texto: str = Field(default="", max_length=120, description="Texto a buscar en la página; si aparece, la firma se coloca sobre él")
    ancla_alineacion: Literal["centro", "izquierda"] = Field(
        default="centro",
        description="'centro': la firma se centra sobre el rótulo (línea con el cargo debajo); 'izquierda': alineada al borde izquierdo del rótulo ('Firma: ____')",
    )
    si_no_hay_ancla: Literal["posicion_fija", "error"] = Field(
        default="posicion_fija",
        description="Qué hacer si se pidió ancla y no aparece: usar la posición fija o responder 422",
    )


class Sello(BaseModel):
    """Por defecto NO se escribe nada: solo la imagen, incrustada en el contenido de la página."""

    texto: bool = Field(default=False, description="Escribir el sello de texto bajo la firma")
    codigo_verificacion: bool = Field(default=True, description="Incluir el código de verificación en el sello (si texto es true)")


class FirmaRequest(BaseModel):
    solicitud_id: int = Field(ge=0)
    codigo: str = Field(min_length=1, max_length=40, pattern=r"^[A-Za-z0-9_.:/-]+$")
    pdf_base64: str = Field(min_length=1)
    firmante: Firmante
    fecha_firma: str = Field(description="Hora local de Panamá en ISO 8601, p. ej. 2026-11-18T14:05:12")
    posicion: Posicion = Field(default_factory=Posicion)
    sello: Sello = Field(default_factory=Sello)

    @field_validator("pdf_base64", mode="before")
    @classmethod
    def _normalizar_base64(cls, v):
        """Acepta lo que mandan los conectores de Power Automate, no solo la cadena limpia:

        - el objeto de contenido binario `{"$content-type": "...", "$content": "<base64>"}`
          (lo que produce interpolar `contentBytes` o el cuerpo de «Obtener contenido del archivo»);
        - un data URI `data:application/pdf;base64,<base64>`;
        - base64 con saltos de línea o espacios.
        """
        if isinstance(v, dict):
            contenido = v.get("$content")
            if not isinstance(contenido, str):
                raise ValueError("el objeto de pdf_base64 no trae '$content' con el archivo en base64")
            v = contenido
        if not isinstance(v, str):
            raise ValueError("pdf_base64 debe ser una cadena base64 (o el objeto {'$content': ...})")
        v = v.strip()
        if v.startswith("data:"):
            coma = v.find(",")
            v = v[coma + 1:] if coma >= 0 else ""
        if v.startswith("%PDF"):
            raise ValueError(
                "pdf_base64 trae el PDF decodificado, no en base64. En Power Automate interpolar un archivo "
                "dentro de una cadena lo decodifica: use base64(triggerBody()?['Documento']?['contentBytes']) "
                "o base64(body('Obtener_contenido_del_archivo'))"
            )
        return "".join(v.split())

    @field_validator("fecha_firma")
    @classmethod
    def _fecha_valida(cls, v: str) -> str:
        try:
            datetime.fromisoformat(v)
        except ValueError as e:
            raise ValueError(f"fecha_firma no es ISO 8601: {v!r}") from e
        return v

    def fecha(self) -> datetime:
        return datetime.fromisoformat(self.fecha_firma)


class FirmaResponse(BaseModel):
    ok: Literal[True] = True
    codigo: str
    pdf_firmado_base64: str
    sha256: str = Field(description="SHA-256 del archivo firmado tal como se devuelve")
    codigo_verificacion: str = Field(description="12 caracteres estampados en el sello; derivados del original + código + fecha")
    paginas: int
    pagina_firmada: int
    ancla_encontrada: Optional[bool] = Field(default=None, description="null si no se pidió ancla")
    firmante: str
    fecha_firma: str
    duracion_ms: int


class ErrorResponse(BaseModel):
    ok: Literal[False] = False
    error: str
    mensaje: str
    correlation_id: Optional[str] = None


class SaludResponse(BaseModel):
    ok: bool
    version: str
    origen_imagenes: str
    imagen_por_defecto: str
    imagen_por_defecto_ok: bool
    hora_servidor_utc: str
