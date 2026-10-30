---
name: create-db-configs
description: Create the askdb database YAML file for a new SQLite database, given its file path or sqlite:/// URL. Use this whenever someone wants to add, configure, onboard, or register a database in askdb, or has a SQLite file and asks for its schema YAML, config, or upload file, even if they don't say "YAML" or "configure". It inspects the real schema (tables, types, keys, foreign keys), asks the user about anything the schema can't tell it (names, business rules, which tables, which value lists are safe to show), writes the file, and checks it with the same rules the admin screen uses.
compatibility: Run from the askdb repository root with uv (the script imports the askdb package). SQLite only.
---

# Create database config files for askdb

The output is the schema YAML that support uploads in the admin Databases panel. The file says what the model sees for every question, so its descriptions decide answer quality. Two rules shape the work:

- Business rules and metric definitions go in the database `description` and nowhere else. Never put them in code.
- The YAML never holds secrets. The `path` is a file path, not a credential.

The script `scripts/askdb_config.py` does the mechanical parts. You do the understanding and the questions.

## Step 1: Get the database

Ask for the file path or `sqlite:///` URL if the user didn't give one. If the user gives a non-SQLite URL, say that v1 supports SQLite only and stop.

## Step 2: Inspect the schema

```bash
uv run python ${CLAUDE_SKILL_DIR}/scripts/askdb_config.py inspect "<db-url-or-path>"
```

The output is JSON. For each table it gives row count, columns (name, declared type, primary key, `references` for foreign keys), and `value_lists`: text columns with at most 12 distinct values, such as status codes. It reads the database read-only and changes nothing.

Summarize for the user: table names with row counts, and any tables that look unrelated to analytics (migration logs, sessions, auth tokens). Those are candidates to exclude.

## Step 3: Ask what the schema can't tell you

Use AskUserQuestion for anything below you can't settle from the schema. Batch the questions into one call where you can. Don't ask about things with a clear default.

- **Include which tables?** Default: all, minus any you flagged as unrelated to analytics.
- **Name.** Default: the file name without extension, lowercased, with spaces and other characters replaced by `_`. Only letters, digits, `_` and `-` are allowed, and it must be unique in the app.
- **Display name.** Ask for the label shown in the UI.
- **Fiscal year start month.** Default `1` (calendar year).
- **Value lists.** For each `value_lists` entry, ask whether to list its values in the description. The description is sent to the model on every question, so only list values that are codes or categories. Never list values from personal or contact columns: names, emails, phone numbers, addresses. Propose the list and get a yes or no for each.
- **Business rules.** The schema can't say what "active customer", "revenue", or "churn" means. Ask for the definitions the team uses, and where they're unknown, leave them out rather than guess.

## Step 4: Draft the descriptions

Write a short `description` for each table and for each column whose meaning isn't obvious from its name. Base them on names, types, keys, and the value lists the user approved. Mark anything you inferred rather than confirmed, and show the user the drafts before rendering. Keep them plain. The model sees these verbatim.

Database-level `description`: a few sentences on what the data is about, its time range if known, and the business rules from Step 3. Show it to the user and confirm it.

## Step 5: Render and validate

Write the answers to a spec file, for example `create-db-configs-spec.json`, in the scratchpad or a temp folder, outside the repository:

```json
{
  "name": "sales",
  "display_name": "Sales Analytics",
  "path": "/absolute/path/to/sales.db",
  "fiscal_year_start_month": 1,
  "description": "Database-level description, confirmed by the user.",
  "tables": [
    {
      "name": "invoices",
      "description": "Billing invoices generated for subscriptions.",
      "columns": [{"name": "status", "description": "paid, pending, overdue, refunded"}]
    }
  ]
}
```

Only the listed tables are written. Columns, types, keys, and references come from the database, so the spec only carries descriptions. The `path` must be absolute.

Then render the file into the same folder as the database file, named `<name>.yaml`. Do not write it anywhere else unless the user asks for a different location. For `chinook/chinook_sqlite.sqlite` that means `chinook/chinook_sqlite.yaml`:

```bash
uv run python ${CLAUDE_SKILL_DIR}/scripts/askdb_config.py render <spec.json> <db-folder>/<name>.yaml
```

Exit code 0 means the file was written and passes the same checks the admin screen applies. Exit code 1 means nothing was written and the JSON lists the errors. Fix the spec and render again. Common errors:

- a name with spaces or symbols, which must be letters, digits, `_` and `-`
- a table in the spec that isn't in the database
- a database file that can't be opened read-only

## Step 6: Hand off

Tell the user:

- where the file is, and the exact path to enter in the admin form. The form's path field is what the app uses, so it should match the `path` in the file
- how many tables and columns were written
- which descriptions you inferred and they should review, and which value lists were included or left out
- that uploading it in the admin Databases panel runs the same checks again

Do not copy the file into `config/databases/` unless the user asks. The app loads that folder at startup, and the admin upload is the normal route.

## Reference

- `scripts/askdb_config.py` — `inspect` and `render` subcommands. Read it if the output looks wrong.
