# Specification: a local SSRS reporting database + analytics

[Русский](TZ.md) · **English**

> This document is the **source of truth** for the project. Any AI agent
> continuing the work must read it in full before the first code change and
> update section [15. Decision journal](#15-decision-journal) after making
> changes.
>
> The Russian version, [`TZ.md`](TZ.md), is the original. When the two disagree,
> the Russian one wins; both must be updated in the same commit.

---

## 1. Context and goal

The organisation runs a set of SSRS (SQL Server Reporting Services) reports.
Information about them lives in an Excel file and cannot be analysed: there is
no way to answer "which reports can be decommissioned", "which tables are
critical", "how much data does each report serve".

**Goal:** turn the Excel export into a local relational database with ready-made
analytics that can be handed to a colleague as a single file or archive and run
without administrator rights and without a server.

**Not a goal:** replacing SSRS, online monitoring, multi-user work, users
writing data.

---

## 2. Key decisions (agreed with the customer)

| Question | Decision | Rationale |
| --- | --- | --- |
| DBMS | **DuckDB** (the file `data/reports.duckdb`) | Embedded, a single file, zero install, columnar — ideal for analytical aggregates. Its SQL dialect is close to PostgreSQL. |
| Visualisation, option A | **Streamlit + Plotly** | A full interactive app, ad-hoc SQL, filters. For colleagues with Docker or Python. |
| Visualisation, option B | **A self-contained HTML file** | For colleagues who have nothing but a browser. Sent by email, opened by double-click. |
| Delivery to colleagues | A Docker image **or** a single HTML file | The customer confirmed: Docker and a browser are available on their machines. Python is not guaranteed. |
| Load mode | **Full refresh every time** | History is not required. The ETL is idempotent: a repeat run recreates the database from scratch. |
| Analytics priority | **Data volume and cost** | The main scenario is how much data each report serves and what that costs. |

### 2.1. A deliberate deviation on option B

DuckDB-WASM inside the HTML was discussed initially. **It was implemented
differently:** data is embedded into the HTML as JSON and aggregation runs in
plain JavaScript.

The reason: when a page is opened over the `file://` protocol, DuckDB-WASM needs
to load a `.wasm` file and a worker script, which the browser's CORS policy
blocks; working around it with inline base64 inflates the file to tens of
megabytes and stays fragile. The data volume (hundreds of reports, thousands of
tables) is orders of magnitude below the threshold where a database engine is
needed. The customer's requirement — "opens by double-click, nothing to
install" — is met fully and more reliably.

If the HTML is ever served over HTTP, moving to DuckDB-WASM is possible without
changing the data model.

---

## 3. Source data

### 3.1. The file available today

An Excel file (`.xls`/`.xlsx`) with one row per report. **The actual headers are
confirmed by the customer** (they are Russian; the translations are given for
reference only — the strings themselves must not be changed):

| Header in the file | Model field | Meaning |
| --- | --- | --- |
| `№` | `report_no` | Row number in the source table |
| `ТС` | `network` | Retail network |
| `Завод` | `plant` | Plant inside the network |
| `Каталог 1-го уровня` | `folder_l1` | The user-facing path to the report, level 1 |
| `Каталог 2-го уровня` | `folder_l2` | Level 2 |
| `Каталог 3-го уровня` | `folder_l3` | Level 3 |
| `Наименование отчета` | `report_name` | Report name (required field) |
| `Используется view` | `uses_view` | Whether the report reaches data through a view |
| `Таблицы источники данных` | `source_tables` | List of tables separated by `;` |
| `View` | `source_views` | List of views separated by `;` |
| `Mat.view` | `source_matviews` | List of materialised views |
| `Временные таблицы` | `source_temp_tables` | Temporary and generated objects |
| `Функции/процедуры` | `source_routines` | List of functions and procedures |
| `Ср. дл. (сек)` | `avg_duration_sec` | Average query duration, seconds |
| `Кол-во обращений` | `exec_count` | How many times the report ran |

Parsing rules:

- The list of tables sits **in a single cell**, items in `schema.table` form,
  separator `;`.
- `catalog_path` is **assembled** from the three level columns: empty levels are
  skipped, a slash inside a value is replaced with a hyphen. A single "catalog"
  column is supported as a fallback for files of the older structure.
- The report uniqueness key is `(network, plant, catalog_path, report_name)`:
  the same report exists for different networks and plants, and such rows must
  not be collapsed.
- `report_no` is stored **as text** (values like `1.2` occur).
- `uses_view` is parsed tolerantly: `да/нет`, `yes/no`, `1/0`, `+/-`. An
  unrecognised value yields `NULL`, not `FALSE` — "unknown" and "no" lead to
  different conclusions.
- `Наименование отчета` is written without «ё»; matching folds `ё` to `е`, so
  both spellings work.

**The object kind is determined by the column the object came from** — that is
more reliable than guessing from the name: a table called `V_SALES` stays a
table if it sits in the tables column. `dim_table.kind_source` records where the
kind is known from: "column", "mask" or "default".

Name masks (`reports.object_patterns` in `mapping.yml`) are the **last resort**,
and they apply only to the "source tables" column. Exactly one is enabled by
default: `VIEW: ["V_*", "VW_*"]`. The list is locked down by a test, because a
wrongly guessed kind would silently drop a table out of table №2 and out of the
volume calculation — a new mask is added deliberately, not in passing.

**The mask is not applied to objects found in the sizes file.** Having segments
means the object is physically stored — it is a table, whatever it is called.
For that, before parsing the reports, not only a schema index is built from the
sizes file (see section 7, item 2.1) but also the set of all names in it.

**Usage statistics arrive in the main file.** The `Кол-во обращений` and
`Ср. дл. (сек)` columns are loaded straight into `fact_report_usage`. A separate
statistics file is still supported and **overrides** these values: it is
considered the fresher source and may carry fields the main file does not
(users, period boundaries).

### 3.1.1. Data never leaves the machine

The customer works with production data, and **the source files and the built
database never reach the repository**. Enforced by `.gitignore`: `data/raw/*`,
`data/*.duckdb`, `dist/`. Only code, SQL and the column-name config live in git.
Checked by `test_raw_data_is_git_ignored`.

The consequences every agent must respect:

- never commit the contents of `data/` and `dist/`;
- never put real report, table or schema names into code, tests, the README,
  commit messages or PR descriptions;
- use only synthetic data from `python -m reportsdb sample` for examples;
- the Docker image contains `data/` — it is meant for sharing inside the team
  and must not be published to public registries.

### 3.2. The sizes file — a database segment export

The second file: one row per **segment**, not per table.

| Header | Model field | Meaning |
| --- | --- | --- |
| `№` | — | Sequence number, not used |
| `ТС` | `network` | Retail network. Optional |
| `Завод` | `plant` | Plant. Optional |
| `OWNER` | `schema_name` | Schema |
| `SEGMENT_NAME` | `table_name` | Segment name |
| `SEGMENT_TYPE` | `segment_type` | `TABLE`, `TABLE PARTITION`, `INDEX`, … |
| `SIZE_MB` | `total_mb` | Segment size, MB |
| `PERCENT_OF_TOTAL` | `percent_of_total` | Share of total database volume, % |
| `Глубина хранения` | `retention_days` | How many days of data the table keeps |
| `PERCENT_OF_SCHEMA` | — | Share within the schema, not used |

Four rules without which the numbers will be wrong:

1. **Segments of one table are summed.** A partitioned table has several export
   rows; `SIZE_MB` and `PERCENT_OF_TOTAL` are added up, and the number of summed
   rows goes into `segment_count`.
2. **Only segments of type `TABLE*` are counted.** The list is set in
   `mapping.yml` (`table_sizes.segment_types`). Index and LOB segments carry
   their own names and cannot be attributed to a table from this export — so
   index size is **not** part of table volume. The number of skipped segments is
   printed in the load summary.
3. **Size is tracked per plant.** The same table occupies different space on
   different plants, so segments are summed per `(table, network, plant)`. If
   the `ТС` and `Завод` columns are absent, rows fall into
   "(не указана)" / "(не указан)" and apply to all reports.
4. **The sizes file is the only source of the list of database tables.** **All**
   its rows are loaded, not just the tables mentioned in reports: a table that
   appears in no report is created in `dim_table` with
   `kind_source = 'файл размеров'` and lands in the `v_tables_catalog` catalog.
   The converse does not hold: an object mentioned in a report but absent from
   the sizes file is **not added** to the catalog — the "report ↔ table" bridge
   merely references that list. The number of tables seen only in the sizes file
   is printed in the load summary; how many report references are left without a
   size is visible in `size_coverage_pct` and in the caption under table №1.

### 3.2.1. What will appear later

Row counts per table (`row_count`) and the statistics period boundaries. The
model fields exist and stay empty while there is no data — the views are ready
for that.

### 3.3. Rule: no assumptions about columns

Excel column names are not known in advance and may be in Russian. Therefore:

- The "file column → model field" correspondence is defined **only** in
  `config/mapping.yml`.
- Before the first load the agent must run `reportsdb profile <file>`, look at
  the real headers and fill in the mapping.
- Hard-coding column names in the ETL code is **forbidden**.
- Matching ignores case and edge whitespace; a list of synonyms per field is
  supported.

---

## 4. Data model

A star schema with a bridge for the many-to-many relation.

```
        dim_report ──< bridge_report_table >── dim_table
             │                                     │
             │                                     │
      fact_report_usage                     fact_table_size
```

### 4.1. `dim_report` — reports

| Field | Type | Description |
| --- | --- | --- |
| `report_id` | `INTEGER PK` | Surrogate key |
| `report_no` | `VARCHAR` | The number from the "№" column of the source file; as text |
| `report_name` | `VARCHAR NOT NULL` | Report name |
| `network` | `VARCHAR` | Retail network |
| `plant` | `VARCHAR` | Plant |
| `uses_view` | `BOOLEAN` | The report reaches data through a view |
| `catalog_path` | `VARCHAR` | The path assembled from the three level columns |
| `folder_l1`…`folder_l3` | `VARCHAR` | The first three folder levels — for grouping |
| `folder_depth` | `INTEGER` | Nesting depth |
| `description` | `VARCHAR` | Optional |
| `owner` | `VARCHAR` | Optional |
| `source_row` | `INTEGER` | Row number in the source file — for tracing |

`UNIQUE (network, plant, catalog_path, report_name)`.

### 4.2. `dim_table` — source objects

| Field | Type | Description |
| --- | --- | --- |
| `table_id` | `INTEGER PK` | Surrogate key |
| `schema_name` | `VARCHAR NOT NULL` | Schema; `(unknown)` if undetermined |
| `table_name` | `VARCHAR NOT NULL` | Table name |
| `full_name` | `VARCHAR NOT NULL UNIQUE` | `schema_name.table_name`, lowercase |
| `object_kind` | `VARCHAR` | `TABLE` / `VIEW` / `MATERIALIZED VIEW` / `TEMP` / `ROUTINE` |
| `kind_source` | `VARCHAR` | Where the kind is known from: column / mask / sizes file / default |
| `schema_source` | `VARCHAR` | Where the schema is known from: `ссылка` (present in the text) / `файл размеров` (recovered from a single name match) / `не определена` |
| `is_parsed_ok` | `BOOLEAN` | Derived from `schema_source`: `TRUE` if the schema is known at all |

Normalisation: trimming spaces, stripping square brackets `[dbo].[Orders]` →
`dbo.Orders`, lowercasing for the matching key. The display name is kept as-is.

### 4.3. `bridge_report_table` — the bridge

| Field | Type |
| --- | --- |
| `report_id` | `INTEGER` |
| `table_id` | `INTEGER` |

`PRIMARY KEY (report_id, table_id)` — duplicates inside one cell collapse.

### 4.4. `fact_table_size` — sizes

The key is the triple `(table_id, network, plant)`: the size of one table
differs across plants, so a table has as many rows as the plants it appeared on
in the sizes file.

| Field | Type | Description |
| --- | --- | --- |
| `table_id` | `INTEGER PK` | |
| `network` | `VARCHAR PK` | Retail network; "(не указана)" if absent from the file |
| `plant` | `VARCHAR PK` | Plant; "(не указан)" if absent from the file |
| `row_count` | `BIGINT` | Number of rows |
| `data_mb` | `DOUBLE` | Data, MB |
| `index_mb` | `DOUBLE` | Indexes, MB |
| `total_mb` | `DOUBLE` | Total across all segments of the table |
| `percent_of_total` | `DOUBLE` | The table's share of total database volume, % |
| `segment_count` | `INTEGER` | How many export rows were summed into this table |
| `retention_days` | `INTEGER` | Data retention depth, days |
| `measured_at` | `DATE` | Measurement date |

### 4.5. `fact_report_usage` — usage (populated later)

| Field | Type | Description |
| --- | --- | --- |
| `report_id` | `INTEGER PK` | |
| `exec_count` | `BIGINT` | Executions in the period |
| `distinct_users` | `INTEGER` | Unique users |
| `avg_duration_sec` | `DOUBLE` | Average query duration, **seconds** |
| `last_executed_at` | `DATE` | Last execution |
| `period_start`, `period_end` | `DATE` | Period boundaries |

### 4.6. `etl_run` and `etl_reject` — housekeeping

`etl_run` records the load time, the source file name, its SHA-256, the row
counts and the code version. `etl_reject` keeps rows that could not be parsed,
with a reason — silent data loss is unacceptable.

---

## 5. Views (SQL views)

Created in `sql/02_views.sql`. All of them must work correctly with empty
`fact_table_size` / `fact_report_usage`.

### 5.0. `v_report_table_size` — a table's size for a specific report

A housekeeping view all the others rely on. Since size is stored per plant, a
naive `JOIN fact_table_size USING (table_id)` would multiply a report's rows by
the number of plants its table lives on, inflating volume proportionally. The
view takes **exactly one** size row per "report + table" pair: first the
measurement of the report's own plant, and if there is none, the shared
"(не указана)" / "(не указан)" measurement.

Columns: `report_id`, `table_id`, `total_mb`, `row_count`, `retention_days`,
`percent_of_total`, `segment_count`, `has_size` (a size was found),
`size_is_plant_specific` (found specifically for the report's plant, not the
shared one).

**Rule for the agent:** join a report to a size only through this view. A direct
join to `fact_table_size` in new queries is a bug.

### 5.1. `v_report_footprint` — data volume per report ⭐ priority

For each report:

- `table_count` — the number of source tables;
- `gross_mb` — the sum of `total_mb` of all its tables;
- `exclusive_mb` — the sum of `total_mb` of **only the tables no other report uses**;
- `shared_mb` = `gross_mb − exclusive_mb`;
- `gross_rows`, `exclusive_rows` — the same by row count;
- `size_coverage_pct` — the share of the report's tables with a known size;
- `exclusive_pct_of_db` — what share of the whole database the report's
  exclusive tables occupy; summing across reports is correct, as with
  `exclusive_mb`.

> **Critical.** Summed across all reports, `gross_mb` counts shared tables many
> times over and is **not** storage volume. The real benefit of decommissioning
> a report is `exclusive_mb`. Both metrics must be present, and the UI must show
> an explanation next to `gross_mb`. `size_coverage_pct` guards against
> conclusions drawn from incomplete data.

### 5.2. `v_table_criticality` — table criticality

`full_name`, `report_count` (how many reports depend on it), `total_mb`,
`row_count`, `is_orphan` (`report_count = 0`), the list of reports.

### 5.3. `v_report_cost_value` — cost versus value

Joins footprint and usage. Cost is measured by **two independent quantities**:
data volume (`exclusive_mb`) and time (`total_duration_sec` = `exec_count` ×
`avg_duration_sec`). A report can be cheap in data and expensive in time — those
are different reasons to act.

Fields: `exclusive_mb`, `exec_count`, `avg_duration_sec`, `total_duration_sec`,
`mb_per_execution`, and `quadrant` ∈ {expensive and unused, expensive and used,
cheap and unused, cheap and used, no usage data}. Quadrant boundaries are
medians over non-empty values.

### 5.4. `v_decommission_candidates` — decommission candidates

Reports sorted by descending `exclusive_mb`, among those where `exec_count = 0`
or `NULL`. The `confidence` column is lowered when:

- there is no usage data;
- **the report reaches data through a view** — behind the view stand tables that
  are not in the source list, so that report's volume is not fully counted;
- `size_coverage_pct < 100` — sizes are not known for all tables.

### 5.5. `v_network_overview` — DC comparison

"Network × plant" pairs, brought together from two independent sides: reports
come from the reports file, sizes from the sizes file. The join is a
`FULL JOIN`: a plant can exist in one file and be absent from the other, and
such a DC must stay in the comparison with its own numbers rather than
disappear.

Columns: the number of reports and how many of them go through a view; the
plant's database volume and its number of tables; how many tables and megabytes
are under reports and how many are not; the share of volume under reports; the
number of schemas; the median retention depth; the freed volume; total
executions and total time; "MB per report".

### 5.5.1. `v_report_duration` — execution time

Reports sorted by total time. Fields: `avg_duration_sec`, `total_duration_sec`,
`duration_band` (under a second / from 1 s / from 10 s / a minute or more). This
is the **second cost metric**, independent of volume.

### 5.5.2. `v_catalog_overview` and `v_schema_overview`

`v_catalog_overview` groups by **all three** catalog levels and carries
`reports_with_view`, `exclusive_pct_of_db`, `exec_count`,
`total_duration_sec`. `v_schema_overview` adds `percent_of_db`.

Aggregates by catalog folders (`folder_l1`) and by database schemas: the number
of reports, the number of tables, the total volume.

### 5.6. The five core views per DC ⭐

The customer defined these as the core ones. **A DC is a "network + plant"
pair:** the same plant name occurs in different retail networks and means
different sites, so all five views carry both columns and uniqueness is counted
by the pair.

| № | View | What it relates | Cardinality |
| --- | --- | --- | --- |
| 1 | `v_tables_catalog` | Table + plant and its size | one to one |
| 2 | `v_rc_report_tables` | A report and its tables | one to many |
| 3 | `v_rc_report_routines` | A report and its functions/procedures | one to many |
| 4 | `v_rc_report_usage` | A report and user executions | one to one |
| 5 | `v_rc_report_retention` | A report and retention depth | one to one |

Plus `v_rc_summary` — the page header: how many reports, tables, views,
materialised views, temporary objects and routines the DC has.

Rules without which the numbers will be wrong:

- **№1 is exactly the sizes file, no more and no less.** The catalog of tables
  exists first, and only then the reports that use some of them. **Every** row
  of the sizes file lands in the view, including tables no report reaches, and
  **nothing beyond it does**: objects from reports without a measurement are not
  mixed into the list, and the "report ↔ table" bridge merely references it. The
  row unit is a "table + plant" pair, because each plant has its own size. The
  `report_count` column in №1 is **informational**: it answers "is the table
  used", but it does not select rows. Metrics counting tables (for example "not
  used by any report") use `COUNT(DISTINCT full_name)`: counting rows would
  inflate them by the number of plants the table lives on.
- **№2 includes `TABLE` only.** Views, materialised views, temporary and
  generated objects are filtered out: they do not represent physical storage.
- **Volume must not be summed across all DCs at once** — one table serves
  reports of several DCs. In the interface, metrics count each object once, and
  any discrepancy between the row count and the object count is shown
  explicitly.
- **№4 measures executions, not unique users.** The customer confirmed that the
  activity measure is "Кол-во обращений". The `distinct_users` column stays in
  the model and will be populated if a separate source appears.
- **A report's retention depth is the maximum over its tables.** The report
  shows as many days as its longest-retaining table. Bands: up to 30 / 31–45 /
  over 45 days, plus "not set".

### 5.7. `v_report_overlap` — report overlap

Pairs of reports with a Jaccard coefficient over the sets of **real tables**
(`object_kind = 'TABLE'`) ≥ 0.8 — candidates for consolidation. A restriction:
only pairs where both have ≥ 2 tables.

**Only reports of the same DC are compared.** The same report existing on
several plants is normal design, not a duplicate: there is nothing to merge, and
cross-plant pairs pile up so fast that real candidates disappear behind them.
The comparison goes through `COALESCE` rather than directly: `NULL = NULL` is
untrue in SQL, and reports without a plant would drop out of the comparison
entirely. The DC restriction also makes a name-and-catalog filter unnecessary:
inside one plant the "name + catalog" pair is unique by the report key.

The **"Similar reports"** page (the "Reports" section) does not read this view
directly — it has its own query with the same principle (TABLE only, one DC
only) but with a slider-controlled similarity threshold and a per-pair breakdown
on click: which tables are shared and which belong to only one of the reports.
The "at least 2 shared tables" floor is mandatory in that query too — without
it, any two reports using a common lookup such as a calendar would become
candidates, and on real data there can be tens of thousands of such pairs.

---

## 6. Components and repository layout

```
ReportsDB/
├── README.md, README.en.md    Quick start for a colleague (bilingual)
├── docs/TZ.md, TZ.en.md       This document
├── docs/RUNBOOK.md, ARCHITECTURE.md  + their .en.md versions
├── config/mapping.yml         Excel column mapping → model (THE single place)
├── data/
│   ├── raw/                   Input Excel files (never reach git)
│   └── reports.duckdb         ETL result (never reaches git)
├── sql/
│   ├── 01_schema.sql          DDL
│   └── 02_views.sql           Views
├── src/reportsdb/
│   ├── cli.py                 Entry point: profile | build | export | sample
│   ├── config.py              Loading mapping.yml, paths
│   ├── profile_source.py      Excel profiling: sheets, headers, samples
│   ├── etl.py                 Loading and normalising
│   ├── export_html.py         Building the self-contained HTML
│   ├── diagnose.py            Reconciling the sizes file with the database
│   └── sample_data.py         Synthetic data generator
├── app/Home.py, app/views/    Streamlit app (pages/ does not fit: no nested sections)
├── docker/                    Dockerfile, docker-compose.yml
├── scripts/run.sh, run.bat    One-command start
└── pyproject.toml
```

---

## 7. ETL: behavioural requirements

0. **Structure version.** Any change to `sql/01_schema.sql` or
   `sql/02_views.sql` must be accompanied by raising `SCHEMA_VERSION` in
   `src/reportsdb/config.py` and updating the digest in
   `test_schema_version_matches_view_set`. Otherwise users are left with an
   incompatible database and the app cannot warn them about it.
1. **Idempotence.** `build` always recreates `reports.duckdb` from scratch. An
   existing file is renamed to `reports.duckdb.bak` (one copy).

   1.1. **Several files per role.** The export arrives one file per plant, so
   `build` accepts a list of reports files, and `table_sizes.files` and
   `report_usage.files` are lists. Files are **concatenated before parsing**
   rather than loaded one after another: `report_id` and `table_id` are numbered
   sequentially across the whole set, and loading one file at a time would give
   identical keys to different rows. The plant is taken from the ТС and Завод
   columns inside the file, not from which file it is — otherwise sizes of
   different plants would silently add up into one. If there are several sizes
   files and they have no plant column, `stats.size_files_without_plant` is set:
   the load will not fail, but the numbers are wrong and staying silent about it
   is not an option. Adding one plant to an already-built database is
   deliberately unsupported — it creates a state of "half the plants fresh, half
   stale" that cannot be inferred from the database.
2. **Parsing the table list.** Separator `;` (configurable). Empty items are
   dropped. An item without a dot → first an attempt to recover the schema from
   the sizes file (see 2.1); if that fails, `schema_name = '(unknown)'`,
   `schema_source = 'не определена'`, `is_parsed_ok = FALSE`. An item with two
   or more dots → the last two segments are taken (the `db.schema.table` case),
   the rest is discarded.

   2.1. **Schema recovery from the sizes file.** Before loading the reports, a
   "table name → schema" index is built from the sizes file — only for names
   that occur there with exactly one schema. A reference without a schema whose
   name is in the index gets that schema and
   `schema_source = 'файл размеров'`; ambiguous names (several schemas at once)
   are not recovered. The index is built once, before `_load_reports`, so it
   does not depend on the order in which the reports and the sizes file are
   loaded into the database. The same pass collects the set of all names in the
   sizes file — it is used to keep the name mask away from physically stored
   objects (see 3.1).
3. **Catalog path normalisation.** Backslashes → forward slashes, repeats
   collapsed, a leading `/`, no trailing `/`. An empty path → `/`.
4. **Rejects.** A row without a report name goes into `etl_reject` and does not
   abort the load. A row without tables is loaded — a report with no sources is
   valid and interesting to the analytics.
5. **Load report.** To stdout: rows read / loaded / rejected, the number of
   unique tables, the number of unparsed items.
6. **Encoding and types.** All text fields are read as strings — it is
   unacceptable for pandas to turn `1.2` into a table name or to lose leading
   zeros.

---

## 8. The Streamlit app (option A)

The menu is assembled in sections through `st.navigation` — "Data", "DC
analytics", "Reports", "References", "Tools". Each of the five core views got
**its own page** inside the "DC analytics" section: tabs inside a single page
hid content and made it impossible to link to a particular slice.

Two rules common to all pages:

- **The DC selection lives in `session_state`** and is not reset when moving
  between pages of the section.
- **Search is one field covering several columns at once**: the customer must be
  able to find a row both by table name and by report name without deciding in
  advance which one they are looking for (`search_box` in `app/_shared.py`).

Pages:

0. **Load data** — the only write path into the database through the interface.
   Files are taken from `data/raw/` or dragged onto the page (and saved into the
   same folder). The user states the role of each file and presses "Load".

   Column checking runs **per selected file** — one tab per file. For every
   expected field it shows whether the column was found and what exactly stops
   working without it; the list of losses is collected into one block. Only a
   missing report name blocks the load; a missing `SIZE_MB` blocks the use of
   the sizes file. A missing `SEGMENT_TYPE` produces a warning: without it,
   indexes land in table volume and inflate it.

   Before a rebuild the app must close its database connections
   (`release_db()`): on Windows a file in use can be neither renamed nor
   overwritten. After the rebuild the caches are cleared, otherwise the
   analytics pages keep showing the old data.

   **Clearing the database** — a collapsed block right under the current
   state, above every `st.stop()` on the page: lower down the button simply
   would not render when the folder is empty or no reports file is selected.
   It erases all data and leaves an **empty database of the right
   structure** rather than deleting the file: the analytics pages still open
   and show zeros instead of a "database not found" error, and the structure
   version stays current. The `.bak` backup is deleted along with the
   database — clearing is asked for when working data should not remain on
   the machine, and a backup next to it would keep exactly what was meant to
   be erased; a checkbox preserves it when needed. Source files in
   `data/raw/` are left alone, and the page states plainly how many of them
   remain. Protection against a misclick: the block is collapsed and a
   confirmation checkbox is required.

1. **Overview** — the main dashboard, everything scoped to the selected DC, the
   selector in the header, the chosen "network + plant" pair shown in the
   subtitle. Top to bottom:

   - **a row of tiles**: reports, tables, table volume (GB), database schemas.
     Tables and volume come from catalog №1, that is from the sizes file, and
     are counted per table, not per "table + plant" row;
   - **three donut slices** of three shares each: volume by usage (several
     reports / one / unused), retention depth, report executions. More than
     three slices is not allowed — see the decision journal;
   - **reference tables**: reports by first-level catalog folders (bars in one
     colour) and database schemas **as a table**. Schemas specifically as a
     table: there are many of them, the bars ran far down the page, and for some
     schemas the size is unknown — on a chart that was empty space. Schemas
     without a size do not enter the table; their number is stated in the
     caption;
   - **source data quality** (rejects, references without a schema). Not scoped
     by DC: this is a property of the load, not of a site.

   Table height is computed from the row count (`table_height()`) — a fixed
   height on a short table leaves a band of empty rows.

   1.1. **№1 "Tables and sizes"** — the only page in "DC analytics" that does
   **not** obey the DC selector in the header: it is a standalone catalog of
   tables from the sizes file and from nothing else. A row is a "table + plant"
   pair. It has its own filters: network, plant, relation to reports ("all" /
   "used by reports" / "unused"), search by name. KPIs: "table + plant" rows,
   unique tables, total volume, how many tables no report uses. Under the table
   there is a caption stating how many report references point to tables outside
   the sizes file: they are not added to the list, but staying silent about them
   is not an option — such reports have understated volume.

   1.5. **Report card** — the report list as full-width row buttons: a click
   anywhere in the row unfolds the full picture, a second click folds it back.
   It shows which tables the report uses and how much each weighs, how many
   times it ran, its views, temporary objects and routines, and neighbouring
   reports using the same tables. Sorting is a separate control; the full table
   with column sorting and export lives in a collapsed block.

   1.6. **№2 "Report → tables"** — two views of one relation. "By reports": a
   row is a report, showing which tables it consists of. "By tables": a row is a
   table, showing which reports it lands in. A click on a row in either view
   gives a breakdown: the row's metrics and its source tables (or consuming
   reports). In the "by tables" view the volume is taken as the maximum over the
   table's rows, not as a sum: one table appears there in several reports.

