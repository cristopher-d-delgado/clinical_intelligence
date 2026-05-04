"""
download_datasets.py — Download all datasets for the ICU Clinical Intelligence project.

Handles all 5 datasets with zero manual steps where possible:

    Dataset              Source          Auth needed?
    ─────────────────────────────────────────────────────────────────
    MIMIC-III Demo       PhysioNet       None (direct wget/curl)
    MTSamples            Kaggle          Kaggle API key (~30 sec setup)
    PubMedQA             HuggingFace     None
    EHRSQL               HuggingFace     None
    VitalDB              pip library     None (installed, not downloaded)

Usage:
    # Download everything
    python download_datasets.py

    # Download specific datasets only
    python download_datasets.py --datasets mimic mtsamples pubmedqa ehrsql

    # Skip a dataset (e.g. if Kaggle key not set up yet)
    python download_datasets.py --skip mtsamples

    # Check what's already downloaded without re-downloading
    python download_datasets.py --check

Kaggle API key setup (one-time, takes 30 seconds):
    1. Go to https://www.kaggle.com/settings → API → Create New Token
    2. This downloads kaggle.json to your Downloads folder
    3. Move it:  mv ~/Downloads/kaggle.json ~/.kaggle/kaggle.json
    4. chmod 600 ~/.kaggle/kaggle.json
    Then re-run this script — MTSamples will download automatically.
"""
# Define the imports
import argparse
from email.charset import BASE64
import json
import os
import shutil
import subprocess
import sys
import time
import zipfile
from pathlib import Path

# ---- Try optional imports gracefully ----
# Access Hugging Face Datasets
try: 
    from datasets import load_dataset
    HF_AVAILABLE = True
except ImportError:
    HF_AVAILABLE = False

# Access Kaggle 
try: 
    import kaggle as kg
    KAGGLE_AVAILABLE = True
except ImportError:
    KAGGLE_AVAILABLE = False
    
# Import requests module and tqdm
try: 
    import requests 
    from tqdm import tqdm
    REQUESTS_AVAILABLE = True
except ImportError:
    REQUESTS_AVAILABLE = False

# ---- Define colors for terminal output ----
GREEN  = "\033[92m"
YELLOW = "\033[93m"
RED    = "\033[91m"
BLUE   = "\033[94m"
RESET  = "\033[0m"
BOLD   = "\033[1m"

def ok(msg):    print(f"  {GREEN}✓{RESET}  {msg}")
def warn(msg):  print(f"  {YELLOW}!{RESET}  {msg}")
def err(msg):   print(f"  {RED}✗{RESET}  {msg}")
def info(msg):  print(f"  {BLUE}→{RESET}  {msg}")
def head(msg):  print(f"\n{BOLD}{msg}{RESET}")

# ---- Directory Layout ----
BASE = Path(__file__).parent
DATA = BASE / "data"
MIMIC_DIR = DATA / "mimic_demo"
TEXT_DIR = DATA / "text"
HF_DIR = DATA / "huggingface"

# ===============================================
# Dataset 1 - MIMIC III Clinical Database Demo
# ===============================================

# Define MIMIC III URL
MIMIC_BASE_URL = (
    "https://physionet.org/files/mimiciii-demo/1.4/"
)

# All CSV files in the MIMIC-III demo
MIMIC_FILES = [
    "ADMISSIONS.csv",
    "CALLOUT.csv",
    "CAREGIVERS.csv",
    "CHARTEVENTS.csv",
    "CPTEVENTS.csv",
    "DATETIMEEVENTS.csv",
    "DIAGNOSES_ICD.csv",
    "DRGCODES.csv",
    "D_CPT.csv",
    "D_ICD_DIAGNOSES.csv",
    "D_ICD_PROCEDURES.csv",
    "D_ITEMS.csv",
    "D_LABITEMS.csv",
    "ICUSTAYS.csv",
    "INPUTEVENTS_CV.csv",
    "INPUTEVENTS_MV.csv",
    "LABEVENTS.csv",
    "MICROBIOLOGYEVENTS.csv",
    "NOTEEVENTS.csv",       # Empty in demo but file exists
    "OUTPUTEVENTS.csv",
    "PATIENTS.csv",
    "PRESCRIPTIONS.csv",
    "PROCEDUREEVENTS_MV.csv",
    "PROCEDURES_ICD.csv",
    "SERVICES.csv",
    "TRANSFERS.csv",
]

