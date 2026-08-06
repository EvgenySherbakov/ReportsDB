# Changelog

[Русский](CHANGELOG.md) · **English**

Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), versions
follow [semantic versioning](https://semver.org/).

Detailed decisions with their rationale live in the journal
([specification, section 15](docs/TZ.md#15-журнал-решений), Russian only). This
file records only what a user can see.

## [Unreleased]

### Added

- **Unique reports** — a new page in the "Reports" section, the other side of
  "Similar reports": it shows what is a network's or a plant's own. A report is
  unique when the other side has neither a namesake nor a close twin by table
  set. **The comparison scope is a switch:**
  - *between networks* — what other trade networks (ТС) do not have. The unit of
    counting is a network's report: one standing on several of its plants counts
    once;
  - *between plants of one network* — what the neighbours do not have. The unit
    of counting is a report record on a plant.

  A summary of "reports total / unique / share / volume of their tables /
  executions", a stack of "unique and present on the other side", and a
  similarity threshold slider. Clicking a report shows its tables with size and
  retention depth, its executions, average duration and why the report counts as
  unique: the closest report on the other side is named together with the
  similarity value. A network or plant with nothing to compare against is named
  outright — there "every report is unique" only means there was nothing to
  compare with.
- The `v_report_twin` and `v_network_report_twin` views — available on the
  "SQL query" page: a report's namesakes and closest twins for both comparison
  scopes, and a network's report as a whole with its plants on one line.
- The demo data now contains "network-wide" reports — one name across different
  networks and on several plants of a network. Without that overlap the new page
  would show 100% unique on the demo database and check nothing.

### Fixed

- **The app crashed with a full-screen error** when the database was opened
  while another process held it — for example, a load or a clear running in a
  neighbouring tab. DuckDB does not allow opening the same file for reading and
  for writing at once: such an attempt failed with an unhandled error and broke
  the whole page, including "Load data", which by design is supposed to be the
  recovery path for any failure. Now a busy database shows a "The database is
  currently busy" warning asking to wait and refresh, and "Load data" stays
  fully usable — a file can be picked and loaded without waiting for someone
  else to release the database.

## [1.5.0] — 2026-08-06

Working with lists. The search accepts several values at once, the found tables
show all of their reports, and the result is taken away as a ready Excel
workbook. Loaded data can be erased from the interface.

### Added

- **Clearing the database** on the "Load data" page: a collapsed block with a
  confirmation erases all loaded data. An empty database of the right structure
  remains — the analytics still opens and shows zeros. The
  `reports.duckdb.bak` backup is deleted along with the database (a checkbox
  keeps it), and source files in `data/raw/` are left alone. The same thing as
  the `scripts\rdb.bat clear` command.

- **Searching by several values at once** in every table: separated by a comma,
  a semicolon or pasted as a list from Excel — a row enters the result if it
  matches at least one. Below the table it says how many values the query holds
  and which of them found nothing. The field became multi-line: a column from
  Excel could not be pasted into a single-line one — the browser merged the
  lines into a single word.
- **An "Exact match" checkbox** for the search: names are compared in full
  rather than as a part. For a list of ready names this is essential —
  otherwise `itm.log` drags in `dbo.catalog` and `wh1.errlog`. A name without a
  schema also counts as a match, so `TRIP_LOG` finds `sdd.trip_log`. Present in
  the colleague export too, as a toggle in the header.
- **All reports for the found tables at once** on №2 → "By tables": a block with
  "table + report" relations for the whole found list. Previously reports could
  only be seen one table per click.
- **That block exports to Excel, as two sheets.** "Отчёты": as many rows as
  there are reports, one per plant; the "Таблицы" column lists **all** of the
  report's tables separated by `;`, not only the found ones, plus an
  "Из них из запроса" column. "Таблицы": as many rows as there are found
  tables, with all of a table's reports separated by `;`. Russian headers,
  frozen header row, fitted column widths, and numbers stay numbers.
- `.streamlit/config.toml` was added to the Docker image: inside the container
  the dark theme was lost, a rim appeared around chart segments, and Streamlit
  usage statistics were switched back on.
- The RUNBOOK now describes handing the image to a colleague who has no access
  to the repository: building, `docker save`, transferring the file,
  `docker load`, running and updating.

### Fixed

- The Overview crashed on a completely empty database: after clearing there is
  no digest of the source files, and the caption at the bottom of the page
  ended in an error.
- On an empty database the missing-data warning said "table sizes not loaded"
  instead of stating that the database is empty and offering to load files.
- The search parsed the query as a regular expression: `[dbo].[Orders]` found
  the wrong rows, and a lone `*` crashed the page with an error.

## [1.0.0] — 2026-08-02

The first version. An Excel export describing SSRS reports is turned into a
local DuckDB database with ready-made analytics and two ways to look at the
data.

### Loading data

- Loading through the interface: files are placed into `data/raw/` or dragged
  onto the page, and the role of each is stated manually.
- **Several files per role** — one file per plant. Files are concatenated and
  loaded in a single pass; the plant is taken from the `ТС` and `Завод` columns
  inside the file.
- Column checking before the load: one tab per file, with an explanation of
  exactly what stops working without each missing column.
- Excel column names live only in `config/mapping.yml` — the code does not know
  them.
- A full rebuild of the database on every load; the previous version is kept as
  `reports.duckdb.bak`.
- Recovery of a table's schema from the sizes file when a report reference has
  none and the name occurs there with exactly one schema.
- The object kind comes from the file column; when there is none, from a name
  mask (`v_`, `vw_` → view), but never for objects that have segments.
- A database structure version: after a code update the app asks for a data
  reload in plain words instead of failing with an SQL error.
- The `diagnose` command — reconciling the sizes file with the database when
  the numbers disagree.

### Analytics

- **Five core views per DC** (network + plant): tables and sizes, report →
  tables, report → routines, report → executions, retention depth.
- **Report card** — clicking a row unfolds tables, sizes, statistics, routines
  and neighbouring reports.
- **Volume and cost** — two independent metrics: data and time.
- **Decommission candidates** with a confidence level.
- **Similar reports** — pairs from one plant with a matching set of tables.
- **ABC analysis** across four measures: the report's table volume, the freed
  volume, the execution count, the total time.
- **References**: all source objects with their kind and criticality; a
  comparison of plants by database volume and the share under reports.
- **Ad-hoc SQL** with a reference of the database structure.
- The `gross_mb` / `exclusive_mb` split: only the second one adds up across
  reports and means space that is actually freed.

### Handing it to colleagues

- **A single HTML file** with the same contents as the app: the same tabs, the
  same metrics, the same click-a-row breakdown. Opens by double-click, works
  offline, nothing to install.
- **A Docker image** — a one-command start.

### Other

- Dark theme by default; the chart palette is validated for distinguishability.
- Bilingual documentation: Russian and English versions side by side.
- 135 tests of model integrity and calculation correctness.

### Known limitations

- The analysis reflects the contents of the Excel export, not the actual
  dependencies of the reports: access through stored procedures and views is
  not visible in it.
- No history is kept — every load replaces the previous one.
- One plant cannot be added to an existing database; only a full rebuild.

[Unreleased]: https://github.com/EvgenySherbakov/ReportsDB/compare/v1.5.0...HEAD
[1.5.0]: https://github.com/EvgenySherbakov/ReportsDB/compare/v1.0.0...v1.5.0
[1.0.0]: https://github.com/EvgenySherbakov/ReportsDB/releases/tag/v1.0.0
