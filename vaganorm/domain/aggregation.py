from __future__ import annotations

from typing import Any

import pandas as pd

from .normalizers import EDUCATION_LEVELS, clean_null, valid_ibge_code


def _first_value(series: pd.Series) -> str | None:
    for value in series:
        cleaned = clean_null(value)
        if cleaned is not None:
            return cleaned
    return None


def _aggregate_education(series: pd.Series) -> str | None:
    found: set[str] = set()
    has_empty = False
    for value in series:
        cleaned = clean_null(value)
        if cleaned is None:
            has_empty = True
            continue
        found.update(code.strip() for code in cleaned.split(",") if code.strip() in EDUCATION_LEVELS)
    if has_empty:
        return ", ".join(EDUCATION_LEVELS)
    if not found:
        return None
    first = min(EDUCATION_LEVELS.index(code) for code in found)
    return ", ".join(EDUCATION_LEVELS[first:])


def _aggregate_sex(series: pd.Series) -> str | None:
    codes: set[str] = set()
    for value in series:
        cleaned = clean_null(value)
        if cleaned:
            codes.update(code.strip() for code in cleaned.split(","))
    if "Ind" in codes or {"Masc", "Fem"}.issubset(codes):
        return "Ind"
    if "Masc" in codes:
        return "Masc"
    if "Fem" in codes:
        return "Fem"
    return None


def calculate_qtd_indv(quantity: int, mode: str, multiplier: int | None) -> int:
    if mode == "custom":
        if multiplier is None or multiplier <= 0:
            raise ValueError("O multiplicador personalizado deve ser um inteiro positivo.")
        return quantity * multiplier
    if mode != "standard":
        raise ValueError(f"Modo de cálculo de QTD_INDV inválido: {mode}.")
    return max(quantity * 20, 250)


def aggregate_query_data(
    df: pd.DataFrame,
    excluded_indices: set[int],
    qtd_indv_mode: str = "standard",
    qtd_indv_multiplier: int | None = None,
    qtd_indv_overrides: dict[str, int] | None = None,
) -> tuple[list[dict[str, Any]], list[str]]:
    qtd_indv_overrides = qtd_indv_overrides or {}
    warnings: list[str] = []
    work = df.drop(index=[idx for idx in excluded_indices if idx in df.index]).copy()
    valid_mask = work["COD_IBGE"].apply(valid_ibge_code)
    rejected = int((~valid_mask).sum())
    if rejected:
        warnings.append(f"{rejected} linha(s) sem código IBGE válido não foram incluídas no _querieData.")
    work = work[valid_mask]
    if work.empty:
        return [], warnings

    work["_COD"] = work["COD_IBGE"].astype(str).str.strip()
    records: list[dict[str, Any]] = []
    for code, group in work.groupby("_COD", sort=False):
        quantities = pd.to_numeric(group["QUANTIDADE_DE_VAGAS"], errors="coerce").fillna(0)
        total_quantity = int(quantities.sum())
        minimums = pd.to_numeric(group["IDADE_MINIMA"], errors="coerce")
        maximums = pd.to_numeric(group["IDADE_MAXIMA"], errors="coerce")
        min_unbounded = group["IDADE_MINIMA"].apply(clean_null).isna().any()
        max_unbounded = group["IDADE_MAXIMA"].apply(clean_null).isna().any()
        sex_code = _aggregate_sex(group["COD_SEXO"])
        records.append({
            "COD_IBGE": code,
            "COD_IBGE_UF": code[:2],
            "COD_IBGE_MUN": code[2:],
            "UF": _first_value(group["UF"]),
            "CIDADE": _first_value(group["CIDADE"]),
            "QUANTIDADE_DE_VAGAS": total_quantity,
            "IDADE_MINIMA": None if min_unbounded else (int(minimums.min()) if minimums.notna().any() else None),
            "IDADE_MAXIMA": None if max_unbounded else (int(maximums.max()) if maximums.notna().any() else None),
            "COD_ESCOL": _aggregate_education(group["COD_ESCOL"]),
            "SEXO": {"Masc": "Masculino", "Fem": "Feminino", "Ind": "Indiferente"}.get(sex_code),
            "COD_SEXO": sex_code,
            "QTD_INDV": qtd_indv_overrides.get(
                code,
                calculate_qtd_indv(total_quantity, qtd_indv_mode, qtd_indv_multiplier),
            ),
        })
    return records, warnings