2. **Volume and cost** ⭐ — built around the question "which tables does a
   report refer to and how much do they weigh in total": the
   `v_report_tables_summary` view gives the table count, the list and the size
   sum on one line. Two tabs for the two cost metrics: "Data volume" (top 20 by
   `exclusive_mb`, a scatter of volume against frequency) and "Execution time"
   (top 20 by total time, a distribution by query duration). Filters: network,
   plant, three catalog levels, going through a view, size coverage. Levels 2
   and 3 cascade from the one above.
3. **Tables** — a reference of **all source objects**, not only tables:
   `v_table_criticality`, search, a filter by object kind using Russian names, a
   checkbox for "only those not used by reports". At the top there is a
   "how many objects of each kind" breakdown: without it there is no way to see
   that views exist in the data at all. The table has a "Kind determined by"
   column — file column / name mask / sizes file / default.
4. **Decommission candidates** — `v_decommission_candidates` with a filter by
   `confidence` and CSV export.

   4.1. **Similar reports** — pairs of reports with a similar or identical set
   of tables (see 5.7); the similarity threshold is a slider, and clicking a
   pair reveals the list of shared and differing tables.

   4.2. **ABC analysis** — reports sorted by the chosen measure and cut by
   cumulative share: A up to 80%, B up to 95%, C the tail. Tiles per group, a
   cumulative curve with 80/95% reference lines, a donut of group shares, and a
   table with a group filter. A Pareto chart must not be drawn: it puts bars and
   a curve on two different axes.

   There are four measures, and the order is not accidental. The first is
   "Report's table volume, MB" (`gross_mb`): how much data the report reads;
   almost every report has it and it reads without caveats. "Freed on
   decommission, MB" (`exclusive_mb`) is second: it counts only tables no one
   else touches, and for a report living on shared tables it is honestly zero —
   such a report does not enter the analysis by that measure at all. Then
   "Execution count" and "Total execution time".

   Under the measure selector its definition is printed, and under the tiles —
   **how many reports did not enter the analysis and why**. Without that line
   the reader's main question, "why is the list shorter than the number of
   reports in the database", goes unanswered.

