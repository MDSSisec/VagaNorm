from __future__ import annotations

import re
import unicodedata
from decimal import Decimal, InvalidOperation
from numbers import Integral, Real
from typing import Any

import pandas as pd


VALID_UFS = {
    "AC", "AL", "AP", "AM", "BA", "CE", "DF", "ES", "GO",
    "MA", "MT", "MS", "MG", "PA", "PB", "PR", "PE", "PI",
    "RJ", "RN", "RS", "RO", "RR", "SC", "SP", "SE", "TO",
}

EDUCATION_LEVELS = ["EFI", "EFC", "EMI", "EMC", "ES-MAIS"]
EDUCATION_DESCRIPTIONS = {
    "EFI": "Ensino Fundamental Incompleto",
    "EFC": "Ensino Fundamental Completo",
    "EMI": "Ensino Médio Incompleto",
    "EMC": "Ensino Médio Completo",
    "ES-MAIS": "Ensino Superior ou mais",
}
SEX_DESCRIPTIONS = {"Masc": "Masculino", "Fem": "Feminino", "Ind": "Indiferente"}


def normalize_text(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    normalized = unicodedata.normalize("NFKD", text)
    return "".join(c for c in normalized if not unicodedata.combining(c)).upper().strip()


def clean_null(value: Any) -> str | None:
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    text = str(value).strip()
    return None if normalize_text(text) in {"", "NAN", "NONE", "NULL", "-", "N/A"} else text


def normalize_header(value: Any) -> str:
    text = normalize_text(value)
    text = re.sub(r"[^A-Z0-9]+", "_", text).strip("_")
    aliases = {
        "MUNICIPIO": "CIDADE",
        "MUNICÍPIO": "CIDADE",
        "ESTADO": "UF",
        "QTD_VAGAS": "QUANTIDADE_DE_VAGAS",
        "QUANTIDADE_VAGAS": "QUANTIDADE_DE_VAGAS",
        "QUANTIDADE_DE_VAGA": "QUANTIDADE_DE_VAGAS",
        "FAIXA_DE_IDADE": "FAIXA_ETARIA",
        "IDADE": "FAIXA_ETARIA",
        "ESCOLARIDADE": "GRAU_DE_INSTRUCAO",
        "GRAU_INSTRUCAO": "GRAU_DE_INSTRUCAO",
        "REFERENCIA": "REFERENCIA(OPCIONAL)",
        "BAIRRO": "BAIRRO(OPCIONAL)",
    }
    canonical = aliases.get(text, text)
    if canonical == "REFERENCIA_OPCIONAL":
        return "REFERENCIA(OPCIONAL)"
    if canonical == "BAIRRO_OPCIONAL":
        return "BAIRRO(OPCIONAL)"
    return canonical


def normalize_uf(value: Any) -> str | None:
    text = clean_null(value)
    if text is None:
        return None
    uf = normalize_text(text)
    return uf if uf in VALID_UFS else None


def parse_age(value: Any) -> tuple[int | None, int | None, str | None]:
    if isinstance(value, Real) and not isinstance(value, bool):
        numeric = float(value)
        if numeric.is_integer():
            age = int(numeric)
            if 14 <= age <= 100:
                return age, None, None
            return age, None, "Idade mínima fora do intervalo permitido (14–100)"
    text = clean_null(value)
    if text is None:
        return None, None, None
    t = normalize_text(text).lower()
    minimum: int | None = None
    maximum: int | None = None

    match = re.fullmatch(r"\s*(\d{1,3})\s*[-/]\s*(\d{1,3})(?:\s*anos?)?\s*", t)
    if match:
        minimum, maximum = int(match.group(1)), int(match.group(2))
    else:
        match = re.search(r"(?:entre\s+)?(\d{1,3})\s+(?:a|e|ao|ate)\s+(\d{1,3})(?:\s*anos?)?", t)
        if match:
            minimum, maximum = int(match.group(1)), int(match.group(2))
        else:
            exclusive_min = re.search(r"(?:maior\s+(?:que|de)|acima\s+de|>)\s*(\d{1,3})", t)
            inclusive_min = re.search(r"(?:a\s+partir\s+de|partir\s+de|>=)\s*(\d{1,3})", t)
            exclusive_max = re.search(r"(?:menor\s+que|abaixo\s+de|<(?!=))\s*(\d{1,3})", t)
            inclusive_max = re.search(r"(?:ate|menor\s+de|<=)\s*(\d{1,3})", t)
            if exclusive_min:
                minimum = int(exclusive_min.group(1)) + 1
            elif inclusive_min:
                minimum = int(inclusive_min.group(1))
            elif exclusive_max:
                maximum = int(exclusive_max.group(1)) - 1
            elif inclusive_max:
                maximum = int(inclusive_max.group(1))
            else:
                single = re.fullmatch(r"\s*(\d{1,3})(?:\s*anos?)?\s*", t)
                if single:
                    minimum = int(single.group(1))
                else:
                    return None, None, "Formato de faixa etária não reconhecido"

    if minimum is not None and not 14 <= minimum <= 100:
        return minimum, maximum, "Idade mínima fora do intervalo permitido (14–100)"
    if maximum is not None and not 14 <= maximum <= 100:
        return minimum, maximum, "Idade máxima fora do intervalo permitido (14–100)"
    if minimum is not None and maximum is not None and minimum > maximum:
        return minimum, maximum, "Idade mínima maior que a idade máxima"
    return minimum, maximum, None


def parse_education(value: Any) -> list[str] | None:
    text = clean_null(value)
    if text is None:
        return EDUCATION_LEVELS[:]
    t = normalize_text(text)
    if re.search(r"\b(?:INDIFERENTE|QUALQUER|SEM\s+(?:EXIGENCIA|RESTRICAO))\b", t):
        return EDUCATION_LEVELS[:]
    if re.search(r"\bALFABETIZAD[OA]\b", t):
        return EDUCATION_LEVELS[:]

    if re.search(r"SUPERIOR|GRADUAC|POS.?GRADUAC|MESTRADO|DOUTORADO|TECNOLOG", t):
        if re.search(r"INCOMPLET|CURSANDO", t):
            return ["EMC", "ES-MAIS"]
        return ["ES-MAIS"]
    if re.search(r"TECNIC|MEDIO\s+COMPLETO|ENSINO\s+MEDIO\s+COMPLETO|\bEMC\b", t):
        return ["EMC", "ES-MAIS"]
    if re.search(r"MEDIO\s+INCOMPLET|ENSINO\s+MEDIO|SEGUNDO\s+GRAU|2\s*[O°]\s*GRAU|CURSANDO", t):
        return ["EMI", "EMC", "ES-MAIS"]
    if re.search(r"FUNDAMENTAL\s+COMPLETO|PRIMEIRO\s+GRAU\s+COMPLETO|\bEFC\b", t):
        return ["EFC", "EMI", "EMC", "ES-MAIS"]
    if re.search(r"FUNDAMENTAL|PRIMEIRO\s+GRAU|1\s*[O°]\s*GRAU|\bEFI\b", t):
        return EDUCATION_LEVELS[:]
    return None


def parse_sex(value: Any) -> list[str] | None:
    text = clean_null(value)
    if text is None:
        return ["Ind"]
    t = normalize_text(text)
    if re.search(r"\b(?:INDIFERENTE|AMBOS|QUALQUER|TODOS)\b|SEM\s+(?:RESTRICAO|RESTRICOES|PREFERENCIA)", t):
        return ["Ind"]
    masculine = bool(re.search(r"\b(?:MASCULINO|HOMEM|HOMENS|MASC|MALE|M)\b", t))
    feminine = bool(re.search(r"\b(?:FEMININO|MULHER|MULHERES|FEM|FEMALE|F)\b", t))
    if masculine and feminine:
        return ["Ind"]
    if masculine:
        return ["Masc"]
    if feminine:
        return ["Fem"]
    return None


def parse_quantity(value: Any) -> tuple[int | None, str | None]:
    if isinstance(value, Integral) and not isinstance(value, bool):
        result = int(value)
        return (result, None) if result >= 0 else (None, "Quantidade de vagas não pode ser negativa")
    if isinstance(value, Real) and not isinstance(value, bool):
        numeric = float(value)
        if not numeric.is_integer():
            return None, "Quantidade de vagas deve ser um número inteiro"
        result = int(numeric)
        return (result, None) if result >= 0 else (None, "Quantidade de vagas não pode ser negativa")
    text = clean_null(value)
    if text is None:
        return None, "Quantidade de vagas não informada"
    if "." in text and "," in text:
        normalized = text.replace(".", "").replace(",", ".")
    elif "," in text:
        normalized = text.replace(",", ".")
    elif re.fullmatch(r"\d{1,3}(?:\.\d{3})+", text):
        normalized = text.replace(".", "")
    else:
        normalized = text
    try:
        number = Decimal(normalized)
    except InvalidOperation:
        return None, "Quantidade de vagas não numérica"
    if not number.is_finite() or number != number.to_integral_value():
        return None, "Quantidade de vagas deve ser um número inteiro"
    result = int(number)
    if result < 0:
        return None, "Quantidade de vagas não pode ser negativa"
    return result, None


def valid_ibge_code(value: Any) -> bool:
    text = clean_null(value)
    return bool(text and re.fullmatch(r"\d{7}", text))


def join_codes(values: list[str] | None) -> str | None:
    return ", ".join(values) if values else None
