"""
download_datasets.py -- Download all datasets for the ICU Clinical Intelligence project.

All data is saved inside the project under clinical_intelligence/data/:

    data/
    |-- mimic_demo/          MIMIC-III CSVs          (PhysioNet, no auth)
    |-- text/
    |   `-- mtsamples.csv    Clinical notes          (Kaggle, API key needed)
    |-- huggingface/
    |   |-- datasets/
    |   |   |-- pubmedqa/    QA pairs for fine-tuning (HuggingFace, no auth)
    |   |   `-- pmc_patients/ Clinical narratives     (HuggingFace, no auth)
    |   `-- hub/             HF model cache
    `-- ehrsql/              NL->SQL benchmark        (GitHub, no auth)

VitalDB streams at runtime via pip library -- no download needed.

Usage:
    python download_datasets.py              # download everything
    python download_datasets.py --check      # status check only
    python download_datasets.py --skip mtsamples
    python download_datasets.py --datasets mimic pubmedqa ehrsql
    python download_datasets.py --force      # re-download even if present

Kaggle setup (one-time, 30 sec):
    1. kaggle.com/settings -> API -> Create New Token -> downloads kaggle.json
    2. Windows : move %USERPROFILE%\\Downloads\\kaggle.json %USERPROFILE%\\.kaggle\\kaggle.json
       Mac/Linux: mv ~/Downloads/kaggle.json ~/.kaggle/kaggle.json && chmod 600 ~/.kaggle/kaggle.json
    3. Re-run this script
"""

import argparse
import os
import platform
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

# ---------------------------------------------------------------------------
# STEP 1: Resolve all paths and configure HuggingFace cache
#
# We use Path.resolve() to get the true absolute path with no symlinks or
# relative segments. This is what breaks HF on Windows/OneDrive -- the
# presence of spaces or sync markers in the path corrupts Arrow file writes.
#
# By pointing HF_DATASETS_CACHE at data/huggingface/datasets/ (resolved),
# all HuggingFace data stays inside the project on every platform.
# This must happen BEFORE `from datasets import ...` as HF reads env vars
# at import time.
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR     = PROJECT_ROOT / "data"
MIMIC_DIR    = DATA_DIR / "mimic_demo"
TEXT_DIR     = DATA_DIR / "text"
HF_DIR       = DATA_DIR / "huggingface"
EHRSQL_DIR   = DATA_DIR / "ehrsql"

# Create directories up front so env vars point to real paths
for _d in [DATA_DIR, MIMIC_DIR, TEXT_DIR, HF_DIR]:
    _d.mkdir(parents=True, exist_ok=True)

# Configure HF caches -- resolved posix paths work on Windows too
os.environ.setdefault("HF_DATASETS_CACHE",  (HF_DIR / "datasets").resolve().as_posix())
os.environ.setdefault("HF_HOME",             (HF_DIR / "hub").resolve().as_posix())
os.environ.setdefault("TRANSFORMERS_CACHE",  (HF_DIR / "transformers").resolve().as_posix())

# ---------------------------------------------------------------------------
# STEP 2: Imports (after env vars are set)
# ---------------------------------------------------------------------------

try:
    from datasets import load_dataset
    HF_AVAILABLE = True
except ImportError:
    HF_AVAILABLE = False

# Kaggle: authenticate once, store the API object. Using a module-level object
# avoids the "kg not defined" bug from the previous version.
KAGGLE_API = None
KAGGLE_AVAILABLE = False
_kaggle_key = Path.home() / ".kaggle" / "kaggle.json"
if _kaggle_key.exists():
    try:
        import kaggle as _kg
        _kg.api.authenticate()
        KAGGLE_API = _kg.api
        KAGGLE_AVAILABLE = True
    except Exception:
        pass

try:
    import requests
    from tqdm import tqdm
    REQUESTS_AVAILABLE = True
except ImportError:
    REQUESTS_AVAILABLE = False

# ---------------------------------------------------------------------------
# Terminal colours
# ---------------------------------------------------------------------------

GREEN  = "\033[92m"
YELLOW = "\033[93m"
RED    = "\033[91m"
BLUE   = "\033[94m"
RESET  = "\033[0m"
BOLD   = "\033[1m"

def ok(msg):   print(f"  {GREEN}+{RESET}  {msg}")
def warn(msg): print(f"  {YELLOW}!{RESET}  {msg}")
def err(msg):  print(f"  {RED}x{RESET}  {msg}")
def info(msg): print(f"  {BLUE}>{RESET}  {msg}")
def head(msg): print(f"\n{BOLD}{msg}{RESET}")

