"""
ingest.py — Load MIMIC-III demo CSVs into a local SQLite database.

Usage:
    python ingest.py --data_dir ./data/mimic_demo --db_path ./db/mimic.db

Download the MIMIC-III demo CSVs from:
    https://physionet.org/content/mimiciii-demo/1.4/
    (no signup required for the demo)
"""

import sqlite3
import pandas as pd
import argparse
import logging
import time
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

# --- Schema: expected tables and their primary time columns ----------------------
TABLES = {
    # Core patient / admission tables
    "PATIENTS":         {"time_cols": ["dob", "dod", "dod_hosp", "dod_ssn"]},
    "ADMISSIONS":       {"time_cols": ["admittime", "dischtime", "deathtime", "edregtime", "edouttime"]},
    "ICUSTAYS":         {"time_cols": ["intime", "outtime"]},

    # Clinical event tables (large — chunked on load)
    "CHARTEVENTS":      {"time_cols": ["charttime", "storetime"], "chunksize": 100_000},
    "LABEVENTS":        {"time_cols": ["charttime"], "chunksize": 100_000},
    "OUTPUTEVENTS":     {"time_cols": ["charttime", "storetime"]},
    "INPUTEVENTS_CV":   {"time_cols": ["charttime", "storetime"]},
    "INPUTEVENTS_MV":   {"time_cols": ["starttime", "endtime", "storetime"]},

    # Orders / prescriptions
    "PRESCRIPTIONS":    {"time_cols": ["startdate", "enddate"]},
    "PROCEDURES_ICD":   {"time_cols": []},

    # Diagnoses / codes
    "DIAGNOSES_ICD":    {"time_cols": []},
    "D_ICD_DIAGNOSES":  {"time_cols": []},
    "D_ICD_PROCEDURES": {"time_cols": []},
    "D_ITEMS":          {"time_cols": []},
    "D_LABITEMS":       {"time_cols": []},

    # Clinical observations
    "MICROBIOLOGYEVENTS": {"time_cols": ["chartdate", "charttime"]},
    "DATETIMEEVENTS":   {"time_cols": ["charttime", "storetime"]},

    # Notes — present in full MIMIC-III, REMOVED in demo
    # "NOTEEVENTS":     {"time_cols": ["chartdate", "charttime"]},

    # Care / services
    "CALLOUT":          {"time_cols": ["createtime", "updatetime", "acknowledgetime", "outcometime"]},
    "CAREGIVERS":       {"time_cols": []},
    "SERVICES":         {"time_cols": ["transfertime"]},
    "TRANSFERS":        {"time_cols": ["intime", "outtime"]},

    # CPT / DRG
    "CPTEVENTS":        {"time_cols": ["chartdate"]},
    "DRGCODES":         {"time_cols": []},
}


def parse_args():
    """
    Parse command-line arguments for the ingestion pipeline.

    Arguments:
        --data_dir  : Path to the directory containing MIMIC-III demo CSV files.
                    Defaults to ./data/mimic_demo.
        --db_path   : Path where the output SQLite database will be written.
                    Defaults to ./db/mimic.db. Parent directory is created
                    automatically if it does not exist.
        --if_exists : Behaviour when a table already exists in the database.
                    'replace' drops and recreates the table (default, safe to
                    re-run), 'append' adds rows to the existing table, 'fail'
                    raises an error.
        --verify    : Boolean flag. When passed, prints a row count for every
                    table after loading completes so you can confirm all data
                    landed correctly.

    Returns:
        argparse.Namespace with attributes: data_dir, db_path, if_exists, verify.
    """
    p = argparse.ArgumentParser(description="Ingest MIMIC-III demo into SQLite")
    p.add_argument("--data_dir", default="./data/mimic_demo",
                    help="Directory containing MIMIC-III demo CSV files")
    p.add_argument("--db_path",  default="./db/mimic.db",
                    help="Output SQLite database path")
    p.add_argument("--if_exists", default="replace", choices=["replace", "append", "fail"],
                    help="What to do if a table already exists")
    p.add_argument("--verify", action="store_true",
                    help="Print row counts after loading")
    return p.parse_args()


