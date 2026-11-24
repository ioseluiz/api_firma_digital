"""Contrato JSON de la API (pydantic v2). Es lo que Power Automate manda y recibe."""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import model_validator, BaseModel, Field, field_validator


def normalizar_base64(v):
    """Acepta lo que mandan los conectores de Power Automate, no solo la cadena limpia:

    - el objeto de contenido binario `{"$content-type": "...", "$content": "<base64>"}`
      (lo que produce interpolar `contentBytes` o el cuerpo de «Obtener contenido del archivo»);
    - un data URI `data:application/pdf;base64,<base64>`;
    - base64 con saltos de línea o espacios.

    El base64 doble (base64 de base64, cuando el flujo aplica `base64()` a un `contentBytes`
    que ya era base64) se resuelve al decodificar, en `function_app._decodificar_pdf`.
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


CODIGO_PATRON = r"^[A-Za-z0-9_.:/-]+$"


class Firmante(BaseModel):
    nombre: str = Field(default="", max_length=120, description="Solo se usa si sello.texto es true: 'Ing. Juan Pérez'")
    cargo: str = Field(default="", max_length=160)
    email: str = Field(default="", max_length=160)
    imagen_id: str = Field(
        default="",
        max_length=80,
        pattern=r"^[A-Za-z0-9_.-]*$",
        description="Nombre del archivo de firma en el origen (p. ej. 'gerente.png'). Sin rutas. Obligatorio si no viene imagen_base64.",
    )
    imagen_base64: str = Field(
        default="",
        max_length=4_000_000,
        description="La imagen de firma (PNG o JPEG) en base64, tal como la guarda el gerente en la app (adjunto de FIRMA_Roles). Si viene, manda sobre imagen_id.",
    )

    @field_validator("imagen_base64", mode="before")
    @classmethod
    def _normalizar_imagen(cls, v):
        if v is None or v == "":
            return ""
        return normalizar_base64(v)

    @model_validator(mode="after")
    def _alguna_imagen(self):
        if not self.imagen_base64 and not self.imagen_id:
            raise ValueError("Hace falta firmante.imagen_id o firmante.imagen_base64.")
        return self


def lienzo_a_tupla(lienzo: Optional[str]) -> Optional[tuple[float, float]]:
    """'880x640' → (880.0, 640.0); None o vacío → None."""
    if not lienzo:
        return None
    a, b = lienzo.lower().split("x")
    return float(a), float(b)


class Posicion(BaseModel):
    pagina: int = Field(default=0, ge=0, description="0 = última página; n = página n (base 1)")
    x_mm: float = Field(default=120.0, ge=0, description="Desde el borde izquierdo, en mm")
    y_mm: float = Field(default=40.0, ge=0, description="Desde el borde inferior hasta la base de la imagen, en mm")
    ancho_mm: float = Field(default=50.0, gt=0, le=200)
    x_pct: Optional[float] = Field(default=None, ge=0, le=100, description="Centro de la firma como % del ancho de la página desde la izquierda (clic del gerente). Con y_pct, manda sobre ancla y posición fija")
    y_pct: Optional[float] = Field(default=None, ge=0, le=100, description="Centro de la firma como % del alto de la página desde arriba")
    lienzo: Optional[str] = Field(default=None, pattern=r"^[1-9]\d{0,4}x[1-9]\d{0,4}$", description="'AnchoxAlto' del lienzo en que la app mostró la página (GET /visor/{codigo}/pagina/{n}/{token}?lienzo=AnchoxAlto). Si viene, x_pct/y_pct son % de ese lienzo")
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
    codigo: str = Field(min_length=1, max_length=40, pattern=CODIGO_PATRON)
    pdf_base64: str = Field(min_length=1)
    firmante: Firmante
    fecha_firma: str = Field(description="Hora local de Panamá en ISO 8601, p. ej. 2026-11-18T14:05:12")
    posicion: Posicion = Field(default_factory=Posicion)
    sello: Sello = Field(default_factory=Sello)

    @field_validator("pdf_base64", mode="before")
    @classmethod
    def _normalizar_base64(cls, v):
        return normalizar_base64(v)

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
    firma_mm: Optional[dict[str, float]] = Field(default=None, description="Dónde quedó la imagen en la página firmada: x, y (esquina superior izquierda, desde arriba a la izquierda), ancho, alto, en mm")
    firmante: str
    fecha_firma: str
    duracion_ms: int


class VisorRequest(BaseModel):
    """Lo que F01 manda para publicar el original en el visor."""

    solicitud_id: int = Field(ge=0)
    codigo: str = Field(min_length=1, max_length=40, pattern=CODIGO_PATRON)
    pdf_base64: str = Field(min_length=1)

    @field_validator("pdf_base64", mode="before")
    @classmethod
    def _normalizar_base64(cls, v):
        return normalizar_base64(v)


class VisorResponse(BaseModel):
    ok: Literal[True] = True
    codigo: str
    url_visor: str = Field(description="URL corta y estable (.../visor/{codigo}/abrir/{token}) que redirige a un SAS fresco; cabe en 255 caracteres. Sin VISOR_SECRETO es la SAS directa")
    url_sas: str = Field(description="URL SAS de solo lectura generada en esta llamada (vence en dias_vigencia); para diagnóstico")
    vence_utc: str
    blob: str
    bytes: int
    dias_vigencia: int


class PaginaInfo(BaseModel):
    numero: int
    ancho_mm: float
    alto_mm: float


class VisorInfoResponse(BaseModel):
    ok: Literal[True] = True
    codigo: str
    paginas: int
    tamanos: list[PaginaInfo]


class VisorBorradoResponse(BaseModel):
    ok: Literal[True] = True
    codigo: str
    borrado: bool = Field(description="false si no había nada que borrar; nunca es un error")


class ErrorResponse(BaseModel):
    ok: Literal[False] = False
    error: str
    mensaje: str
    correlation_id: Optional[str] = None


class SaludResponse(BaseModel):
    ok: bool
    version: str
    origen_imagenes: str
    visor: str = Field(default="ninguno", description="blob | ninguno")
    imagen_por_defecto: str
    imagen_por_defecto_ok: bool
    hora_servidor_utc: str
