"""Create canonical document metadata for the downloaded 3GPP corpus.

The original manifest remains untouched as a download provenance record.
This script writes ``manifest.canonical.json``, which is the manifest used by
the ingestion and retrieval pipeline.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any, Dict


DEFAULT_CORPUS = Path("data/raw/rel18")


def canonical_ids(filename: str) -> Dict[str, str]:
    stem = Path(filename).stem
    source_id, separator, ftp_version_code = stem.rpartition("-")
    if not separator:
        raise ValueError(f"Filename has no FTP version code: {filename}")
    match = re.fullmatch(r"(\d{5,})(-.+)?", source_id)
    if not match:
        raise ValueError(f"Unrecognised 3GPP source identifier: {filename}")
    digits, part_suffix = match.groups()
    spec_family = f"{digits[:2]}.{digits[2:]}"
    return {
        "series": digits[:2],
        "spec_family": spec_family,
        "spec_id": f"{spec_family}{part_suffix or ''}",
        "part_suffix": part_suffix or "",
        "ftp_version_code": ftp_version_code,
    }


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description="Normalize a 3GPP download manifest for ingestion.")
    parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    parser.add_argument("--hash-content", action="store_true", help="Add SHA-256 hashes; slower for a full corpus")
    args = parser.parse_args()

    corpus = args.corpus.resolve()
    source_path = corpus / "manifest.json"
    source = json.loads(source_path.read_text(encoding="utf-8"))
    release_match = re.search(r"(\d+)", str(source["release"]))
    if not release_match:
        raise ValueError("Manifest release is not numeric")
    release = int(release_match.group(1))

    documents = []
    for record in source["specs"]:
        path = Path(record["local_path"])
        ids = canonical_ids(record["zip_name"])
        document: Dict[str, Any] = {
            "document_id": f"rel{release}__{ids['spec_id']}__{ids['ftp_version_code']}",
            "release": release,
            **ids,
            "source_filename": path.name,
            "source_zip": record["zip_name"],
            "source_url": record["zip_url"],
            "local_path": str(path.resolve()),
            "original_format": path.suffix.lower().lstrip("."),
            "conversion_status": "not_started",
            "source_size_bytes": path.stat().st_size,
        }
        if args.hash_content:
            document["source_sha256"] = sha256(path)
        documents.append(document)

    output = {
        "schema_version": 1,
        "source_manifest": str(source_path.resolve()),
        "release": release,
        "document_count": len(documents),
        "documents": documents,
    }
    output_path = corpus / "manifest.canonical.json"
    output_path.write_text(json.dumps(output, indent=2), encoding="utf-8")
    print(f"Wrote {len(documents)} documents to {output_path}")


if __name__ == "__main__":
    main()
