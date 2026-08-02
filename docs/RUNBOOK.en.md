# Runbook

[Русский](RUNBOOK.md) · **English**

Three ways to run it. Pick one — they are independent of each other.

| Option | What you need | Who it suits |
| --- | --- | --- |
| [A. Docker](#option-a--docker) | Docker Desktop | Most people: install once, then a single command |
| [B. Python](#option-b--python) | Python 3.11+ | Those who will change the code or load data themselves |
| [C. HTML file](#option-c--a-single-html-file) | Just a browser | Those who only need to look at the numbers |

In all three, **the data stays on your computer**. Nothing is sent anywhere; the
internet is needed only during installation.

---

## Option A — Docker

### Step 1. Install Docker Desktop

Download from <https://www.docker.com/products/docker-desktop/>, install and
start it. Wait until the whale icon stops blinking.

Check it in a terminal (PowerShell on Windows, Terminal on macOS):

```bash
docker --version
```

You should see a line like `Docker version 27.x.x`.

### Step 2. Get the project

```bash
git clone <repository address>
cd ReportsDB
```

If git is not installed, download the archive with "Code → Download ZIP" on the
repository page and unpack it.

### Step 3. Put your data files in place

Copy your Excel files into the `data/raw/` folder inside the project. You can
also skip this step — files can be uploaded straight from the interface.

### Step 4. Run

```bash
docker compose -f docker/docker-compose.yml up --build
```

The first start takes 2–5 minutes: the Python image is downloaded and libraries
are installed. Later starts take seconds.

When the terminal prints `You can now view your Streamlit app`, open
<http://localhost:8501> in a browser.

### Step 5. Load the data

**Load data** in the left menu. Then follow the steps on the page: choose the
reports file, optionally the sizes and usage files, and press **Load**. More
detail — [below](#how-to-load-data).

### Stopping

`Ctrl+C` in the terminal. Or, from another window:

```bash
docker compose -f docker/docker-compose.yml down
```

Data is not lost when stopping: the `data/` folder is mounted into the container
directly and lives on your disk.

### Starting again

```bash
docker compose -f docker/docker-compose.yml up
```

Without `--build` — the image is already built.

---

## Option B — Python

### Step 1. Install Python 3.11 or newer

<https://www.python.org/downloads/> — when installing on Windows, be sure to
tick **"Add Python to PATH"**.

Check:

```bash
python --version     # Windows
python3 --version    # macOS / Linux
```

### Step 2. Get the project

```bash
git clone <repository address>
cd ReportsDB
```

### Step 3. Run with a single command

**Windows:**

```bat
scripts\run.bat
```

**macOS / Linux:**

```bash
./scripts/run.sh
```

The script creates an isolated environment, installs the libraries and opens the
app at <http://localhost:8501>. If there is no database yet, it builds one from
demo data so that there is something to look at.

### If the script did not work — do it manually

```bash
python -m venv .venv

# Windows:
.venv\Scripts\activate
# macOS / Linux:
source .venv/bin/activate

pip install -e .
streamlit run app/Home.py
```

### Step 4. Load the data

Either through the interface (the **Load data** page) or with a command:

```bat
scripts\rdb.bat build data\raw\your_file.xlsx
```

macOS/Linux — `scripts/rdb.sh build data/raw/your_file.xlsx`.

### Stopping

`Ctrl+C` in the terminal.

---

## Option C — a single HTML file

For colleagues who only need to look. Nothing to install.

### How to get the file

Whoever already has the project running (option A or B) does one of two things:

- in the menu, **Tools → File for colleagues**, presses **Build HTML file** and
  then **Download**;
- or runs in a terminal:

  ```bat
  scripts\rdb.bat export-html
  ```

  (macOS/Linux — `scripts/rdb.sh export-html`.)
  The file appears at `dist/reportsdb.html`.

### How to use it

Send the file to a colleague any way you like — email, messenger, a USB stick.
The recipient saves it to disk and opens it by double-click.

Inside it is the same as in the app: tabs follow the same menu sections
(Overview, all of "DC analytics" №1–№5, the whole "Reports" section — card,
volume and cost, decommission candidates, similar reports, ABC analysis — and
the object reference). The same metrics, the same columns, the same
click-a-row breakdown, one DC selector for the whole file. Lists are complete,
nothing is truncated. Search, sorting and CSV export of the current selection
work without internet, and there are light and dark themes.

Only data loading and ad-hoc SQL are left out — those are the database owner's
tools.

The file is a **snapshot of the data at build time**. To refresh it, build it
again and resend.

---

## How to load data

The **Load data** page in the left menu. No command line needed.

### Step 1. Files

Two equivalent ways:

- copy the files into the `data/raw/` folder inside the project — they appear in
  the lists on the page;
- drag the files onto the upload area on the page — they are saved into the same
  folder.

Formats: `.xlsx`, `.xls`, `.xlsm`, `.csv`, `.tsv`.

### Step 2. Say which file is which

| Field | What to choose |
| --- | --- |
| **Reports** | Required. One row per report: number, network, plant, three catalog levels, name, "uses a view" flag, source tables, average duration and execution count |
| **Table sizes** | If available. A database segment export: `ТС`, `Завод`, `OWNER`, `SEGMENT_NAME`, `SEGMENT_TYPE`, `SIZE_MB`, `PERCENT_OF_TOTAL`, `Глубина хранения`. `ТС` and `Завод` are optional, but without them a size is treated as common to all plants, whereas in reality each plant has its own |
| **Usage frequency** | Usually not needed: execution count and duration are already in the main file. A separate file is only needed if statistics are kept separately — then its values override the main file |

The sizes file may be missing — load it later when it appears. The analytics
works without it, the volume columns will simply be empty.

If a workbook has several sheets, a sheet selector appears.

### Several files — one per plant

**Each field accepts several files at once.** That is exactly what to do when
the export arrives one file per plant: tick all the reports files in the first
field, all the sizes files in the second, and press "Load" once. The files are
concatenated and loaded in a single pass.

The plant of each row is taken from the **ТС** and **Завод** columns inside the
file, not from which file it came from. So the selection order does not matter,
and the same file accidentally chosen twice spoils nothing.

> **If the sizes files have no ТС and Завод columns** and there is more than one
> of them, the page shows a red warning. The load will not fail, but sizes of
> different plants will add up into one and the numbers will be inflated. Add
> the columns to the export, or load the sizes files one at a time.

The database is rebuilt in full every time: one load = the whole database from
the listed files. Adding one plant to an already-built database is not
possible — this is deliberate, so that there is never a question about which
data in the database is fresh and which is not.

> **If the numbers in table №1 do not match your file** — more tables than you
> counted, or the wrong volume — run the reconciliation:
>
> ```
> scripts\rdb.bat diagnose
> ```
>
> (macOS/Linux — `scripts/rdb.sh diagnose`.) Through `rdb`, not
> `python -m reportsdb`: the app lives in the project's virtual environment, and
> a plain console does not see the package — it answers "No module named
> reportsdb".
>
> It reads the sizes file exactly the way the loader does and shows at which
> step the numbers diverge: which columns were recognised, how many rows of each
> segment type were counted, how many unique tables that yields and whether it
> matches the database. Table names are not printed; to see them locally, use
> the `--show-names` flag.
>
> The two most common causes:
>
> 1. **The segment type column was not recognised.** Then index and LOB segments
>    count as tables: both the number of tables and the volume are inflated.
>    Write the actual column name into `config/mapping.yml` →
>    `table_sizes.columns.segment_type`.
> 2. **You counted by `SEGMENT_NAME` without the schema.** One table name occurs
>    in several schemas, and those are different tables — counting by
>    `OWNER.SEGMENT_NAME` gives more. The reconciliation shows both numbers.

### Step 3. Check the columns

Each selected file gets its own tab. The table shows which column of the file
corresponds to which expected field:

- ✅ — the column was found;
- • — not found. Below the tables, a "what will be unavailable" list expands
  with an explanation for each item.

Only two things block a load: a missing report name, and a missing `SIZE_MB` in
the sizes file. Everything else is a warning.

`SEGMENT_TYPE` deserves separate attention: without it, index and LOB segments
land in the table volume and inflate it.

**If the report name was not found:** open `config/mapping.yml`, find
`reports: → columns: → report_name:` and add your file's header as a new list
item. Save and refresh the page in the browser.

Case, stray spaces and «ё» instead of «е» make no difference — matching does not
distinguish them.

### Columns for the five core tables

The five views per DC are populated from these columns:

| Table | Column needed |
| --- | --- |
| №1 Object and size | `SIZE_MB` in the sizes file |
| №2 Report → tables | `Таблицы источники данных`; plus `View`, `Mat.view`, `Временные таблицы` — so that those objects do **not** end up in №2 |
| №3 Report → routines | `Функции/процедуры` in the reports file |
| №4 Report → executions | `Кол-во обращений` in the reports file |
| №5 Report → retention | `Глубина хранения` in the sizes file |

If the file has no separate `View`, `Mat.view` and `Временные таблицы` columns,
the object kind is decided by a name mask from `config/mapping.yml` — one is
enabled by default: the prefix `v_` (and `vw_`) means a view. The mask is not
applied to objects found in the sizes file: they have segments, therefore they
are physically stored and they are tables. Where each object's kind came from is
visible in the **Tables** reference — the "Kind determined by" column.

The load page addresses every missing column separately and states which table
will stay empty because of it.

### Step 4. Press "Load"

The database is rebuilt from scratch in seconds. After that the page shows how
many rows were read, how many reports were loaded, and how many unique tables
and relations were found.

Warnings, if something did not add up:

| Message | What it means |
| --- | --- |
| Rows rejected | The row has an empty report name. The list is in the expandable block below |
| References to tables without a schema | The source list has a name without a dot, e.g. `TempTable`. The schema is recorded as `(unknown)` |
| Sizes did not match | The sizes file contains tables that appear in no report. This is usually normal |
| Non-table segments skipped | Index and LOB segments. By design: they cannot be attributed to a table from this export and are not part of table volume |
| Statistics did not match | Report names in the statistics file did not match the catalog. Check the spelling |

The previous database is kept next to the new one as `reports.duckdb.bak` — if
you loaded the wrong file, you can restore it by renaming it back.

---

## Frequently asked questions

**"The database was built by an earlier version of the program."** Appears after
`git pull` when the code was updated but the database was not. Open **Load
data**, select the files and press **Load** — that is all it takes. The source
files in `data/raw/` are untouched, and the previous database is kept as
`reports.duckdb.bak`.

**Port 8501 is busy.** Start on another port:
`streamlit run app/Home.py --server.port 8502`; for Docker, adjust `ports` in
`docker/docker-compose.yml`.

**Cyrillic in CSV opens as gibberish.** Exports contain a BOM and open correctly
in Excel. If they still do not, choose UTF-8 encoding when opening.

**Will the data end up on GitHub?** No. The `data/` and `dist/` folders are
covered by `.gitignore`, and only code reaches the repository.

**How to refresh the data.** Put the new file in place and press "Load" again.
The database is rebuilt every time — there will be no accumulation or
duplicates.

**Nothing opens after `docker compose up`.** Check that Docker Desktop is
running and wait for the line `You can now view your Streamlit app` — on the
first start this takes a few minutes.