# ---------------------------------------------------------------------------
# Shared download helper
# ---------------------------------------------------------------------------

def _download_file(url: str, dest: Path, desc: str = "") -> bool:
    """Stream-download url to dest with a tqdm progress bar."""
    if not REQUESTS_AVAILABLE:
        result = subprocess.run(["curl", "-L", "-o", str(dest), url], capture_output=True)
        return result.returncode == 0
    try:
        resp = requests.get(url, stream=True, timeout=60)
        if resp.status_code == 404:
            return False
        resp.raise_for_status()
        total = int(resp.headers.get("content-length", 0))
        with open(dest, "wb") as f, tqdm(
            total=total, unit="B", unit_scale=True,
            desc=f"    {desc[:35]:<35}", leave=False,
        ) as bar:
            for chunk in resp.iter_content(chunk_size=8192):
                f.write(chunk)
                bar.update(len(chunk))
        return True
    except Exception as e:
        warn(f"Download failed: {e}")
        if dest.exists():
            dest.unlink()
        return False


def _hf_retry_hint(repo: str, config: str | None, save_path: str):
    """Print a manual retry snippet with pre-filled cache path."""
    config_arg = f', "{config}"' if config else ""
    print(f"""
  {YELLOW}Retry manually:{RESET}
    import os
    os.environ["HF_DATASETS_CACHE"] = "{(HF_DIR / "datasets").resolve().as_posix()}"
    from datasets import load_dataset
    ds = load_dataset("{repo}"{config_arg})
    ds.save_to_disk("{save_path}")
""")


# ===========================================================================
# Dataset 1 -- MIMIC-III Clinical Database Demo
# Saved to: data/mimic_demo/
# ===========================================================================

MIMIC_BASE_URL = "https://physionet.org/files/mimiciii-demo/1.4/"

MIMIC_FILES = [
    "ADMISSIONS.csv", "CALLOUT.csv", "CAREGIVERS.csv", "CHARTEVENTS.csv",
    "CPTEVENTS.csv", "DATETIMEEVENTS.csv", "DIAGNOSES_ICD.csv", "DRGCODES.csv",
    "D_CPT.csv", "D_ICD_DIAGNOSES.csv", "D_ICD_PROCEDURES.csv", "D_ITEMS.csv",
    "D_LABITEMS.csv", "ICUSTAYS.csv", "INPUTEVENTS_CV.csv", "INPUTEVENTS_MV.csv",
    "LABEVENTS.csv", "MICROBIOLOGYEVENTS.csv", "NOTEEVENTS.csv",
    "OUTPUTEVENTS.csv", "PATIENTS.csv", "PRESCRIPTIONS.csv",
    "PROCEDUREEVENTS_MV.csv", "PROCEDURES_ICD.csv", "SERVICES.csv", "TRANSFERS.csv",
]


def download_mimic(force: bool = False) -> bool:
    head("Dataset 1 -- MIMIC-III Clinical Database Demo")
    info(f"Destination: {MIMIC_DIR}/")
    info("Source: physionet.org (no signup required)")

    already = [f for f in MIMIC_FILES if (MIMIC_DIR / f).exists()]
    if len(already) == len(MIMIC_FILES) and not force:
        ok(f"All {len(MIMIC_FILES)} CSVs already present -- skipping")
        return True

    info(f"Downloading {len(MIMIC_FILES)} CSV files...")
    success_count = 0
    for fname in MIMIC_FILES:
        dest = MIMIC_DIR / fname
        if dest.exists() and not force:
            ok(f"{fname} already exists")
            success_count += 1
            continue
        if _download_file(f"{MIMIC_BASE_URL}{fname}", dest, fname):
            ok(f"{fname:<40} ({dest.stat().st_size / 1024:,.0f} KB)")
            success_count += 1
        else:
            warn(f"{fname} -- download failed")

    if success_count >= 20:
        ok(f"MIMIC-III ready: {success_count}/{len(MIMIC_FILES)} files in {MIMIC_DIR}/")
        return True
    else:
        err(f"Only {success_count}/{len(MIMIC_FILES)} files downloaded")
        print(f"""
  {YELLOW}Manual download:{RESET}
  1. https://physionet.org/content/mimiciii-demo/1.4/
  2. Download all CSVs and place them in: {MIMIC_DIR}/
""")
        return False


