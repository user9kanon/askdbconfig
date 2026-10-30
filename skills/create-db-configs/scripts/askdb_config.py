"""Inspect a SQLite database and render its askdb database YAML.

Run from the repository root so the project's environment provides ``askdb``:

    uv run python ${CLAUDE_SKILL_DIR}/scripts/askdb_config.py inspect <db-url>
    uv run python ${CLAUDE_SKILL_DIR}/scripts/askdb_config.py render <spec.json> <out.yaml>

``inspect`` only reads the database (read-only connection) and prints JSON.
``render`` takes the answers gathered from the user, writes the YAML, and checks
it with the same loader and checks the app runs at startup. It exits 1 on failure.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from contextlib import closing
from pathlib import Path
from typing import Any

from askdb.adapters.sqlite import SqliteAdapter
from askdb.config import ConfigError, database_from_dict, dump_database_yaml
from askdb.registry import ID_PATTERN
from askdb.startup import check_database

# A text column with at most this many distinct values is offered as a candidate for
# listing its values in the description (e.g. status codes).
ENUM_MAX_VALUES = 12
ENUM_SAMPLE_ROWS = 5000


def sqlite_path(url: str) -> Path:
    """Accept ``sqlite:///path`` (SQLAlchemy style) or a plain file path. Only SQLite is supported."""
    if "://" in url and not url.startswith("sqlite:///"):
        raise SystemExit(f"only SQLite is supported in v1; got: {url.split('://', 1)[0]}://")
    raw = url[len("sqlite:///") :] if url.startswith("sqlite:///") else url
    return Path(raw).expanduser().resolve()


def foreign_keys(conn: sqlite3.Connection, table: str) -> dict[str, str]:
    rows = conn.execute(f'PRAGMA foreign_key_list("{table}")').fetchall()
    return {row[3]: f"{row[2]}.{row[4]}" for row in rows if row[4]}


def enum_candidates(conn: sqlite3.Connection, table: str, columns: list[dict[str, Any]]) -> list[dict[str, Any]]:
    found = []
    for col in columns:
        if col["type"].upper() not in ("TEXT", "VARCHAR", "CHAR", "CLOB", ""):
            continue
        values = conn.execute(
            f'SELECT DISTINCT "{col["name"]}" FROM (SELECT "{col["name"]}" FROM "{table}" LIMIT {ENUM_SAMPLE_ROWS}) '
            f"WHERE \"{col['name']}\" IS NOT NULL ORDER BY 1 LIMIT {ENUM_MAX_VALUES + 1}"
        ).fetchall()
        if 0 < len(values) <= ENUM_MAX_VALUES:
            found.append({"column": col["name"], "values": [row[0] for row in values]})
    return found


def inspect(url: str) -> dict[str, Any]:
    path = sqlite_path(url)
    if not path.is_file():
        raise SystemExit(f"database file not found: {path}")
    try:
        tables = SqliteAdapter(path).introspect()
    except sqlite3.Error as exc:
        raise SystemExit(f"cannot open database read-only: {exc}")

    report = {"path": str(path), "tables": []}
    with closing(SqliteAdapter(path).connect()) as conn:
        for name, columns in tables.items():
            fks = foreign_keys(conn, name)
            cols = [
                {
                    "name": c.name,
                    "type": c.type,
                    "primary_key": c.primary_key,
                    "references": fks.get(c.name),
                }
                for c in columns
            ]
            row_count = conn.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0]
            report["tables"].append(
                {
                    "name": name,
                    "row_count": row_count,
                    "columns": cols,
                    "value_lists": enum_candidates(conn, name, cols),
                }
            )
    return report


def render(spec: dict[str, Any], out: Path) -> dict[str, Any]:
    """Build the YAML from the answers, validate it, and write it only if it passes."""
    inspected = {t["name"]: t for t in inspect(spec["path"])["tables"]}
    tables = []
    for wanted in spec["tables"]:
        table_name = wanted["name"]
        if table_name not in inspected:
            raise SystemExit(f"table {table_name} is not in the database")
        described = {c["name"]: c.get("description") for c in wanted.get("columns", [])}
        cols = []
        for col in inspected[table_name]["columns"]:
            entry = {"name": col["name"], "type": col["type"]}
            if col["primary_key"]:
                entry["primary_key"] = True
            if col["references"]:
                entry["references"] = col["references"]
            if described.get(col["name"]):
                entry["description"] = described[col["name"]]
            cols.append(entry)
        tables.append({"name": table_name, "description": wanted["description"], "columns": cols})

    data = {
        "name": spec["name"],
        "display_name": spec["display_name"],
        "path": spec["path"],
        "calendar": {"fiscal_year_start_month": spec.get("fiscal_year_start_month", 1)},
        "description": spec["description"],
        "tables": tables,
    }
    try:
        config = database_from_dict(data, str(out))
    except ConfigError as exc:
        return {"ok": False, "errors": [str(exc)]}
    # The same name rule the admin screen applies (askdb.registry.check_draft).
    errors = []
    if not ID_PATTERN.match(config.name):
        errors.append("name may use only letters, digits, _ and -")
    errors += check_database(config)
    if errors:
        return {"ok": False, "errors": errors}
    out.write_text(dump_database_yaml(data), encoding="utf-8")
    return {"ok": True, "written": str(out), "tables": [t["name"] for t in tables]}


def main(argv: list[str]) -> int:
    if len(argv) == 3 and argv[1] == "inspect":
        print(json.dumps(inspect(argv[2]), indent=2, default=str))
        return 0
    if len(argv) == 4 and argv[1] == "render":
        spec = json.loads(Path(argv[2]).read_text(encoding="utf-8"))
        result = render(spec, Path(argv[3]))
        print(json.dumps(result, indent=2))
        return 0 if result["ok"] else 1
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
