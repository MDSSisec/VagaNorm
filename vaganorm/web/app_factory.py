from __future__ import annotations

import json
import queue
from pathlib import Path

from flask import Flask, Response, jsonify, render_template, request, send_file, stream_with_context

from ..application.jobs import JobManager
from ..application.pipeline import StandardizationPipeline
from ..domain.normalizers import EDUCATION_DESCRIPTIONS, EDUCATION_LEVELS, SEX_DESCRIPTIONS, VALID_UFS
from ..infrastructure.ibge import IBGEClient
from ..infrastructure.repository import LocalRepository


def create_app(test_config: dict | None = None) -> Flask:
    project_root = Path(__file__).resolve().parents[2]
    app = Flask(
        __name__,
        template_folder=str(project_root / "templates"),
        static_folder=str(project_root / "static"),
    )
    app.config.update(
        MAX_CONTENT_LENGTH=25 * 1024 * 1024,
        DATA_DIRECTORY=project_root / "data",
        IBGE_CSV_PATH=project_root / "codigos_ibge_csv.csv",
    )
    if test_config:
        app.config.update(test_config)

    data_directory = Path(app.config["DATA_DIRECTORY"])
    repository = LocalRepository(data_directory / "vaganorm.db")
    ibge = IBGEClient(repository, Path(app.config["IBGE_CSV_PATH"]))
    pipeline = StandardizationPipeline(ibge, repository)
    manager = JobManager(data_directory / "jobs", pipeline, repository)
    app.extensions["job_manager"] = manager

    @app.get("/")
    def index():
        return render_template(
            "index.html",
            education_levels=EDUCATION_LEVELS,
            education_descriptions=EDUCATION_DESCRIPTIONS,
            sex_descriptions=SEX_DESCRIPTIONS,
            valid_ufs=sorted(VALID_UFS),
        )

    @app.get("/api/health")
    def health():
        catalog_path = Path(app.config["IBGE_CSV_PATH"])
        return jsonify({
            "ok": True,
            "ibge_catalog": {
                "available": catalog_path.is_file(),
                "path": str(catalog_path),
            },
        })

    @app.post("/api/jobs")
    def create_job():
        upload = request.files.get("file")
        if upload is None or not upload.filename:
            return jsonify({"error": "Selecione uma planilha .xlsx."}), 400
        if not upload.filename.lower().endswith(".xlsx"):
            return jsonify({"error": "Formato não suportado. Envie um arquivo .xlsx."}), 400
        qtd_indv_mode = request.form.get("qtd_indv_mode", "standard")
        if qtd_indv_mode not in {"standard", "custom"}:
            return jsonify({"error": "Regra de QTD_INDV inválida."}), 400
        qtd_indv_multiplier = None
        if qtd_indv_mode == "custom":
            raw_multiplier = request.form.get("qtd_indv_multiplier", "").strip()
            if not raw_multiplier.isdigit() or int(raw_multiplier) <= 0:
                return jsonify({
                    "error": "Informe um multiplicador inteiro positivo para QTD_INDV."
                }), 400
            qtd_indv_multiplier = int(raw_multiplier)

        options = {
            "ibge": True,
            "ages": True,
            "education": True,
            "sex": True,
            "qtd_indv_mode": qtd_indv_mode,
            "qtd_indv_multiplier": qtd_indv_multiplier,
        }
        job = manager.create(upload, options)
        return jsonify(job.public_dict()), 202

    @app.get("/api/jobs/<job_id>")
    def get_job(job_id: str):
        job = manager.get(job_id)
        if job is None:
            return jsonify({"error": "Processamento não encontrado."}), 404
        return jsonify(job.public_dict())

    @app.get("/api/jobs/<job_id>/issues")
    def get_issues(job_id: str):
        job = manager.get(job_id)
        if job is None:
            return jsonify({"error": "Processamento não encontrado."}), 404
        if job.result is None:
            return jsonify({"issues": []})
        return jsonify({"issues": [issue.public_dict() for issue in job.result.issues]})

    @app.post("/api/jobs/<job_id>/decisions")
    def submit_decisions(job_id: str):
        job = manager.get(job_id)
        if job is None:
            return jsonify({"error": "Processamento não encontrado."}), 404
        payload = request.get_json(silent=True) or {}
        values = payload.get("values")
        if not isinstance(values, dict):
            return jsonify({"error": "O campo 'values' deve ser um objeto."}), 400
        try:
            manager.submit_decisions(job, values, bool(payload.get("remember")))
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 409
        return jsonify(job.public_dict()), 202

    @app.get("/api/jobs/<job_id>/allocation")
    def get_allocation(job_id: str):
        job = manager.get(job_id)
        if job is None:
            return jsonify({"error": "Processamento não encontrado."}), 404
        if job.status != "allocation_review":
            return jsonify({"error": "A conferência de QTD_INDV ainda não está disponível."}), 409
        return jsonify(manager.allocation_public(job))

    @app.post("/api/jobs/<job_id>/allocation")
    def submit_allocation(job_id: str):
        job = manager.get(job_id)
        if job is None:
            return jsonify({"error": "Processamento não encontrado."}), 404
        payload = request.get_json(silent=True) or {}
        values = payload.get("values")
        if not isinstance(values, dict):
            return jsonify({"error": "O campo 'values' deve ser um objeto por código IBGE."}), 400
        try:
            manager.submit_allocation(job, values)
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 409
        return jsonify(job.public_dict()), 202

    @app.get("/api/jobs/<job_id>/events")
    def job_events(job_id: str):
        job = manager.get(job_id)
        if job is None:
            return jsonify({"error": "Processamento não encontrado."}), 404

        @stream_with_context
        def generate():
            yield f"data: {json.dumps({'event': 'status', **job.public_dict()}, ensure_ascii=False)}\n\n"
            while True:
                try:
                    event = job.events.get(timeout=10)
                    yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                except queue.Empty:
                    yield "data: {\"event\": \"ping\"}\n\n"
                if job.status in {"ready", "error"} and job.events.empty():
                    return

        return Response(generate(), mimetype="text/event-stream", headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        })

    @app.get("/api/jobs/<job_id>/download/<kind>")
    def download(job_id: str, kind: str):
        job = manager.get(job_id)
        if job is None:
            return jsonify({"error": "Processamento não encontrado."}), 404
        path = job.artifacts.get(kind)
        if job.status != "ready" or path is None or not path.exists():
            return jsonify({"error": "Arquivo ainda não está disponível."}), 409
        names = {
            "xlsx": "vagas_padronizadas.xlsx",
            "json": "vagas_padronizadas.json",
            "query": "vagas_padronizadas_querieData.json",
            "report": "relatorio_processamento.json",
        }
        return send_file(path, as_attachment=True, download_name=names[kind])

    @app.errorhandler(413)
    def too_large(_error):
        return jsonify({"error": "O arquivo excede o limite de 25 MB."}), 413

    return app
