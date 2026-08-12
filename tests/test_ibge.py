import unittest
from pathlib import Path
from unittest.mock import Mock

from vaganorm.infrastructure.ibge import IBGEClient, LocalMunicipalityCatalog


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CATALOG_PATH = PROJECT_ROOT / "codigos_ibge_csv.csv"


class IBGECatalogTests(unittest.TestCase):
    def test_catalog_contains_all_ufs_and_sao_paulo(self):
        catalog = LocalMunicipalityCatalog(CATALOG_PATH)
        sao_paulo = catalog.municipalities_for_uf("SP")
        self.assertEqual(sao_paulo["SAO PAULO"], ("São Paulo", "3550308"))
        self.assertGreaterEqual(len(sao_paulo), 600)

    def test_client_uses_csv_before_database_or_api(self):
        repository = Mock()
        client = IBGEClient(repository, CATALOG_PATH)
        client.session.get = Mock(side_effect=AssertionError("A API não deveria ser chamada"))

        code, name, suggestions = client.resolve("sp", "Sao Paulo")

        self.assertEqual(code, "3550308")
        self.assertEqual(name, "São Paulo")
        self.assertEqual(suggestions, [])
        repository.municipalities_for_uf.assert_not_called()
        client.session.get.assert_not_called()

        accented_code, accented_name, _ = client.resolve("SP", "São Paulo")
        self.assertEqual((accented_code, accented_name), ("3550308", "São Paulo"))

    def test_code_is_validated_against_local_catalog(self):
        client = IBGEClient(Mock(), CATALOG_PATH)
        self.assertEqual(client.resolve_code("SP", "3550308"), "São Paulo")
        self.assertIsNone(client.resolve_code("RJ", "3550308"))

    def test_interstate_exact_matches_include_uf_name_and_code(self):
        client = IBGEClient(Mock(), CATALOG_PATH)
        cases = [
            ("RS", "Guarapuava", "PR", "4109401"),
            ("SC", "Londrina", "PR", "4113700"),
            ("PR", "Nova Santa Rita", "RS", "4313375"),
        ]
        for original_uf, city, expected_uf, expected_code in cases:
            with self.subTest(city=city):
                code, name, suggestions = client.resolve(original_uf, city)
                self.assertIsNone(code)
                self.assertIsNone(name)
                self.assertEqual(suggestions[0]["name"], city)
                self.assertEqual(suggestions[0]["uf"], expected_uf)
                self.assertEqual(suggestions[0]["code"], expected_code)
                self.assertIn(expected_uf, suggestions[0]["label"])

    def test_same_region_has_priority_for_homonymous_municipality(self):
        client = IBGEClient(Mock(), CATALOG_PATH)
        _, _, suggestions = client.resolve("SC", "Lajeado")
        self.assertEqual(
            [(item["uf"], item["code"]) for item in suggestions[:2]],
            [("RS", "4311403"), ("TO", "1712009")],
        )

    def test_interstate_similar_name_and_national_code_lookup(self):
        client = IBGEClient(Mock(), CATALOG_PATH)
        _, _, suggestions = client.resolve("RS", "Guarapuva")
        self.assertEqual(suggestions[0]["name"], "Guarapuava")
        self.assertEqual(suggestions[0]["uf"], "PR")
        self.assertEqual(client.resolve_code_national("4311403"), ("RS", "Lajeado"))


if __name__ == "__main__":
    unittest.main()
