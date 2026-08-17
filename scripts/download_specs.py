"""Download the complete Rel-18 3GPP specification corpus from official FTP.

Examples:
  python scripts/download_specs.py
  python scripts/download_specs.py --release 18 --specs 38.331 --dry-run
  python scripts/download_specs.py --release 18 --series 37 38 --output data/raw
  python scripts/download_specs.py --release 18 --specs 38.331 38.321 23.501

``latest/Rel-<n>`` holds one current version of every spec for a release.
With no filters, this downloads every series and specification in Rel-18.
Each completed run writes a manifest for later ingestion, citations, and filters.
"""

from __future__ import annotations

import argparse
import io
import json
import logging
import re
import sys
import time
import zipfile
from html import unescape
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.parse import urljoin

import requests

FTP_ROOT = "https://www.3gpp.org/ftp/Specs/latest"
DEFAULT_OUTPUT = Path("data/raw")
REQUEST_TIMEOUT, RETRY_DELAY, MAX_RETRIES, DEFAULT_DELAY = 120, 5, 3, 1.0
DOCUMENT_SUFFIXES = (".docx", ".doc")

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger(__name__)


def create_session() -> requests.Session:
    session = requests.Session()
    session.headers.update({"User-Agent": "GPP-Pilot/1.0 (3GPP RAG research project)", "Accept": "text/html,application/xhtml+xml,*/*"})
    return session


def get_with_retry(session: requests.Session, url: str) -> Optional[requests.Response]:
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = session.get(url, timeout=REQUEST_TIMEOUT)
            if response.status_code == 200:
                return response
            logger.warning("HTTP %s for %s (attempt %s/%s)", response.status_code, url, attempt, MAX_RETRIES)
        except requests.RequestException as exc:
            logger.warning("Request error for %s: %s (attempt %s/%s)", url, exc, attempt, MAX_RETRIES)
        if attempt < MAX_RETRIES:
            time.sleep(RETRY_DELAY)
    return None


def parse_links(html: str, page_url: str) -> List[Tuple[str, str]]:
    """Parse absolute or relative links from the plain official FTP listings."""
    pattern = re.compile(r'<a\b[^>]*\bhref=["\']([^"\']+)["\'][^>]*>\s*([^<]+?)\s*</a>', re.IGNORECASE)
    return [(unescape(name).strip(), urljoin(page_url, unescape(href).strip())) for href, name in pattern.findall(html) if unescape(name).strip()]


def discover_series(session: requests.Session, release_url: str, delay: float) -> List[Tuple[str, str]]:
    logger.info("Fetching series list from %s", release_url)
    response = get_with_retry(session, release_url)
    if not response:
        return []
    result = [(name, url) for name, url in parse_links(response.text, release_url) if re.fullmatch(r"\d{2}_series", name)]
    time.sleep(delay)
    return sorted(result)


def discover_zips(session: requests.Session, series_name: str, series_url: str, delay: float) -> List[Tuple[str, str]]:
    response = get_with_retry(session, series_url.rstrip("/") + "/")
    if not response:
        logger.warning("Could not list %s", series_name)
        return []
    result = [(name, url) for name, url in parse_links(response.text, series_url) if name.lower().endswith(".zip")]
    time.sleep(delay)
    return sorted(result)


def extract_spec_number(zip_name: str) -> str:
    """Map FTP names to canonical IDs, preserving multipart suffixes.

    ``38331-ia0.zip`` becomes ``38.331`` and ``51010-4-i00.zip`` becomes
    ``51.010-4``.
    """
    source_id = Path(zip_name).stem.rsplit("-", 1)[0]
    match = re.fullmatch(r"(\d{5,})(-.+)?", source_id)
    if not match:
        return source_id
    digits, suffix = match.groups()
    return f"{digits[:2]}.{digits[2:]}{suffix or ''}"


def extract_version(zip_name: str) -> Optional[str]:
    stem = Path(zip_name).stem
    return stem.rsplit("-", 1)[1] if "-" in stem else None


def selected_series(all_series: Iterable[Tuple[str, str]], requested: Optional[List[str]], specs: Optional[List[str]]) -> List[Tuple[str, str]]:
    """Use all series by default; infer a smaller set only for explicit spec filters."""
    inferred = {spec.split(".", 1)[0] for spec in specs or []}
    wanted = {str(item).zfill(2) for item in requested or []} or inferred
    return [(name, url) for name, url in all_series if not wanted or name[:2] in wanted]


