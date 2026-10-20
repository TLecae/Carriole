"""HttpRequest : appelle une API web pour chaque entité (en parallèle, débit limité, reprises)."""

from __future__ import annotations

import re
import threading
import time
from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Literal
from urllib.parse import quote

import httpx
import pyarrow as pa
import pyarrow.compute as pc
from pydantic import Field

from etl_engine.data import get_crs, with_crs
from etl_engine.sdk import ConfigError, Modifier, Params

_PLACEHOLDER = re.compile(r"\{(\w+)\}")
MAX_BODY = 5_000_000  # octets de réponse conservés par appel


class Header(Params):
    name: str = Field("", title="En-tête", description="Nom, ex. Authorization.")
    value: str = Field("", title="Valeur", description="Valeur ; {attribut} et ${env:NOM} (pour un jeton) acceptés.")


class RateLimiter:
    """Au plus `per_second` appels par seconde, tous threads confondus."""

    def __init__(self, per_second: float) -> None:
        self.interval = 1.0 / per_second if per_second > 0 else 0.0
        self.lock = threading.Lock()
        self.next_time = 0.0

    def wait(self) -> None:
        if not self.interval:
            return
        with self.lock:
            now = time.monotonic()
            delay = max(0.0, self.next_time - now)
            self.next_time = max(now, self.next_time) + self.interval
        if delay:
            time.sleep(delay)


