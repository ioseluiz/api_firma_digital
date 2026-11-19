"""Errores del servicio. Cada uno sabe con qué HTTP y con qué código se responde."""


class ErrorFirma(Exception):
    http = 500
    codigo = "error_interno"

    def __init__(self, mensaje: str):
        super().__init__(mensaje)
        self.mensaje = mensaje


class PeticionInvalida(ErrorFirma):
    http = 400
    codigo = "peticion_invalida"


class PdfInvalido(ErrorFirma):
    http = 400
    codigo = "pdf_invalido"


class PaginaFueraDeRango(ErrorFirma):
    http = 400
    codigo = "pagina_fuera_de_rango"


class ImagenNoEncontrada(ErrorFirma):
    http = 404
    codigo = "imagen_no_encontrada"


class PdfDemasiadoGrande(ErrorFirma):
    http = 413
    codigo = "pdf_demasiado_grande"


class AnclaNoEncontrada(ErrorFirma):
    http = 422
    codigo = "ancla_no_encontrada"
