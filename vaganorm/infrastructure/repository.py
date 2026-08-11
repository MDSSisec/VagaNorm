from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class LocalRepository:
    def __init__(self, database_path: Path):
        self.database_path = database_path
        database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    @contextmanager
    def _connection(self):
        connection = sqlite3.connect(self.database_path, timeout=10)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._connection() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS municipalities (
                    uf TEXT NOT NULL,
                    normalized_name TEXT NOT NULL,
                    display_name TEXT NOT NULL,
                    ibge_code TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (uf, normalized_name)
                );
                CREATE INDEX IF NOT EXISTS idx_municipalities_uf
                    ON municipalities (uf);

                CREATE TABLE IF NOT EXISTS decisions (
                    kind TEXT NOT NULL,
                    normalized_key TEXT NOT NULL,
                    value_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (kind, normalized_key)
                );
                """
            )

    def municipalities_for_uf(self, uf: str) -> dict[str, tuple[str, str]]:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT normalized_name, display_name, ibge_code FROM municipalities WHERE uf = ?",
                (uf,),
            ).fetchall()
        return {row["normalized_name"]: (row["display_name"], row["ibge_code"]) for row in rows}

    def save_municipalities(self, uf: str, municipalities: list[dict[str, str]]) -> None:
        now = datetime.now(timezone.utc).isoformat()
        values = [
            (uf, item["normalized_name"], item["display_name"], item["ibge_code"], now)
            for item in municipalities
        ]
        with self._connection() as connection:
            connection.executemany(
                """
                INSERT INTO municipalities
                    (uf, normalized_name, display_name, ibge_code, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(uf, normalized_name) DO UPDATE SET
                    display_name = excluded.display_name,
                    ibge_code = excluded.ibge_code,
                    updated_at = excluded.updated_at
                """,
                values,
            )

    def get_decision(self, kind: str, key: str) -> Any | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT value_json FROM decisions WHERE kind = ? AND normalized_key = ?",
                (kind, key),
            ).fetchone()
        return json.loads(row["value_json"]) if row else None

    def save_decision(self, kind: str, key: str, value: Any) -> None:
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO decisions (kind, normalized_key, value_json, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(kind, normalized_key) DO UPDATE SET
                    value_json = excluded.value_json,
                    updated_at = excluded.updated_at
                """,
                (kind, key, json.dumps(value, ensure_ascii=False), datetime.now(timezone.utc).isoformat()),
            )
