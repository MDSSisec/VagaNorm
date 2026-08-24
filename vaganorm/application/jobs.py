from __future__ import annotations

import queue
import shutil
import threading
import traceback
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from ..domain.models import PipelineResult
from ..domain.aggregation import aggregate_query_data
from ..infrastructure.excel import InputValidationError, read_input
from ..infrastructure.exporter import export_all
from ..infrastructure.repository import LocalRepository
from .pipeline import StandardizationPipeline


@dataclass
class Job:
    id: str
    directory: Path
    input_path: Path
    options: dict[str, Any]
    status: str = "received"
    progress: int = 0
    message: str = "Arquivo recebido"
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    source_df: pd.DataFrame | None = None
    input_warnings: list[str] = field(default_factory=list)
    result: PipelineResult | None = None
    decisions: dict[str, Any] = field(default_factory=dict)
    allocation: list[dict[str, Any]] = field(default_factory=list)
    qtd_indv_overrides: dict[str, int] = field(default_factory=dict)
    artifacts: dict[str, Path] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    events: queue.Queue = field(default_factory=queue.Queue, repr=False)

    def public_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "status": self.status,
            "progress": self.progress,
            "message": self.message,
            "created_at": self.created_at.isoformat(),
            "issues": len(self.result.issues) if self.result else 0,
            "warnings": self.result.warnings if self.result else [],
            "changes": self.result.changes if self.result else {},
            "rows": len(self.result.dataframe) if self.result is not None else 0,
            "errors": self.errors,
            "downloads": list(self.artifacts) if self.status == "ready" else [],
            "localities": len(self.allocation),
            "qtd_indv_total": sum(
                self.qtd_indv_overrides.get(item["COD_IBGE"], item["QTD_INDV"])
                for item in self.allocation
            ),
        }


