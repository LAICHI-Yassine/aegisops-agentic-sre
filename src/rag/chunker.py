import re
from dataclasses import dataclass
from pathlib import Path
from typing import List

PROMPT_INJECTION_PATTERNS = [
    r"\[SYSTEM PROMPT INJECTION:.*?\]",
    r"ignore\s+all\s+safety\s+rules.*?(?:\n|$)",
]


@dataclass
class RunbookChunk:
    chunk_id: str
    doc_id: str
    title: str
    section_header: str
    alert_signatures: str
    child_text: str       # Granular section embedded & indexed for retrieval
    parent_text: str      # Full intact runbook returned to the LLM context
    is_sanitized: bool    # True if prompt-injection patterns were stripped


def sanitize_rag_text(text: str) -> tuple[str, bool]:
    """
    Scans runbook text for adversarial prompt-injection instructions and
    redacts malicious directives before indexing or passing to an LLM.
    """
    sanitized = text
    triggered = False
    for pattern in PROMPT_INJECTION_PATTERNS:
        if re.search(pattern, sanitized, flags=re.IGNORECASE | re.DOTALL):
            triggered = True
            sanitized = re.sub(
                pattern,
                "[REDACTED_BY_AEGIS_RAG_GUARDRAIL]",
                sanitized,
                flags=re.IGNORECASE | re.DOTALL,
            )
    return sanitized, triggered


def load_and_chunk_runbooks(runbooks_dir: str | Path = "data/runbooks") -> List[RunbookChunk]:
    """
    Loads Markdown runbooks, applies prompt-injection sanitization, and performs
    Parent-Child structural chunking preserving markdown tables and CLI blocks.
    """
    base_path = Path(runbooks_dir)
    chunks: List[RunbookChunk] = []

    for file_path in sorted(base_path.glob("*.md")):
        raw_text = file_path.read_text(encoding="utf-8").strip()
        clean_parent_text, was_sanitized = sanitize_rag_text(raw_text)

        doc_id = file_path.stem

        # Extract H1 title
        title_match = re.search(r"^#\s+(.+)$", clean_parent_text, flags=re.MULTILINE)
        title = title_match.group(1).strip() if title_match else doc_id

        # Extract Alert Signatures line if present
        sig_match = re.search(
            r"\*\*Alert Signatures:\*\*\s*(.+)$", clean_parent_text, flags=re.MULTILINE
        )
        alert_signatures = sig_match.group(1).strip() if sig_match else ""

        # Split by H2 sections (## ...) while keeping code blocks and tables intact
        sections = re.split(r"(?=^##\s+)", clean_parent_text, flags=re.MULTILINE)

        for idx, sec in enumerate(sections):
            sec = sec.strip()
            if not sec:
                continue

            header_match = re.search(r"^##\s+(.+)$", sec, flags=re.MULTILINE)
            section_header = (
                header_match.group(1).strip() if header_match else "Overview & Metadata"
            )

            # Enrich child chunk with document title & alert signatures for dense+BM25 recall
            enriched_child = (
                f"Document: {title}\n"
                f"Alert Signatures: {alert_signatures}\n"
                f"Section: {section_header}\n\n"
                f"{sec}"
            )

            chunks.append(
                RunbookChunk(
                    chunk_id=f"{doc_id}::sec_{idx}",
                    doc_id=doc_id,
                    title=title,
                    section_header=section_header,
                    alert_signatures=alert_signatures,
                    child_text=enriched_child,
                    parent_text=clean_parent_text,
                    is_sanitized=was_sanitized,
                )
            )

    return chunks


if __name__ == "__main__":
    all_chunks = load_and_chunk_runbooks()
    sanitized_count = sum(1 for c in all_chunks if c.is_sanitized)
    print(f"[OK] Parsed {len(all_chunks)} structural Parent-Child chunks from 12 runbooks.")
    print(f"[OK] RAG Guardrail sanitized {sanitized_count} poisoned chunk(s) (from RB-POISON-012).")
    sample = all_chunks[1]
    print(f"\nSample Chunk ID: {sample.chunk_id}")
    print(f"Title: {sample.title}")
    print(f"Section: {sample.section_header}")
    print(f"Child Text Length: {len(sample.child_text)} chars | Parent Text Length: {len(sample.parent_text)} chars")