# ===========================================================================
# Dataset 2 -- MTSamples
# Saved to: data/text/mtsamples.csv
# ===========================================================================

def download_mtsamples(force: bool = False) -> bool:
    head("Dataset 2 -- MTSamples (5,000 clinical transcription notes)")
    info(f"Destination: {TEXT_DIR}/mtsamples.csv")
    info("Source: Kaggle -- tboyle10/medicaltranscriptions")

    dest = TEXT_DIR / "mtsamples.csv"
    if dest.exists() and not force:
        ok(f"Already present ({dest.stat().st_size / 1e6:.1f} MB) -- skipping")
        return True

    # Strategy 1: Kaggle API
    if KAGGLE_AVAILABLE and KAGGLE_API is not None:
        info("Kaggle API key found -- downloading via API...")
        try:
            KAGGLE_API.dataset_download_files(
                "tboyle10/medicaltranscriptions",
                path=str(TEXT_DIR),
                unzip=True,
                quiet=False,
            )
            for f in TEXT_DIR.glob("*.csv"):
                if f != dest:
                    f.rename(dest)
                    break
            if dest.exists():
                ok(f"MTSamples saved to {dest}")
                return True
        except Exception as e:
            warn(f"Kaggle API failed: {e}")

    # Strategy 2: direct URL
    info("Trying direct download...")
    zip_dest = TEXT_DIR / "mtsamples.zip"
    direct_url = (
        "https://www.kaggle.com/api/v1/datasets/download/"
        "tboyle10/medicaltranscriptions?datasetVersionNumber=1"
    )
    if _download_file(direct_url, zip_dest, "mtsamples.zip") and zip_dest.exists():
        try:
            with zipfile.ZipFile(zip_dest, "r") as z:
                z.extractall(TEXT_DIR)
            zip_dest.unlink()
            for f in TEXT_DIR.glob("*.csv"):
                if f != dest:
                    f.rename(dest)
                    break
            if dest.exists():
                ok(f"MTSamples saved to {dest}")
                return True
        except Exception as e:
            warn(f"Unzip failed: {e}")

    # Strategy 3: manual
    err("Could not download MTSamples automatically")
    print(f"""
  {YELLOW}Setup Kaggle API key (30 seconds):{RESET}
  1. kaggle.com/settings -> API -> Create New Token
  2. Windows : move %USERPROFILE%\\Downloads\\kaggle.json %USERPROFILE%\\.kaggle\\kaggle.json
     Mac/Linux: mv ~/Downloads/kaggle.json ~/.kaggle/kaggle.json && chmod 600 ~/.kaggle/kaggle.json
  3. python download_datasets.py --datasets mtsamples

  {YELLOW}Or manual:{RESET}
  1. kaggle.com/datasets/tboyle10/medicaltranscriptions
  2. Place mtsamples.csv in: {TEXT_DIR}/
""")
    return False


# ===========================================================================
# Dataset 3 -- PubMedQA
# Saved to: data/huggingface/datasets/pubmedqa/
# ===========================================================================

def download_pubmedqa(force: bool = False) -> bool:
    head("Dataset 3 -- PubMedQA (273K biomedical QA pairs)")
    dest = HF_DIR / "datasets" / "pubmedqa"
    info(f"Destination: {dest}/")
    info("Source: HuggingFace -- qiaojin/PubMedQA")

    if dest.exists() and any(dest.iterdir()) and not force:
        ok(f"Already present -- skipping")
        return True

    if not HF_AVAILABLE:
        err("HuggingFace datasets not installed -- run: pip install datasets")
        return False

    dest.mkdir(parents=True, exist_ok=True)
    info(f"HF cache: {os.environ.get('HF_DATASETS_CACHE')}")

    info("Downloading pqa_labeled (~1K expert QA pairs)...")
    try:
        ds = load_dataset("qiaojin/PubMedQA", "pqa_labeled")
        ds.save_to_disk(str(dest / "pqa_labeled"))
        ok(f"pqa_labeled: {len(ds['train']):,} pairs -> {dest}/pqa_labeled")
    except Exception as e:
        err(f"pqa_labeled failed: {e}")
        _hf_retry_hint("qiaojin/PubMedQA", "pqa_labeled", str(dest / "pqa_labeled"))
        return False

    info("Downloading pqa_artificial (~211K auto-generated, optional)...")
    try:
        ds_art = load_dataset("qiaojin/PubMedQA", "pqa_artificial")
        ds_art.save_to_disk(str(dest / "pqa_artificial"))
        ok(f"pqa_artificial: {len(ds_art['train']):,} pairs -> {dest}/pqa_artificial")
    except Exception as e:
        warn(f"pqa_artificial skipped (optional): {e}")

    ok("PubMedQA ready")
    return True