# Define Helper function to request and download dataset
def _download_file(url: str, dest: Path, desc: str = "") -> bool:
    """
    Downloads a single file with a progress bar. Returns True on successful download.
    """
    try:
        # Obtain Response
        resp = requests.get(url, stream=True, timeout=30)
        if resp.status_code == 404:
            return False
        resp.raise_for_status()

        total = int(resp.headers.get("content-length", 0))
        with open(dest, "wb") as f, tqdm(
            total=total, unit="B", unit_scale=True,
            desc=f"    {desc[:35]:<35}", leave=False
        ) as bar:
            for chunk in resp.iter_content(chunk_size=8192):
                f.write(chunk)
                bar.update(len(chunk))
        return True
    except Exception as e:
        warn(f"Download failed for {url}: {e}")
        if dest.exists():
            dest.unlink()
        return False

def download_mimic(force: bool = False):
    head("Dataset 1 — MIMIC-III Clinical Database Demo")
    info("Source: physionet.org (no signup required for demo)")

    # Make Directory to store MIMIC Dataset
    MIMIC_DIR.mkdir(parents=True, exist_ok=True)

    # Handle if files already exist in directory
    already = [f for f in MIMIC_FILES if (MIMIC_DIR / f).exists()]
    if len(already) == len(MIMIC_FILES) and not force:
        ok(f"All {len(MIMIC_FILES)} MIMIC-III CSVs already present — skipping")
        return True

    info(f"Downloading {len(MIMIC_FILES)} CSV files to {MIMIC_DIR}/")

    success_count = 0
    for fname in MIMIC_FILES:
        dest = MIMIC_DIR / fname
        if dest.exists() and not force:
            ok(f"{fname} already exists")
            success_count += 1
            continue

        url = f"{MIMIC_BASE_URL}{fname}"

        success = _download_file(url, dest, fname)

        if success:
            size_kb = dest.stat().st_size / 1024
            ok(f"{fname:<40} ({size_kb:,.0f} KB)")
            success_count += 1
        else:
            warn(f"{fname} — download failed (may not exist in demo)")

    if success_count >= 20:  # Most files present
        ok(f"MIMIC-III demo ready: {success_count}/{len(MIMIC_FILES)} files in {MIMIC_DIR}/")
        return True
    else:
        err(f"Only {success_count}/{len(MIMIC_FILES)} files downloaded")
        _mimic_manual_instructions()
        return False
    
def _mimic_manual_instructions():
    print(f"""
        {YELLOW}Manual download instructions:{RESET}
        1. Go to: https://physionet.org/content/mimiciii-demo/1.4/
        2. Click "Files" tab → download all CSVs
        3. Place them in: {MIMIC_DIR}/
    """)

