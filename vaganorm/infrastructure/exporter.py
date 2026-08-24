from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd

from ..domain.aggregation import aggregate_query_data
from .excel import order_output, write_xlsx
from ..domain.normalizers import normalize_city_name


def _records(df: pd.DataFrame) -> list[dict[str, Any]]:
    clean = df.astype(object).where(pd.notna(df), None)
    return clean.to_dict(orient="records")


def export_all(
    df: pd.DataFrame,
    directory: Path,
    excluded_indices: set[int],
    report: dict[str, Any],
    campaign_code: str,
    partner_name: str,
    qtd_indv_mode: str = "standard",
    qtd_indv_multiplier: int | None = None,
    qtd_indv_overrides: dict[str, int] | None = None,
) -> tuple[dict[str, Path], list[str]]:
    directory.mkdir(parents=True, exist_ok=True)
    ordered = order_output(df)
    prefix = f"AC{campaign_code}_{partner_name}_vagas"
    paths = {
        "xlsx": directory / f"{prefix}_padronizado.xlsx",
        "json": directory / f"{prefix}_json.json",
        "query": directory / f"{prefix}_querieData.json",
        "report": directory / f"{prefix}_relatorio.json",
    }
    write_xlsx(ordered, paths["xlsx"])
    paths["json"].write_text(json.dumps(_records(ordered), ensure_ascii=False, indent=2), encoding="utf-8")
    query_records, warnings = aggregate_query_data(
        ordered,
        excluded_indices,
        qtd_indv_mode=qtd_indv_mode,
        qtd_indv_multiplier=qtd_indv_multiplier,
        qtd_indv_overrides=qtd_indv_overrides,
    )
    for record in query_records:
        record["CIDADE"] = normalize_city_name(record["CIDADE"])
    paths["query"].write_text(json.dumps(query_records, ensure_ascii=False, indent=2), encoding="utf-8")
    report = {**report, "export_warnings": warnings, "query_records": len(query_records)}
    paths["report"].write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return paths, warnings
