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

    def all_municipalities(self) -> dict[str, dict[str, tuple[str, str]]]:
        if self._municipalities is None:
            self._municipalities = self._load()
        return self._municipalities


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
    REGIONS = {
        "AC": "Norte", "AL": "Nordeste", "AP": "Norte", "AM": "Norte",
        "BA": "Nordeste", "CE": "Nordeste", "DF": "Centro-Oeste", "ES": "Sudeste",
        "GO": "Centro-Oeste", "MA": "Nordeste", "MT": "Centro-Oeste", "MS": "Centro-Oeste",
        "MG": "Sudeste", "PA": "Norte", "PB": "Nordeste", "PR": "Sul",
        "PE": "Nordeste", "PI": "Nordeste", "RJ": "Sudeste", "RN": "Nordeste",
        "RS": "Sul", "RO": "Norte", "RR": "Norte", "SC": "Sul",
        "SP": "Sudeste", "SE": "Nordeste", "TO": "Norte",
    }

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
        self._national_by_name: dict[str, list[tuple[str, str, str]]] | None = None
        self._national_by_code: dict[str, tuple[str, str]] | None = None

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

    def _national_indexes(self) -> tuple[
        dict[str, list[tuple[str, str, str]]],
        dict[str, tuple[str, str]],
    ]:
        if self._national_by_name is None or self._national_by_code is None:
            by_name: dict[str, list[tuple[str, str, str]]] = {}
            by_code: dict[str, tuple[str, str]] = {}
            try:
                catalog = self.local_catalog.all_municipalities()
            except MunicipalityCatalogError as exc:
                self.catalog_error = str(exc)
                catalog = {}
            for candidate_uf, municipalities in catalog.items():
                for normalized_name, (display_name, code) in municipalities.items():
                    by_name.setdefault(normalized_name, []).append((candidate_uf, display_name, code))
                    by_code[code] = (candidate_uf, display_name)
            self._national_by_name = by_name
            self._national_by_code = by_code
        return self._national_by_name, self._national_by_code

    def suggest_national(self, city: str, original_uf: str | None = None) -> list[dict[str, str]]:
        by_name, _ = self._national_indexes()
        normalized = normalize_text(city)
        matched_names = [normalized] if normalized in by_name else difflib.get_close_matches(
            normalized, by_name.keys(), n=8, cutoff=0.62
        )
        original_region = self.REGIONS.get(original_uf or "")
        candidates: list[tuple[float, str, str, str]] = []
        for matched_name in matched_names:
            similarity = difflib.SequenceMatcher(None, normalized, matched_name).ratio()
            for candidate_uf, display_name, code in by_name[matched_name]:
                candidates.append((similarity, candidate_uf, display_name, code))
        candidates.sort(key=lambda item: (
            0 if self.REGIONS.get(item[1]) == original_region else 1,
            self.REGIONS.get(item[1], ""),
            -item[0],
            item[2],
            item[1],
        ))
        return [
            {
                "label": f"{display_name} — {candidate_uf} ({code})",
                "value": code,
                "code": code,
                "name": display_name,
                "uf": candidate_uf,
            }
            for _, candidate_uf, display_name, code in candidates[:8]
        ]

    def resolve(self, uf: str, city: str) -> tuple[str | None, str | None, list[dict[str, str]]]:
        uf = uf.strip().upper()
        municipalities = self.municipalities(uf)
        normalized = normalize_text(city)
        exact = municipalities.get(normalized)
        if exact:
            return exact[1], exact[0], []
        return None, None, self.suggest_national(city, uf)

    def resolve_code(self, uf: str, code: str) -> str | None:
        for display_name, ibge_code in self.municipalities(uf).values():
            if ibge_code == code:
                return display_name
        return None

    def resolve_code_national(self, code: str) -> tuple[str, str] | None:
        _, by_code = self._national_indexes()
        return by_code.get(code)