5. **Networks and plants** — a comparison of DCs with each other (see 5.5).
   Tiles: networks, plants, database volume, share under reports. Below — a
   stacked bar per plant, "under reports / not under reports" (two quantities of
   the same nature and the same scale — honest bars, not two axes) — and a
   comparison table. The "(не указана)/(не указан)" placeholder rows do not
   enter the network and plant counters: that is not a network and not a plant,
   it is an unfilled column.

   5.1. **File for colleagues** (the "Tools" section) — building the
   self-contained HTML. A separate page rather than a block at the bottom of the
   loader: the file is not built at the same moment the data is loaded, and on
   the load page it also ended up behind an `st.stop()` and simply did not
   render until files were selected. It shows what goes inside, the date and
   size of the already-built file, and reminds the user that it contains working
   data. The download button lives outside the click handler — otherwise it
   would disappear on the very first rerun that the click itself triggers.

6. **SQL** — an ad-hoc query against the database with a 5000-row limit and CSV
   export. At the top there is a structure reference: every table and view with
   its purpose, and under it the list of fields with types and explanations for
   the non-obvious ones. Columns are read from `information_schema`, while the
   descriptions live in a dictionary in the code, so the reference never drifts
   from the schema. The same place states the two rules without which a query
   gives wrong numbers: join a report to a size only through
   `v_report_table_size`, and never sum `gross_mb` across reports.