def download_and_extract(session: requests.Session, item: Dict[str, Any], series_dir: Path, force: bool) -> Optional[Path]:
    base_name = Path(item["zip_name"]).stem
    existing = [path for path in series_dir.glob(f"{base_name}.*") if path.suffix.lower() in DOCUMENT_SUFFIXES]
    if existing and not force:
        logger.info("    already present: %s", existing[0].name)
        return existing[0]
    response = get_with_retry(session, item["zip_url"])
    if not response:
        return None
    try:
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            members = [entry for entry in archive.infolist() if not entry.is_dir() and entry.filename.lower().endswith(DOCUMENT_SUFFIXES) and not Path(entry.filename).is_absolute() and ".." not in Path(entry.filename).parts]
            if not members:
                logger.warning("    no Word document in %s", item["zip_name"])
                return None
            member = max(members, key=lambda entry: entry.file_size)
            output = series_dir / f"{base_name}{Path(member.filename).suffix.lower()}"
            with archive.open(member) as source, output.open("wb") as destination:
                destination.write(source.read())
            logger.info("    saved %s (%.1f MB)", output.name, output.stat().st_size / 1024 / 1024)
            return output
    except zipfile.BadZipFile:
        logger.error("    invalid ZIP: %s", item["zip_name"])
        return None


def build_plan(session: requests.Session, args: argparse.Namespace, release_url: str) -> List[Dict[str, Any]]:
    all_series = discover_series(session, release_url, args.delay)
    if not all_series:
        raise RuntimeError(f"No series found at {release_url}")
    series = selected_series(all_series, args.series, args.specs)
    if not series:
        raise RuntimeError("None of the requested series/specification prefixes exist in this release.")
    requested_specs = set(args.specs or [])
    plan: List[Dict[str, Any]] = []
    for series_name, series_url in series:
        logger.info("Scanning %s", series_name)
        for zip_name, zip_url in discover_zips(session, series_name, series_url, args.delay):
            spec_number = extract_spec_number(zip_name)
            if requested_specs and spec_number not in requested_specs:
                continue
            plan.append({"release": f"Rel-{args.release}", "series": series_name[:2], "spec_number": spec_number, "ftp_version": extract_version(zip_name), "zip_name": zip_name, "zip_url": zip_url})
    return plan


def write_manifest(output_dir: Path, args: argparse.Namespace, plan: List[Dict[str, Any]], downloaded: int, failed: int) -> None:
    manifest = {"source": "3GPP official FTP", "release": f"Rel-{args.release}", "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "requested_series": args.series or "all", "requested_specs": args.specs or "all", "total_specs": len(plan), "downloaded_or_present": downloaded, "failed": failed, "specs": plan}
    (output_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")


def run(args: argparse.Namespace) -> None:
    if args.delay < 0:
        raise ValueError("--delay must be zero or greater")
    output_dir = Path(args.output) / f"rel{args.release}"
    output_dir.mkdir(parents=True, exist_ok=True)
    release_url = f"{FTP_ROOT}/Rel-{args.release}/"
    try:
        plan = build_plan(create_session(), args, release_url)
    except RuntimeError as exc:
        logger.error("%s", exc)
        sys.exit(1)
    logger.info("Plan: %d specifications for Rel-%s", len(plan), args.release)
    if not plan:
        logger.error("No matching ZIPs found. Check --specs values (e.g. 38.331).")
        sys.exit(1)
    if args.dry_run:
        (output_dir / "download_plan.json").write_text(json.dumps(plan, indent=2), encoding="utf-8")
        logger.info("Dry run: wrote %s", output_dir / "download_plan.json")
        return
    session, downloaded, failed = create_session(), 0, 0
    for index, item in enumerate(plan, start=1):
        series_dir = output_dir / f"{item['series']}_series"
        series_dir.mkdir(parents=True, exist_ok=True)
        logger.info("[%d/%d] TS %s (%s)", index, len(plan), item["spec_number"], item["zip_name"])
        path = download_and_extract(session, item, series_dir, args.force)
        if path:
            item.update({"local_path": str(path.resolve()), "document_format": path.suffix.lower().lstrip(".")})
            downloaded += 1
        else:
            failed += 1
        time.sleep(args.delay)
    write_manifest(output_dir, args, plan, downloaded, failed)
    logger.info("Finished: %d available, %d failed. Manifest: %s", downloaded, failed, output_dir / "manifest.json")


def main() -> None:
    parser = argparse.ArgumentParser(description="Download every current Rel-18 3GPP Word specification from official FTP by default.")
    parser.add_argument("--release", default="18", help="Release number (default: 18)")
    parser.add_argument("--series", nargs="+", help="Optional restriction to selected series, e.g. 38 37")
    parser.add_argument("--specs", nargs="+", help="Optional restriction to exact specs, e.g. 38.331 38.321 23.501")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT), help="Corpus root (default: data/raw)")
    parser.add_argument("--dry-run", action="store_true", help="Discover and save a plan, without downloading")
    parser.add_argument("--force", action="store_true", help="Re-download documents already present")
    parser.add_argument("--delay", type=float, default=DEFAULT_DELAY, help="Delay between requests in seconds (default: 1)")
    run(parser.parse_args())


if __name__ == "__main__":
    main()