# =====================================================
# Dataset 2 - MTSamples (Clinical Transcription Notes)
# =====================================================
def download_mtsamples(force: bool = False):
    head("Dataset 2 — MTSamples (5,000 clinical transcription notes)")
    info("Source: Kaggle — tboyle10/medicaltranscriptions")

    TEXT_DIR.mkdir(parents=True, exist_ok=True)
    dest = TEXT_DIR / "mtsamples.csv"

    if dest.exists() and not force:
        size_mb = dest.stat().st_size / 1_000_000
        ok(f"MTSamples already present ({size_mb:.1f} MB) — skipping")
        return True

    # --- Strategy 1: Kaggle API -------------------------------------
    kaggle_key = Path.home() / ".kaggle" / "kaggle.json"
    if kaggle_key.exists():
        info("Kaggle API key found — downloading via API...")
        try:
            kg.api.authenticate()
            kg.api.dataset_download_files(
                "tboyle10/medicaltranscriptions",
                path=str(TEXT_DIR),
                unzip=True,
                quiet=False,
            )
            # Kaggle may name it differently — find and rename
            for f in TEXT_DIR.glob("*.csv"):
                if "transcription" in f.name.lower() or "mtsample" in f.name.lower():
                    f.rename(dest)
                    break
            if dest.exists():
                ok(f"MTSamples downloaded via Kaggle API → {dest}")
                return True
        except Exception as e:
            warn(f"Kaggle API failed: {e}")
    
    # --- Strategy 2: Direct URL (Kaggle dataset direct link) ------------
    info("Trying direct download...")
    direct_url = (
        "https://www.kaggle.com/api/v1/datasets/download/"
        "tboyle10/medicaltranscriptions?datasetVersionNumber=1"
    )
    zip_dest = TEXT_DIR / "mtsamples.zip"
    success = _download_file(direct_url, zip_dest, "mtsamples.zip")

    if success and zip_dest.exists():
        try:
            with zipfile.ZipFile(zip_dest, "r") as z:
                z.extractall(TEXT_DIR)
            zip_dest.unlink()
            # Rename extracted file to standard name
            for f in TEXT_DIR.glob("*.csv"):
                if dest not in [f]:
                    f.rename(dest)
                    break
            if dest.exists():
                ok(f"MTSamples downloaded → {dest}")
                return True
        except Exception as e:
            warn(f"Unzip failed: {e}")

    # ── Strategy 3: Manual instructions ──────────────────────────────────
    err("Could not download MTSamples automatically")
    _mtsamples_manual_instructions()
    return False

def _mtsamples_manual_instructions():
    print(f"""
    {YELLOW}Two options to get MTSamples:{RESET}

    Option A — Kaggle API (recommended, 30 seconds):
    1. Go to https://www.kaggle.com/settings → API → "Create New Token"
    2. This downloads kaggle.json
    3. mv ~/Downloads/kaggle.json ~/.kaggle/kaggle.json
    4. chmod 600 ~/.kaggle/kaggle.json
    5. Re-run: python download_datasets.py --datasets mtsamples

    Option B — Manual download:
    1. Go to: https://www.kaggle.com/datasets/tboyle10/medicaltranscriptions
    2. Download → place mtsamples.csv in: {TEXT_DIR}/
""")


# ════════════════════════════════════════════════════════════════════════════
# Dataset 3 — PubMedQA (fine-tuning data for Track 2)
# ════════════════════════════════════════════════════════════════════════════

def download_pubmedqa(force: bool = False):
    head("Dataset 3 — PubMedQA (273K biomedical QA pairs)")
    info("Source: HuggingFace — qiaojin/PubMedQA")

    dest = HF_DIR / "pubmedqa"

    if dest.exists() and any(dest.iterdir()) and not force:
        ok(f"PubMedQA already present at {dest}/ — skipping")
        return True

    if not HF_AVAILABLE:
        err("HuggingFace datasets library not installed")
        err("Fix: pip install datasets")
        return False

    dest.mkdir(parents=True, exist_ok=True)

    info("Downloading PubMedQA (this may take a minute)...")
    try:
        # pqa_labeled = 1K expert-annotated (best for fine-tuning)
        # pqa_artificial = 211K (for pre-training style tasks)
        ds = load_dataset("qiaojin/PubMedQA", "pqa_labeled")
        ds.save_to_disk(str(dest / "pqa_labeled"))

        ds_art = load_dataset("qiaojin/PubMedQA", "pqa_artificial")
        ds_art.save_to_disk(str(dest / "pqa_artificial"))

        n_labeled    = len(ds["train"])
        n_artificial = len(ds_art["train"])
        ok(f"PubMedQA saved → {dest}/")
        ok(f"  pqa_labeled:    {n_labeled:,} expert-annotated QA pairs")
        ok(f"  pqa_artificial: {n_artificial:,} auto-generated QA pairs")
        return True
    except Exception as e:
        if "LocalEntryNotFoundError" in str(type(e)) or "ConnectionError" in str(type(e)):
            err("No internet connection or HuggingFace Hub unreachable")
            err("Make sure you're online and retry")
        else:
            err(f"PubMedQA download failed: {e}")
        _pubmedqa_manual_instructions()
        return False


