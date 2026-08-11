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


if __name__ == "__main__":
    unittest.main()
