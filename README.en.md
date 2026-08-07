# ReportsDB — a local analytics database for SSRS reporting

[Русский](README.md) · **English**

An Excel export describing SSRS reports is turned into a local analytics
database (**DuckDB**, a single file) with ready-made views and two ways to look
at the data:

| Option | For whom | How it starts |
| --- | --- | --- |
| **Streamlit app** | anyone with Docker or Python | `docker compose up` → browser |
| **A single HTML file** | anyone with only a browser | double-click the file |

The main question the database answers: **how much data does each report serve,
and what would actually be freed if the report were decommissioned.**

### Documentation

| Document | About |
| --- | --- |
| [`docs/RUNBOOK.en.md`](docs/RUNBOOK.en.md) | **Step-by-step start** for each option, and loading data |
| [`docs/ARCHITECTURE.en.md`](docs/ARCHITECTURE.en.md) | Components: what the solution is made of |
| [`docs/TZ.en.md`](docs/TZ.en.md) | Specification — the source of truth for the project |
| [`CHANGELOG.en.md`](CHANGELOG.en.md) | Changelog: what changed and when |
| [`CLAUDE.md`](CLAUDE.md) | Working rules for the AI agent (Russian only — it is the agent's brief) |

---

## Quick start

### Try it on demo data (nothing of your own required)

One command: `scripts\run.bat` (Windows) or `./scripts/run.sh` (Linux/macOS) —
demo data is generated automatically if no database exists yet.

The same thing step by step:

```bash
scripts/rdb.sh sample                                   # synthetic Excel
scripts/rdb.sh build --config config/mapping.sample.yml \
    data/raw/sample_reports.xlsx                        # build the database
scripts/run.sh                                          # open the analytics
```

### Load your own data

Files stay **on your machine only**: `data/raw/`, `data/*.duckdb` and `dist/`
are covered by `.gitignore`, and only code reaches the repository.

The mapping is configured for the actual headers — no config editing needed:

- **reports file:** `№`, `ТС` (retail network), `Завод` (plant),
  `Каталог 1/2/3-го уровня` (catalog levels), `Наименование отчета` (report
  name), `Используется view` (uses a view), `Таблицы источники данных` (source
  tables), and — if present — `View`, `Mat.view`, `Временные таблицы` (temp
  tables), `Функции/процедуры` (routines). `Ср. дл. (сек)` and
  `Кол-во обращений` are **not read** from this file, even where the columns
  physically exist — statistics comes only from a separate file, see below;
- **sizes file** (a database segment export): `OWNER`, `SEGMENT_NAME`,
  `SEGMENT_TYPE`, `SIZE_MB`, `PERCENT_OF_TOTAL`, `Глубина хранения`
  (retention days);
- **statistics file** (required for executions and duration):
  `Наименование отчета`, `ТС`, `Завод`, `Кол-во обращений`, `Ср. дл. (сек)`;
- **SQL query file** (optional): `№`, `ТС`, `Завод`,
  `Каталог 1/2/3-го уровня`, `Наименование отчета`, `Запрос к базе данных` —
  shown under "Tools → Database queries" and on the report card.

The object kind comes from the column the object is listed in. If there are no
per-kind columns, a name mask from `config/mapping.yml` applies — one is enabled
by default: the prefix `v_` (and `vw_`) means a view.

A mask is the last resort. An object found in the sizes file stays a table no
matter what: it has segments, therefore it is physically stored, whatever it is
called. The **Tables** reference shows where each object's kind came from —
the "Kind determined by" column.

**Through the UI — no command line needed.** The **Load data** page in the left
menu: put files into `data/raw/` or drag them onto the page, say which file is
which, check the column mapping and press **Load**. Step by step — in the
[RUNBOOK](docs/RUNBOOK.en.md#how-to-load-data).

**Through the command line.** Use `scripts\rdb.bat` (Windows) or
`scripts/rdb.sh` (macOS/Linux): the app lives in the project's virtual
environment, and `python -m reportsdb` from a plain console does not see it —
it fails with "No module named reportsdb".

```bat
scripts\rdb.bat profile data\raw\reports.xlsx   :: what is in the file
scripts\rdb.bat build   data\raw\reports.xlsx   :: build the database
scripts\rdb.bat diagnose                        :: reconcile sizes with the database
scripts\rdb.bat clear                           :: erase all data
scripts\run.bat                                 :: look at the data
```

```bash
scripts/rdb.sh profile data/raw/reports.xlsx    # what is in the file
scripts/rdb.sh build   data/raw/reports.xlsx    # build the database
scripts/rdb.sh diagnose                         # reconcile sizes with the database
scripts/rdb.sh clear                            # erase all data
scripts/run.sh                                  # look at the data
```

`diagnose` is for when the numbers in table №1 disagree with your source file:
it shows which columns were recognised, how many rows of each segment type were
counted, and whether the total matches the database. It never prints table
names — only numbers.

`clear` erases all loaded data and leaves an empty database of the right
structure — the analytics still opens and shows zeros. `reports.duckdb.bak` is
deleted along with the database: clearing is asked for when working data should
not remain on the machine, and a backup next to it would keep exactly what was
meant to be erased (`--keep-backup` if you want it). The same thing exists in
the UI: **Load data → Clear the database**. Source files in `data/raw/` are left
alone.

Column names are not hard-coded anywhere — only in `config/mapping.yml`.

---

## Handing it to colleagues

**Via Docker** — all they need is Docker:

```bash
docker compose -f docker/docker-compose.yml up --build
# http://localhost:8501
```

The image includes the contents of `data/` — that is, the built database and
the source files. This is deliberate, so that one command is enough for a
colleague, but it means the image contains working data: do not publish it to
public registries.

**If the colleague has no access to the repository**, hand over the image as a
file:

```bat
docker compose -f docker\docker-compose.yml build
docker save reportsdb:latest -o reportsdb-image.tar
```

The colleague runs `docker load -i reportsdb-image.tar`, then
`docker run -d -p 8501:8501 --name reportsdb reportsdb:latest`. The file comes
out around 1 GB; step by step, together with updating and mounting their own
folder, — in the [RUNBOOK](docs/RUNBOOK.en.md#handing-the-image-to-a-colleague).

**Via HTML** — they need nothing at all:

```bash
scripts/rdb.sh export-html      # → dist/reportsdb.html
```

Or in the app: **Tools → File for colleagues** → "Build HTML file".

The file is self-contained: data, styles and scripts inside, not a single
external request. It opens by double-click, works offline, and supports light
and dark themes, search, sorting and CSV export.

**Inside it is the same as in the app.** Tabs follow the same menu groups:
Overview, all of "DC analytics" (№1–№5), the entire "Reports" section (report
card, volume and cost, decommission candidates, similar reports, ABC analysis)
and the tables reference. The same metrics, the same columns, the same
click-a-row breakdown, one DC selector for the whole file. Nothing is
truncated: lists are complete.

Only data loading and ad-hoc SQL are left out — those are the database owner's
tools, not the reader's.

---

## What the analytics shows

### Five core views per DC

A DC (distribution centre) is a "network + plant" pair. Menu section
**"DC analytics"**, one page per view:

| № | View | What it relates |
| --- | --- | --- |
| 1 | `v_tables_catalog` | Catalog of tables and sizes: a "table + plant" pair, one to one. Exactly what the sizes file contains — including tables no report refers to, and **only** those |
| 2 | `v_rc_report_tables` | A report and its tables — `TABLE` only, no views or temp objects |
| 3 | `v_rc_report_routines` | A report and its functions/procedures |
| 4 | `v_rc_report_usage` | A report and user executions |
| 5 | `v_rc_report_retention` | A report and retention depth: up to 30 / 31–45 / over 45 days |

### The rest of the analytics

| View | Question it answers |
| --- | --- |
| `v_report_footprint` | How much data sits behind each report |
| `v_table_criticality` | Which object is used by how many reports — what breaks if it changes |
| `v_report_cost_value` | Volume versus how often the report is used |
| `v_report_duration` | Execution time: average duration and total cost |
| `v_decommission_candidates` | What can be decommissioned and how much it frees |
| `v_network_overview` | DC comparison: the plant's database volume, how much of it is under reports and how much is not |
| `v_catalog_overview`, `v_schema_overview` | Distribution across catalog folders and schemas |
| `v_report_overlap` | Reports of one plant with an almost identical set of sources |
| `v_report_twin` | A report's twins: in other networks and on other plants of its own network (the `scope` column) |
| `v_network_report_twin` | A network's report as a whole: plants collapsed, showing whether other networks have it |
| `v_report_tables_summary` | A report, the list of its tables and their total size |

### Report card

The **"Report card"** page: clicking a row in the report list unfolds the full
picture — which tables the report uses and how much each weighs, how many times
it ran, its views and procedures, the SQL query text (if loaded from a separate
file), and neighbouring reports sharing the same tables.

### Similar reports

The **"Similar reports"** page (the "Reports" section) looks for pairs
referencing the same set of real tables — candidates for consolidation. The
similarity threshold (Jaccard index) is a slider; clicking a pair shows which
tables are shared and which belong to only one of the reports.

**Only reports of the same plant are compared.** The same report existing on
several plants is normal design, not a duplicate: there is nothing to merge,
and cross-plant pairs pile up so fast that real candidates disappear behind
them.

### Unique reports

The **"Уникальные отчёты"** ("Unique reports", the "Reports" section) answers the
opposite question: **what is a network's or a plant's own**. A report is unique
when the other side has neither a namesake nor a close twin by table set.

**The comparison scope is a switch**, because there are two questions:

- **Between networks** — what other trade networks (ТС) do not have. Networks
  overlap by a large share of their reports (roughly 70% on the customer's data),
  and the differences between them are the main thing to see. The unit of counting
  here is a network's report: one standing on several of its plants counts once.
- **Between plants of one network** — what the neighbours do not have. The unit
  of counting is a report record on a plant.

At the top there is a summary: reports in total, how many of them are unique,
their share, the volume of their tables and the number of executions. Clicking a
report shows its tables with size and retention depth, its executions, average
duration and **why** it counts as unique: the closest report on the other side is
named together with the similarity value.

The similarity threshold is a slider: "the same report" on different sites drifts
apart by table set over time, and where "the same" ends is for the reader to
decide. When there is nothing to compare with (a single network in the database,
or a single plant in the network), the page says so outright: "every report is
unique" there means a lack of data rather than a conclusion.

### ABC analysis

The **"ABC analysis"** page sorts reports by the chosen measure and cuts by
cumulative share: **A** — the first reports giving 80% of the total, **B** — the
next ones up to 95%, **C** — the long tail. It answers "where to start":
typically 20–30% of reports carry 80% of the load, work on group A pays off, and
group C is a ready-made list of decommission candidates.

There are four measures, and they answer different questions:

| Measure | What it shows |
| --- | --- |
| **Report's table volume, MB** | How much data the report reads. A shared table counts for every report that reads it: that is exactly right for comparing reports with each other, but the column total exceeds the database volume and is not the database volume |
| **Freed on decommission, MB** | How much space is freed if the report is retired: only its own tables. For a report living on shared tables this is an honest zero |
| **Execution count** | How many times the report was run |
| **Total execution time** | Executions × average duration |

A report with a zero measure does not enter the analysis — the page states
plainly how many such reports there are and why.

### Database queries

The **"Запросы к БД"** ("Database queries", the "Tools" section) page shows the
SQL text a report is built from — loaded from a separate SQL query file. The
question runs the other way round compared with the report card: **search covers
the query text**, so "which reports touch this table" is answered right here —
including tables that never made it into the "source tables" column of the
original file.

Clicking a report shows the query with syntax highlighting, a `.sql` export, and
next to it **the report's declared sources** flagged "present in the query". Any
discrepancy between the file's source list and the query text is visible at once:
if the query holds a table missing from the list, the report's volume is
understated. A "no" can also be legitimate — the object may arrive through a view
or a routine.

### About `gross_mb` and `exclusive_mb` — important

Shared tables belong to several reports at once:

- **`gross_mb`** — the sum of the sizes of all the report's tables. **Do not add
  it up across reports**: on demo data the sum of `gross_mb` overstates the real
  volume twofold.
- **`exclusive_mb`** — only tables that no other report touches. This is the
  volume freed when the report is decommissioned. It adds up honestly.
- **`size_coverage_pct`** — the share of the report's tables whose size is
  known; protects against conclusions drawn from incomplete data.
- **`exclusive_pct_of_db`** — what share of the whole database the report's
  exclusive tables occupy.

Cost is measured by two independent quantities: **data volume** and **time**
(`total_duration_sec` = executions × average duration). A report can be cheap in
data and expensive in time — those are different reasons to act.

### About table sizes

The sizes file is a database segment export, one row per segment. Segments of
one table (partitions) are **summed**; index and LOB segments are skipped —
they cannot be attributed to a table from this export, so index size is **not**
included in table volume.

### About reports going through views

If a report reaches data through a view, its list of source tables is knowingly
incomplete: behind the view stand tables that are not in the list. Such a report
never gets high confidence among decommission candidates.

---

## Data model

```
        dim_report ──< bridge_report_table >── dim_table
             │                                     │
      fact_report_usage                     fact_table_size
```

A list of tables inside one Excel cell (`dbo.Orders;dbo.Customers`) is expanded
into a many-to-many relation. Parsing tolerates `[dbo].[Orders]`, stray spaces,
three-part names `db.schema.table`, and names without a schema.

If the schema is missing but the table name appears in the sizes file with
exactly one schema, that schema is filled in automatically (the sizes file lists
every table in the database regardless of reports). Ambiguous names are left
alone. Visible in the **Tables** reference in the "Schema determined by" column.

`fact_table_size` and `fact_report_usage` are always created. Until the
corresponding files are loaded they stay empty — the views still work, and the
app states plainly which data is missing.

---

## Checks

```bash
pytest -q
```

135 tests: parsing of table names, paths and yes/no flags; mapping of the actual
headers of both files; building a path from catalog levels; segment summing and
index filtering; duration units; absence of dangling keys; every view working on
empty facts; HTML self-containment; the check that `exclusive_mb` never exceeds
real storage volume; and a guard making sure every loaded field reaches the
analytics.

---

## Layout

```
config/mapping.yml   Excel column mapping → model (the single place)
sql/                 DDL and views
src/reportsdb/       ETL, profiling, HTML export, CLI
app/Home.py          app menu (sections via st.navigation)
app/views/           app pages
docker/              Dockerfile and compose
docs/                specification, runbook, architecture
```

## After updating the code

If after `git pull` the app says "the database was built by an earlier version
of the program", open **Load data** and press **Load**. The database structure
is versioned, and the app recognises the incompatibility itself instead of
failing with an SQL error.

## Limitations

The analysis reflects the contents of the Excel export, not the actual
dependencies of the reports. Access through stored procedures and views is not
visible in the source list. Reliable sources are `.rdl` parsing or the
`ReportServer.ExecutionLog`; see section 13 of the specification.