def _pubmedqa_manual_instructions():
    print(f"""
    {YELLOW}Manual alternative:{RESET}
    python3 -c "
    from datasets import load_dataset
    ds = load_dataset('qiaojin/PubMedQA', 'pqa_labeled')
    ds.save_to_disk('{HF_DIR}/pubmedqa/pqa_labeled')
    "
""")


# ════════════════════════════════════════════════════════════════════════════
# Dataset 4 — EHRSQL (NL → SQL benchmark for Track 4)
# ════════════════════════════════════════════════════════════════════════════

def download_ehrsql(force: bool = False):
    head("Dataset 4 — EHRSQL (NL→SQL pairs on MIMIC schema)")
    info("Source: HuggingFace — glee4810/EHRSQL")

    dest = HF_DIR / "ehrsql"

    if dest.exists() and any(dest.iterdir()) and not force:
        ok(f"EHRSQL already present at {dest}/ — skipping")
        return True

    if not HF_AVAILABLE:
        err("HuggingFace datasets library not installed")
        err("Fix: pip install datasets")
        return False

    dest.mkdir(parents=True, exist_ok=True)

    info("Downloading EHRSQL...")
    try:
        ds = load_dataset("glee4810/EHRSQL")
        ds.save_to_disk(str(dest))

        splits = {k: len(v) for k, v in ds.items()}
        ok(f"EHRSQL saved → {dest}/")
        for split, count in splits.items():
            ok(f"  {split}: {count:,} NL→SQL pairs")
        return True
    except Exception as e:
        if "LocalEntryNotFoundError" in str(type(e)) or "ConnectionError" in str(type(e)):
            err("No internet connection or HuggingFace Hub unreachable")
        else:
            err(f"EHRSQL download failed: {e}")
        print(f"""
  {YELLOW}Manual alternative:{RESET}
    python3 -c "
    from datasets import load_dataset
    ds = load_dataset('glee4810/EHRSQL')
    ds.save_to_disk('{dest}')
    "
""")
        return False


# ════════════════════════════════════════════════════════════════════════════
# Dataset 5 — VitalDB (Track 3 time-series transformer)
# ════════════════════════════════════════════════════════════════════════════

def check_vitaldb():
    head("Dataset 5 — VitalDB (6,388 surgical vital sign cases)")
    info("Source: pip library — data streamed at runtime (no bulk download)")

    try:
        import vitaldb
        ok("vitaldb library installed — data streams on demand")
        ok("No download needed: vitaldb.load(['HR', 'PLETH_SPO2']) fetches at runtime")

        # Quick check — list available tracks
        info("Verifying API connection...")
        try:
            tracks = vitaldb.trks("HR")
            ok(f"VitalDB API reachable — {len(tracks)} cases with heart rate data")
        except Exception:
            warn("VitalDB API check skipped (needs internet at runtime)")
        return True
    except ImportError:
        warn("vitaldb not installed yet")
        info("Fix: pip install vitaldb")
        info("Data streams live — no bulk download needed")
        return False


# ════════════════════════════════════════════════════════════════════════════
# PMC-Patients (bonus — clinical narratives for RAG)
# ════════════════════════════════════════════════════════════════════════════

def download_pmc_patients(force: bool = False):
    head("Bonus — PMC-Patients (167K clinical narratives for RAG)")
    info("Source: HuggingFace — AGBonnet/augmented-clinical-notes")

    dest = HF_DIR / "pmc_patients"

    if dest.exists() and any(dest.iterdir()) and not force:
        ok(f"PMC-Patients already present at {dest}/ — skipping")
        return True

    if not HF_AVAILABLE:
        err("HuggingFace datasets library not installed")
        return False

    dest.mkdir(parents=True, exist_ok=True)

    info("Downloading PMC-Patients (167K records — may take a few minutes)...")
    try:
        ds = load_dataset("AGBonnet/augmented-clinical-notes")
        ds.save_to_disk(str(dest))
        total = sum(len(v) for v in ds.values())
        ok(f"PMC-Patients saved → {dest}/ ({total:,} clinical narratives)")
        return True
    except Exception as e:
        warn(f"PMC-Patients download failed: {e}")
        warn("This dataset is optional — MTSamples covers RAG for Track 2")
        return False


