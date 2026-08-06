# Solution architecture

[Русский](ARCHITECTURE.md) · **English**

A short description of the components: what the solution is made of, who is
responsible for what, and how data flows through the system.

Details and rationale for decisions — in the [specification](TZ.en.md).
Instructions for running it — in the [RUNBOOK](RUNBOOK.en.md).

---

## The big picture

```
   Excel files                 CORE                   INTERFACES
   (local)

  ┌───────────┐        ┌──────────────┐         ┌────────────────────┐
  │ Reports   │        │              │         │ Streamlit          │
  │ + catalog ├───────▶│              │────────▶│ pages on           │
  │ + tables  │        │     ETL      │         │ localhost:8501     │
  └───────────┘        │   (Python)   │         └────────────────────┘
  ┌───────────┐        │              │  reports
  │ Table     ├───────▶│ normalising, │──.duckdb┌────────────────────┐
  │ sizes     │        │ parsing the  │────────▶│ HTML file          │
  └───────────┘        │ relations    │         │ data inside,       │
  ┌───────────┐        │              │         │ open by            │
  │ Execution ├───────▶│              │         │ double-click       │
  │ frequency │        └──────┬───────┘         └────────────────────┘
  └───────────┘               │
                              ▼
                     ┌──────────────────┐
                     │ mapping.yml      │  column names — only here
                     │ sql/*.sql        │  schema and views
                     └──────────────────┘
```

Everything runs on one machine. There is no server, no network, no external
service.

---

## Components

### 1. Storage — DuckDB

**What it is:** an embedded analytical DBMS. The whole database is a single
file, `data/reports.duckdb`.

**Why this one:** it needs no installation or administration, the file is
copied like an ordinary document, and the columnar engine computes aggregates
over sizes and relations quickly. Its SQL is close to PostgreSQL.

**Data model** — a star schema with a bridge for the many-to-many relation:

| Object | Contains |
| --- | --- |
| `dim_report` | Reports: number, name, retail network, plant, catalog levels, "uses a view" flag |
| `dim_table` | Source objects: tables, views, materialised views, temporary objects, routines — distinguished by `object_kind` |
| `bridge_report_table` | "Report ↔ table" relations, expanded from the cell holding the list |
| `fact_table_size` | Table sizes per "table + network + plant": MB, share of database volume, segment count, retention depth |
| `fact_report_usage` | Usage: executions and average duration in seconds |
| `etl_run`, `etl_reject` | The load journal and rejected rows |

Both fact tables are always created. While there is no data they stay empty,
and the views still work.

### 2. Loader — ETL

`src/reportsdb/` — reading Excel/CSV, normalising, writing to the database.

| Module | Responsible for |
| --- | --- |
| `config.py` | Project paths, reading `mapping.yml`, matching headers |
| `excel.py` | Reading a sheet as text, without guessing types |
| `normalize.py` | Parsing `schema.table`, normalising catalog paths and numbers |
| `etl.py` | Full rebuild of the database, journal, rejects |
| `profile_source.py` | Showing the structure of a file before loading |
| `export_html.py` | Building the standalone HTML |
| `sample_data.py` | Synthetic data for checks without real files |
| `cli.py` | Commands `profile`, `build`, `export-html`, `sample`, `diagnose`, `clear` |

**Key properties:**

- **Idempotence.** Every load rebuilds the database from scratch; the previous
  version is kept as `reports.duckdb.bak`. No duplicates, no accumulation.
- **Nothing is lost silently.** Rows that could not be parsed go into
  `etl_reject` with a reason instead of disappearing.
- **Tolerance for messy input.** `[dbo].[Orders]`, stray spaces, names like
  `db.schema.table`, names without a schema, duplicates inside one cell and
  yes/no flags written in different ways are all handled.
- **Object kind comes from the column, not from the name.** A name mask is
  enabled in `mapping.yml` and serves as a fallback; it is not applied to
  objects from the sizes file — segments mean physical storage.
- **Segment aggregation.** The sizes file arrives one row per database segment:
  partitions of one table are summed, index and LOB segments are skipped.

### 3. Mapping configuration — `config/mapping.yml`

**The single** place where Excel column names are written down. They do not
appear in the ETL code.

