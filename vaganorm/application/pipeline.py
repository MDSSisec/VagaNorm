from __future__ import annotations

import hashlib
from collections import defaultdict
from typing import Any

import pandas as pd

from ..domain.models import Issue, PipelineResult
from ..domain.normalizers import (
    EDUCATION_LEVELS,
    clean_null,
    join_codes,
    normalize_text,
    normalize_uf,
    parse_age,
    parse_education,
    parse_quantity,
    parse_sex,
    valid_ibge_code,
)
from ..infrastructure.ibge import IBGEClient, IBGEUnavailable
from ..infrastructure.repository import LocalRepository


class StandardizationPipeline:
    def __init__(self, ibge: IBGEClient, repository: LocalRepository):
        self.ibge = ibge
        self.repository = repository

    @staticmethod
    def issue_id(kind: str, key: str) -> str:
        digest = hashlib.sha1(f"{kind}|{key}".encode("utf-8")).hexdigest()[:12]
        return f"{kind}-{digest}"

    def run(
        self,
        source: pd.DataFrame,
        options: dict[str, bool],
        submitted_decisions: dict[str, Any] | None = None,
    ) -> PipelineResult:
        submitted_decisions = submitted_decisions or {}
        df = source.copy(deep=True)
        for column in ["COD_IBGE", "IDADE_MINIMA", "IDADE_MAXIMA", "COD_ESCOL", "COD_SEXO"]:
            if column not in df.columns:
                df[column] = None

        issues_by_id: dict[str, Issue] = {}
        warnings: list[str] = []
        warning_keys: set[str] = set()
        excluded: set[int] = set()
        changes: defaultdict[str, int] = defaultdict(int)

        def decision(kind: str, key: str) -> Any | None:
            issue_id = self.issue_id(kind, key)
            if issue_id in submitted_decisions:
                return submitted_decisions[issue_id]
            return self.repository.get_decision(kind, key)

        def add_issue(
            kind: str,
            key: str,
            title: str,
            message: str,
            index: int,
            original: Any,
            context: dict[str, Any] | None = None,
            suggestions: list[dict[str, str]] | None = None,
        ) -> None:
            issue_id = self.issue_id(kind, key)
            issue = issues_by_id.get(issue_id)
            if issue is None:
                issue = Issue(
                    id=issue_id,
                    kind=kind,
                    key=key,
                    title=title,
                    message=message,
                    original=original,
                    context=context or {},
                    suggestions=suggestions or [],
                )
                issues_by_id[issue_id] = issue
            issue.rows.append(index + 2)
            issue.row_indices.append(index)

        for index, row in df.iterrows():
            raw_uf = clean_null(row.get("UF"))
            raw_city = clean_null(row.get("CIDADE"))
            location_resolved = False

            if raw_uf is None and raw_city is None:
                key = "empty-location"
                chosen = decision("missing_location", key)
                if chosen == "exclude":
                    excluded.add(index)
                elif chosen != "keep":
                    add_issue(
                        "missing_location", key, "Localização ausente",
                        "Defina se as linhas sem UF e cidade devem ficar fora do _querieData.",
                        index, None,
                    )

            uf = normalize_uf(raw_uf)
            if uf is None and (raw_uf is not None or raw_city is not None):
                key = (
                    f"{normalize_text(raw_uf)}|{normalize_text(raw_city)}"
                    if raw_city else normalize_text(raw_uf)
                )
                chosen = decision("invalid_uf", key)
                corrected = normalize_uf(chosen.get("uf")) if isinstance(chosen, dict) else normalize_uf(chosen)
                chosen_code = chosen.get("code") if isinstance(chosen, dict) else None
                official_name = None
                if corrected and valid_ibge_code(chosen_code):
                    official_name = self.ibge.resolve_code(corrected, str(chosen_code).strip())
                if corrected and official_name:
                    uf = corrected
                    df.at[index, "UF"] = corrected
                    df.at[index, "CIDADE"] = official_name
                    df.at[index, "COD_IBGE"] = str(chosen_code).strip()
                    changes["UF"] += 1
                    changes["CIDADE"] += 1
                    changes["COD_IBGE"] += 1
                    location_resolved = True
                elif corrected and not isinstance(chosen, dict):
                    uf = corrected
                    df.at[index, "UF"] = corrected
                    changes["UF"] += 1
                else:
                    suggestions = self.ibge.suggest_national(raw_city) if raw_city else []
                    add_issue(
                        "invalid_uf", key, "UF ausente ou inválida",
                        "Selecione uma localidade sugerida ou informe uma UF válida.",
                        index, raw_uf,
                        {"uf": raw_uf, "city": raw_city}, suggestions,
                    )
            elif uf:
                if raw_uf != uf:
                    changes["UF"] += 1
                df.at[index, "UF"] = uf

            if options.get("ibge", True) and uf and raw_city and not location_resolved:
                city_key = f"{uf}|{normalize_text(raw_city)}"
                chosen = decision("municipality", city_key)
                chosen_code = chosen.get("code") if isinstance(chosen, dict) else chosen
                chosen_uf = normalize_uf(chosen.get("uf")) if isinstance(chosen, dict) else uf
                if valid_ibge_code(chosen_code):
                    chosen_code = str(chosen_code).strip()
                    try:
                        if chosen_uf:
                            official_name = self.ibge.resolve_code(chosen_uf, chosen_code)
                        else:
                            national = self.ibge.resolve_code_national(chosen_code)
                            chosen_uf, official_name = national if national else (None, None)
                    except IBGEUnavailable as exc:
                        official_name = None
                        warning_key = f"ibge-manual-{chosen_uf or uf}"
                        if warning_key not in warning_keys:
                            warnings.append(
                                str(exc) + " O código manual não pôde ser conferido."
                            )
                            warning_keys.add(warning_key)
                    if official_name and chosen_uf:
                        if df.at[index, "UF"] != chosen_uf:
                            changes["UF"] += 1
                        if df.at[index, "CIDADE"] != official_name:
                            changes["CIDADE"] += 1
                        df.at[index, "UF"] = chosen_uf
                        df.at[index, "COD_IBGE"] = chosen_code
                        df.at[index, "CIDADE"] = official_name
                        changes["COD_IBGE"] += 1
                    else:
                        add_issue(
                            "municipality", city_key, "Código IBGE incompatível",
                            f"O código {chosen_code} não corresponde à UF selecionada.",
                            index, raw_city, {"uf": uf},
                        )
                else:
                    try:
                        code, display_name, suggestions = self.ibge.resolve(uf, raw_city)
                    except IBGEUnavailable as exc:
                        code, display_name, suggestions = None, None, []
                        warning_key = f"ibge-{uf}"
                        if warning_key not in warning_keys:
                            warnings.append(str(exc) + " O cache local será usado quando disponível.")
                            warning_keys.add(warning_key)
                    if code:
                        df.at[index, "COD_IBGE"] = code
                        df.at[index, "CIDADE"] = display_name
                        changes["COD_IBGE"] += 1
                    elif not valid_ibge_code(row.get("COD_IBGE")):
                        add_issue(
                            "municipality", city_key, "Município não identificado",
                            "Selecione uma sugestão ou informe o código IBGE de sete dígitos.",
                            index, raw_city, {"uf": uf}, suggestions,
                        )

            if options.get("ages", True):
                raw_age = clean_null(row.get("FAIXA_ETARIA"))
                age_key = normalize_text(raw_age)
                minimum, maximum, age_error = parse_age(raw_age)
                chosen = decision("age", age_key) if age_error else None
                if age_error and isinstance(chosen, dict):
                    minimum = chosen.get("min")
                    maximum = chosen.get("max")
                    _, _, age_error = parse_age(
                        f"{minimum}-{maximum}" if minimum is not None and maximum is not None
                        else (f">={minimum}" if minimum is not None else f"<={maximum}")
                    )
                if age_error:
                    add_issue("age", age_key, "Faixa etária não reconhecida", age_error, index, raw_age)
                else:
                    df.at[index, "IDADE_MINIMA"] = minimum
                    df.at[index, "IDADE_MAXIMA"] = maximum
                    if raw_age is not None:
                        changes["IDADE"] += 1

            if options.get("education", True):
                raw_education = clean_null(row.get("GRAU_DE_INSTRUCAO"))
                education_key = normalize_text(raw_education)
                levels = parse_education(raw_education)
                if levels is None:
                    chosen = decision("education", education_key)
                    if isinstance(chosen, list) and chosen and all(item in EDUCATION_LEVELS for item in chosen):
                        levels = [item for item in EDUCATION_LEVELS if item in chosen]
                if levels is None:
                    add_issue(
                        "education", education_key, "Escolaridade não reconhecida",
                        "Selecione todos os níveis aceitos para esta vaga.", index, raw_education,
                    )
                else:
                    df.at[index, "COD_ESCOL"] = join_codes(levels)
                    changes["COD_ESCOL"] += 1

            if options.get("sex", True):
                raw_sex = clean_null(row.get("SEXO"))
                sex_key = normalize_text(raw_sex)
                codes = parse_sex(raw_sex)
                if codes is None:
                    chosen = decision("sex", sex_key)
                    if isinstance(chosen, list) and chosen and all(item in {"Masc", "Fem", "Ind"} for item in chosen):
                        codes = ["Ind"] if "Ind" in chosen or {"Masc", "Fem"}.issubset(chosen) else chosen
                if codes is None:
                    add_issue(
                        "sex", sex_key, "Sexo não reconhecido",
                        "Selecione a regra de sexo aplicável.", index, raw_sex,
                    )
                else:
                    df.at[index, "COD_SEXO"] = join_codes(codes)
                    changes["COD_SEXO"] += 1

            quantity, quantity_error = parse_quantity(row.get("QUANTIDADE_DE_VAGAS"))
            if quantity_error:
                quantity_key = normalize_text(row.get("QUANTIDADE_DE_VAGAS"))
                chosen = decision("quantity", quantity_key)
                quantity, quantity_error = parse_quantity(chosen)
            if quantity_error:
                add_issue(
                    "quantity", normalize_text(row.get("QUANTIDADE_DE_VAGAS")),
                    "Quantidade de vagas inválida", quantity_error, index,
                    clean_null(row.get("QUANTIDADE_DE_VAGAS")),
                )
            else:
                df.at[index, "QUANTIDADE_DE_VAGAS"] = quantity

        issues = sorted(issues_by_id.values(), key=lambda item: (item.kind, item.rows[0]))
        return PipelineResult(df, issues, warnings, dict(changes), excluded)
