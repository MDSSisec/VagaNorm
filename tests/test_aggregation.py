import unittest

import pandas as pd

from vaganorm.domain.aggregation import aggregate_query_data, calculate_qtd_indv


class AggregationTests(unittest.TestCase):
    def test_qtd_indv_standard_and_custom_rules(self):
        self.assertEqual(calculate_qtd_indv(5, "standard", None), 250)
        self.assertEqual(calculate_qtd_indv(20, "standard", None), 400)
        self.assertEqual(calculate_qtd_indv(5, "custom", 7), 35)
        with self.assertRaises(ValueError):
            calculate_qtd_indv(5, "custom", 0)

    def test_invalid_codes_are_not_grouped(self):
        frame = pd.DataFrame([
            self.row(None, "SP", "Cidade A", 2),
            self.row(None, "RJ", "Cidade B", 3),
        ])
        records, warnings = aggregate_query_data(frame, set())
        self.assertEqual(records, [])
        self.assertTrue(warnings)

    def test_scope_and_sex_are_combined(self):
        frame = pd.DataFrame([
            self.row("3550308", "SP", "São Paulo", 2, None, 40, "EMC, ES-MAIS", "Masc"),
            self.row("3550308", "SP", "São Paulo", 3, 21, None, "EFC, EMI, EMC, ES-MAIS", "Fem"),
        ])
        records, _ = aggregate_query_data(frame, set())
        self.assertEqual(len(records), 1)
        record = records[0]
        self.assertEqual(record["QUANTIDADE_DE_VAGAS"], 5)
        self.assertIsNone(record["IDADE_MINIMA"])
        self.assertIsNone(record["IDADE_MAXIMA"])
        self.assertEqual(record["COD_SEXO"], "Ind")
        self.assertEqual(record["COD_ESCOL"], "EFC, EMI, EMC, ES-MAIS")

    def test_custom_multiplier_uses_aggregated_quantity_without_floor(self):
        frame = pd.DataFrame([
            self.row("3550308", "SP", "São Paulo", 2),
            self.row("3550308", "SP", "São Paulo", 3),
        ])
        records, _ = aggregate_query_data(
            frame, set(), qtd_indv_mode="custom", qtd_indv_multiplier=7
        )
        self.assertEqual(records[0]["QUANTIDADE_DE_VAGAS"], 5)
        self.assertEqual(records[0]["QTD_INDV"], 35)

    def test_locality_override_replaces_calculated_qtd_indv(self):
        frame = pd.DataFrame([self.row("3550308", "SP", "São Paulo", 5)])
        records, _ = aggregate_query_data(
            frame,
            set(),
            qtd_indv_mode="standard",
            qtd_indv_overrides={"3550308": 321},
        )
        self.assertEqual(records[0]["QTD_INDV"], 321)

    @staticmethod
    def row(code, uf, city, quantity, minimum=18, maximum=60, education="EMC, ES-MAIS", sex="Ind"):
        return {
            "COD_IBGE": code,
            "UF": uf,
            "CIDADE": city,
            "QUANTIDADE_DE_VAGAS": quantity,
            "IDADE_MINIMA": minimum,
            "IDADE_MAXIMA": maximum,
            "COD_ESCOL": education,
            "SEXO": sex,
            "COD_SEXO": sex,
        }


if __name__ == "__main__":
    unittest.main()
