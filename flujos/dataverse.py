"""Acceso mínimo a la Web API de Dataverse con el token de `az` (sin SDK, solo urllib).

Uso desde los otros scripts:
    from dataverse import Dataverse
    dv = Dataverse()                      # lee DATAVERSE_URL o usa IN_DevTest
    dv.get("workflows", params={"$select": "name", "$filter": "category eq 5"})
    dv.patch("workflows(<id>)", {"clientdata": "..."})
"""

from __future__ import annotations

import json
import os
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Optional

ORG_DEVTEST = "https://org76e0c9a2.crm.dynamics.com"
AZ = os.path.join(os.environ.get("USERPROFILE", ""), ".venv-azcli", "Scripts", "az.bat")


def token_para(recurso: str) -> str:
    az = AZ if os.path.exists(AZ) else "az"
    r = subprocess.run(
        [az, "account", "get-access-token", "--resource", recurso, "--query", "accessToken", "-o", "tsv"],
        capture_output=True, text=True, shell=(az == "az"),
    )
    if r.returncode != 0 or not r.stdout.strip():
        raise SystemExit(f"No se pudo obtener el token de az para {recurso}: {r.stderr.strip()[:300]}\n"
                         "¿Hiciste `az login`? (" + az + ")")
    return r.stdout.strip()


class Dataverse:
    def __init__(self, org_url: Optional[str] = None) -> None:
        self.org = (org_url or os.environ.get("DATAVERSE_URL") or ORG_DEVTEST).rstrip("/")
        self.base = f"{self.org}/api/data/v9.2/"
        self._token = token_para(self.org)

    def _req(self, metodo: str, ruta: str, cuerpo: Any = None, params: Optional[dict] = None, extra: Optional[dict] = None) -> Any:
        url = self.base + ruta
        if params:
            url += "?" + urllib.parse.urlencode(params, quote_via=urllib.parse.quote, safe="$,()'/=")
        datos = json.dumps(cuerpo, ensure_ascii=False).encode("utf-8") if cuerpo is not None else None
        cab = {
            "Authorization": f"Bearer {self._token}",
            "Accept": "application/json",
            "OData-MaxVersion": "4.0",
            "OData-Version": "4.0",
        }
        if datos is not None:
            cab["Content-Type"] = "application/json; charset=utf-8"
        if extra:
            cab.update(extra)
        req = urllib.request.Request(url, data=datos, method=metodo, headers=cab)
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                texto = resp.read().decode("utf-8")
                return json.loads(texto) if texto else {"_status": resp.status, "_headers": dict(resp.headers)}
        except urllib.error.HTTPError as e:
            detalle = e.read().decode("utf-8", errors="replace")
            try:
                detalle = json.dumps(json.loads(detalle).get("error", {}), ensure_ascii=False, indent=1)
            except Exception:
                pass
            raise SystemExit(f"{metodo} {ruta} -> HTTP {e.code}\n{detalle[:3000]}") from None

    def get(self, ruta: str, params: Optional[dict] = None) -> Any:
        return self._req("GET", ruta, params=params)

    def patch(self, ruta: str, cuerpo: dict, extra: Optional[dict] = None) -> Any:
        return self._req("PATCH", ruta, cuerpo, extra={"If-Match": "*", **(extra or {})})

    def post(self, ruta: str, cuerpo: dict, extra: Optional[dict] = None) -> Any:
        return self._req("POST", ruta, cuerpo, extra=extra)

    def delete(self, ruta: str) -> Any:
        return self._req("DELETE", ruta)

    def quien_soy(self) -> dict:
        return self.get("WhoAmI")
