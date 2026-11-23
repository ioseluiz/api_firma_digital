"""Pruebas del visor: /api/v1/visor con un doble del cliente de Blob (sin red, SAS firmado de verdad)."""

import base64
import json
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlsplit

import pytest
from azure.core.exceptions import ResourceNotFoundError
from azure.storage.blob import UserDelegationKey

from firma.visor import MAX_DIAS, AlmacenVisorBlob, AlmacenVisorNinguno, nombre_blob, token_para, url_corta
from tests.test_api import cuerpo_valido, peticion


class FalsoBlobClient:
    def __init__(self, almacen: dict, nombre: str) -> None:
        self._almacen = almacen
        self._nombre = nombre

    def upload_blob(self, data, overwrite=False, content_settings=None, **_):
        if self._nombre in self._almacen and not overwrite:
            raise RuntimeError("ya existe")
        self._almacen[self._nombre] = {"data": bytes(data), "content_settings": content_settings}

    def exists(self) -> bool:
        return self._nombre in self._almacen

    def get_blob_properties(self):
        class P:
            size = len(self._almacen[self._nombre]["data"]) if self._nombre in self._almacen else 0
        return P()

    def delete_blob(self):
        if self._nombre not in self._almacen:
            raise ResourceNotFoundError("no existe")
        del self._almacen[self._nombre]

    def download_blob(self):
        if self._nombre not in self._almacen:
            raise ResourceNotFoundError("no existe")
        datos = self._almacen[self._nombre]["data"]

        class D:
            def readall(self_inner):
                return datos
        return D()


class FalsoBlobServiceClient:
    """Imita lo que `AlmacenVisorBlob` usa: clave de delegación y clientes de blob."""

    def __init__(self) -> None:
        self.blobs: dict[str, dict] = {}
        self.claves_pedidas: list[tuple[datetime, datetime]] = []

    def get_user_delegation_key(self, start, expiry):
        self.claves_pedidas.append((start, expiry))
        fmt = "%Y-%m-%dT%H:%M:%SZ"
        clave = UserDelegationKey()
        clave.signed_oid = "00000000-0000-0000-0000-000000000001"
        clave.signed_tid = "00000000-0000-0000-0000-000000000002"
        clave.signed_start = start.strftime(fmt)
        clave.signed_expiry = expiry.strftime(fmt)
        clave.signed_service = "b"
        clave.signed_version = "2024-11-04"
        clave.value = base64.b64encode(b"\x01" * 32).decode("ascii")
        return clave

    def get_blob_client(self, container, blob):
        assert container == "visor"
        return FalsoBlobClient(self.blobs, blob)


@pytest.fixture
def blob_falso() -> FalsoBlobServiceClient:
    return FalsoBlobServiceClient()


@pytest.fixture
def app_visor(app_local, blob_falso, monkeypatch):
    """`app_local` con el visor apuntando al doble de Blob."""
    import function_app

    almacen = AlmacenVisorBlob("https://cuenta.blob.core.windows.net/", "visor", 7, cliente=blob_falso)
    monkeypatch.setattr(function_app, "almacen_desde_entorno", lambda: almacen)
    function_app.reiniciar_visor()
    yield function_app
    function_app.reiniciar_visor()


def _publicar(app, pdf, **extra):
    cuerpo = {"solicitud_id": 12, "codigo": "FIRMA-2026-000012", "pdf_base64": base64.b64encode(pdf).decode("ascii")}
    cuerpo.update(extra)
    return app.visor_publicar.build().get_user_function()(peticion(cuerpo, "v1/visor"))


def _ruta(app, codigo, metodo):
    import azure.functions as func

    req = func.HttpRequest(method=metodo, url=f"http://localhost/api/v1/visor/{codigo}", body=b"",
                           headers={}, route_params={"codigo": codigo})
    return app.visor_codigo.build().get_user_function()(req)


def test_publicar_devuelve_sas_de_lectura(app_visor, blob_falso, pdf_1_pagina):
    resp = _publicar(app_visor, pdf_1_pagina)
    assert resp.status_code == 200, resp.get_body()
    d = json.loads(resp.get_body())
    assert d["ok"] is True and d["codigo"] == "FIRMA-2026-000012"
    assert d["blob"] == "FIRMA-2026-000012.pdf" and d["bytes"] == len(pdf_1_pagina) and d["dias_vigencia"] == 7
    partes = urlsplit(d["url_visor"])
    assert partes.scheme == "https" and partes.path == "/visor/FIRMA-2026-000012.pdf"
    q = parse_qs(partes.query)
    assert q["sp"] == ["r"] and q["spr"] == ["https"] and "sig" in q and q["rsct"] == ["application/pdf"]
    assert "skoid" in q  # firmado con clave de delegación de usuario, no con clave de cuenta
    vence = datetime.fromisoformat(d["vence_utc"])
    assert timedelta(days=6, hours=23) < vence - datetime.now(timezone.utc) <= timedelta(days=7)
    guardado = blob_falso.blobs["FIRMA-2026-000012.pdf"]
    assert guardado["data"].startswith(b"%PDF") and guardado["content_settings"].content_type == "application/pdf"


