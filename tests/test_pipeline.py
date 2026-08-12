import shutil
import unittest
import uuid
from pathlib import Path

import pandas as pd

from vaganorm.application.pipeline import StandardizationPipeline
from vaganorm.infrastructure.repository import LocalRepository


class FakeIBGE:
    def resolve(self, uf, city):
        if uf == "SP" and city.lower().startswith("são paulo"):
            return "3550308", "São Paulo", []
        return None, None, [{"label": "São Paulo", "value": "3550308"}]

    def resolve_code(self, uf, code):
        values = {
            ("SP", "3550308"): "São Paulo",
            ("PR", "4113700"): "Londrina",
        }
        return values.get((uf, code))

    def resolve_code_national(self, code):
        values = {"3550308": ("SP", "São Paulo"), "4113700": ("PR", "Londrina")}
        return values.get(code)


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp_path = Path(__file__).resolve().parent / f"runtime_{uuid.uuid4().hex}"
        self.temp_path.mkdir()
        self.repository = LocalRepository(self.temp_path / "test.db")
        self.pipeline = StandardizationPipeline(FakeIBGE(), self.repository)

    def tearDown(self):
        shutil.rmtree(self.temp_path)

    def test_complete_row_is_standardized(self):
        frame = pd.DataFrame([{
            "UF": "sp", "CIDADE": "São Paulo", "QUANTIDADE_DE_VAGAS": "2",
            "FAIXA_ETARIA": "maior de 18", "GRAU_DE_INSTRUCAO": "Médio completo",
            "SEXO": "ambos",
        }])
        result = self.pipeline.run(frame, {"ibge": True, "ages": True, "education": True, "sex": True})
        self.assertEqual(result.issues, [])
        row = result.dataframe.iloc[0]
        self.assertEqual(row["UF"], "SP")
        self.assertEqual(row["COD_IBGE"], "3550308")
        self.assertEqual(row["IDADE_MINIMA"], 19)
        self.assertEqual(row["COD_ESCOL"], "EMC, ES-MAIS")
        self.assertEqual(row["COD_SEXO"], "Ind")

    def test_equal_unknown_values_are_grouped(self):
        frame = pd.DataFrame([
            {"UF": "SP", "CIDADE": "cidade errada", "QUANTIDADE_DE_VAGAS": 1},
            {"UF": "SP", "CIDADE": "cidade errada", "QUANTIDADE_DE_VAGAS": 2},
        ])
        result = self.pipeline.run(frame, {"ibge": True, "ages": False, "education": False, "sex": False})
        self.assertEqual(len(result.issues), 1)
        self.assertEqual(result.issues[0].rows, [2, 3])

    def test_submitted_decision_resolves_issue(self):
        frame = pd.DataFrame([{"UF": "XX", "CIDADE": "São Paulo", "QUANTIDADE_DE_VAGAS": 1}])
        first = self.pipeline.run(frame, {"ibge": False, "ages": False, "education": False, "sex": False})
        issue = first.issues[0]
        second = self.pipeline.run(
            frame,
            {"ibge": False, "ages": False, "education": False, "sex": False},
            {issue.id: "SP"},
        )
        self.assertEqual(second.issues, [])
        self.assertEqual(second.dataframe.iloc[0]["UF"], "SP")

    def test_interstate_decision_updates_grouped_rows_atomically(self):
        frame = pd.DataFrame([
            {"UF": "SC", "CIDADE": "Londrina", "QUANTIDADE_DE_VAGAS": 1},
            {"UF": "SC", "CIDADE": "Londrina", "QUANTIDADE_DE_VAGAS": 2},
        ])
        first = self.pipeline.run(
            frame, {"ibge": True, "ages": False, "education": False, "sex": False}
        )
        self.assertEqual(len(first.issues), 1)
        self.assertEqual(first.issues[0].rows, [2, 3])
        decision = {"code": "4113700", "uf": "PR"}
        second = self.pipeline.run(
            frame,
            {"ibge": True, "ages": False, "education": False, "sex": False},
            {first.issues[0].id: decision},
        )
        self.assertEqual(second.issues, [])
        self.assertEqual(second.dataframe["UF"].tolist(), ["PR", "PR"])
        self.assertEqual(second.dataframe["CIDADE"].tolist(), ["Londrina", "Londrina"])
        self.assertEqual(second.dataframe["COD_IBGE"].tolist(), ["4113700", "4113700"])

    def test_remembered_interstate_decision_restores_corrected_uf(self):
        frame = pd.DataFrame([
            {"UF": "SC", "CIDADE": "Londrina", "QUANTIDADE_DE_VAGAS": 1},
        ])
        options = {"ibge": True, "ages": False, "education": False, "sex": False}
        first = self.pipeline.run(frame, options)
        issue = first.issues[0]
        self.repository.save_decision(
            "municipality", issue.key, {"code": "4113700", "uf": "PR"}
        )
        second = self.pipeline.run(frame, options)
        self.assertEqual(second.issues, [])
        self.assertEqual(second.dataframe.iloc[0]["UF"], "PR")
        self.assertEqual(second.dataframe.iloc[0]["COD_IBGE"], "4113700")

    def test_manually_entered_code_is_resolved_nationally(self):
        frame = pd.DataFrame([
            {"UF": "SC", "CIDADE": "Londrina", "QUANTIDADE_DE_VAGAS": 1},
        ])
        options = {"ibge": True, "ages": False, "education": False, "sex": False}
        first = self.pipeline.run(frame, options)
        second = self.pipeline.run(
            frame,
            options,
            {first.issues[0].id: {"code": "4113700", "uf": None}},
        )
        self.assertEqual(second.issues, [])
        self.assertEqual(second.dataframe.iloc[0]["UF"], "PR")
        self.assertEqual(second.dataframe.iloc[0]["CIDADE"], "Londrina")
        self.assertEqual(second.dataframe.iloc[0]["COD_IBGE"], "4113700")


if __name__ == "__main__":
    unittest.main()