Requirements: fully offline operation (no CDN), Cyrillic in labels, one palette,
CSV export from every table, one consistent look
(`inject_theme()` in `app/_shared.py`) — metric cards, soft table borders, muted
menu section headers, identical in the light and dark themes.

---

## 9. The self-contained HTML (option B)

A single file, `dist/reportsdb.html`. Inside: data as JSON, styles and scripts
inline, zero external requests. It must open over `file://` in Chrome, Edge and
Firefox.

**Its contents mirror the app's sections.** A colleague without Python and
Docker must see the same thing as the database owner: tabs are grouped by the
same menu groups, and each carries the same metrics, the same columns and the
same breakdown of the selected row.

| Tab group | Tabs |
| --- | --- |
| Data | Overview: four DC-scoped tiles, DC comparison, database schemas as a table, top 20 by freed volume |
| DC analytics | №1 Tables and sizes · №2 Report → tables · №3 Report → routines · №4 Report → executions · №5 Retention depth |
| Reports | Report card · Volume and cost · Decommission candidates · Similar reports · ABC analysis |
| References | Source objects: tables, views, mat.views, temporary objects, routines |

Common properties:

- **One DC selector for the whole file** — as in the app. Metric tiles are
  computed from what is actually displayed, not from the whole database.
- **№2 is split into two views**, "By reports" and "By tables", plus the full
  list of relations; a click on a row in any of them unfolds the breakdown.
