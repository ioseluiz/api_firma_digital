"""Pruebas de las funciones HTTP llamándolas directamente con azure.functions.HttpRequest."""

import base64
import json

import azure.functions as func
import pymupdf


def peticion(cuerpo, ruta="v1/firmar", metodo="POST", headers=None) -> func.HttpRequest:
    datos = json.dumps(cuerpo).encode("utf-8") if not isinstance(cuerpo, (bytes, bytearray)) else cuerpo
    return func.HttpRequest(
        method=metodo, url=f"http://localhost/api/{ruta}", body=datos,
        headers=headers or {"Content-Type": "application/json"},
    )


def cuerpo_valido(pdf: bytes, **extra) -> dict:
    c = {
        "solicitud_id": 123,
        "codigo": "FIRMA-2026-000123",
        "pdf_base64": base64.b64encode(pdf).decode("ascii"),
        "firmante": {"nombre": "Ing. Juan Pérez", "cargo": "Gerente", "email": "jperez@pancanal.com", "imagen_id": "gerente.png"},
        "fecha_firma": "2026-11-18T14:05:12",
        "posicion": {"pagina": 0, "x_mm": 120, "y_mm": 40, "ancho_mm": 50, "ancla_texto": ""},
        "sello": {"texto": True, "codigo_verificacion": True},
    }
    c.update(extra)
    return c


def test_salud_ok(app_local):
    resp = app_local.salud.build().get_user_function()(peticion(b"", "v1/salud", "GET"))
    assert resp.status_code == 200
    datos = json.loads(resp.get_body())
    assert datos["ok"] is True and datos["origen_imagenes"] == "local" and datos["imagen_por_defecto_ok"] is True


def test_salud_sin_imagen_por_defecto_responde_503(app_local, monkeypatch):
    monkeypatch.setenv("FIRMAS_IMAGEN_POR_DEFECTO", "no-existe.png")
    resp = app_local.salud.build().get_user_function()(peticion(b"", "v1/salud", "GET"))
    assert resp.status_code == 503


def test_firmar_ok(app_local, pdf_con_ancla):
    req = peticion(cuerpo_valido(pdf_con_ancla, posicion={"pagina": 0, "ancla_texto": "Firma del Gerente:"}),
                   headers={"Content-Type": "application/json", "X-Correlation-Id": "corr-1"})
    resp = app_local.firmar.build().get_user_function()(req)
    assert resp.status_code == 200, resp.get_body()
    assert resp.headers["X-Correlation-Id"] == "corr-1"
    datos = json.loads(resp.get_body())
    assert datos["ok"] is True
    assert datos["codigo"] == "FIRMA-2026-000123"
    assert datos["paginas"] == 3 and datos["pagina_firmada"] == 3 and datos["ancla_encontrada"] is True
    assert len(datos["sha256"]) == 64 and len(datos["codigo_verificacion"]) == 12
    pdf = base64.b64decode(datos["pdf_firmado_base64"])
    doc = pymupdf.open(stream=pdf, filetype="pdf")
    assert len(doc[2].get_images()) == 1
    assert "Ing. Juan Pérez" in doc[2].get_text()


def test_firmar_peticion_minima_solo_imagen(app_local, pdf_1_pagina):
    """Sin nombre, sin cargo, sin sello: lo mínimo que el flujo necesita mandar."""
    minima = {
        "solicitud_id": 1, "codigo": "FIRMA-2026-000001",
        "pdf_base64": base64.b64encode(pdf_1_pagina).decode("ascii"),
        "firmante": {"imagen_id": "gerente.png"},
        "fecha_firma": "2026-11-18T14:05:12",
    }
    texto_antes = pymupdf.open(stream=pdf_1_pagina, filetype="pdf")[0].get_text()
    resp = app_local.firmar.build().get_user_function()(peticion(minima))
    assert resp.status_code == 200, resp.get_body()
    datos = json.loads(resp.get_body())
    page = pymupdf.open(stream=base64.b64decode(datos["pdf_firmado_base64"]), filetype="pdf")[0]
    assert len(page.get_images()) == 1
    assert page.get_text() == texto_antes
    assert datos["firmante"] == ""


def test_firmar_devuelve_pdf_binario_si_se_pide_por_accept(app_local, pdf_con_ancla):
    req = peticion(cuerpo_valido(pdf_con_ancla, posicion={"ancla_texto": "Firma del Gerente:"}),
                   headers={"Content-Type": "application/json", "Accept": "application/pdf"})
    resp = app_local.firmar.build().get_user_function()(req)
    assert resp.status_code == 200
    assert resp.mimetype == "application/pdf"
    cuerpo = resp.get_body()
    assert cuerpo.startswith(b"%PDF")
    assert resp.headers["X-Sha256"] == __import__("hashlib").sha256(cuerpo).hexdigest()
    assert resp.headers["X-Pagina-Firmada"] == "3" and resp.headers["X-Ancla-Encontrada"] == "true"
    assert 'FIRMA-2026-000123_firmado.pdf' in resp.headers["Content-Disposition"]
    assert len(pymupdf.open(stream=cuerpo, filetype="pdf")[2].get_images()) == 1