def test_publicar_acepta_las_mismas_formas_que_firmar(app_visor, pdf_1_pagina):
    b64 = base64.b64encode(pdf_1_pagina).decode("ascii")
    doble = base64.b64encode(b64.encode("ascii")).decode("ascii")
    objeto = {"$content-type": "application/pdf", "$content": b64}
    for valor in (doble, objeto, f"data:application/pdf;base64,{b64}"):
        resp = _publicar(app_visor, pdf_1_pagina, pdf_base64=valor)
        assert resp.status_code == 200, resp.get_body()


def test_publicar_rechaza_lo_que_no_es_pdf(app_visor):
    resp = _publicar(app_visor, b"hola, no soy un PDF")
    assert resp.status_code == 400
    assert json.loads(resp.get_body())["error"] == "pdf_invalido"


def test_publicar_rechaza_pdf_grande(app_visor, pdf_1_pagina, monkeypatch):
    monkeypatch.setenv("FIRMA_MAX_PDF_MB", "0")
    resp = _publicar(app_visor, pdf_1_pagina)
    assert resp.status_code == 413


def test_publicar_sobrescribe_en_reintento(app_visor, blob_falso, pdf_1_pagina, pdf_con_ancla):
    assert _publicar(app_visor, pdf_1_pagina).status_code == 200
    assert _publicar(app_visor, pdf_con_ancla).status_code == 200
    assert blob_falso.blobs["FIRMA-2026-000012.pdf"]["data"] == pdf_con_ancla


def test_codigo_se_sanea_en_el_nombre_del_blob():
    assert nombre_blob("FIRMA-2026-000012") == "FIRMA-2026-000012.pdf"
    assert nombre_blob("a/b:c d") == "a-b-c-d.pdf"


def test_dias_se_recortan_al_tope_de_azure(blob_falso):
    almacen = AlmacenVisorBlob("https://cuenta.blob.core.windows.net", "visor", 45, cliente=blob_falso)
    r = almacen.publicar("X-1", b"%PDF-1.7 prueba")
    assert r.dias == MAX_DIAS
    inicio, vence = blob_falso.claves_pedidas[-1]
    assert vence - inicio <= timedelta(days=MAX_DIAS, minutes=5)


def test_borrar_existente_e_inexistente(app_visor, pdf_1_pagina):
    assert _publicar(app_visor, pdf_1_pagina).status_code == 200
    r1 = _ruta(app_visor, "FIRMA-2026-000012", "DELETE")
    assert r1.status_code == 200 and json.loads(r1.get_body())["borrado"] is True
    r2 = _ruta(app_visor, "FIRMA-2026-000012", "DELETE")
    assert r2.status_code == 200 and json.loads(r2.get_body())["borrado"] is False


def test_renovar_devuelve_sas_nuevo_o_404(app_visor, pdf_1_pagina):
    assert _ruta(app_visor, "FIRMA-2026-000012", "GET").status_code == 404
    assert _publicar(app_visor, pdf_1_pagina).status_code == 200
    resp = _ruta(app_visor, "FIRMA-2026-000012", "GET")
    assert resp.status_code == 200
    d = json.loads(resp.get_body())
    assert "sig=" in d["url_visor"] and d["bytes"] == len(pdf_1_pagina)


def test_codigo_de_ruta_invalido(app_visor):
    resp = _ruta(app_visor, "esto no vale", "GET")
    assert resp.status_code == 400


def test_sin_visor_configurado_responde_503(app_local, pdf_1_pagina, monkeypatch):
    monkeypatch.setenv("VISOR_ORIGEN", "ninguno")
    app_local.reiniciar_visor()
    resp = _publicar(app_local, pdf_1_pagina)
    assert resp.status_code == 503
    assert json.loads(resp.get_body())["error"] == "visor_no_disponible"
    assert isinstance(app_local.obtener_visor(), AlmacenVisorNinguno)


def test_salud_reporta_el_visor(app_visor):
    resp = app_visor.salud.build().get_user_function()(peticion(b"", "v1/salud", "GET"))
    assert resp.status_code == 200
    assert json.loads(resp.get_body())["visor"] == "blob"


def test_firmar_sigue_funcionando_con_visor(app_visor, pdf_1_pagina):
    resp = app_visor.firmar.build().get_user_function()(peticion(cuerpo_valido(pdf_1_pagina)))
    assert resp.status_code == 200


def _abrir(app, codigo, token, metodo="GET"):
    import azure.functions as func

    req = func.HttpRequest(method=metodo, url=f"http://localhost/api/v1/visor/{codigo}/abrir/{token}", body=b"",
                           headers={}, route_params={"codigo": codigo, "token": token})
    return app.visor_abrir.build().get_user_function()(req)