def coerce_timestamps(df: pd.DataFrame, time_cols: list) -> pd.DataFrame:
    """
    Convert known datetime columns from raw CSV strings to ISO-format strings
    that SQLite can sort and compare correctly.

    MIMIC-III stores all dates as plain strings (e.g. '2112-07-14 02:31:00').
    SQLite has no native datetime type, so date arithmetic functions like
    JULIANDAY() and DATETIME() require the strings to be well-formed ISO-8601.
    This function uses pandas to parse each time column, which handles
    inconsistent formats and missing values gracefully:
        - Valid dates   → converted to 'YYYY-MM-DD HH:MM:SS' ISO string
        - Unparseable   → converted to None (SQL NULL) via errors='coerce'
        - Missing (NaT) → replaced with None (SQL NULL)

    This ensures that every time-windowed SQL query in the feature engineering
    layer (e.g. 'vitals in the first 24 hours of ICU stay') produces correct
    results rather than silently returning NULL on malformed values.

    Args:
        df        : pandas DataFrame containing the raw CSV data for one table.
        time_cols : List of column name strings to parse as datetimes. Columns
                    not present in the DataFrame are silently skipped.

    Returns:
        The same DataFrame with time columns converted in-place.
    """
    for col in time_cols:
        if col in df.columns:
            df[col] = pd.to_datetime(df[col], errors="coerce").astype(str)
            df[col] = df[col].replace("NaT", None)
    return df


def load_table(
    conn: sqlite3.Connection,
    csv_path: Path,
    table_name: str,
    time_cols: list,
    chunksize: int | None,
    if_exists: str,
    ) -> int:
    """
    Load a single MIMIC-III CSV file into a SQLite table.

    Applies three transformations to every row before writing:
        1. Column names are lowercased (SUBJECT_ID -> subject_id) to match
        SQL convention and the feature queries in sql/features.sql.
        2. Datetime columns are coerced to ISO strings via coerce_timestamps()
        so that JULIANDAY() and DATETIME() work correctly in SQLite.
        3. The pandas row index is excluded from the insert (index=False) to
        avoid an unwanted extra column in every table.

    Large tables (CHARTEVENTS, LABEVENTS) are loaded in chunks to avoid
    spiking memory. The first chunk uses the caller-supplied if_exists mode
    (typically 'replace') to create a fresh table; every subsequent chunk
    uses 'append' so rows accumulate rather than overwriting each other.
    Smaller tables are loaded in a single read-write operation.

    Args:
        conn       : Active sqlite3.Connection to the target database.
        csv_path   : Path object pointing to the CSV file to load.
        table_name : Name of the destination SQLite table (written lowercase).
        time_cols  : List of column names to parse as datetimes.
        chunksize  : Number of rows per chunk for large tables. None means
                    load the entire CSV into memory at once.
        if_exists  : Passed to DataFrame.to_sql() for the first chunk.
                    One of 'replace', 'append', or 'fail'.

    Returns:
        Integer total number of rows successfully written to SQLite.
    """
    total_rows = 0
    first_chunk = True

    if chunksize:
        reader = pd.read_csv(csv_path, chunksize=chunksize, low_memory=False)
        for chunk in reader:
            chunk.columns = chunk.columns.str.lower()
            chunk = coerce_timestamps(chunk, time_cols)
            mode = if_exists if first_chunk else "append"
            chunk.to_sql(table_name.lower(), conn, if_exists=mode, index=False)
            total_rows += len(chunk)
            first_chunk = False
    else:
        df = pd.read_csv(csv_path, low_memory=False)
        df.columns = df.columns.str.lower()
        df = coerce_timestamps(df, time_cols)
        df.to_sql(table_name.lower(), conn, if_exists=if_exists, index=False)
        total_rows = len(df)

    return total_rows


