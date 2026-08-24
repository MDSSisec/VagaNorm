import io
import json
import shutil
import time
import unittest
import uuid
from pathlib import Path

import pandas as pd

from vaganorm.web.app_factory import create_app


class WebFlowTests(unittest.TestCase):
    def setUp(self):
        self.temp_path = Path(__file__).resolve().parent / f"runtime_{uuid.uuid4().hex}"
        self.temp_path.mkdir()
        self.app = create_app({
            "TESTING": True,
            "DATA_DIRECTORY": self.temp_path,
        })
        self.client = self.app.test_client()

    def tearDown(self):
        shutil.rmtree(self.temp_path)

    def workbook(self):
        buffer = io.BytesIO()
        frame = pd.DataFrame([{
            "UF": "SP",
            "CIDADE": "São Paulo",
            "QUANTIDADE_DE_VAGAS": 2,
            "FAIXA_ETARIA": "18-40",
            "GRAU_DE_INSTRUCAO": "Médio completo",
            "SEXO": "Indiferente",
            "COD_IBGE": "3550308",
        }])
        with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
            frame.to_excel(writer, sheet_name="Lista", index=False)
        buffer.seek(0)
        return buffer

    def workbook_for(self, uf, city, quantity=2):
        buffer = io.BytesIO()
        frame = pd.DataFrame([{
            "UF": uf,
            "CIDADE": city,
            "QUANTIDADE_DE_VAGAS": quantity,
            "FAIXA_ETARIA": "18-40",
            "GRAU_DE_INSTRUCAO": "Médio completo",
            "SEXO": "Indiferente",
        }])
        with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
            frame.to_excel(writer, sheet_name="Lista", index=False)
        buffer.seek(0)
        return buffer

    def job_data(self, **overrides):
        data = {
            "file": (self.workbook(), "entrada.xlsx"),
            "campaign_code": "123",
            "partner": "Parceiro Teste",
        }
        data.update(overrides)
        return data

    def test_home_and_static_assets_are_served(self):
        home = self.client.get("/")
        static = self.client.get("/static/app.js")
        self.assertEqual(home.status_code, 200)
        self.assertEqual(static.status_code, 200)
        self.assertIn(b'id="campaign-code"', home.data)
        self.assertIn(b'id="partner-name"', home.data)
        home.close()
        static.close()

    def test_job_reaches_ready_and_downloads(self):
        response = self.client.post("/api/jobs", data=self.job_data(**{
            "campaign_code": "00123",
            "partner": "Magazine Luíza",
            "ibge": "false",
            "ages": "true",
            "education": "true",
            "sex": "true",
        }), content_type="multipart/form-data")
        self.assertEqual(response.status_code, 202)
        job_id = response.get_json()["id"]
        job = self.wait_for(job_id, {"allocation_review", "error", "review"})
        self.assertEqual(job["status"], "allocation_review", job)
        allocation = self.get_allocation(job_id)
        self.assertEqual(allocation["localities"], 1)
        self.assertEqual(allocation["total_qtd_indv"], 250)
        self.confirm_allocation(job_id, allocation)
        job = self.wait_for(job_id, {"ready", "error"})
        self.assertEqual(job["status"], "ready", job)
        expected_names = {
            "xlsx": "AC00123_MagazineLuiza_vagas_padronizado.xlsx",
            "json": "AC00123_MagazineLuiza_vagas_json.json",
            "query": "AC00123_MagazineLuiza_vagas_querieData.json",
            "report": "AC00123_MagazineLuiza_vagas_relatorio.json",
        }
        manager = self.app.extensions["job_manager"]
        for kind, expected_name in expected_names.items():
            download = self.client.get(f"/api/jobs/{job_id}/download/{kind}")
            self.assertEqual(download.status_code, 200)
            self.assertIn(f'filename={expected_name}', download.headers["Content-Disposition"])
            self.assertEqual(manager.get(job_id).artifacts[kind].name, expected_name)
            download.close()

    def test_review_decision_is_applied(self):
        workbook = io.BytesIO()
        frame = pd.DataFrame([{
            "UF": "XX", "CIDADE": "São Paulo", "QUANTIDADE_DE_VAGAS": 1,
        }])
        with pd.ExcelWriter(workbook, engine="openpyxl") as writer:
            frame.to_excel(writer, sheet_name="Lista", index=False)
        workbook.seek(0)
        response = self.client.post("/api/jobs", data={
            "file": (workbook, "entrada.xlsx"),
            "campaign_code": "123", "partner": "Parceiro Teste",
            "ibge": "false", "ages": "false", "education": "false", "sex": "false",
        }, content_type="multipart/form-data")
        job_id = response.get_json()["id"]
        job = self.wait_for(job_id, {"review", "error"})
        self.assertEqual(job["status"], "review", job)
        issues = self.client.get(f"/api/jobs/{job_id}/issues").get_json()["issues"]
        self.assertEqual(len(issues), 1)
        decision = self.client.post(f"/api/jobs/{job_id}/decisions", json={
            "values": {issues[0]["id"]: "SP"}, "remember": False,
        })
        self.assertEqual(decision.status_code, 202)
        job = self.wait_for(job_id, {"allocation_review", "error", "review"})
        self.assertEqual(job["status"], "allocation_review", job)
        self.confirm_allocation(job_id, self.get_allocation(job_id))
        job = self.wait_for(job_id, {"ready", "error"})
        self.assertEqual(job["status"], "ready", job)

    def test_custom_qtd_indv_multiplier_reaches_query_and_report(self):
        response = self.client.post("/api/jobs", data=self.job_data(**{
            "ibge": "false", "ages": "true", "education": "true", "sex": "true",
            "qtd_indv_mode": "custom", "qtd_indv_multiplier": "7",
        }), content_type="multipart/form-data")
        self.assertEqual(response.status_code, 202)
        job_id = response.get_json()["id"]
        job = self.wait_for(job_id, {"allocation_review", "error", "review"})
        self.assertEqual(job["status"], "allocation_review", job)
        allocation = self.get_allocation(job_id)
        self.assertEqual(allocation["localities"], 1)
        self.assertEqual(allocation["total_qtd_indv"], 14)
        self.confirm_allocation(
            job_id, allocation, {"3550308": 99},
            qtd_indv_mode="custom", qtd_indv_multiplier=7,
        )
        job = self.wait_for(job_id, {"ready", "error"})
        self.assertEqual(job["status"], "ready", job)

        query_response = self.client.get(f"/api/jobs/{job_id}/download/query")
        query_data = json.loads(query_response.data.decode("utf-8"))
        query_response.close()
        self.assertEqual(query_data[0]["QUANTIDADE_DE_VAGAS"], 2)
        self.assertEqual(query_data[0]["QTD_INDV"], 99)

        report_response = self.client.get(f"/api/jobs/{job_id}/download/report")
        report = json.loads(report_response.data.decode("utf-8"))
        report_response.close()
        self.assertEqual(report["options"]["qtd_indv_mode"], "custom")
        self.assertEqual(report["options"]["qtd_indv_multiplier"], 7)
        self.assertEqual(report["options"]["campaign_code"], "123")
        self.assertEqual(report["options"]["partner_name"], "ParceiroTeste")
        self.assertTrue(report["options"]["ibge"])
        self.assertTrue(report["options"]["ages"])
        self.assertTrue(report["options"]["education"])
        self.assertTrue(report["options"]["sex"])
        self.assertEqual(report["qtd_indv_review"]["unique_localities"], 1)
        self.assertEqual(report["qtd_indv_review"]["calculated_total"], 14)
        self.assertEqual(report["qtd_indv_review"]["confirmed_total"], 99)
        self.assertEqual(
            report["qtd_indv_review"]["changes"]["3550308"],
            {"calculated": 14, "confirmed": 99},
        )

    def test_custom_multiplier_must_be_positive_integer(self):
        response = self.client.post("/api/jobs", data=self.job_data(**{
            "qtd_indv_mode": "custom", "qtd_indv_multiplier": "0",
        }), content_type="multipart/form-data")
        self.assertEqual(response.status_code, 400)
        self.assertIn("multiplicador", response.get_json()["error"].lower())

    def test_campaign_and_partner_are_validated_by_backend(self):
        cases = [
            ({"partner": "Parceiro"}, "campanha"),
            ({"campaign_code": "0", "partner": "Parceiro"}, "campanha"),
            ({"campaign_code": "12A", "partner": "Parceiro"}, "campanha"),
            ({"campaign_code": " 123", "partner": "Parceiro"}, "campanha"),
            ({"campaign_code": "１２３", "partner": "Parceiro"}, "campanha"),
            ({"campaign_code": "123"}, "parceiro"),
            ({"campaign_code": "123", "partner": "../—"}, "parceiro"),
        ]
        for fields, expected_error in cases:
            with self.subTest(fields=fields):
                data = {"file": (self.workbook(), "entrada.xlsx"), **fields}
                response = self.client.post(
                    "/api/jobs", data=data, content_type="multipart/form-data"
                )
                self.assertEqual(response.status_code, 400)
                self.assertIn(expected_error, response.get_json()["error"].lower())
                response.close()

    def test_interstate_decision_propagates_to_all_data_exports(self):
        data = self.job_data(file=(self.workbook_for("SC", "Londrina"), "entrada.xlsx"))
        response = self.client.post(
            "/api/jobs", data=data, content_type="multipart/form-data"
        )
        job_id = response.get_json()["id"]
        job = self.wait_for(job_id, {"review", "error"})
        self.assertEqual(job["status"], "review", job)
        issue = self.client.get(f"/api/jobs/{job_id}/issues").get_json()["issues"][0]
        suggestion = next(item for item in issue["suggestions"] if item["code"] == "4113700")
        self.assertEqual((suggestion["name"], suggestion["uf"]), ("Londrina", "PR"))

        decision = self.client.post(f"/api/jobs/{job_id}/decisions", json={
            "values": {issue["id"]: {"code": suggestion["code"], "uf": suggestion["uf"]}},
            "remember": True,
        })
        self.assertEqual(decision.status_code, 202)
        job = self.wait_for(job_id, {"allocation_review", "review", "error"})
        self.assertEqual(job["status"], "allocation_review", job)
        allocation = self.get_allocation(job_id)
        self.assertEqual(allocation["rows"][0]["cod_ibge"], "4113700")
        self.assertEqual(allocation["rows"][0]["uf"], "PR")
        self.confirm_allocation(job_id, allocation)
        job = self.wait_for(job_id, {"ready", "error"})
        self.assertEqual(job["status"], "ready", job)

        json_response = self.client.get(f"/api/jobs/{job_id}/download/json")
        complete_json = json.loads(json_response.data.decode("utf-8"))
        json_response.close()
        self.assertEqual(
            (complete_json[0]["UF"], complete_json[0]["CIDADE"], complete_json[0]["COD_IBGE"]),
            ("PR", "Londrina", "4113700"),
        )
        query_response = self.client.get(f"/api/jobs/{job_id}/download/query")
        query_json = json.loads(query_response.data.decode("utf-8"))
        query_response.close()
        self.assertEqual(
            (query_json[0]["UF"], query_json[0]["CIDADE"], query_json[0]["COD_IBGE"]),
            ("PR", "LONDRINA", "4113700"),
        )
        excel_response = self.client.get(f"/api/jobs/{job_id}/download/xlsx")
        excel = pd.read_excel(io.BytesIO(excel_response.data), sheet_name="Lista", dtype=object)
        excel_response.close()
        self.assertEqual(
            (excel.iloc[0]["UF"], excel.iloc[0]["CIDADE"], str(excel.iloc[0]["COD_IBGE"])),
            ("PR", "Londrina", "4113700"),
        )

        repeated = self.client.post("/api/jobs", data=self.job_data(
            file=(self.workbook_for("SC", "Londrina"), "entrada.xlsx")
        ), content_type="multipart/form-data")
        repeated_job_id = repeated.get_json()["id"]
        repeated_job = self.wait_for(
            repeated_job_id, {"allocation_review", "review", "error"}
        )
        self.assertEqual(repeated_job["status"], "allocation_review", repeated_job)
        repeated_allocation = self.get_allocation(repeated_job_id)
        self.assertEqual(repeated_allocation["rows"][0]["uf"], "PR")
        self.assertEqual(repeated_allocation["rows"][0]["cod_ibge"], "4113700")

    def test_invalid_uf_suggestion_propagates_to_all_data_exports(self):
        response = self.client.post("/api/jobs", data=self.job_data(
            file=(self.workbook_for("S", "ITAJAI"), "entrada.xlsx")
        ), content_type="multipart/form-data")
        job_id = response.get_json()["id"]
        job = self.wait_for(job_id, {"review", "error"})
        self.assertEqual(job["status"], "review", job)
        issue = self.client.get(f"/api/jobs/{job_id}/issues").get_json()["issues"][0]
        self.assertEqual(issue["kind"], "invalid_uf")
        self.assertEqual(issue["context"], {"uf": "S", "city": "ITAJAI"})
        suggestion = next(item for item in issue["suggestions"] if item["code"] == "4208203")
        self.assertEqual((suggestion["name"], suggestion["uf"]), ("Itajaí", "SC"))

        decision = self.client.post(f"/api/jobs/{job_id}/decisions", json={
            "values": {issue["id"]: {"uf": "SC", "code": "4208203"}},
            "remember": True,
        })
        self.assertEqual(decision.status_code, 202)
        job = self.wait_for(job_id, {"allocation_review", "review", "error"})
        self.assertEqual(job["status"], "allocation_review", job)
        allocation = self.get_allocation(job_id)
        self.assertEqual(
            (allocation["rows"][0]["uf"], allocation["rows"][0]["cidade"], allocation["rows"][0]["cod_ibge"]),
            ("SC", "Itajaí", "4208203"),
        )
        self.confirm_allocation(job_id, allocation)
        job = self.wait_for(job_id, {"ready", "error"})
        self.assertEqual(job["status"], "ready", job)

        json_response = self.client.get(f"/api/jobs/{job_id}/download/json")
        complete_json = json.loads(json_response.data.decode("utf-8"))
        json_response.close()
        query_response = self.client.get(f"/api/jobs/{job_id}/download/query")
        query_json = json.loads(query_response.data.decode("utf-8"))
        query_response.close()
        excel_response = self.client.get(f"/api/jobs/{job_id}/download/xlsx")
        excel = pd.read_excel(io.BytesIO(excel_response.data), sheet_name="Lista", dtype=object)
        excel_response.close()
        self.assertEqual(
            (complete_json[0]["UF"], complete_json[0]["CIDADE"], complete_json[0]["COD_IBGE"]),
            ("SC", "Itajaí", "4208203"),
        )
        self.assertEqual(
            (query_json[0]["UF"], query_json[0]["CIDADE"], query_json[0]["COD_IBGE"]),
            ("SC", "ITAJAI", "4208203"),
        )
        self.assertEqual(
            (excel.iloc[0]["UF"], excel.iloc[0]["CIDADE"], str(excel.iloc[0]["COD_IBGE"])),
            ("SC", "Itajaí", "4208203"),
        )
        report_response = self.client.get(f"/api/jobs/{job_id}/download/report")
        report = json.loads(report_response.data.decode("utf-8"))
        report_response.close()
        self.assertEqual(
            {field: report["changes"][field] for field in ("UF", "CIDADE", "COD_IBGE")},
            {"UF": 1, "CIDADE": 1, "COD_IBGE": 1},
        )

        repeated = self.client.post("/api/jobs", data=self.job_data(
            file=(self.workbook_for("S", "ITAJAI"), "entrada.xlsx")
        ), content_type="multipart/form-data")
        repeated_job_id = repeated.get_json()["id"]
        repeated_job = self.wait_for(
            repeated_job_id, {"allocation_review", "review", "error"}
        )
        self.assertEqual(repeated_job["status"], "allocation_review", repeated_job)

    def wait_for(self, job_id, terminal_statuses):
        job = None
        for _ in range(100):
            job = self.client.get(f"/api/jobs/{job_id}").get_json()
            if job["status"] in terminal_statuses:
                return job
            time.sleep(0.02)
        return job

    def get_allocation(self, job_id):
        response = self.client.get(f"/api/jobs/{job_id}/allocation")
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        response.close()
        return payload

    def confirm_allocation(self, job_id, allocation, replacements=None, qtd_indv_mode="standard", qtd_indv_multiplier=None):
        values = {row["cod_ibge"]: row["qtd_indv"] for row in allocation["rows"]}
        values.update(replacements or {})
        response = self.client.post(
            f"/api/jobs/{job_id}/allocation",
            json={"values": values,
                  "qtd_indv_mode": qtd_indv_mode,
                  "qtd_indv_multiplier": qtd_indv_multiplier,},
        )
        self.assertEqual(response.status_code, 202, response.get_json())
        response.close()


if __name__ == "__main__":
    unittest.main()