- **A row breakdown** exists everywhere it does in the app: the report card,
  both views of №2, a pair in "Similar reports". A second click clears the
  selection.
- **No `LIMIT`** in the export queries: truncation would silently show less than
  the app does. The only filter is the meaningful similarity threshold in
  "Similar reports".
- Sorting, search, filters, CSV export of the current selection, light and dark
  themes. Target size — up to 5 MB.

**What the file does not contain:** data loading, ad-hoc SQL and Plotly
charts — those are the database owner's tools, not the reader's. The charts that
remain are drawn in pure CSS.

---

## 10. Delivery to colleagues

- **Docker:** `docker compose -f docker/docker-compose.yml up` → Streamlit on
  `http://localhost:8501`. The image contains both the code and the built
  database.
- **Browser:** the `dist/reportsdb.html` file by email.
- **Python:** `scripts/run.sh` / `run.bat` — they create a venv through `uv` and
  start the app.

---

## 11. The agent's procedure when a real Excel file appears

0. Make sure `data/raw/` is under `.gitignore` (see 3.1.1). The customer's file
   is never published anywhere.
1. Put the file into `data/raw/`.
2. `python -m reportsdb profile data/raw/<file>` — print the sheets, headers, 5
   sample values each, and the share of empties.
3. Fill in `config/mapping.yml` from the facts. Guess nothing: if the meaning of
   a column is unclear, ask the customer.
4. `python -m reportsdb build` — build the database. Check the summary and
   `etl_reject`.
5. Run `tests/` — the integrity checks.
6. `python -m reportsdb export-html` — build the HTML.
7. Update section 15 and push.

---

## 12. Quality control

The checks live in `tests/test_integrity.py` and run on synthetic data as well
as on real data:

- both sides of every bridge relation exist (no dangling keys);
- `full_name` in `dim_table` is unique;
- the sum of `exclusive_mb` across all reports ≤ the sum of `total_mb` across
  all tables — the key guard against double counting;
- source row count = loaded + rejected;
- all views execute on empty facts and return a result without errors;
- **every loaded field appears in at least one view** — otherwise the column was
  read for nothing (`test_every_loaded_field_is_visible_in_analytics`);
- `total_duration_sec` equals `exec_count × avg_duration_sec`;
- `SCHEMA_VERSION` is written into the database, and the SQL digest is checked
  against it: editing the schema without raising the version fails a test;
- table №1 contains **exactly** the rows of the sizes file: nothing lost,
  nothing added from reports, and no "table + plant" pair shown twice; №2
  contains only `TABLE`, №3 only `ROUTINE`, and the 30/45 retention boundaries
  are placed correctly;
- one table has different sizes on different plants, and the `v_report_table_size`
  resolver does not multiply the report's rows: its row count equals the row
  count of `bridge_report_table`, and a report gets its own plant's measurement;
- the customer's actual headers (`№`, `Наименование отчета`, `Каталог`,
  `Таблицы источники данных`) are matched without config edits;
- `.gitignore` covers `data/raw/*`, `data/*.duckdb` and `dist/`;
- a schema recovered from a single name match in the sizes file yields exactly
  one `dim_table` row (it does not duplicate the sizes-file record); an
  ambiguous name (several schemas at once) is not recovered and stays
  `(unknown)`; `is_parsed_ok` is always consistent with `schema_source`;
- similar reports never cross plants: neither the view nor the export query
  contains a cross-plant pair;
- exactly one name mask is enabled, and an object with segments in the sizes
  file stays a table even when its name starts with `v_`.

---

## 13. Limitations and risks

| Risk | Mitigation |
| --- | --- |
| The table list in Excel is incomplete or stale (filled in by hand) | An explicit caveat in the UI: the analysis reflects the contents of the file, not actual dependencies. Reliable sources are `.rdl` parsing or `ReportServer.ExecutionLog`. |
| Double counting of shared tables | `exclusive_mb` was introduced; `gross_mb` is marked as unsafe to sum. |
| Incomplete size data | `size_coverage_pct` and a lowered `confidence`. |
| A report may pull data through stored procedures and views | Recorded as a known limitation; when data appears, add `object_type` to `dim_table`. |