def create_indexes(conn: sqlite3.Connection):
    """
    Create indexes on the most frequently joined and filtered columns.

    Indexes are built after all tables are fully loaded rather than during
    insertion because building an index on a complete table is significantly
    faster than maintaining it incrementally across thousands of inserts.

    The following indexes are created:

        chartevents(subject_id, charttime) -- composite index used by every
            time-windowed vital sign query (e.g. vitals in first 24h of ICU
            stay). The composite order matches the WHERE clause pattern:
            filter by patient first, then narrow by time range.
        chartevents(itemid)                -- used when filtering by vital
            sign type (heart rate, SpO2, etc.) across all patients.
        labevents(subject_id, charttime)   -- same pattern as chartevents
            but for lab results.
        labevents(itemid)                  -- filter by lab test type.
        admissions(subject_id)             -- joins from admissions to patients.
        icustays(subject_id)               -- joins from icustays to patients.
        icustays(hadm_id)                  -- joins from icustays to admissions.
        diagnoses_icd(subject_id)          -- comorbidity lookups per patient.
        diagnoses_icd(icd9_code)           -- filter by diagnosis code prefix.
        prescriptions(subject_id)          -- medication lookups per patient.

    Without these indexes, every feature query performs a full table scan
    over CHARTEVENTS (330K rows in the demo, millions in full MIMIC-III).
    With them, the same queries run in milliseconds.

    IF NOT EXISTS ensures the function is safe to call on a database that
    already has indexes from a previous run.

    Args:
        conn : Active sqlite3.Connection to the target database.
    """
    log.info("Creating indexes...")
    indexes = [
        ("idx_chartevents_subject",  "chartevents(subject_id, charttime)"),
        ("idx_chartevents_item",     "chartevents(itemid)"),
        ("idx_labevents_subject",    "labevents(subject_id, charttime)"),
        ("idx_labevents_item",       "labevents(itemid)"),
        ("idx_admissions_subject",   "admissions(subject_id)"),
        ("idx_icustays_subject",     "icustays(subject_id)"),
        ("idx_icustays_hadm",        "icustays(hadm_id)"),
        ("idx_diagnoses_subject",    "diagnoses_icd(subject_id)"),
        ("idx_diagnoses_icd",        "diagnoses_icd(icd9_code)"),
        ("idx_prescriptions_subject","prescriptions(subject_id)"),
    ]
    for name, target in indexes:
        try:
            conn.execute(f"CREATE INDEX IF NOT EXISTS {name} ON {target}")
        except sqlite3.OperationalError as e:
            log.warning(f"  Index {name} skipped: {e}")
    conn.commit()
    log.info("Indexes created.")


def verify_load(conn: sqlite3.Connection):
    """
    Print a row count for every table in the database as a load sanity check.

    Queries sqlite_master to discover all tables dynamically, then runs
    SELECT COUNT(*) on each one. Only called when --verify is passed on the
    command line, as counting rows in large tables (e.g. CHARTEVENTS) adds
    a few seconds to the run.

    Expected row counts for the MIMIC-III demo:
        patients        100    (exactly 100 patients in the demo)
        admissions      129    (some patients have multiple admissions)
        icustays        136    (some admissions have multiple ICU stays)
        chartevents   ~330K    (the largest table by far)
        labevents      ~28K
        noteevents        0    (notes are removed from the demo)

    Any table showing 0 rows unexpectedly indicates the CSV was not found,
    was empty, or failed to load silently.

    Args:
        conn : Active sqlite3.Connection to the target database.
    """
    log.info("\n── Row counts ──────────────────────────────")
    cursor = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    )
    tables = [r[0] for r in cursor.fetchall()]
    for t in tables:
        count = conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        log.info(f"  {t:<28} {count:>8,} rows")
    log.info("────────────────────────────────────────────\n")


