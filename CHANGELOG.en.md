# Changelog

[Русский](CHANGELOG.md) · **English**

Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), versions
follow [semantic versioning](https://semver.org/).

Detailed decisions with their rationale live in the journal
([specification, section 15](docs/TZ.md#15-журнал-решений), Russian only). This
file records only what a user can see.

## [Unreleased]

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

[Unreleased]: https://github.com/EvgenySherbakov/ReportsDB/compare/v1.0.0...HEAD
[1.0.0]: https://github.com/EvgenySherbakov/ReportsDB/releases/tag/v1.0.0