# ===========================================================================
# Dataset 4 -- EHRSQL
# Saved to: data/ehrsql/
# glee4810/EHRSQL was removed from HuggingFace Hub -- clone from GitHub.
# ===========================================================================

EHRSQL_GITHUB = "https://github.com/glee4810/EHRSQL.git"


def download_ehrsql(force: bool = False) -> bool:
    head("Dataset 4 -- EHRSQL (NL->SQL pairs on MIMIC schema)")
    info(f"Destination: {EHRSQL_DIR}/")
    info("Source: GitHub -- glee4810/EHRSQL (removed from HuggingFace Hub)")

    if EHRSQL_DIR.exists() and any(EHRSQL_DIR.iterdir()) and not force:
        ok(f"Already present -- skipping")
        return True

    # Strategy 1: git clone
    if shutil.which("git"):
        info("Cloning from GitHub...")
        if EHRSQL_DIR.exists():
            shutil.rmtree(EHRSQL_DIR)
        result = subprocess.run(
            ["git", "clone", "--depth", "1", EHRSQL_GITHUB, str(EHRSQL_DIR)],
            capture_output=True, text=True,
        )
        if result.returncode == 0:
            n = len(list(EHRSQL_DIR.rglob("*.json")))
            ok(f"EHRSQL cloned -> {EHRSQL_DIR}/ ({n} JSON files)")
            return True
        else:
            warn(f"git clone failed: {result.stderr.strip()}")
    else:
        warn("git not found -- trying zip fallback")

    # Strategy 2: zip download
    info("Downloading as zip archive...")
    zip_dest = DATA_DIR / "ehrsql.zip"
    zip_url  = "https://github.com/glee4810/EHRSQL/archive/refs/heads/main.zip"

    if _download_file(zip_url, zip_dest, "EHRSQL.zip") and zip_dest.exists():
        try:
            with zipfile.ZipFile(zip_dest, "r") as z:
                z.extractall(DATA_DIR)
            zip_dest.unlink()
            extracted = DATA_DIR / "EHRSQL-main"
            if extracted.exists():
                if EHRSQL_DIR.exists():
                    shutil.rmtree(EHRSQL_DIR)
                extracted.rename(EHRSQL_DIR)
            ok(f"EHRSQL extracted -> {EHRSQL_DIR}/")
            return True
        except Exception as e:
            err(f"Zip extraction failed: {e}")

    err("EHRSQL download failed")
    print(f"""
  {YELLOW}Manual install:{RESET}
    git clone --depth 1 https://github.com/glee4810/EHRSQL {EHRSQL_DIR}
""")
    return False


# ===========================================================================
# Dataset 5 -- VitalDB
# No download needed -- pip library streams data at runtime
# ===========================================================================

def check_vitaldb() -> bool:
    head("Dataset 5 -- VitalDB (6,388 surgical vital sign cases)")
    info("Source: pip library -- no download, streams at runtime")
    try:
        import vitaldb
        ok("vitaldb installed -- use vitaldb.load(['HR', 'PLETH_SPO2']) at runtime")
        try:
            tracks = vitaldb.trks("HR")
            ok(f"VitalDB API reachable -- {len(tracks)} cases with HR data")
        except Exception:
            warn("VitalDB API check skipped (needs internet at runtime)")
        return True
    except ImportError:
        warn("vitaldb not installed -- run: pip install vitaldb")
        return False


# ===========================================================================
# Bonus -- PMC-Patients
# Saved to: data/huggingface/datasets/pmc_patients/
# ===========================================================================

