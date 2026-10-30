---
name: create-eval-dataset
description: Create an evaluation dataset for an askdb database from two inputs, the SQLite database (path or sqlite:/// URL) and its database config YAML (made by create-db-configs). The dataset is a set of questions with the correct answers (expected SQL) that shows whether the app's generated queries are right. Use this whenever someone wants to create test questions, a gold set, or an eval set for askdb, or wants to measure accuracy for a database, even if they never say "evaluation". It drafts realistic questions across the standard categories, writes the expected SQL, checks every expected query against the real database, asks the user to confirm the answers, and leaves the finished file in place.
compatibility: Run from the askdb repository root with uv. Needs the SQLite database file and its config YAML.
---

# Create an evaluation dataset

## Inputs

The user gives two things:

1. The database: a file path or `sqlite:///` URL.
2. The database config YAML, the schema file that `create-db-configs` produced.

If either is missing, ask for it. If the database is not SQLite, say that askdb v1 supports SQLite only and stop.

The output is an eval set YAML file. Each case is a question and the SQL whose result is the correct answer. The format is shown in `references/sample_eval.yaml`. The set is only as good as its expected SQL. A wrong expected query produces a false alarm on a correct app, or hides a wrong app behind a matching mistake. So the answers need the user's confirmation. Don't present them as verified until they confirm.

Rules that apply here:

- The eval file is for evaluation only. It is never loaded at app startup and never sent to the model. Don't put eval content into the database description.
- Each `expected_sql` must be a single read-only SELECT. Check it runs.
- Read the database only through a read-only connection, such as Python's `sqlite3.connect("file:<path>?mode=ro", uri=True)`.

## Step 1: Check the inputs match

Load the config YAML. Check that its `path` points to the same file as the database the user gave. The path may use `${NAME}`, so expand it from the environment first. If they differ, ask the user which one is right before going on. Then read the schema of the database (table names, columns, keys) with a read-only connection, and note any table in the YAML that is missing from the database.

## Step 2: Learn the business rules

Read the config's `description` and table descriptions. Check each of these:

- Does it define the metrics the team cares about (for example, what counts as revenue, active, or churned)?
- Does it give the time range of the data? Expected SQL should use explicit periods (`'2025-01-01'` to `'2026-01-01'`), never `date('now')`, so the answers don't change over time.

If a metric the user will ask about has no definition, ask for it (Step 3) instead of choosing one yourself. An expected query built on a guessed rule is a wrong answer that looks right.

## Step 3: Ask the user

Use AskUserQuestion for what the YAML and database can't tell you. Batch questions where you can. Sensible defaults are fine to state and move on.

- **How many cases?** Default about 25 to 30, which covers every category.
- **Which questions matter?** Ask for two or three real questions their users ask. Use them as anchors.
- **Metric definitions** that are missing from the description (Step 2).
- **Reference date.** Default: explicit periods in the data's range.
- **Known tricky areas** the team wants covered, such as refunds, churn, or partial months.
- **Output path.** Default: `<name>_eval.yaml` in the same folder as the database file, where `<name>` is the config's `name`. Take the folder from the config's `path`. For the Chinook database that means `chinook/chinook_sqlite_eval.yaml`. Don't write anywhere else unless the user asks for a different location.

## Step 4: Draft the cases

Cover these categories, roughly in these proportions:

- `totals_trend`: totals and monthly or quarterly trends. About a third.
- `ranking`: top N. Add a deterministic tie-break to the `ORDER BY`, or the expected rows can change between runs.
- `filter`: totals or lists with a filter on status, category, or period.
- `comparison`: two periods or groups. Include at least one follow-up case that uses `conversation` (prior turns with `question` and `sql`).
- `clarification` (`expected_outcome: clarification`): an ambiguous question the description gives no default for, such as "show me the top customers" when "top" isn't defined.
- `non_data` (`expected_outcome: declined`): a request that isn't a question about the data, such as writing a poem.

Write questions the way real users type them. Mix formal and casual phrasing and different lengths. Avoid questions that are one keyword away from the schema. Each case gets a `notes` line explaining the expected answer, for the human reviewer.

Use `category` names from the list above, and keep `id` values lowercase with underscores.

## Step 5: Write and check the file

Write the set to the output path, with `database:` set to the config's `name`. Copy the shape from `references/sample_eval.yaml`; its table and column names are made up, so replace them with the real ones. Then check it:

```bash
uv run python ${CLAUDE_SKILL_DIR}/scripts/check_eval_set.py <eval.yaml> --config <db.yaml>
```

The check validates the format, runs every expected SQL read-only, and prints sample rows for each case. Exit code 1 means errors: fix them and check again. It also prints warnings:

- an expected query that returns no rows. Such a case can't tell a wrong answer from an empty one, so change the question or the period.
- a case that has both `expected_outcome` and `expected_sql`. Keep only the one that applies.

If the config's `path` uses `${NAME}`, export that environment variable first. The check needs the same environment the app has.

## Step 6: Confirm with the user

Show the user a table of case id, question, and a summary of the expected result (row count and first rows). Ask them to confirm each row. Expect corrections. Fix each wrong one, run the check again, and show the changed cases. Don't mark the set final until the user has confirmed it.

## Step 7: Finish

Leave the file where it was written. Tell the user:

- the file path
- the number of cases per category
- any cases still flagged for review, and any warnings the check left open

## Reference

- `references/sample_eval.yaml` — a small eval set showing every case field and category. Format only: its SQL targets made-up tables, so it won't run against a real database.
- `scripts/check_eval_set.py` — format and SQL check, with sample rows.
