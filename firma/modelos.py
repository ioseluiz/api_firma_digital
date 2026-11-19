"""Contrato JSON de la API (pydantic v2). Es lo que Power Automate manda y recibe."""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator


class Firmante(BaseModel):
    nombre: str = Field(min_length=1, max_length=120, description="Como aparece en el sello: 'Ing. Juan Pérez'")
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
    si_no_hay_ancla: Literal["posicion_fija", "error"] = Field(
        default="posicion_fija",
        description="Qué hacer si se pidió ancla y no aparece: usar la posición fija o responder 422",
    )


class Sello(BaseModel):
    texto: bool = True
    codigo_verificacion: bool = True


class FirmaRequest(BaseModel):
    solicitud_id: int = Field(ge=0)
    codigo: str = Field(min_length=1, max_length=40, pattern=r"^[A-Za-z0-9_.:/-]+$")
    pdf_base64: str = Field(min_length=1)
    firmante: Firmante
    fecha_firma: str = Field(description="Hora local de Panamá en ISO 8601, p. ej. 2026-11-18T14:05:12")
    posicion: Posicion = Field(default_factory=Posicion)
    sello: Sello = Field(default_factory=Sello)

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