def test_errores_siguen_siendo_json_aunque_se_pida_pdf(app_local, pdf_1_pagina):
    c = cuerpo_valido(pdf_1_pagina)
    c["firmante"]["imagen_id"] = "nadie.png"
    req = peticion(c, headers={"Content-Type": "application/json", "Accept": "application/pdf"})
    resp = app_local.firmar.build().get_user_function()(req)
    assert resp.status_code == 404
    assert json.loads(resp.get_body())["error"] == "imagen_no_encontrada"


def test_firmar_acepta_objeto_de_contenido_de_power_automate(app_local, pdf_1_pagina):
    """`{"$content-type": ..., "$content": ...}` es lo que produce interpolar contentBytes en un flujo."""
    c = cuerpo_valido(pdf_1_pagina)
    c["pdf_base64"] = {"$content-type": "application/pdf", "$content": base64.b64encode(pdf_1_pagina).decode("ascii")}
    resp = app_local.firmar.build().get_user_function()(peticion(c))
    assert resp.status_code == 200, resp.get_body()


def test_firmar_acepta_data_uri_y_saltos_de_linea(app_local, pdf_1_pagina):
    b64 = base64.b64encode(pdf_1_pagina).decode("ascii")
    con_saltos = "\n".join(b64[i:i + 76] for i in range(0, len(b64), 76))
    for valor in (f"data:application/pdf;base64,{b64}", con_saltos, f"  {b64}  "):
        resp = app_local.firmar.build().get_user_function()(peticion(cuerpo_valido(pdf_1_pagina, pdf_base64=valor)))
        assert resp.status_code == 200, resp.get_body()


def test_firmar_acepta_base64_doble(app_local, pdf_1_pagina):
    """`base64(triggerBody()?['file']?['contentBytes'])` en el desencadenador manual codifica dos veces."""
    b64 = base64.b64encode(pdf_1_pagina).decode("ascii")
    doble = base64.b64encode(b64.encode("ascii")).decode("ascii")
    con_saltos = base64.b64encode("\r\n".join(b64[i:i + 76] for i in range(0, len(b64), 76)).encode("ascii")).decode("ascii")
    for valor in (doble, con_saltos):
        resp = app_local.firmar.build().get_user_function()(peticion(cuerpo_valido(pdf_1_pagina, pdf_base64=valor)))
        assert resp.status_code == 200, resp.get_body()
        datos = json.loads(resp.get_body())
        assert base64.b64decode(datos["pdf_firmado_base64"]).startswith(b"%PDF")


def test_firmar_acepta_base64_del_objeto_binario(app_local, pdf_1_pagina):
    """`base64(contentBytes)` sobre el objeto binario puede codificar su JSON `{"$content-type", "$content"}`."""
    b64 = base64.b64encode(pdf_1_pagina).decode("ascii")
    objeto = json.dumps({"$content-type": "application/octet-stream", "$content": b64})
    valor = base64.b64encode(objeto.encode("utf-8")).decode("ascii")
    resp = app_local.firmar.build().get_user_function()(peticion(cuerpo_valido(pdf_1_pagina, pdf_base64=valor)))
    assert resp.status_code == 200, resp.get_body()
    assert base64.b64decode(json.loads(resp.get_body())["pdf_firmado_base64"]).startswith(b"%PDF")


def test_firmar_base64_de_objeto_sin_content_es_pdf_invalido(app_local, pdf_1_pagina):
    valor = base64.b64encode(b'{"nombre": "x.pdf"}').decode("ascii")
    resp = app_local.firmar.build().get_user_function()(peticion(cuerpo_valido(pdf_1_pagina, pdf_base64=valor)))
    assert resp.status_code == 400
    assert json.loads(resp.get_body())["error"] == "pdf_invalido"


def test_firmar_base64_doble_con_interior_roto_es_pdf_invalido(app_local, pdf_1_pagina):
    interior = "JVBERi0xLjcN%%%roto%%%"
    valor = base64.b64encode(interior.encode("ascii")).decode("ascii")
    resp = app_local.firmar.build().get_user_function()(peticion(cuerpo_valido(pdf_1_pagina, pdf_base64=valor)))
    assert resp.status_code == 400
    assert json.loads(resp.get_body())["error"] == "pdf_invalido"


def test_firmar_pdf_decodificado_explica_el_error(app_local, pdf_1_pagina):
    """Interpolar `contentBytes` en una cadena de Power Automate manda el PDF decodificado (empieza por %PDF)."""
    crudo = pdf_1_pagina.decode("utf-8", errors="replace")
    resp = app_local.firmar.build().get_user_function()(peticion(cuerpo_valido(pdf_1_pagina, pdf_base64=crudo)))
    assert resp.status_code == 400
    datos = json.loads(resp.get_body())
    assert datos["error"] == "peticion_invalida"
    assert "base64(" in datos["mensaje"]


