from __future__ import annotations

from pathlib import Path

import pandas as pd

from ..domain.normalizers import clean_null, normalize_header, normalize_text


EXPECTED_COLUMNS = [
    "UF", "CIDADE", "REFERENCIA(OPCIONAL)", "BAIRRO(OPCIONAL)",
    "QUANTIDADE_DE_VAGAS", "FAIXA_ETARIA", "GRAU_DE_INSTRUCAO",
    "SEXO", "CARGO", "BENEFICIOS", "DESCRICAO", "SALARIO", "PCD",
]

REQUIRED_COLUMNS = ["UF", "CIDADE", "QUANTIDADE_DE_VAGAS"]

OUTPUT_ORDER = [
    "COD_IBGE", "UF", "CIDADE", "REFERENCIA(OPCIONAL)", "BAIRRO(OPCIONAL)",
    "QUANTIDADE_DE_VAGAS", "FAIXA_ETARIA", "IDADE_MINIMA", "IDADE_MAXIMA",
    "GRAU_DE_INSTRUCAO", "COD_ESCOL", "SEXO", "COD_SEXO", "CARGO",
    "BENEFICIOS", "DESCRICAO", "SALARIO", "PCD",
]


class InputValidationError(ValueError):
    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("; ".join(errors))


def read_input(path: Path) -> tuple[pd.DataFrame, list[str]]:
    warnings: list[str] = []
    try:
        workbook = pd.ExcelFile(path, engine="openpyxl")
    except Exception as exc:
        raise InputValidationError([f"Não foi possível abrir o arquivo como .xlsx: {exc}"]) from exc
    try:
        if "Lista" not in workbook.sheet_names:
            raise InputValidationError(["A planilha precisa conter uma aba chamada 'Lista'."])
        df = pd.read_excel(workbook, sheet_name="Lista", header=0, dtype=object)
    finally:
        workbook.close()
    normalized_columns = [normalize_header(column) for column in df.columns]
    duplicates = sorted({column for column in normalized_columns if normalized_columns.count(column) > 1})
    if duplicates:
        raise InputValidationError([f"Cabeçalhos duplicados após normalização: {', '.join(duplicates)}."])
    df.columns = normalized_columns

    missing = [column for column in REQUIRED_COLUMNS if column not in df.columns]
    if missing:
        raise InputValidationError([f"Colunas obrigatórias ausentes: {', '.join(missing)}."])
    optional_missing = [column for column in EXPECTED_COLUMNS if column not in df.columns and column not in REQUIRED_COLUMNS]
    if optional_missing:
        warnings.append(f"Colunas opcionais ausentes e criadas vazias: {', '.join(optional_missing)}.")

    df = df.dropna(how="all").reset_index(drop=True)
    if not df.empty:
        def is_total_row(row: pd.Series) -> bool:
            first = next((clean_null(value) for value in row if clean_null(value) is not None), None)
            return bool(first and normalize_text(first) in {"TOTAL", "TOTAL GERAL", "TOTAIS"})

        total_mask = df.apply(is_total_row, axis=1)
        total_count = int(total_mask.sum())
        if total_count:
            warnings.append(f"{total_count} linha(s) de totalização foram removidas.")
            df = df[~total_mask].reset_index(drop=True)

    if df.empty:
        raise InputValidationError(["A aba 'Lista' não contém vagas para processar."])

    for column in EXPECTED_COLUMNS:
        if column not in df.columns:
            df[column] = None
    return df, warnings


def order_output(df: pd.DataFrame) -> pd.DataFrame:
    result = df.copy()
    for column in OUTPUT_ORDER:
        if column not in result.columns:
            result[column] = None
    extras = [column for column in result.columns if column not in OUTPUT_ORDER and not column.startswith("_")]
    return result[OUTPUT_ORDER + extras]


def write_xlsx(df: pd.DataFrame, path: Path) -> None:
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        df.to_excel(writer, sheet_name="Lista", index=False)
