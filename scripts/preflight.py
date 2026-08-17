"""Verify the local runtime before expensive ingestion or indexing work."""

from __future__ import annotations

import json
import os
import sys

from dotenv import load_dotenv
import torch

from app.config import CONVERSION_MANIFEST

load_dotenv()


def main() -> None:
    failures = []
    print(f"Python: {sys.executable}")
    print(f"CUDA available: {torch.cuda.is_available()}")
    if torch.cuda.is_available():
        print(f"GPU: {torch.cuda.get_device_name(0)}")
    else:
        failures.append("CUDA is unavailable")
    print(f"Groq key configured: {bool(os.getenv('GROQ_API_KEY'))}")
    if not os.getenv("GROQ_API_KEY"):
        failures.append("GROQ_API_KEY is missing from .env")
    if not CONVERSION_MANIFEST.exists():
        failures.append(f"Conversion manifest missing: {CONVERSION_MANIFEST}")
    else:
        manifest = json.loads(CONVERSION_MANIFEST.read_text(encoding="utf-8"))
        usable = [doc for doc in manifest["documents"] if doc.get("converted_path")]
        missing = [doc["document_id"] for doc in usable if not os.path.isfile(doc["converted_path"])]
        print(f"Usable documents: {len(usable)}/{len(manifest['documents'])}")
        print(f"Missing converted paths: {len(missing)}")
        if len(usable) != len(manifest["documents"]) or missing:
            failures.append("Conversion output is incomplete")
    if failures:
        raise SystemExit("Preflight failed: " + "; ".join(failures))
    print("Preflight passed")


if __name__ == "__main__":
    main()
