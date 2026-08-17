"""Extract ordered DOCX content into clause-aware JSONL chunks and assets."""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Iterator

from docx import Document
from docx.table import Table
from docx.text.paragraph import Paragraph
from dotenv import load_dotenv

from app.config import ASSETS, CHUNKS_PATH, CONVERSION_MANIFEST, INGESTION_REPORT, PROCESSED
from app.schemas import Chunk

load_dotenv()
CLAUSE_RE = re.compile(r"^\s*(\d+(?:\.\d+)+|\d+)\s*(?:[.:]\s*|\s+)(.*)$")
VISION_REQUEST_LOCK = threading.Lock()
VISION_NEXT_REQUEST_AT = 0.0
VISION_COOLDOWN_UNTIL = 0.0


def wait_for_vision_slot() -> None:
    """Keep all document workers below Groq's request limit and shared cooldown."""
    global VISION_NEXT_REQUEST_AT
    interval = 60 / int(os.getenv("GROQ_VISION_RPM", "28"))
    while True:
        with VISION_REQUEST_LOCK:
            now = time.monotonic()
            target = max(VISION_NEXT_REQUEST_AT, VISION_COOLDOWN_UNTIL)
            if target <= now:
                VISION_NEXT_REQUEST_AT = now + interval
                return
            delay = target - now
        time.sleep(delay)


def register_vision_cooldown(seconds: float) -> None:
    """Share Groq's TPM retry delay across all parallel document workers."""
    global VISION_COOLDOWN_UNTIL
    with VISION_REQUEST_LOCK:
        VISION_COOLDOWN_UNTIL = max(VISION_COOLDOWN_UNTIL, time.monotonic() + seconds)


def retry_after_seconds(error: Exception) -> float | None:
    match = re.search(r"try again in ([0-9.]+)s", str(error), flags=re.IGNORECASE)
    return float(match.group(1)) + 1.0 if match else None


def ordered_blocks(document: Document) -> Iterator[Paragraph | Table]:
    """Yield paragraphs and tables in body order, not in separate collections."""
    body = document.element.body
    for child in body.iterchildren():
        if child.tag.endswith("}p"):
            yield Paragraph(child, document)
        elif child.tag.endswith("}tbl"):
            yield Table(child, document)


def heading_level(paragraph: Paragraph) -> int | None:
    name = paragraph.style.name.lower() if paragraph.style else ""
    match = re.search(r"heading\s*(\d+)", name)
    if match:
        return int(match.group(1))
    text = paragraph.text.strip()
    if CLAUSE_RE.match(text) and len(text) < 180:
        return text.split()[0].count(".") + 1
    return None


def clause_and_title(text: str) -> tuple[str | None, str]:
    match = CLAUSE_RE.match(text)
    return (match.group(1), match.group(2).strip()) if match else (None, text.strip())


def markdown_table(table: Table) -> str:
    rows = [[" ".join(cell.text.split()) for cell in row.cells] for row in table.rows]
    rows = [row for row in rows if any(row)]
    if not rows:
        return ""
    width = max(map(len, rows))
    rows = [row + [""] * (width - len(row)) for row in rows]
    header = rows[0]
    return "\n".join(["| " + " | ".join(header) + " |", "| " + " | ".join("---" for _ in header) + " |", *["| " + " | ".join(row) + " |" for row in rows[1:]]])


