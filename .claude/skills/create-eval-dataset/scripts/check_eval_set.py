"""Check an evaluation set's format and run its expected SQL against the database.

Run from the repository root:

    uv run python ${CLAUDE_SKILL_DIR}/scripts/check_eval_set.py <eval.yaml> --config <db.yaml>

Errors make the check fail (exit 1). Warnings are printed but do not fail it.
The database is opened read-only, through the same layer the app uses.
"""

from __future__ import annotations

import json
import re
import sys
from datetime import date
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))

from askdb.adapters.sqlite import SqliteAdapter  # noqa: E402
from askdb.adapters.base import QueryError  # noqa: E402
from askdb.config import ConfigError, load_database_config  # noqa: E402
from askdb.sqlguard import UnsafeSQL, validate  # noqa: E402

# High row cap so expected SQL is compared in full, not truncated.
EXPECTED_ROW_LIMIT = 1_000_000
TIMEOUT_SECONDS = 60
OUTCOMES = {"clarification", "declined"}
CASE_KEYS = {"id", "question", "category", "expected_sql", "expected_outcome", "today", "conversation", "notes"}
ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")


class Report:
    def __init__(self) -> None:
        self.errors: list[str] = []
        self.warnings: list[str] = []
        self.summary: dict[str, Any] | None = None
        self.samples: list[dict[str, Any]] = []

    def error(self, where: str, message: str) -> None:
        self.errors.append(f"{where}: {message}")

    def warn(self, where: str, message: str) -> None:
        self.warnings.append(f"{where}: {message}")


def check_sql(report: Report, where: str, sql: str, adapter: SqliteAdapter) -> dict[str, Any] | None:
    """Validate that the SQL is one SELECT and run it. Returns the result, or None."""
    try:
        validate(sql)
    except UnsafeSQL as exc:
        report.error(where, f"expected SQL is not a single read-only SELECT: {exc}")
        return None
    try:
        result = adapter.execute(sql, EXPECTED_ROW_LIMIT, TIMEOUT_SECONDS)
    except QueryError as exc:
        report.error(where, f"expected SQL failed against the database: {exc}")
        return None
    if result.total == 0:
        report.warn(where, "expected SQL returns no rows, so the case cannot tell a wrong answer from an empty one")
    return result


def check_case(report: Report, index: int, case: Any, adapter: SqliteAdapter, seen: set[str]) -> None:
    where = f"cases[{index}]"
    if not isinstance(case, dict):
        report.error(where, "must be a mapping")
        return
    case_id = case.get("id")
    where = f"cases[{index}] ({case_id})" if case_id else where

    for key in case:
        if key not in CASE_KEYS:
            report.error(where, f"unknown key '{key}' (check for a typo)")
    if not isinstance(case_id, str) or not ID_PATTERN.match(case_id):
        report.error(where, "id must be letters, digits, _ or -")
    elif case_id in seen:
        report.error(where, "duplicate id")
    else:
        seen.add(case_id)
    if not isinstance(case.get("question"), str) or not case["question"].strip():
        report.error(where, "question is required")
    if not isinstance(case.get("category"), str) or not case["category"].strip():
        report.error(where, "category is required")
    if "today" in case:
        try:
            date.fromisoformat(str(case["today"]))
        except ValueError:
            report.error(where, "today must be an ISO date such as 2025-06-30")

    has_sql = "expected_sql" in case
    has_outcome = "expected_outcome" in case
    if not has_sql and not has_outcome:
        report.error(where, "needs expected_sql, or expected_outcome for a clarification or decline case")
    if has_outcome and case["expected_outcome"] not in OUTCOMES:
        report.error(where, f"expected_outcome must be one of {sorted(OUTCOMES)}")
    if has_outcome and has_sql:
        report.warn(where, "has both expected_outcome and expected_sql; the outcome is what is graded")

    if has_sql:
        result = check_sql(report, where, str(case["expected_sql"]), adapter)
        if result:
            report.samples.append({
                "id": case_id,
                "rows": result.total,
                "columns": result.columns,
                "first_rows": [list(r) for r in result.rows[:3]],
            })

    for n, turn in enumerate(case.get("conversation") or []):
        if not isinstance(turn, dict) or not turn.get("question") or not turn.get("sql"):
            report.error(where, f"conversation turn {n + 1} needs question and sql")
            continue
        check_sql(report, f"{where} conversation turn {n + 1}", str(turn["sql"]), adapter)


def check_set(eval_path: Path, config_path: Path) -> Report:
    report = Report()
    try:
        config = load_database_config(config_path)
    except ConfigError as exc:
        report.error("config", str(exc))
        return report

    try:
        data = yaml.safe_load(eval_path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        report.error("eval file", f"cannot read as YAML: {exc}")
        return report
    if not isinstance(data, dict):
        report.error("eval file", "top level must be a mapping with database and cases")
        return report
    if data.get("database") != config.name:
        report.error("database", f"must be '{config.name}' to match the database config, got {data.get('database')!r}")
    cases = data.get("cases")
    if not isinstance(cases, list) or not cases:
        report.error("cases", "must be a non-empty list")
        return report

    adapter = SqliteAdapter(Path(config.path))
    seen: set[str] = set()
    for index, case in enumerate(cases):
        check_case(report, index, case, adapter, seen)

    categories: dict[str, int] = {}
    for case in cases:
        if isinstance(case, dict) and isinstance(case.get("category"), str):
            categories[case["category"]] = categories.get(case["category"], 0) + 1
    report.summary = {"cases": len(cases), "categories": categories}
    return report


def main(argv: list[str]) -> int:
    if len(argv) != 4 or argv[2] != "--config":
        print(__doc__)
        return 2
    report = check_set(Path(argv[1]), Path(argv[3]))
    print(json.dumps({"ok": not report.errors, "errors": report.errors, "warnings": report.warnings,
                      "summary": report.summary, "samples": report.samples}, indent=2, default=str))
    return 0 if not report.errors else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