# ════════════════════════════════════════════════════════════════════════════
# Status check
# ════════════════════════════════════════════════════════════════════════════

def check_status():
    head("Dataset status check")

    checks = {
        "MIMIC-III demo":   (MIMIC_DIR, "PATIENTS.csv"),
        "MTSamples":        (TEXT_DIR,  "mtsamples.csv"),
        "PubMedQA":         (HF_DIR / "pubmedqa", "pqa_labeled"),
        "EHRSQL":           (HF_DIR / "ehrsql",   "dataset_dict.json"),
        "PMC-Patients":     (HF_DIR / "pmc_patients", "dataset_dict.json"),
    }

    all_good = True
    for name, (directory, marker) in checks.items():
        path = directory / marker
        if path.exists():
            ok(f"{name:<25} ready")
        else:
            warn(f"{name:<25} NOT downloaded")
            all_good = False

    # VitalDB — check import
    try:
        import vitaldb
        ok(f"{'VitalDB':<25} ready (library installed)")
    except ImportError:
        warn(f"{'VitalDB':<25} NOT installed  →  pip install vitaldb")
        all_good = False

    print()
    if all_good:
        ok("All datasets ready — run: python ingest.py")
    else:
        warn("Some datasets missing — re-run download_datasets.py")


# ════════════════════════════════════════════════════════════════════════════
# Main
# ════════════════════════════════════════════════════════════════════════════

ALL_DATASETS = ["mimic", "mtsamples", "pubmedqa", "ehrsql", "vitaldb", "pmc"]

def parse_args():
    p = argparse.ArgumentParser(
        description="Download all datasets for the ICU Clinical Intelligence project",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python download_datasets.py                        # download everything
  python download_datasets.py --datasets mimic pubmedqa ehrsql
  python download_datasets.py --skip mtsamples       # skip Kaggle dataset
  python download_datasets.py --check                # status check only
  python download_datasets.py --force                # re-download even if present
        """
    )
    p.add_argument(
        "--datasets", nargs="+",
        choices=ALL_DATASETS, default=ALL_DATASETS,
        help="Which datasets to download (default: all)"
    )
    p.add_argument(
        "--skip", nargs="+",
        choices=ALL_DATASETS, default=[],
        help="Datasets to skip"
    )
    p.add_argument(
        "--check", action="store_true",
        help="Check download status only, don't download"
    )
    p.add_argument(
        "--force", action="store_true",
        help="Re-download even if files already exist"
    )
    return p.parse_args()


def main():
    args = parse_args()

    print(f"""
        {BOLD}ICU Clinical Intelligence — Dataset Downloader{RESET}
        {"─" * 48}
        Datasets will be saved to: {DATA}/
    """)

    if args.check:
        check_status()
        return

    targets = [d for d in args.datasets if d not in args.skip]
    results = {}

    fn_map = {
        "mimic":     download_mimic,
        "mtsamples": download_mtsamples,
        "pubmedqa":  download_pubmedqa,
        "ehrsql":    download_ehrsql,
        "vitaldb":   check_vitaldb,
        "pmc":       download_pmc_patients,
    }

    for name in targets:
        fn = fn_map[name]
        try:
            if name == "vitaldb":
                results[name] = fn()
            else:
                results[name] = fn(force=args.force)
        except KeyboardInterrupt:
            print("\n\nInterrupted by user")
            sys.exit(1)
        except Exception as e:
            err(f"Unexpected error for {name}: {e}")
            results[name] = False

    # ── Summary ──────────────────────────────────────────────────────────
    head("Summary")
    all_ok = True
    for name, success in results.items():
        if success:
            ok(f"{name}")
        else:
            err(f"{name} — failed (see instructions above)")
            all_ok = False

    print()
    if all_ok:
        print(f"{GREEN}{BOLD}All datasets ready.{RESET}")
        print(f"Next step: {BOLD}python ingest.py --data_dir ./data/mimic_demo --db_path ./db/mimic.db{RESET}\n")
    else:
        print(f"{YELLOW}Some datasets need manual steps — follow the instructions above.{RESET}\n")


if __name__ == "__main__":
    main()