For every model field a list of synonyms is given; matching ignores case, edge
whitespace and the Russian «ё»/«е» distinction. To support a new file format it
is enough to add a line to this YAML — the code does not change.

`config/mapping.sample.yml` — the same for the demo data.

### 4. Views — `sql/`

| File | Contains |
| --- | --- |
| `01_schema.sql` | Table DDL |
| `02_views.sql` | Analytical views |

All the analytics are SQL views over the model, not code inside the app. They
are visible from any SQL client and usable without Streamlit.

| View | Question it answers |
| --- | --- |
| `v_report_footprint` | How much data sits behind each report |
| `v_table_criticality` | How many reports use a given table |
| `v_report_cost_value` | Volume and time against usage frequency, quadrants |
| `v_network_overview` | DC comparison: reports and the plant's database volume side by side — how much data is under reports, how much is not |
| `v_report_duration` | Execution time as a separate cost metric |
| `v_decommission_candidates` | What can be retired and how much it frees |
| `v_catalog_overview`, `v_schema_overview` | Summary by folders and schemas |
| `v_report_overlap` | Reports **of one plant** with an almost identical set of sources (`TABLE` only; the "Similar reports" page uses its own query with the same principle and a threshold slider) |
| `v_report_twin` | A report's twins: one row per "report × comparison scope" (`scope` = `NETWORK` — other networks, `PLANT` — other plants of its own network). Namesakes and the closest match by table set, with the similarity. The view holds no uniqueness threshold — the reader sets it with a slider on the page |
| `v_network_report_twin` | A network's report as a whole: one row per "network + name", plants collapsed. The unit of counting for "how do the networks differ" |
| `v_tables_catalog`, `v_rc_report_tables`, `v_rc_report_routines`, `v_rc_report_usage`, `v_rc_report_retention` | The five core views per DC. №1 is exactly the contents of the sizes file: the list of database tables comes from there and nowhere else |
| `v_report_table_size` | The size of a table for a specific report: takes the measurement of that report's plant. Reports and sizes are joined through it |
| `v_report_tables_summary` | A report, its tables on one line, and their total size |

**The key feature of the calculation model.** Shared tables belong to several
reports, so the views carry two different volume metrics:

- `gross_mb` — the sum of all the report's tables; **it must not be added up
  across reports**, since summing counts shared tables many times over;
- `exclusive_mb` — only tables that no other report touches; this is the volume
  that is actually freed when the report is decommissioned.

Plus `size_coverage_pct` — the share of the report's tables with a known size, a
guard against conclusions drawn from incomplete data. The invariant "the sum of
`exclusive_mb` never exceeds real storage volume" is locked down by a test.

### 5. Interface A — Streamlit

`app/` — a web app on local port 8501.

`app/Home.py` — the entry point: it assembles the menu through `st.navigation`.
Sections are nested, so the `pages/` folder does not fit — pages live in
`app/views/`.

| Section | Page | Purpose |
| --- | --- | --- |
| Data | `overview.py` | Overview: metrics, catalog, schemas, data quality |
| Data | `load.py` | Loading files (several per role), checking columns, rebuilding the database |
| DC analytics | `rc1_tables.py` | №1 An object and its size, one to one |
| DC analytics | `rc2_report_tables.py` | №2 A report and its tables, one to many |
| DC analytics | `rc3_routines.py` | №3 A report and its functions and procedures |
| DC analytics | `rc4_usage.py` | №4 A report and user executions |
| DC analytics | `rc5_retention.py` | №5 A report and retention depth |
| Reports | `report_card.py` | Report card: click a row → tables, sizes, statistics |
| Reports | `cost.py` | Which tables a report refers to and how much they weigh |
| Reports | `candidates.py` | The decommission list with a confidence level |
| Reports | `report_overlap.py` | Similar reports of one plant: shared and differing tables |
| Reports | `unique_reports.py` | Unique reports: what other networks or the neighbours in its own network do not have (scope is a switch), with their tables, volume and executions |
| Reports | `abc.py` | ABC analysis: where the load is concentrated for the chosen measure |
| References | `tables.py` | All source objects: criticality, kind, dependencies |
| References | `networks.py` | Plant comparison: database volume, share under reports |
| Tools | `export.py` | Building the self-contained HTML file for colleagues |
| Tools | `sql.py` | An ad-hoc query against the database |

