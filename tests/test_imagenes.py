import pytest

from firma.errores import ImagenNoEncontrada
from firma.imagenes import OrigenLocal, origen_desde_entorno


def test_origen_local_lee_y_cachea(carpeta_firmas, imagen_firma):
    o = OrigenLocal(carpeta_firmas)
    assert o.obtener("gerente.png") == imagen_firma
    (carpeta_firmas / "gerente.png").write_bytes(b"cambiado")
    assert o.obtener("gerente.png") == imagen_firma, "en caché durante el TTL"


def test_origen_local_no_existe(carpeta_firmas):
    with pytest.raises(ImagenNoEncontrada):
        OrigenLocal(carpeta_firmas).obtener("suplente.png")


@pytest.mark.parametrize("malo", ["", "../gerente.png", "sub/gerente.png", "gerente png", "..\\x.png"])
def test_origen_local_rechaza_ids_con_ruta(carpeta_firmas, malo):
    with pytest.raises(ImagenNoEncontrada):
        OrigenLocal(carpeta_firmas).obtener(malo)


def test_origen_desde_entorno_local(carpeta_firmas, monkeypatch):
    monkeypatch.setenv("FIRMAS_ORIGEN", "local")
    monkeypatch.setenv("FIRMAS_CARPETA", str(carpeta_firmas))
    o = origen_desde_entorno()
    assert o.nombre == "local" and o.obtener("gerente.png")


def test_origen_desde_entorno_desconocido(monkeypatch):
    monkeypatch.setenv("FIRMAS_ORIGEN", "ftp")
    with pytest.raises(RuntimeError):
        origen_desde_entorno()