def run_smoke_test(conn: sqlite3.Connection):
    """
    Run a single end-to-end SQL query to verify the database is working correctly.

    Computes the average ICU length of stay in hours across all ICU stays
    using SQLite's JULIANDAY() function:

        AVG((JULIANDAY(outtime) - JULIANDAY(intime)) * 24)

    JULIANDAY() converts an ISO datetime string to a floating-point number
    representing days since January 1, 4713 BC. Subtracting two values gives
    the difference in days; multiplying by 24 converts to hours.

    This single query exercises three things simultaneously:
        - Timestamps: if coerce_timestamps() failed, JULIANDAY() returns NULL
        and the average will be NULL rather than a number.
        - Table load: if icustays did not load, the query raises an error.
        - SQLite math: confirms the JULIANDAY() function is available.

    A result in the range 80-120 hours indicates the database is healthy.
    A result of None means timestamps were not parsed correctly.
    A crash means the icustays table is missing entirely.

    Args:
        conn : Active sqlite3.Connection to the target database.
    """
    sql = """
        SELECT
            ROUND(AVG(
                (JULIANDAY(outtime) - JULIANDAY(intime)) * 24
            ), 2) AS avg_icu_hours
        FROM icustays
        WHERE intime IS NOT NULL AND outtime IS NOT NULL
    """
    result = conn.execute(sql).fetchone()[0]
    log.info(f"Smoke test — avg ICU stay: {result} hours")


def main():
    """
    Orchestrate the full MIMIC-III demo ingestion pipeline.

    Execution order:
        1. parse_args()         -- read CLI configuration
        2. Validate data_dir    -- exit early with a clear message if not found
        3. Open SQLite          -- create parent directories if needed
        4. Set PRAGMAs          -- journal_mode=WAL and synchronous=NORMAL for
                                faster bulk inserts without sacrificing safety
        5. Loop over TABLES     -- for each registered table, locate the CSV
                                (tries uppercase then lowercase filename),
                                skip with a warning if not found, otherwise
                                call load_table()
        6. create_indexes()     -- build all indexes after data is fully loaded
        7. verify_load()        -- print row counts if --verify was passed
        8. run_smoke_test()     -- always runs regardless of --verify
        9. Log summary          -- total tables, total rows, elapsed time
        10. Close connection    -- flush and release the SQLite file lock

    The two SQLite PRAGMAs set in step 4:
        journal_mode=WAL      Write-Ahead Logging allows reads to proceed
                            concurrently with writes and makes bulk inserts
                            significantly faster than the default rollback
                            journal mode.
        synchronous=NORMAL    Reduces how often SQLite flushes pages to disk.
                            Faster than the default FULL mode and safe enough
                            for a local development database where power-loss
                            durability is not a concern.

    Expected runtime: under 10 seconds for the full demo dataset.
    Expected output:  a single mimic.db file containing 22 tables, 10 indexes,
                    and approximately 412K total rows.
    """
    args = parse_args()
    data_dir = Path(args.data_dir)
    db_path  = Path(args.db_path)

    if not data_dir.exists():
        log.error(f"Data directory not found: {data_dir}")
        log.error("Download the MIMIC-III demo from: "
                    "https://physionet.org/content/mimiciii-demo/1.4/")
        raise SystemExit(1)

    db_path.parent.mkdir(parents=True, exist_ok=True)
    log.info(f"Connecting to SQLite: {db_path}")
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")   # faster concurrent writes
    conn.execute("PRAGMA synchronous=NORMAL") # safe but faster than FULL

    total_tables = 0
    total_rows   = 0
    t_start = time.time()

    for table_name, meta in TABLES.items():
        # Try both upper and lower case filenames
        csv_path = data_dir / f"{table_name}.csv"
        if not csv_path.exists():
            csv_path = data_dir / f"{table_name.lower()}.csv"
        if not csv_path.exists():
            log.warning(f"  {table_name:<28} — CSV not found, skipping")
            continue

        t0 = time.time()
        rows = load_table(
            conn=conn,
            csv_path=csv_path,
            table_name=table_name,
            time_cols=[c.lower() for c in meta["time_cols"]],
            chunksize=meta.get("chunksize"),
            if_exists=args.if_exists,
        )
        elapsed = time.time() - t0
        log.info(f"  {table_name:<28} {rows:>8,} rows  ({elapsed:.1f}s)")
        total_tables += 1
        total_rows   += rows

    create_indexes(conn)

    if args.verify:
        verify_load(conn)

    run_smoke_test(conn)

    elapsed_total = time.time() - t_start
    log.info(f"Done. {total_tables} tables · {total_rows:,} rows · {elapsed_total:.1f}s")
    log.info(f"Database saved to: {db_path}")
    conn.close()


if __name__ == "__main__":
    main()