---

## 14. Definition of done

- [x] The database schema and views are created and work on empty facts.
- [x] The ETL builds the database from Excel using the config, keeps a journal
      and rejects.
- [x] The Streamlit app runs locally and in Docker.
- [x] The self-contained HTML opens over `file://`.
- [x] Synthetic data allows everything to be checked before a real file arrives.
- [x] The integrity tests pass.
- [x] The mapping is confirmed against the customer's actual headers.
- [x] Loading through the interface: file selection, preview, the "Load" button.
- [x] Verified on the customer's real file (locally, outside the repository).
- [x] The sizes file structure (a database segment export) is supported.
- [x] Usage frequency and duration are loaded from the main file.
- [ ] A real sizes file has been loaded.

---

## 15. Decision journal

Append-only. Every entry records a decision and the reason behind it — that is
what makes the log worth keeping. New entries go into **both** language
versions in the same commit.

| Date | Decision |
| --- | --- |
| 2026-07-30 | The stack (DuckDB + Streamlit + HTML), the load mode (full refresh) and the analytics priority (volume and cost) were fixed. |
| 2026-07-30 | Option B was implemented on embedded JSON + JS instead of DuckDB-WASM — see 2.1. |
| 2026-07-30 | The `gross_mb` / `exclusive_mb` split was introduced to guard against double counting of shared tables. |
| 2026-07-30 | The project skeleton was implemented and verified on synthetic data; a real Excel file is awaited. |
| 2026-07-30 | The customer confirmed the actual headers: `№`, `Наименование отчета`, `Каталог`, `Таблицы источники данных`. The mapping and the synthetic generator were aligned with them, and the `report_no` field was added. |
| 2026-07-30 | The requirement was fixed: data is loaded locally only and never reaches git (section 3.1.1). |
| 2026-07-30 | The "Load data" page was added: choosing files from the folder or dragging them in, previewing the column mapping, rebuilding on a button press. The command line stopped being mandatory. |
| 2026-07-30 | Verified on the customer's real file: 3 reports, 15 tables, 16 relations, no rejects. |
| 2026-07-30 | `docs/RUNBOOK.md` (step-by-step start) and `docs/ARCHITECTURE.md` (solution components) were added. |
| 2026-07-30 | **A new input file structure.** The catalog arrived as three level columns; network, plant and the "uses a view" flag appeared; frequency and duration moved into the main file. The report uniqueness key was widened to `(network, plant, catalog, name)`. |
| 2026-07-30 | The sizes file is a database segment export. Segments of one table are summed; non-table types (indexes, LOB) are skipped: they cannot be tied to a table from this export. |
| 2026-07-30 | Duration is stored in seconds (`avg_duration_sec`), as in the source file. A second cost metric was added — total time `exec_count × avg_duration_sec`. |
| 2026-07-30 | `uses_view` lowers the confidence of a decommission candidate: behind a view stand tables outside the source list. |
| 2026-07-30 | The load page was reworked: column checking per selected file in its own tab, and for each field a statement of what stops working without it. |
| 2026-07-30 | Catalog levels 2 and 3, `percent_of_total` and `segment_count` were carried through to the views — until then they were loaded but shown nowhere. A guard test was added to catch such a gap. |
| 2026-07-30 | A database structure version was introduced (`SCHEMA_VERSION`, the `etl_run.schema_version` column). A database built by an earlier version of the code now produces a clear message with instructions instead of an SQL error. The load page works on any database — otherwise the recovery path would be unreachable. |
| 2026-07-30 | The menu was rebuilt into sections (`st.navigation`), and each of the five views moved to its own page in the "DC analytics" section. A report card with row selection was added. All tables got one search covering both table and report names. |
| 2026-07-30 | The `v_report_tables_summary` view was added: a report, the list of its tables and their total size on one line — a direct answer to "what does the report refer to and how much does it weigh". |
| 2026-07-30 | **The five core views per DC** (section 5.6). The object kind was introduced: tables, views, materialised views, temporary objects and routines arrive in separate columns and are distinguished in the model, so table №2 contains only real tables and №3 only routines. Retention depth per table was added. A DC is defined as a "network + plant" pair. |
| 2026-07-30 | Name masks for the object kind were left empty by default: a wrongly guessed kind would silently drop a table out of the analytics. The source of the kind is kept in `kind_source`. |
| 2026-07-30 | The `v_report_duration` view and the "Execution time" tab were added: duration became a full metric rather than just a column in a table. |
| 2026-07-31 | **Table size is tracked per plant.** The `fact_table_size` key was widened to `(table_id, network, plant)`, and `ТС` and `Завод` columns appeared in the sizes file. The same table weighs differently on different plants, and merging those measurements into one was wrong. |
| 2026-07-31 | The `v_report_table_size` resolver view was introduced: a report must not be joined to a size directly — a join to `fact_table_size` would multiply rows by the number of plants and inflate volume proportionally. The resolver takes the report's own plant's measurement, falling back to the shared one. All six previous direct joins were moved onto it. |
| 2026-07-31 | **№1 "Tables and sizes" was decoupled from reports** (`v_rc_tables` → `v_tables_catalog`). The table list exists on its own: every row of the sizes file lands in the catalog, including tables no report reaches. `report_count` was kept as an informational column. The loader creates the missing `dim_table` rows with `kind_source = 'файл размеров'`. |
| 2026-07-31 | Row buttons were made to look like a table: monospace font, fixed column widths, a header, 12 rows per page. Large slab buttons filled the whole screen and pushed the selected row's breakdown below the fold — yet it must be looked at together with the table. The header was made a disabled button: a plain `div` had different font metrics and the columns drifted away from the headings. Filters were collected into one row, and the export and full table moved below the breakdown. |
| 2026-07-31 | **Row selection is a click on the row itself.** `st.dataframe` selects a row only through the checkbox in the left column: it ignores a click on a cell (verified on Streamlit 1.60, the latest version — there is no workaround inside the widget; Streamlit forces links in columns to open in a new tab). So the row became a full-width button — the `row_picker` helper in `app/_shared.py`. The selection is stored by the value of a key column rather than by row number: the number moves when sorting changes. The full sortable table stayed in a collapsed block. |
| 2026-07-31 | The Overview was scoped by DC: network and plant selectors in the header, and the chosen pair shown in the title. Three metrics remained — reports, tables, table volume; tables and volume come from catalog №1, that is from the sizes file. Share of the database, size coverage, reports through views and the relation count were removed: they answered questions nobody asks on an overview page. |
| 2026-07-31 | №2 was split into two views: "By reports" (which tables a report consists of) and "By tables" (which reports a table lands in). A click on a row unfolds the breakdown. The "report → table relations" metric was dropped as a technical quantity. |
| 2026-07-31 | A structure reference appeared on the SQL page: the column list is read from the database itself, while the purpose of objects and the meaning of non-obvious fields come from a dictionary in the code, so the reference never drifts from the schema. Ready-made per-plant queries were added. |
| 2026-07-31 | The table schema is lowercased on load. From the reports it arrives as "dbo", from the segment export as "DBO" — schema summaries were doubling, and half the volume went to a twin schema. Checked by `test_schema_names_have_one_case`. |
| 2026-07-31 | `.streamlit/config.toml` was added: the highlight colour is the blue from the palette instead of the default red. Red marks problems in the app, and a selected row must not look like an error. |
| 2026-07-31 | The "is empty" check in the interface was moved to `pd.isna`. The "a value not equal to itself" trick catches `float('nan')`, but an integer column with NULLs arrives from DuckDB as a nullable `Int32`, where empty is `pd.NA`, and `pd.NA != pd.NA` returns `pd.NA`: the report card crashed with "boolean value of NA is ambiguous". The `num` and `is_blank` helpers moved into `app/_shared.py` so that the rule is one for all pages. Checked by `tests/test_ui_helpers.py`. |
| 2026-07-31 | Interface verification was extended with **clicking a row**: the previous sweep only opened pages, so the report card — which is drawn only after a row is selected — stayed unverified, and that is exactly where the bug lived. |
| 2026-07-31 | The `scripts/rdb.bat` and `rdb.sh` wrappers were added. The package is installed into the project's virtual environment, so `python -m reportsdb` from a plain console answers "No module named reportsdb" — the customer hit this on the very first command. The wrapper runs the command with the same Python as the app; the documentation was moved onto it. |
| 2026-07-31 | The `diagnose` command was added — reconciling the sizes file with the database. The customer saw more tables in №1 than in their own file, and that could only be checked on their machine: the data never leaves it. The command reads the file exactly the way the loader does and shows where the numbers diverge. Table names are not printed by default (`--show-names` — locally). |
| 2026-07-31 | An unrecognised segment type column stopped being a silent error: previously indexes and LOB silently became tables and inflated both the table count and the volume. Now the load prints an error in the summary and on the load page, and rows with an empty type are counted separately. Checked by `test_missing_segment_type_column_is_reported`. |
| 2026-07-31 | **The sizes file is the only source of the list of database tables.** Objects mentioned in reports but absent from the sizes file are no longer mixed into №1: the trailing `UNION` with a `size_unknown` flag was removed, and the "report ↔ table" bridge merely references the list. So that such references do not vanish silently, their number is printed under the table with a pointer to №2 and to "size coverage". The "not used by reports" metric was moved from counting rows to counting tables: a table has as many rows as the plants it lives on. |
| 2026-08-01 | The "Similar reports" page was added (the "Reports" section): pairs of reports referencing the same set of real tables (Jaccard index, threshold on a slider), with shared and differing tables revealed on click. Pairs where the match is explained by the same report existing on different plants (identical name and catalog) were excluded — otherwise a legitimate multi-plant copy of a report would constantly appear among "similar" ones. The "at least 2 shared tables" floor — without it any two reports sharing a lookup such as a calendar would count as a candidate, and on real data there can be tens of thousands of such pairs. |
| 2026-08-01 | **Schema recovery from the sizes file.** A table reference without a schema (`(unknown)`) no longer stays unresolved if the table name occurs in the sizes file with exactly one schema — that schema is assigned. Previously such a reference and its "real" record from the sizes file existed as two different `dim_table` rows (one an orphan with `(unknown)`, the other from the sizes file with the real schema), so the size and retention depth of that table never reached the report. Ambiguous names (occurring under several schemas at once) are left alone — they stay `(unknown)`, and a guess is not passed off as a fact. The source of the schema is kept in the new `dim_table.schema_source` column (`ссылка` / `файл размеров` / `не определена`), on the same principle as `kind_source`; `is_parsed_ok` is now derived from it. `SCHEMA_VERSION` was raised to 8. |
| 2026-08-01 | A consistent look for the interface: metric cards instead of bare numbers, a soft border and rounding for tables, muted menu section headers, a thin rule under the page title. All colours are a tint over the current background (`rgba`, not a hex code), so it looks the same in the light and dark themes — verified by switching both. One injector, `inject_theme()` in `app/_shared.py`, called once from `Home.py`. The selectors (`data-testid`) were verified against the real DOM through Playwright rather than guessed from the documentation — Streamlit does not document them and changes them between versions. |
| 2026-08-01 | **Dark theme by default.** `base = "dark"` in `.streamlit/config.toml`, background `#0f1419`. The chart palette was moved to the dark steps of the same reference palette (`#3987e5` / `#d95926` / `#199e70`) — these are not lightened light values but steps chosen separately for a dark surface; verified with `scripts/validate_palette.js --mode dark --surface #0f1419 --pairs all`, all checks passed. `backgroundColor` in the config and `SURFACE` in `app/_shared.py` must match: that colour draws the gaps between chart segments. |
| 2026-08-01 | **Donuts are strictly three slices.** In a circle the reader compares every slice with every other, so validation runs in `--pairs all` mode. The fourth palette colour does not pass it: yellow and orange are indistinguishable even with full colour vision (ΔE 10.6 against a floor of 15), and a sweep of all quadruples produced only two passing sets, both of which break the fixed palette order. So the `donut()` helper in `app/_shared.py` refuses to draw more than three slices: the tail is folded into "Other", and where there are more classes, bars in one colour are used. Slices are labelled with percentages right on the chart: identity does not rest on colour alone. |
| 2026-08-01 | The Overview was rebuilt as a dashboard: a row of tiles (reports, tables, volume, schemas), three donut slices ("volume by usage", "retention depth", "report executions"), and reference tables below. Database schemas are shown as a table rather than bars: there are many of them, the chart ran far down the page, and for some schemas the size is unknown — on a chart that was empty space. Table height is computed from the row count (`table_height()`): a fixed height on a short table left a band of empty rows. |
| 2026-08-01 | The "ABC analysis" page was added: reports are sorted by the chosen measure (volume / executions / time) and cut by cumulative share — A up to 80%, B up to 95%, C the tail. A Pareto chart is deliberately not drawn: the classic Pareto puts bars and a curve on two different axes, and two scales in one coordinate system are read incorrectly. Instead there is one cumulative curve with 80/95% reference lines, and per-report values in a table. The first report always lands in A: if it alone gives more than 80%, the condition "cumulative ≤ 80" would not catch it and group A would be empty. |
| 2026-08-01 | **Several files per role — one file per plant.** `build` accepts a list of reports files, and `table_sizes.files` and `report_usage.files` are lists; in the interface file selection became multiple. Files are concatenated before parsing rather than loaded one after another: `report_id` and `table_id` are numbered across the whole set, and loading one file at a time would give identical keys to different rows. The plant is taken from the ТС and Завод columns inside the file — a "file = plant" binding was rejected: it breaks silently on concatenation or on re-selecting a file. Several sizes files without a plant column get their own warning: rows would fall into "(не указан)", plant sizes would add up into one, and no error would occur. Adding one plant to an existing database was deliberately not implemented: a full rebuild leaves no question about what in the database is fresh. Both `file:` (one file) and `files:` (a list) remain valid in the config — old configs work unchanged. A test verifies that slicing the demo data across four plants yields a database identical to loading one file. |
| 2026-08-01 | Building the HTML file for colleagues was moved to its own page, "Tools → File for colleagues". On the load page the button sat at the very bottom and, after the move to multiple file selection, stopped rendering at all: with an empty selection the page stops at `st.stop()` before reaching it. The new page also shows what goes into the file, the date and size of the already-built one, and warns that it contains working data. The download button was moved out of the click handler — otherwise it disappears on the very first rerun that the click itself triggers. |
| 2026-08-02 | **The colleague export was brought in line with the app.** The previous file showed four tables out of ten "DC analytics" and "Reports" pages, part of the data was cut by `LIMIT`, and there were no metrics above the tables at all. Now tabs are grouped by the same menu groups, each with the same tiles, the same columns and the same breakdown of the selected row. `LIMIT` was removed everywhere: silent truncation would show a colleague less than the database owner sees. The only filter is the meaningful similarity threshold in "Similar reports". All "report → object" relations arrive as one dataset from which №2, №3 and the card are built: the report name is not duplicated in relation rows, JS substitutes it by `report_id` — on thousands of relations that is a multiple-fold difference in file size. |
| 2026-08-02 | The table count in the card came from `v_report_footprint.table_count`, which counts **all** objects of a report including views and routines: the tile showed "Tables 8" next to a section headed "Tables (4)". The app takes `v_report_tables_summary` everywhere — the export was moved onto it too, together with the volume sums. The discrepancy was verified by clicking every card, not just the first one. |
| 2026-08-02 | Metric tiles in the export are computed in the browser from what is actually displayed after the DC scope, search and filter, rather than by a query over the whole database. Otherwise, with a DC selected, the numbers on top would stay global and contradict the table below them. Of the export queries only the data source for the header caption remained. |
| 2026-08-02 | The selected row in the export is stored by key value rather than by object reference: views recomputed on every redraw (both views of №2, ABC) produce new objects each time, and comparison by reference would lose the selection. If the row left the selection after a scope change, the selection clears itself so that the breakdown below the table is not about a different report. |
| 2026-08-02 | **Similar reports are searched within one plant only.** Previously pairs were computed across the whole database, and cross-plant duplicates were guarded against by a name-and-catalog filter — which only caught exact copies. A report created on several plants and since drifted slightly in name or catalog still showed up among "similar", although there is nothing to merge: it is the same thing on different sites, normal design rather than a duplicate. Now a pair must be inside one DC (`COALESCE(network) = COALESCE(network) AND COALESCE(plant) = COALESCE(plant)` — a direct comparison would discard reports without a plant, since `NULL = NULL` is untrue in SQL). The old name-and-catalog filter was removed as redundant: inside one plant the "name + catalog" pair is unique by the report key. The rule is duplicated in all three places this query lives: the `v_report_overlap` view, the "Similar reports" page and the colleague export. The page gained a DC selector, the "Plant 1" and "Plant 2" columns collapsed into one, and the "different plants" metric was replaced by "plants with pairs". On demo data the pair count went from 9 to 2. `SCHEMA_VERSION` was raised to 9. |
| 2026-08-02 | **ABC analysis: understandable measures instead of one confusing measure.** The default measure was `exclusive_mb` — "data volume (exclusive)". It counts only tables no other report touches, so for a report living on shared tables it is zero: 69 reports out of 121 entered the analysis, and the total came to 11,301 MB against a database volume of 170,279 MB. The numbers were right, but without explanation they look like a bug. There are now four measures, the first being "Report's table volume, MB" (`gross_mb`): how much data the report reads, present for almost every report and readable without caveats. "Freed on decommission, MB" stayed second with a direct explanation of why it is zero for many. The measure's definition is printed under the selector, the tiles gained "Reports in the analysis" and "Total", and below them — how many reports did **not** enter the analysis and why. Russian numeral agreement was extracted into `plural()`: "23 отчёта", not "23 отчётов". |
| 2026-08-02 | "Orphans" were renamed to "Not used by reports" everywhere in the interface: the column, the filter, the tile, the hint on the SQL page. A term from the data model should not be the word the interface speaks to the customer in. The column name in the database (`is_orphan`) was kept: it is short, conventional, and explained in the structure reference on the SQL page. |
| 2026-08-02 | **The `v_` mask was enabled: views are visible separately from tables.** Until now `object_patterns` was empty and an object without an explicit kind column counted as a table — in the reference the whole list was of one kind, "TABLE". Now `VIEW: ["V_*", "VW_*"]` is enabled by default, but with a guard: the mask is not applied to objects found in the sizes file. Having segments means physical storage — it is a table whatever it is called, and without the guard it would drop out of table №2 and out of the volume calculation. For that, `_schema_recovery_index` became `_sizes_index`, returning both the schemas and the set of names from the sizes file. Object kinds are shown in Russian (`kind_ru` / `OBJECT_KINDS`), and the reference gained a "how many objects of each kind" breakdown and a "Kind determined by" column (column / mask / sizes file / default). Checked by two tests: the mask list is locked down, and a `v_*` object with segments must stay a table. |
| 2026-08-02 | **Plant comparison instead of a plain summary.** `v_network_overview` is assembled from two independent sides — reports from the reports file, sizes from the sizes file — through a `FULL JOIN`: a plant can exist in one file and be absent from the other, and such a DC must stay in the comparison rather than disappear. Added: the plant's database volume, the number of tables, how many of them are under reports and how many are not (in units and in MB), the share of volume under reports, the number of schemas, the median retention depth and "MB per report". The "Networks and plants" page was rebuilt: tiles, a "under reports / not under reports" stack per plant (two quantities of the same nature and the same scale — honest bars, not two axes) and a comparison table. The "(не указана)/(не указан)" placeholder rows were excluded from the network and plant counters: that is not a network and not a plant, it is an unfilled column. `SCHEMA_VERSION` was raised to 10. |
| 2026-08-02 | **The documentation became bilingual.** README, RUNBOOK, ARCHITECTURE and this specification exist in Russian and English side by side: the Russian file is the original, the English one lives next to it with an `.en` suffix, and a language switcher is the first line of each. Separate files rather than two languages inside one: a document duplicated section by section is unreadable in both languages at once. `CLAUDE.md` stayed Russian-only — it is the agent's brief, not user documentation. A test checks that every Russian document has an English counterpart with the same heading structure: without it the two versions drift apart in a couple of commits and the English one quietly turns into a lie. |
| 2026-08-02 | **Version 1.0.0 was released.** The version lives in two places — `pyproject.toml` and `config.VERSION` — and they must match: `VERSION` is written into `etl_run.tool_version`, so any database shows which code built it, and diverging versions turn that record into a lie. Checked by `test_version_is_the_same_everywhere`. A changelog was started, `CHANGELOG.md` / `CHANGELOG.en.md`, in the Keep a Changelog format: what a user can notice goes there, while the details and rationale stay here in the journal. Not to be confused with `SCHEMA_VERSION`: that one governs the database structure and is raised on every SQL edit, independently of the program version. The state is marked with the `v1.0.0` tag; further work accumulates in the "Unreleased" section. |
| 2026-08-02 | **A clear-the-database button** on the load page. It erases all data and leaves an empty database of the right structure rather than deleting the file: otherwise every page would greet the user with a "database not found" error instead of honest zeros, and the structure version would reset to zero and add a second confusing warning. The `.bak` backup is deleted along with the database: clearing is asked for when working data should not remain on the machine, and a backup next to it would keep exactly what was meant to be erased — a checkbox preserves it for those who want the safety net. Source files in `data/raw/` are left alone, and the page states plainly how many remain: those are the user's files, not the contents of the database. The block sits above every `st.stop()` on the page and is collapsed, and the button cannot be pressed without a confirmation checkbox. The same operation is available as the `clear` command, with a typed confirmation and the `--yes` / `--keep-backup` flags. Verifying by clicking found a real bug: on a completely empty database the Overview crashed on `source_sha256[:12]` — after clearing there is no digest. `missing_facts_notice()` also learned to tell an empty database from one without sizes, and offers to load data instead of a pointless "table sizes not loaded". |
| 2026-08-02 | **Handing the image to a colleague was documented step by step**, and a hole turned up on the way: the Dockerfile did not copy `.streamlit/`. Inside the container that lost the dark theme, made `backgroundColor` stop matching `SURFACE` — producing the rim around chart segments that the palette rule warns about — and switched Streamlit usage statistics back on after the project had deliberately disabled them. The build did not fail: a forgotten folder is not an error, it just quietly changes behaviour for the recipient. `COPY .streamlit/` is now in the image, and a test compares the list of copied folders with what the app reads at runtime, plus a separate check that `data/` never lands in `.dockerignore`. The RUNBOOK gained a section on `docker save` / `docker load`: the file size, a warning about the working data inside, what the colleague can and cannot do without a mounted folder (and that a mounted folder completely replaces the image's data), and how to update. |
