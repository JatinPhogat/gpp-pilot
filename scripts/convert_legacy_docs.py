"""Convert legacy .doc files into a separate normalized DOCX corpus.

Never mutates ``data/raw``. The output manifest is the ingestion input because
it records the usable DOCX path for both original DOCX and converted DOC files.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from app.config import CANONICAL_MANIFEST, CONVERSION_MANIFEST, NORMALIZED


def find_soffice(requested: str | None) -> str:
    """Find LibreOffice in PATH or its standard Windows install locations."""
    candidates = [requested, shutil.which("soffice"), shutil.which("soffice.exe")]
    if os.name == "nt":
        candidates.extend([
            r"C:\Program Files\LibreOffice\program\soffice.exe",
            r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
        ])
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return str(candidate)
    raise FileNotFoundError(
        "LibreOffice was not found. Install it, or pass --soffice "
        '"C:\\Program Files\\LibreOffice\\program\\soffice.exe".'
    )


def convert(source: Path, destination_dir: Path, soffice: str, profile_root: Path) -> tuple[str, str | None, str | None]:
    destination_dir.mkdir(parents=True, exist_ok=True)
    expected = destination_dir / f"{source.stem}.docx"
    if expected.exists():
        return "converted", str(expected.resolve()), None
    # LibreOffice locks a user profile. Every concurrent conversion therefore
    # receives its own temporary profile directory.
    profile_dir = profile_root / source.stem
    profile_dir.mkdir(parents=True, exist_ok=True)
    command = [soffice, f"-env:UserInstallation={profile_dir.resolve().as_uri()}", "--headless", "--convert-to", "docx", "--outdir", str(destination_dir), str(source)]
    try:
        result = subprocess.run(command, capture_output=True, text=True, check=False)
    except FileNotFoundError as exc:
        return "failed", None, str(exc)
    if result.returncode == 0 and expected.exists():
        return "converted", str(expected.resolve()), None
    error = (result.stderr or result.stdout or f"LibreOffice exited {result.returncode}").strip()
    return "failed", None, error


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, default=CANONICAL_MANIFEST)
    parser.add_argument("--soffice", help="Path to soffice.exe; auto-detected when omitted")
    parser.add_argument("--limit", type=int, help="Process only the first N legacy documents")
    parser.add_argument("--progress-every", type=int, default=1, help="Print progress every N legacy documents (default: 1)")
    parser.add_argument("--workers", type=int, default=4, help="Parallel LibreOffice conversions (default: 4)")
    args = parser.parse_args()

    try:
        soffice = find_soffice(args.soffice)
    except FileNotFoundError as exc:
        parser.error(str(exc))
    source = json.loads(args.manifest.read_text(encoding="utf-8"))
    legacy_documents = [doc for doc in source["documents"] if doc["original_format"] == "doc"]
    legacy_total = len(legacy_documents) if args.limit is None else min(len(legacy_documents), args.limit)
    if args.workers < 1:
        parser.error("--workers must be at least 1")
    legacy_seen = 0
    converted_count = failed_count = 0
    print(f"Converting {legacy_total} legacy .doc files with: {soffice}", flush=True)
    pending = []
    profile_root = NORMALIZED / ".lo_profiles"
    for document in source["documents"]:
        path = Path(document["local_path"])
        if document["original_format"] == "docx":
            document.update({"conversion_status": "not_required", "converted_path": str(path.resolve()), "conversion_error": None})
            continue
        if args.limit is not None and legacy_seen >= args.limit:
            document.update({"conversion_status": "not_started", "converted_path": None, "conversion_error": None})
            continue
        legacy_seen += 1
        pending.append((legacy_seen, document, path, NORMALIZED / f"{document['series']}_series"))

    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = {executor.submit(convert, path, series_dir, soffice, profile_root): (number, document, path) for number, document, path, series_dir in pending}
        completed = 0
        for future in as_completed(futures):
            number, document, path = futures[future]
            completed += 1
            try:
                status, converted_path, error = future.result()
            except Exception as exc:
                status, converted_path, error = "failed", None, str(exc)
            document.update({"conversion_status": status, "converted_path": converted_path, "conversion_error": error})
            if status == "converted":
                converted_count += 1
            else:
                failed_count += 1
            if completed == 1 or completed % args.progress_every == 0 or status == "failed":
                suffix = f" FAILED: {error}" if status == "failed" else ""
                print(f"[{completed}/{legacy_total}] {document['series']}_series  {path.name}{suffix}", flush=True)

    NORMALIZED.mkdir(parents=True, exist_ok=True)
    output = {**source, "documents": source["documents"]}
    CONVERSION_MANIFEST.write_text(json.dumps(output, indent=2), encoding="utf-8")
    converted = sum(doc["conversion_status"] in {"converted", "not_required"} for doc in source["documents"])
    failed = sum(doc["conversion_status"] == "failed" for doc in source["documents"])
    print(f"Wrote {CONVERSION_MANIFEST}; usable={converted}, failed={failed}; converted_this_run={converted_count}; failed_this_run={failed_count}")


if __name__ == "__main__":
    main()
