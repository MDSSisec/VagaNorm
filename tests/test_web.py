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

    def test_home_and_static_assets_are_served(self):
        home = self.client.get("/")
        static = self.client.get("/static/app.js")
        self.assertEqual(home.status_code, 200)
        self.assertEqual(static.status_code, 200)
        home.close()
        static.close()

    def test_job_reaches_ready_and_downloads(self):
        response = self.client.post("/api/jobs", data={
            "file": (self.workbook(), "entrada.xlsx"),
            "ibge": "false",
            "ages": "true",
            "education": "true",
            "sex": "true",
        }, content_type="multipart/form-data")
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
        for kind in ("xlsx", "json", "query", "report"):
            download = self.client.get(f"/api/jobs/{job_id}/download/{kind}")
            self.assertEqual(download.status_code, 200)
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
        response = self.client.post("/api/jobs", data={
            "file": (self.workbook(), "entrada.xlsx"),
            "ibge": "false", "ages": "true", "education": "true", "sex": "true",
            "qtd_indv_mode": "custom", "qtd_indv_multiplier": "7",
        }, content_type="multipart/form-data")
        self.assertEqual(response.status_code, 202)
        job_id = response.get_json()["id"]
        job = self.wait_for(job_id, {"allocation_review", "error", "review"})
        self.assertEqual(job["status"], "allocation_review", job)
        allocation = self.get_allocation(job_id)
        self.assertEqual(allocation["localities"], 1)
        self.assertEqual(allocation["total_qtd_indv"], 14)
        self.confirm_allocation(job_id, allocation, {"3550308": 99})
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
        response = self.client.post("/api/jobs", data={
            "file": (self.workbook(), "entrada.xlsx"),
            "qtd_indv_mode": "custom", "qtd_indv_multiplier": "0",
        }, content_type="multipart/form-data")
        self.assertEqual(response.status_code, 400)
        self.assertIn("multiplicador", response.get_json()["error"].lower())

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

    def confirm_allocation(self, job_id, allocation, replacements=None):
        values = {row["cod_ibge"]: row["qtd_indv"] for row in allocation["rows"]}
        values.update(replacements or {})
        response = self.client.post(
            f"/api/jobs/{job_id}/allocation",
            json={"values": values},
        )
        self.assertEqual(response.status_code, 202, response.get_json())
        response.close()


if __name__ == "__main__":
    unittest.main()