class HttpRequest(Modifier):
    name = "HttpRequest"
    reusable = False  # toujours relancé (voir Modifier.reusable)
    label = "Appeler une API web"
    category = "Entrées / sorties"
    description = "Appelle une adresse web (GET, POST…) pour chaque entité et range la réponse dans un attribut."
    help = (
        "L'adresse, les en-têtes et le corps peuvent citer des attributs de l'entité avec {attribut} (dans l'adresse, la "
        "valeur est encodée). Exemple : https://api-adresse.data.gouv.fr/search/?q={adresse}&limit=1. Les entités dont la "
        "réponse a un code inférieur à 400 sortent sur « output » avec le corps de la réponse (attribut `_http_body`) "
        "et le code (`_http_status`) ; les autres sortent sur « Failed » avec `_http_error`. Les appels se font en "
        "parallèle (« Appels simultanés ») avec un débit maximal par seconde, et sont repris en cas d'erreur réseau, de "
        "code 429 ou 5xx. Le proxy du poste (HTTPS_PROXY) est utilisé. Pour lire le JSON reçu : « Extraire du JSON »."
    )
    outputs = ("output", "Failed")
    rejected = False
    stateful = True

    class Params(Params):
        url: str = Field(
            "",
            title="Adresse",
            description="http:// ou https:// ; {attribut} pour une valeur de l'entité.",
            examples=["https://exemple.fr/api?code={code}"],
        )
        method: Literal["GET", "POST", "PUT", "PATCH", "DELETE"] = Field(
            "GET", title="Méthode", description="Méthode HTTP."
        )
        headers: list[Header] = Field(
            default_factory=list, title="En-têtes", description="En-têtes ajoutés à chaque appel."
        )
        body: str = Field("", title="Corps", description="Corps de la requête (POST, PUT, PATCH) ; {attribut} accepté.")
        body_attribute: str = Field(
            "_http_body", title="Attribut de la réponse", description="Reçoit le corps de la réponse (texte)."
        )
        status_attribute: str = Field(
            "_http_status", title="Attribut du code", description="Reçoit le code HTTP (vide pour les erreurs réseau)."
        )
        timeout: float = Field(30.0, gt=0, le=600, title="Délai (s)", description="Délai maximal d'un appel.")
        retries: int = Field(
            2,
            ge=0,
            le=10,
            title="Reprises",
            description="Nouvelles tentatives après une erreur réseau, un code 429 ou 5xx.",
        )
        concurrency: int = Field(4, ge=1, le=32, title="Appels simultanés", description="Nombre d'appels en parallèle.")
        rate: float = Field(0.0, ge=0, title="Débit maximal (appels/s)", description="0 = pas de limite.")

    def _check(self) -> None:
        p = self.params
        if not p.url.strip():
            raise ConfigError("adresse non renseignée")
        if not re.match(r"^https?://", p.url.strip(), re.IGNORECASE):
            raise ConfigError("l'adresse doit commencer par http:// ou https://")
        if p.body_attribute == p.status_attribute:
            raise ConfigError("les attributs de la réponse et du code doivent différer")

    def used_attributes(self) -> list[str]:
        p = self.params
        texts = [p.url, p.body, *[h.value for h in p.headers]]
        return sorted({name for t in texts for name in _PLACEHOLDER.findall(t)})

    def output_schema(self, inputs: Mapping[str, pa.Schema | None]) -> dict[str, pa.Schema | None]:
        self._check()
        schema = inputs.get("input")
        if schema is None:
            return {"output": None, "Failed": None}
        missing = [n for n in self.used_attributes() if n not in schema.names]
        if missing:
            raise ConfigError(f"attributs absents : {', '.join(missing)}")
        p = self.params
        ok = schema
        for field in (pa.field(p.body_attribute, pa.string()), pa.field(p.status_attribute, pa.int64())):
            ok = ok.set(ok.get_field_index(field.name), field) if field.name in ok.names else ok.append(field)
        return {"output": ok, "Failed": ok.append(pa.field("_http_error", pa.string()))}

    @staticmethod
    def _fill(template: str, row: Mapping[str, Any], encode: bool) -> str:
        def repl(m: re.Match[str]) -> str:
            value = row.get(m.group(1))
            text = "" if value is None else str(value)
            return quote(text, safe="") if encode else text

        return _PLACEHOLDER.sub(repl, template)

    def _call(
        self, client: httpx.Client, limiter: RateLimiter, row: Mapping[str, Any]
    ) -> tuple[int | None, str | None, str | None]:
        """(code, corps, erreur) d'un appel, avec reprises."""
        p = self.params
        url = self._fill(p.url, row, encode=True)
        headers = {h.name: self._fill(h.value, row, encode=False) for h in p.headers if h.name}
        body = self._fill(p.body, row, encode=False) if p.body else None
        error = ""
        for attempt in range(p.retries + 1):
            self.ctx.check_cancel()
            limiter.wait()
            try:
                with client.stream(p.method, url, headers=headers, content=body) as response:
                    chunks, size = [], 0
                    for chunk in response.iter_bytes():
                        chunks.append(chunk)
                        size += len(chunk)
                        if size > MAX_BODY:
                            break
                    text = b"".join(chunks)[:MAX_BODY].decode(response.encoding or "utf-8", errors="replace")
                    code = response.status_code
                if code == 429 or code >= 500:
                    error = f"HTTP {code}"
                    if attempt < p.retries:
                        time.sleep(min(2**attempt, 10))
                        continue
                return code, text, (None if code < 400 else f"HTTP {code}")
            except httpx.HTTPError as exc:
                error = f"{type(exc).__name__}: {exc}"
                if attempt < p.retries:
                    time.sleep(min(2**attempt, 10))
        return None, None, error

    def process(self, batch: pa.Table) -> dict[str, pa.Table]:
        self._check()
        p = self.params
        missing = [n for n in self.used_attributes() if n not in batch.column_names]
        if missing:
            raise ConfigError(f"attributs absents : {', '.join(missing)}")
        names = sorted({*self.used_attributes()})
        rows = batch.select(names).to_pylist() if names else [{}] * batch.num_rows
        limiter = RateLimiter(p.rate)
        with (
            httpx.Client(timeout=p.timeout, follow_redirects=True) as client,
            ThreadPoolExecutor(p.concurrency) as pool,
        ):
            results = list(pool.map(lambda r: self._call(client, limiter, r), rows))
        codes = pa.array([r[0] for r in results], pa.int64())
        bodies = pa.array([r[1] for r in results], pa.string())
        errors = pa.array([r[2] for r in results], pa.string())
        out = batch
        for name, column in ((p.body_attribute, bodies), (p.status_attribute, codes)):
            out = (
                out.set_column(out.column_names.index(name), name, column)
                if name in out.column_names
                else out.append_column(name, column)
            )
        out = with_crs(out, get_crs(batch))
        ok = pa.array([e is None for e in errors.to_pylist()])
        result: dict[str, pa.Table] = {}
        good, bad = out.filter(ok), out.filter(pc.invert(ok))
        if good.num_rows:
            result["output"] = good
        if bad.num_rows:
            result["Failed"] = with_crs(bad.append_column("_http_error", errors.filter(pc.invert(ok))), get_crs(batch))
        return result