class JobManager:
    def __init__(self, root: Path, pipeline: StandardizationPipeline, repository: LocalRepository):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self.pipeline = pipeline
        self.repository = repository
        self.jobs: dict[str, Job] = {}
        self.lock = threading.RLock()

    def create(self, upload, options: dict[str, Any]) -> Job:
        self.cleanup()
        job_id = uuid.uuid4().hex
        directory = self.root / job_id
        directory.mkdir(parents=True)
        input_path = directory / "entrada.xlsx"
        upload.save(input_path)
        job = Job(job_id, directory, input_path, options)
        with self.lock:
            self.jobs[job_id] = job
        self._start(job)
        return job

    def get(self, job_id: str) -> Job | None:
        with self.lock:
            return self.jobs.get(job_id)

    def _emit(self, job: Job, event: str, **payload: Any) -> None:
        job.events.put({"event": event, **payload})

    def _set_status(self, job: Job, status: str, progress: int, message: str) -> None:
        job.status, job.progress, job.message = status, progress, message
        self._emit(job, "status", **job.public_dict())

    def _start(self, job: Job) -> None:
        thread = threading.Thread(target=self._analyze, args=(job,), daemon=True)
        thread.start()

    def _analyze(self, job: Job) -> None:
        try:
            self._set_status(job, "validating", 10, "Validando a estrutura da planilha")
            if job.source_df is None:
                job.source_df, job.input_warnings = read_input(job.input_path)
            self._set_status(job, "processing", 35, "Aplicando regras de padronização")
            result = self.pipeline.run(job.source_df, job.options, job.decisions)
            result.warnings = job.input_warnings + result.warnings
            job.result = result
            if result.issues:
                self._set_status(
                    job, "review", 70,
                    f"{len(result.issues)} pendência(s) agrupada(s) aguardando revisão",
                )
                self._emit(job, "review", issues=len(result.issues))
            else:
                self._prepare_allocation_review(job)
        except InputValidationError as exc:
            job.errors = exc.errors
            self._set_status(job, "error", 100, "A planilha não passou na validação estrutural")
        except Exception:
            job.errors = [traceback.format_exc()]
            self._set_status(job, "error", 100, "Falha inesperada durante o processamento")

    def submit_decisions(self, job: Job, values: dict[str, Any], remember: bool) -> None:
        if job.status != "review" or job.result is None:
            raise ValueError("Este processamento não está aguardando revisão.")
        known = {issue.id: issue for issue in job.result.issues}
        unknown = sorted(set(values) - set(known))
        if unknown:
            raise ValueError(f"Pendências desconhecidas: {', '.join(unknown)}")
        job.decisions.update(values)
        if remember:
            for issue_id, value in values.items():
                issue = known[issue_id]
                if issue.kind != "missing_location":
                    self.repository.save_decision(issue.kind, issue.key, value)
        self._set_status(job, "processing", 75, "Aplicando as decisões da revisão")
        self._start(job)

    def _prepare_allocation_review(self, job: Job) -> None:
        if job.result is None:
            raise RuntimeError("Resultado ausente.")
        allocation, warnings = aggregate_query_data(
            job.result.dataframe,
            job.result.excluded_query_indices,
            qtd_indv_mode=str(job.options.get("qtd_indv_mode", "standard")),
            qtd_indv_multiplier=job.options.get("qtd_indv_multiplier"),
        )
        for warning in warnings:
            if warning not in job.result.warnings:
                job.result.warnings.append(warning)
        job.allocation = allocation
        job.qtd_indv_overrides = {}
        self._set_status(
            job,
            "allocation_review",
            85,
            f"Conferência de QTD_INDV para {len(allocation)} localidade(s)",
        )
        self._emit(job, "allocation_review", localities=len(allocation))

    def allocation_public(self, job: Job) -> dict[str, Any]:
        rows = [
            {
                "cod_ibge": item["COD_IBGE"],
                "uf": item["UF"],
                "cidade": item["CIDADE"],
                "quantidade_vagas": item["QUANTIDADE_DE_VAGAS"],
                "qtd_indv": job.qtd_indv_overrides.get(item["COD_IBGE"], item["QTD_INDV"]),
            }
            for item in job.allocation
        ]
        return {
            "localities": len(rows),
            "total_qtd_indv": sum(item["qtd_indv"] for item in rows),
            "rows": rows,
        }

    def submit_allocation(self, job: Job, values: dict[str, Any], qtd_indv_mode: str, qtd_indv_multiplier: int | None) -> None:
        if job.status != "allocation_review":
            raise ValueError("Este processamento não está aguardando a conferência de QTD_INDV.")
        expected = {item["COD_IBGE"] for item in job.allocation}
        provided = set(values)
        if provided != expected:
            missing = sorted(expected - provided)
            unknown = sorted(provided - expected)
            details = []
            if missing:
                details.append(f"localidades ausentes: {', '.join(missing)}")
            if unknown:
                details.append(f"localidades desconhecidas: {', '.join(unknown)}")
            raise ValueError("Valores de QTD_INDV incompletos: " + "; ".join(details))
        normalized: dict[str, int] = {}
        for code, value in values.items():
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"QTD_INDV de {code} deve ser um inteiro maior ou igual a zero.")
            normalized[code] = value
        job.qtd_indv_overrides = normalized
        job.options["qtd_indv_mode"] = qtd_indv_mode
        job.options["qtd_indv_multiplier"] = qtd_indv_multiplier
        resultado, _ = aggregate_query_data(
            job.result.dataframe,
            job.result.excluded_query_indices,
            qtd_indv_mode=str(job.options.get("qtd_indv_mode", "standard")),
            qtd_indv_multiplier=job.options.get("qtd_indv_multiplier"),
        )
        job.allocation = resultado
        self._set_status(job, "exporting", 90, "Gerando arquivos com os valores confirmados")
        threading.Thread(target=self._export_safely, args=(job,), daemon=True).start()

    def _export_safely(self, job: Job) -> None:
        try:
            self._export(job)
        except Exception:
            job.errors = [traceback.format_exc()]
            self._set_status(job, "error", 100, "Falha inesperada durante a exportação")

    def _export(self, job: Job) -> None:
        if job.result is None:
            raise RuntimeError("Resultado ausente.")
        initial_by_code = {item["COD_IBGE"]: item["QTD_INDV"] for item in job.allocation}
        final_by_code = {
            code: job.qtd_indv_overrides.get(code, value)
            for code, value in initial_by_code.items()
        }
        changed = {
            code: {"calculated": initial_by_code[code], "confirmed": final}
            for code, final in final_by_code.items()
            if final != initial_by_code[code]
        }
        report = {
            "job_id": job.id,
            "created_at": job.created_at.isoformat(),
            "rows": len(job.result.dataframe),
            "changes": job.result.changes,
            "warnings": job.result.warnings,
            "excluded_from_query": len(job.result.excluded_query_indices),
            "options": job.options,
            "qtd_indv_review": {
                "unique_localities": len(job.allocation),
                "calculated_total": sum(initial_by_code.values()),
                "confirmed_total": sum(final_by_code.values()),
                "changes": changed,
            },
        }
        job.artifacts, export_warnings = export_all(
            job.result.dataframe,
            job.directory / "output",
            job.result.excluded_query_indices,
            report,
            campaign_code=str(job.options["campaign_code"]),
            partner_name=str(job.options["partner_name"]),
            qtd_indv_mode=str(job.options.get("qtd_indv_mode", "standard")),
            qtd_indv_multiplier=job.options.get("qtd_indv_multiplier"),
            qtd_indv_overrides=job.qtd_indv_overrides,
        )
        job.result.warnings.extend(export_warnings)
        self._set_status(job, "ready", 100, "Processamento concluído e arquivos disponíveis")
        self._emit(job, "complete", downloads=list(job.artifacts))

    def cleanup(self, maximum_age: timedelta = timedelta(hours=24)) -> None:
        threshold = datetime.now(timezone.utc) - maximum_age
        with self.lock:
            expired = [job_id for job_id, job in self.jobs.items() if job.created_at < threshold]
            for job_id in expired:
                job = self.jobs.pop(job_id)
                resolved = job.directory.resolve()
                if resolved.parent == self.root.resolve() and resolved.exists():
                    shutil.rmtree(resolved)
