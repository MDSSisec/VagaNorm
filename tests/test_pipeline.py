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
        return "São Paulo" if uf == "SP" and code == "3550308" else None


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


if __name__ == "__main__":
    unittest.main()
