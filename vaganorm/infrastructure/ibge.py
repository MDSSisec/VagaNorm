from __future__ import annotations

import difflib
import csv
import ssl
from pathlib import Path

import requests
from requests.adapters import HTTPAdapter

from ..domain.normalizers import normalize_text
from .repository import LocalRepository


class IBGEUnavailable(ConnectionError):
    pass


class MunicipalityCatalogError(ValueError):
    pass


class LocalMunicipalityCatalog:
    REQUIRED_COLUMNS = {"SIGLA UF", "COD MUN", "NOME MUN"}

    def __init__(self, path: Path | None):
        self.path = path
        self._municipalities: dict[str, dict[str, tuple[str, str]]] | None = None

    def _load(self) -> dict[str, dict[str, tuple[str, str]]]:
        catalog: dict[str, dict[str, tuple[str, str]]] = {}
        if self.path is None or not self.path.is_file():
            return catalog
        try:
            with self.path.open("r", encoding="utf-8-sig", newline="") as handle:
                reader = csv.DictReader(handle, delimiter=";")
                columns = {str(column).strip() for column in (reader.fieldnames or [])}
                missing = self.REQUIRED_COLUMNS - columns
                if missing:
                    raise MunicipalityCatalogError(
                        f"Colunas ausentes no catálogo IBGE: {', '.join(sorted(missing))}."
                    )
                for line_number, row in enumerate(reader, start=2):
                    row = {str(key).strip(): value for key, value in row.items()}
                    uf = str(row.get("SIGLA UF") or "").strip().upper()
                    code = str(row.get("COD MUN") or "").strip()
                    display_name = str(row.get("NOME MUN") or "").strip()
                    if not uf or not display_name or len(code) != 7 or not code.isdigit():
                        raise MunicipalityCatalogError(
                            f"Registro inválido na linha {line_number} do catálogo IBGE."
                        )
                    catalog.setdefault(uf, {})[normalize_text(display_name)] = (display_name, code)
        except (OSError, UnicodeError, csv.Error) as exc:
            raise MunicipalityCatalogError(f"Não foi possível ler o catálogo IBGE local: {exc}") from exc
        return catalog

    def municipalities_for_uf(self, uf: str) -> dict[str, tuple[str, str]]:
        if self._municipalities is None:
            self._municipalities = self._load()
        return self._municipalities.get(uf.strip().upper(), {})


class SystemCertificateAdapter(HTTPAdapter):
    def __init__(self, *args, **kwargs):
        self.ssl_context = ssl.create_default_context()
        super().__init__(*args, **kwargs)

    def init_poolmanager(self, *args, **kwargs):
        kwargs["ssl_context"] = self.ssl_context
        return super().init_poolmanager(*args, **kwargs)

    def proxy_manager_for(self, proxy, **proxy_kwargs):
        proxy_kwargs["ssl_context"] = self.ssl_context
        return super().proxy_manager_for(proxy, **proxy_kwargs)


class IBGEClient:
    def __init__(
        self,
        repository: LocalRepository,
        catalog_path: Path | None = None,
        timeout: int = 10,
    ):
        self.repository = repository
        self.local_catalog = LocalMunicipalityCatalog(catalog_path)
        self.catalog_error: str | None = None
        self.timeout = timeout
        self.session = requests.Session()
        self.session.mount("https://", SystemCertificateAdapter())
        self._memory: dict[str, dict[str, tuple[str, str]]] = {}

    def municipalities(self, uf: str) -> dict[str, tuple[str, str]]:
        uf = uf.strip().upper()
        if uf in self._memory:
            return self._memory[uf]

        try:
            local = self.local_catalog.municipalities_for_uf(uf)
        except MunicipalityCatalogError as exc:
            self.catalog_error = str(exc)
            local = {}
        if local:
            self._memory[uf] = local
            return local

        cached = self.repository.municipalities_for_uf(uf)
        if cached:
            self._memory[uf] = cached
            return cached

        url = f"https://servicodados.ibge.gov.br/api/v1/localidades/estados/{uf}/municipios"
        try:
            response = self.session.get(url, timeout=self.timeout)
            response.raise_for_status()
            payload = response.json()
        except (requests.RequestException, ValueError) as exc:
            local_detail = f" Catálogo local inválido: {self.catalog_error}" if self.catalog_error else ""
            raise IBGEUnavailable(
                f"Não foi possível consultar os municípios de {uf}: {exc}.{local_detail}"
            ) from exc

        items = [
            {
                "normalized_name": normalize_text(item["nome"]),
                "display_name": str(item["nome"]),
                "ibge_code": str(item["id"]),
            }
            for item in payload
        ]
        self.repository.save_municipalities(uf, items)
        result = {item["normalized_name"]: (item["display_name"], item["ibge_code"]) for item in items}
        self._memory[uf] = result
        return result

    def resolve(self, uf: str, city: str) -> tuple[str | None, str | None, list[dict[str, str]]]:
        municipalities = self.municipalities(uf)
        normalized = normalize_text(city)
        exact = municipalities.get(normalized)
        if exact:
            return exact[1], exact[0], []
        matches = difflib.get_close_matches(normalized, municipalities.keys(), n=5, cutoff=0.62)
        suggestions = [
            {"label": municipalities[key][0], "value": municipalities[key][1]}
            for key in matches
        ]
        return None, None, suggestions

    def resolve_code(self, uf: str, code: str) -> str | None:
        for display_name, ibge_code in self.municipalities(uf).values():
            if ibge_code == code:
                return display_name
        return None