def test_con_secreto_la_url_visor_es_corta_y_estable(app_visor, pdf_1_pagina, monkeypatch):
    monkeypatch.setenv("VISOR_SECRETO", "clave-de-prueba")
    resp = _publicar(app_visor, pdf_1_pagina)
    assert resp.status_code == 200, resp.get_body()
    d = json.loads(resp.get_body())
    assert d["url_visor"] == f"http://localhost/api/v1/visor/FIRMA-2026-000012/abrir/{token_para('FIRMA-2026-000012', 'clave-de-prueba')}"
    assert len(d["url_visor"]) < 255
    assert "sig=" in d["url_sas"] and d["url_sas"].startswith("https://cuenta.blob.core.windows.net/visor/")
    # la URL corta no cambia entre llamadas (la SAS sí)
    d2 = json.loads(_ruta(app_visor, "FIRMA-2026-000012", "GET").get_body())
    assert d2["url_visor"] == d["url_visor"]


def test_abrir_sirve_el_pdf_con_cors(app_visor, pdf_1_pagina, monkeypatch):
    monkeypatch.setenv("VISOR_SECRETO", "clave-de-prueba")
    assert _publicar(app_visor, pdf_1_pagina).status_code == 200
    token = token_para("FIRMA-2026-000012", "clave-de-prueba")
    resp = _abrir(app_visor, "FIRMA-2026-000012", token)
    assert resp.status_code == 200 and resp.mimetype == "application/pdf"
    assert resp.get_body() == pdf_1_pagina
    assert resp.headers["Access-Control-Allow-Origin"] == "*"
    assert resp.headers["Content-Disposition"].startswith("inline;")
    head = _abrir(app_visor, "FIRMA-2026-000012", token, "HEAD")
    assert head.status_code == 200 and head.get_body() == b""
    pre = _abrir(app_visor, "FIRMA-2026-000012", token, "OPTIONS")
    assert pre.status_code == 204 and "GET" in pre.headers["Access-Control-Allow-Methods"]


def test_abrir_rechaza_token_malo_y_blob_inexistente(app_visor, pdf_1_pagina, monkeypatch):
    monkeypatch.setenv("VISOR_SECRETO", "clave-de-prueba")
    assert _publicar(app_visor, pdf_1_pagina).status_code == 200
    assert _abrir(app_visor, "FIRMA-2026-000012", "0" * 32).status_code == 404
    token = token_para("FIRMA-2026-000099", "clave-de-prueba")
    assert _abrir(app_visor, "FIRMA-2026-000099", token).status_code == 404


def test_abrir_sin_secreto_configurado_es_404(app_visor, pdf_1_pagina, monkeypatch):
    monkeypatch.delenv("VISOR_SECRETO", raising=False)
    assert _publicar(app_visor, pdf_1_pagina).status_code == 200
    assert _abrir(app_visor, "FIRMA-2026-000012", "x" * 32).status_code == 404


def _ruta_anonima(app, fn, codigo, token, extra=None):
    import azure.functions as func

    params = {"codigo": codigo, "token": token, **(extra or {})}
    req = func.HttpRequest(method="GET", url="http://localhost/api/v1/x", body=b"", headers={}, route_params=params)
    return getattr(app, fn).build().get_user_function()(req)


def test_info_y_pagina_png(app_visor, pdf_con_ancla, monkeypatch):
    monkeypatch.setenv("VISOR_SECRETO", "clave-de-prueba")
    assert _publicar(app_visor, pdf_con_ancla).status_code == 200
    token = token_para("FIRMA-2026-000012", "clave-de-prueba")
    info = _ruta_anonima(app_visor, "visor_info", "FIRMA-2026-000012", token)
    assert info.status_code == 200 and info.headers["Access-Control-Allow-Origin"] == "*"
    d = json.loads(info.get_body())
    assert d["paginas"] == 3 and abs(d["tamanos"][0]["ancho_mm"] - 210) < 1 and abs(d["tamanos"][0]["alto_mm"] - 297) < 1
    png = _ruta_anonima(app_visor, "visor_pagina", "FIRMA-2026-000012", token, {"numero": "3"})
    assert png.status_code == 200 and png.mimetype == "image/png" and png.get_body().startswith(b"\x89PNG")
    ultima = _ruta_anonima(app_visor, "visor_pagina", "FIRMA-2026-000012", token, {"numero": "0"})
    assert ultima.status_code == 200
    assert _ruta_anonima(app_visor, "visor_pagina", "FIRMA-2026-000012", token, {"numero": "9"}).status_code == 404
    assert _ruta_anonima(app_visor, "visor_pagina", "FIRMA-2026-000012", "0" * 32, {"numero": "1"}).status_code == 404


def test_url_corta_escapa_el_codigo():
    u = url_corta("https://h/api/v1", "A/B:C", "s")
    assert "/visor/A%2FB%3AC/abrir/" in u