def caption_image(path: Path) -> str:
    """Describe a retrieved diagram structurally; only called with --caption-images."""
    from groq import Groq
    print(f"  Groq Vision: {path.name}", flush=True)
    client = Groq(api_key=os.environ["GROQ_API_KEY"])
    image_data = base64.b64encode(path.read_bytes()).decode("ascii")
    mime = "image/jpeg" if path.suffix.lower() in {".jpg", ".jpeg"} else f"image/{path.suffix.lower().lstrip('.')}"
    prompt = (
        "Return only the final structural transcription of this 3GPP image. "
        "Preserve visible labels, numbers, message names, arrow directions, and sequence. "
        "If it is not a technical diagram, describe only its visible content. "
        "Do not include analysis, reasoning, <think> tags, or information not visible."
    )
    for attempt in range(5):
        wait_for_vision_slot()
        try:
            response = client.chat.completions.create(
                model=os.getenv("GROQ_VISION_MODEL", "qwen/qwen3.6-27b"),
                messages=[{"role": "user", "content": [{"type": "text", "text": prompt}, {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{image_data}"}}]}],
                temperature=0,
                max_completion_tokens=900,
                reasoning_effort="none",
                reasoning_format="hidden",
            )
            break
        except Exception as error:
            delay = retry_after_seconds(error)
            if delay is None or attempt == 4:
                raise
            print(f"  Groq TPM limit; retrying in {delay:.1f}s", flush=True)
            register_vision_cooldown(delay)
    else:  # Defensive: the loop either breaks or raises.
        raise RuntimeError("Groq Vision request exhausted retries")
    content = response.choices[0].message.content or ""
    return re.sub(r"<think>.*?</think>\\s*", "", content, flags=re.DOTALL).strip()


def extract_images(paragraph: Paragraph, document: Document, asset_dir: Path, prefix: str, image_offset: int, caption_images: bool) -> tuple[list[str], list[str], int]:
    asset_ids: list[str] = []
    captions: list[str] = []
    relationship_ids = []
    for element in paragraph._p.iter():
        if element.tag.endswith("}blip"):
            relationship_id = element.get("{http://schemas.openxmlformats.org/officeDocument/2006/relationships}embed")
        elif element.tag.endswith("}imagedata"):
            relationship_id = element.get("{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id")
        else:
            continue
        if relationship_id and relationship_id not in relationship_ids:
            relationship_ids.append(relationship_id)
    for index, relationship_id in enumerate(relationship_ids, start=1):
        if not relationship_id or relationship_id not in document.part.rels:
            continue
        relationship = document.part.rels[relationship_id]
        # A document can contain a linked, external image relationship. It
        # has no embedded bytes to extract and accessing target_part raises.
        if relationship.is_external:
            continue
        image = relationship.target_part
        extension = image.content_type.split("/")[-1].replace("jpeg", "jpg")
        asset_id = f"{prefix}__img_{image_offset + index:04d}"
        asset_dir.mkdir(parents=True, exist_ok=True)
        output = asset_dir / f"{asset_id}.{extension}"
        output.write_bytes(image.blob)
        asset_ids.append(asset_id)
        # Groq Vision accepts web raster images. WMF/EMF assets are retained,
        # but are not sent as unsupported image MIME types.
        if caption_images and output.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp", ".gif"}:
            try:
                captions.append(f"[FIGURE DESCRIPTION: {asset_id}]\n{caption_image(output)}")
                print(f"  Groq Vision saved: {asset_id}", flush=True)
            except Exception as exc:
                # Keep the document, raw asset, and figure marker even when a
                # remote caption request fails. The visible error can then be
                # diagnosed without silently dropping a whole specification.
                print(f"  Groq Vision FAILED: {asset_id}: {exc}", flush=True)
    return asset_ids, captions, image_offset + len(asset_ids)


def build_chunks(record: dict, max_tokens: int, caption_images: bool) -> tuple[list[Chunk], list[str]]:
    path = Path(record["converted_path"])
    document = Document(path)
    headings: list[tuple[int, str | None, str]] = []
    buffer: list[str] = []
    assets: list[str] = []
    has_table = False
    chunks: list[Chunk] = []
    warnings: list[str] = []
    block_start = 0
    chunk_index = 0
    image_count = 0
    asset_dir = ASSETS / record["document_id"]

    def flush(block_end: int) -> None:
        nonlocal buffer, assets, has_table, block_start, chunk_index
        if not buffer:
            return
        title_path = [title for _, _, title in headings]
        clause_path = [clause for _, clause, _ in headings if clause]
        prefix = " > ".join(title_path)
        text = f"Section path: {prefix}\n\n" + "\n\n".join(buffer)
        # Preserve Markdown table line breaks. Only split an oversized prose
        # block, never flatten a table into a single word stream.
        pieces, current, current_size = [], [], 0
        for block in text.split("\n\n"):
            block_size = len(block.split())
            if current and current_size + block_size > max_tokens:
                pieces.append("\n\n".join(current))
                current, current_size = [], 0
            if block_size > max_tokens and not block.startswith("[TABLE]"):
                words = block.split()
                pieces.extend(" ".join(words[offset:offset + max_tokens]) for offset in range(0, len(words), max_tokens))
            else:
                current.append(block)
                current_size += block_size
        if current:
            pieces.append("\n\n".join(current))
        for portion in pieces:
            chunk_index += 1
            content_type = "mixed" if assets and has_table else ("figure" if assets else ("table" if has_table else "text"))
            clause_id = clause_path[-1] if clause_path else None
            chunk_id = f"{record['document_id']}__clause-{clause_id or 'front'}__{chunk_index:04d}"
            chunks.append(
                Chunk(
                    chunk_id=chunk_id,
                    document_id=record["document_id"],
                    release=record["release"],
                    series=record["series"],
                    spec_id=record["spec_id"],
                    clause_id=clause_id,
                    chunk_index=chunk_index,
                    content_type=content_type,
                    asset_ids=list(assets),
                    text=portion,
                )
            )
        buffer, assets, has_table, block_start = [], [], False, block_end + 1

    for block_index, block in enumerate(ordered_blocks(document)):
        if isinstance(block, Paragraph):
            text = " ".join(block.text.split())
            level = heading_level(block)
            if level and text:
                flush(block_index - 1)
                clause, title = clause_and_title(text)
                headings[:] = [item for item in headings if item[0] < level]
                headings.append((level, clause, title))
                block_start = block_index + 1
                continue
            if text:
                buffer.append(text)
            asset_ids, captions, image_count = extract_images(block, document, asset_dir, f"{record['document_id']}__b{block_index}", image_count, caption_images)
            if asset_ids:
                assets.extend(asset_ids)
                buffer.append("[FIGURE: " + ", ".join(asset_ids) + "]")
                buffer.extend(captions)
        else:
            table_text = markdown_table(block)
            if table_text:
                has_table = True
                buffer.append("[TABLE]\n" + table_text)
            # Tables can contain inline diagrams; extract their original
            # assets and captions in the same document order.
            for row in block.rows:
                for cell in row.cells:
                    for paragraph in cell.paragraphs:
                        asset_ids, captions, image_count = extract_images(paragraph, document, asset_dir, f"{record['document_id']}__table{block_index}", image_count, caption_images)
                        if asset_ids:
                            assets.extend(asset_ids)
                            buffer.append("[FIGURE: " + ", ".join(asset_ids) + "]")
                            buffer.extend(captions)
    flush(block_index if "block_index" in locals() else 0)
    if not chunks:
        warnings.append("no_chunks_created")
    return chunks, warnings


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, default=CONVERSION_MANIFEST)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--max-tokens", type=int, default=250)
    parser.add_argument("--caption-images", action="store_true", help="Call Groq Vision for extracted figures")
    parser.add_argument("--progress-every", type=int, default=1, help="Print progress every N documents (default: 1)")
    parser.add_argument("--spec-id", help="Process one exact spec ID, e.g. 23.031")
    parser.add_argument("--series", help="Process one exact series, e.g. 38")
    parser.add_argument("--output", type=Path, default=CHUNKS_PATH, help="Output JSONL path")
    parser.add_argument("--report-output", type=Path, default=INGESTION_REPORT, help="Output report path")
    parser.add_argument("--workers", type=int, default=4, help="Documents processed concurrently (default: 4)")
    args = parser.parse_args()
    if args.caption_images and not os.getenv("GROQ_API_KEY"):
        parser.error("--caption-images requires GROQ_API_KEY in .env or the environment")
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    usable = [record for record in manifest["documents"] if record.get("conversion_status") in {"converted", "not_required"} and record.get("converted_path")]
    if args.spec_id:
        usable = [record for record in usable if record["spec_id"] == args.spec_id]
        if not usable:
            parser.error(f"Spec not found in conversion manifest: {args.spec_id}")
    if args.series:
        usable = [record for record in usable if str(record["series"]) == str(args.series)]
        if not usable:
            parser.error(f"Series not found in conversion manifest: {args.series}")
    if args.limit:
        usable = usable[:args.limit]
    if args.workers < 1:
        parser.error("--workers must be at least 1")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.report_output.parent.mkdir(parents=True, exist_ok=True)
    report = {"processed": 0, "failed": [], "warnings": []}
    with args.output.open("w", encoding="utf-8") as output:
        with ThreadPoolExecutor(max_workers=args.workers) as executor:
            futures = {executor.submit(build_chunks, record, args.max_tokens, args.caption_images): record for record in usable}
            for index, future in enumerate(as_completed(futures), start=1):
                record = futures[future]
                if index == 1 or index % args.progress_every == 0:
                    print(f"[{index}/{len(usable)}] {record['series']}_series  {record['source_filename']}", flush=True)
                try:
                    chunks, warnings = future.result()
                    for chunk in chunks:
                        output.write(json.dumps(chunk.to_dict(), ensure_ascii=False) + "\n")
                    report["processed"] += 1
                    report["warnings"].extend({"document_id": record["document_id"], "warning": warning} for warning in warnings)
                except Exception as exc:
                    report["failed"].append({"document_id": record["document_id"], "error": str(exc)})
                    print(f"  DOCUMENT FAILED: {record['document_id']}: {exc}", flush=True)
    report["chunk_count"] = sum(1 for _ in args.output.open(encoding="utf-8"))
    args.report_output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"Processed={report['processed']} chunks={report['chunk_count']} failed={len(report['failed'])}")


if __name__ == "__main__":
    main()