The DC selection is shared across pages: it is kept in `session_state`, so it
survives navigation. Search in every table is one field covering both the table
name and the report name at once.

`app/_shared.py` — shared code: the database connection, the palette, Russian
column labels, CSV export, releasing the database file before a rebuild, safe
rendering of empty values (`num`, `is_blank`) and row selection by click
(`row_picker`).

`row_picker` exists because `st.dataframe` selects a row only through the
checkbox in the left column — the widget does not notice a click on the row
itself. So wherever a row must be clickable, that row is a full-width button.

The database is opened **read-only**: data cannot be changed from the
interface, the loader is the only write path.

### 6. Interface B — the standalone HTML

`src/reportsdb/templates/standalone.html` + `export_html.py` → a single file,
`dist/reportsdb.html`.

Data is embedded as JSON, aggregation runs in JavaScript, styles and scripts are
inside. Not a single external request: it opens over `file://` by double-click
and works without internet. Light and dark themes, sorting, search, filters,
CSV export.

**Its contents mirror the app's sections.** Tabs follow the same menu groups as
`st.navigation`: Data, DC analytics (№1–№5), Reports (card, volume and cost,
candidates, similar reports, ABC), References. Each carries the same metric
tiles, the same columns and the same breakdown of the selected row as the
corresponding page. One DC selector for the whole file.

Data loading and ad-hoc SQL are not carried over: those are the database
owner's tools. Charts are drawn in pure CSS — Plotly is not pulled into an
offline file.

Three rules govern the set of queries in `export_html.py`:

- **No `LIMIT`** — truncation would silently show a colleague less than the
  database owner sees. The only filter is the meaningful similarity threshold
  in "Similar reports".
- **Do not duplicate rows.** "Report → object" relations arrive as one dataset
  carrying only `report_id`; the name, network and plant are substituted by JS
  from a lookup.
- Metric tiles are computed in the browser from what is actually displayed
  after the DC scope, search and filter — otherwise the numbers on top would
  contradict the table below them.

Implemented without DuckDB-WASM on purpose — the rationale is in section 2.1 of
the specification.

### 7. Packaging and running

| Component | Purpose |
| --- | --- |
| `docker/Dockerfile`, `docker-compose.yml` | One-command start without Python on the machine |
| `scripts/run.sh`, `run.bat` | Running on Python: environment, dependencies, app |
| `scripts/rdb.sh`, `rdb.bat` | `reportsdb` commands with the same Python as the app: from a plain console the package is invisible, it is installed in the project environment |
| `pyproject.toml` | Dependencies and the `reportsdb` command |
| `tests/test_integrity.py` | Checks of model integrity and calculation correctness |

---

## Data flow during a load

1. Files land in `data/raw/` — manually or by dragging them into the interface.
2. Sheet headers are matched to model fields via `mapping.yml`; the result is
   shown before loading so that a mistake surfaces early.
3. The app releases the database file (otherwise it cannot be replaced on
   Windows).
4. The ETL recreates the database: DDL → reports and tables → facts → views →
   a journal entry.
5. Interface caches are cleared, and the analytics pages show the new data.

Steps 3–5 run when **Load** is pressed; the same logic is available through
`scripts\rdb.bat build`.

---

## Boundaries of the solution

**What the system does:** turns the export into a database with relations and
computes analytics on the volume and demand for reports.

**What it does not do:** it does not connect to SSRS, does not read `.rdl`, does
not watch for changes, and keeps no history — every load replaces the previous
one.

**The main limit on reliability:** the source of the relations is the contents
of an Excel file. Access to data through stored procedures and views is not
visible there, so the source list may be incomplete. For reports flagged "uses a
view" this is known for certain — they are marked and can never receive high
confidence among decommission candidates. Reliable sources are `.rdl` parsing or
the `ReportServer.ExecutionLog`; moving to them would not require changing the
data model.

**Data never leaves the machine.** The `data/` and `dist/` folders are covered
by `.gitignore`, and only code reaches the repository. The Docker image contains
the built database and is meant for sharing inside the team.