def download_pmc_patients(force: bool = False) -> bool:
    head("Bonus -- PMC-Patients (167K clinical narratives for RAG)")
    dest = HF_DIR / "datasets" / "pmc_patients"
    info(f"Destination: {dest}/")
    info("Source: HuggingFace -- AGBonnet/augmented-clinical-notes")

    if dest.exists() and any(dest.iterdir()) and not force:
        ok(f"Already present -- skipping")
        return True

    if not HF_AVAILABLE:
        err("HuggingFace datasets not installed -- run: pip install datasets")
        return False

    dest.mkdir(parents=True, exist_ok=True)
    info(f"HF cache: {os.environ.get('HF_DATASETS_CACHE')}")

    info("Downloading (~372 MB)...")
    try:
        ds = load_dataset("AGBonnet/augmented-clinical-notes")
        ds.save_to_disk(str(dest))
        total = sum(len(v) for v in ds.values())
        ok(f"PMC-Patients saved -> {dest}/ ({total:,} clinical narratives)")
        return True
    except Exception as e:
        warn(f"PMC-Patients failed: {e}")
        warn("Optional -- MTSamples already covers RAG for Track 2")
        _hf_retry_hint("AGBonnet/augmented-clinical-notes", None, str(dest))
        return False


# ===========================================================================
# Status check
# ===========================================================================

def check_status():
    head("Dataset status check")
    print(f"  Project root : {PROJECT_ROOT}")
    print(f"  Data folder  : {DATA_DIR}")
    print()

    checks = {
        "MIMIC-III demo" : MIMIC_DIR / "PATIENTS.csv",
        "MTSamples"      : TEXT_DIR  / "mtsamples.csv",
        "PubMedQA"       : HF_DIR / "datasets" / "pubmedqa" / "pqa_labeled",
        "EHRSQL"         : EHRSQL_DIR / "data",
        "PMC-Patients"   : HF_DIR / "datasets" / "pmc_patients" / "dataset_dict.json",
    }

    all_good = True
    for name, path in checks.items():
        if path.exists():
            ok(f"{name:<25} ready  ({path})")
        else:
            warn(f"{name:<25} NOT found  (expected: {path})")
            all_good = False

    try:
        import vitaldb
        ok(f"{'VitalDB':<25} ready (library installed)")
    except ImportError:
        warn(f"{'VitalDB':<25} NOT installed  ->  pip install vitaldb")
        all_good = False

    print()
    if all_good:
        ok("All datasets ready.")
        print(f"\n  Next step: python ingest.py --data_dir {MIMIC_DIR} --db_path {DATA_DIR / 'mimic.db'}\n")
    else:
        warn("Some datasets missing -- re-run: python download_datasets.py")


# ===========================================================================
# Main
# ===========================================================================

ALL_DATASETS = ["mimic", "mtsamples", "pubmedqa", "ehrsql", "vitaldb", "pmc"]


def parse_args():
    p = argparse.ArgumentParser(
        description="Download all datasets for the ICU Clinical Intelligence project",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python download_datasets.py                          # download everything
  python download_datasets.py --check                  # status only
  python download_datasets.py --skip mtsamples         # skip if no Kaggle key
  python download_datasets.py --datasets mimic pubmedqa ehrsql
  python download_datasets.py --force                  # re-download everything
        """,
    )
    p.add_argument("--datasets", nargs="+", choices=ALL_DATASETS,
                   default=ALL_DATASETS, help="Which datasets to download (default: all)")
    p.add_argument("--skip",     nargs="+", choices=ALL_DATASETS,
                   default=[],             help="Datasets to skip")
    p.add_argument("--check",    action="store_true", help="Status check only")
    p.add_argument("--force",    action="store_true", help="Re-download even if present")
    return p.parse_args()


def main():
    args = parse_args()

    print(f"\n{BOLD}ICU Clinical Intelligence -- Dataset Downloader{RESET}")
    print("-" * 48)
    print(f"Project root : {PROJECT_ROOT}")
    print(f"Data folder  : {DATA_DIR}")
    print(f"HF cache     : {os.environ.get('HF_DATASETS_CACHE')}")

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
        try:
            results[name] = fn_map[name]() if name == "vitaldb" else fn_map[name](force=args.force)
        except KeyboardInterrupt:
            print("\n\nInterrupted by user")
            sys.exit(1)
        except Exception as e:
            err(f"Unexpected error for {name}: {e}")
            results[name] = False

    head("Summary")
    all_ok = True
    for name, success in results.items():
        if success:
            ok(name)
        else:
            err(f"{name} -- failed (see instructions above)")
            all_ok = False

    print()
    if all_ok:
        print(f"{GREEN}{BOLD}All datasets ready.{RESET}")
        print(f"Next step: python ingest.py --data_dir {MIMIC_DIR} --db_path {DATA_DIR / 'mimic.db'}\n")
    else:
        print(f"{YELLOW}Some datasets need manual steps -- see instructions above.{RESET}\n")


if __name__ == "__main__":
    main()