def test_firmar_objeto_sin_content_es_peticion_invalida(app_local, pdf_1_pagina):
    resp = app_local.firmar.build().get_user_function()(peticion(cuerpo_valido(pdf_1_pagina, pdf_base64={"nombre": "x.pdf"})))
    assert resp.status_code == 400
    assert json.loads(resp.get_body())["error"] == "peticion_invalida"


def test_firmar_cuerpo_no_json(app_local):
    req = peticion(b"esto no es json")
    resp = app_local.firmar.build().get_user_function()(req)
    assert resp.status_code == 400
    assert json.loads(resp.get_body())["error"] == "peticion_invalida"


def test_firmar_campos_faltantes(app_local):
    resp = app_local.firmar.build().get_user_function()(peticion({"codigo": "X"}))
    assert resp.status_code == 400
    datos = json.loads(resp.get_body())
    assert datos["error"] == "peticion_invalida"
    assert "pdf_base64" in datos["mensaje"] and "firmante" in datos["mensaje"]


def test_firmar_fecha_invalida(app_local, pdf_1_pagina):
    resp = app_local.firmar.build().get_user_function()(peticion(cuerpo_valido(pdf_1_pagina, fecha_firma="ayer")))
    assert resp.status_code == 400
    assert "fecha_firma" in json.loads(resp.get_body())["mensaje"]


def test_firmar_base64_invalido(app_local, pdf_1_pagina):
    resp = app_local.firmar.build().get_user_function()(peticion(cuerpo_valido(pdf_1_pagina, pdf_base64="%%%no-base64%%%")))
    assert resp.status_code == 400
    assert json.loads(resp.get_body())["error"] == "pdf_invalido"


def test_firmar_pdf_cifrado(app_local, pdf_cifrado):
    resp = app_local.firmar.build().get_user_function()(peticion(cuerpo_valido(pdf_cifrado)))
    assert resp.status_code == 400
    assert json.loads(resp.get_body())["error"] == "pdf_invalido"


def test_firmar_pagina_fuera_de_rango(app_local, pdf_1_pagina):
    resp = app_local.firmar.build().get_user_function()(peticion(cuerpo_valido(pdf_1_pagina, posicion={"pagina": 9})))
    assert resp.status_code == 400
    assert json.loads(resp.get_body())["error"] == "pagina_fuera_de_rango"


def test_firmar_imagen_no_encontrada(app_local, pdf_1_pagina):
    c = cuerpo_valido(pdf_1_pagina)
    c["firmante"]["imagen_id"] = "suplente.png"
    resp = app_local.firmar.build().get_user_function()(peticion(c))
    assert resp.status_code == 404
    assert json.loads(resp.get_body())["error"] == "imagen_no_encontrada"


def test_firmar_imagen_id_con_ruta_se_rechaza(app_local, pdf_1_pagina):
    c = cuerpo_valido(pdf_1_pagina)
    c["firmante"]["imagen_id"] = "../gerente.png"
    resp = app_local.firmar.build().get_user_function()(peticion(c))
    assert resp.status_code == 400  # lo para el modelo (patrón), antes de tocar disco


def test_firmar_ancla_ausente_modo_error(app_local, pdf_1_pagina):
    c = cuerpo_valido(pdf_1_pagina, posicion={"ancla_texto": "No existe", "si_no_hay_ancla": "error"})
    resp = app_local.firmar.build().get_user_function()(peticion(c))
    assert resp.status_code == 422
    assert json.loads(resp.get_body())["error"] == "ancla_no_encontrada"


def test_firmar_pdf_demasiado_grande(app_local, pdf_1_pagina, monkeypatch):
    monkeypatch.setenv("FIRMA_MAX_PDF_MB", "0")
    resp = app_local.firmar.build().get_user_function()(peticion(cuerpo_valido(pdf_1_pagina)))
    assert resp.status_code == 413
    assert json.loads(resp.get_body())["error"] == "pdf_demasiado_grande"


def test_texto_del_sello_configurable(app_local, pdf_1_pagina, monkeypatch):
    monkeypatch.setenv("FIRMA_TEXTO_SELLO", "Aprobado por {nombre} el {fecha} [{codigo}/{verif}]")
    resp = app_local.firmar.build().get_user_function()(peticion(cuerpo_valido(pdf_1_pagina)))
    datos = json.loads(resp.get_body())
    doc = pymupdf.open(stream=base64.b64decode(datos["pdf_firmado_base64"]), filetype="pdf")
    assert "Aprobado por Ing. Juan Pérez el 18/11/2026 14:05" in doc[0].get_text()
