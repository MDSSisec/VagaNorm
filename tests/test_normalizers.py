import unittest

from vaganorm.domain.normalizers import (
    clean_null,
    normalize_header,
    normalize_partner_name,
    normalize_city_name,
    parse_age,
    parse_education,
    parse_quantity,
    parse_sex,
)


class NormalizerTests(unittest.TestCase):
    def test_clean_null_is_case_insensitive(self):
        self.assertIsNone(clean_null("nan"))
        self.assertIsNone(clean_null(" Null "))

    def test_header_aliases(self):
        self.assertEqual(normalize_header("Município"), "CIDADE")
        self.assertEqual(normalize_header("Quantidade vagas"), "QUANTIDADE_DE_VAGAS")

    def test_partner_name_is_safe_pascal_case(self):
        self.assertEqual(normalize_partner_name(" Magazine Luíza "), "MagazineLuiza")
        self.assertEqual(normalize_partner_name("açaí / comércio"), "AcaiComercio")
        self.assertEqual(normalize_partner_name("../—"), "")

    def test_age_ranges_and_exclusive_limits(self):
        self.assertEqual(parse_age("18 a 40 anos"), (18, 40, None))
        self.assertEqual(parse_age("maior de 18"), (19, None, None))
        self.assertEqual(parse_age("menor que 60"), (None, 59, None))

    def test_age_validation(self):
        self.assertIsNotNone(parse_age("70-18")[2])
        self.assertIsNotNone(parse_age("999 anos")[2])

    def test_education_distinguishes_superior(self):
        self.assertEqual(parse_education("Superior completo"), ["ES-MAIS"])
        self.assertEqual(parse_education("Superior cursando"), ["EMC", "ES-MAIS"])

    def test_both_sexes_become_indifferent(self):
        self.assertEqual(parse_sex("masculino e feminino"), ["Ind"])
        self.assertEqual(parse_sex("sem restrição de sexo"), ["Ind"])

    def test_quantity_validation(self):
        self.assertEqual(parse_quantity("1.000"), (1000, None))
        self.assertEqual(parse_quantity("2.0"), (2, None))
        self.assertEqual(parse_quantity(2.0), (2, None))
        self.assertIsNotNone(parse_quantity("1,5")[1])
        self.assertIsNotNone(parse_quantity("-2")[1])
    def test_apostrophe_validation(self):
        self.assertEqual(normalize_city_name("São Paulo"), "SAO PAULO")
        self.assertEqual(normalize_city_name("Conceição"), "CONCEICAO")
        self.assertEqual(normalize_city_name("Olho d'Água"), "OLHO DAGUA")

if __name__ == "__main__":
    unittest.main()
