"""
PDF processor using pymupdf4llm for reliable markdown conversion.
Handles all academic PDFs uniformly — no per-PDF custom code needed.
"""
import re
import logging
from pathlib import Path

from processors.base import BaseProcessor
from engine import TranslationEngine
from utils.text_utils import normalize_whitespace

logger = logging.getLogger(__name__)

STOP_HEADINGS = [
    "references", "acknowledgments", "acknowledgements",
    "conflict of interest", "supplementary",
    "appendix", "supporting information", "data availability",
]


class PdfProcessor(BaseProcessor):
    def __init__(self, html_path: str, output_dir: Path,
                 engine: TranslationEngine | None = None, concurrency: int = 1):
        super().__init__(html_path, output_dir, concurrency=concurrency)
        self.engine = engine if engine is not None else TranslationEngine(concurrency=concurrency)
        self._title = ""
        self._authors = ""
        self._journal_info = ""
        self._doi = ""

    def _extract_meta(self):
        try:
            import pymupdf4llm
            md = pymupdf4llm.to_markdown(self.html_path, pages=[0])
        except Exception:
            # Fallback: use fitz for metadata only
            import fitz
            doc = fitz.open(self.html_path)
            meta = doc.metadata or {}
            self._title = (meta.get("title") or Path(self.html_path).stem).strip()
            self._authors = (meta.get("author") or "").strip()
            doc.close()
            self._journal_info = "PDF Document"
            return

        # Extract title from first H1 in markdown
        for line in md.splitlines():
            if line.startswith("# "):
                # Strip markdown bold markers and leading #
                t = line.lstrip("# ").replace("**", "").strip()
                if t and len(t) > 5:
                    self._title = t
                    break
        if not self._title:
            self._title = Path(self.html_path).stem

        # Try to find author info from bold text after title
        lines = md.splitlines()
        for i, line in enumerate(lines):
            if line.startswith("# ") and self._title in line:
                for j in range(i+1, min(i+10, len(lines))):
                    nl = lines[j].strip()
                    if nl and not nl.startswith("#") and not nl.startswith(">"):
                        # Clean up markdown formatting
                        nl = re.sub(r'<sup>.*?</sup>', '', nl)
                        nl = nl.replace("**", "").replace("*", "").strip()
                        if nl and len(nl) > 20:
                            self._authors = nl[:300]
                            break
                break

        # Try DOI from first page
        doi_m = re.search(r'(?:doi|DOI)[:\s]*([^\s\n]+)', md[:5000])
        if doi_m:
            self._doi = doi_m.group(1).strip().rstrip('.')

        self._journal_info = "PDF Document"

    def extract_title(self) -> str:
        if not self._title:
            self._extract_meta()
        return self._title

    def _translate(self, text: str, context: str = "") -> str:
        result = self.engine.translate(text, context=context)
        result = result.replace("[¶SEP¶]", "").replace("[¶SEP¶", "").replace("¶SEP¶]", "")
        return result.strip()

    def extract_and_translate(self) -> str:
        self._extract_meta()

        md_parts: list[str] = []
        paper_parts: list[str] = []

        title_cn = self._translate(self._title)
        md_parts.append(f"# {title_cn}\n\n")
        paper_parts.append(f"# {self._title}\n\n")
        if self._authors:
            md_parts.append(f"**作者**: {self._authors}\n\n")
        if self._doi:
            md_parts.append(f"**DOI**: {self._doi}\n\n")
        md_parts.append("---\n\n")
        paper_parts.append("---\n\n")

        # Convert PDF to markdown with pymupdf4llm
        try:
            import pymupdf4llm
            full_md = pymupdf4llm.to_markdown(self.html_path)
        except Exception:
            logger.error("pymupdf4llm conversion failed")
            paper_parts.append("*(PDF extraction failed)*\n")
            self._write_paper("".join(paper_parts))
            self.engine.shutdown()
            return "".join(md_parts)

        # Parse markdown into sections
        sections = self._parse_markdown_sections(full_md)

        # Fallback: if no sections found, split entire text into paragraphs
        if not sections:
            paragraphs = [p for p in full_md.split('\n\n') if p.strip() and not p.strip().startswith('#')]
            # Filter out very short/garbled lines
            paragraphs = [p.strip() for p in paragraphs if len(p.strip()) > 30]
            sections = [(2, "Content", paragraphs)]

        # Translate section by section
        for heading_level, heading_text, paragraphs in sections:
            if not paragraphs:
                continue

            self._current_heading = heading_text
            translated_heading = self._translate(heading_text)

            if heading_level >= 2:
                prefix = "#" * heading_level
                md_parts.append(f"{prefix} {translated_heading}\n\n")
                paper_parts.append(f"{prefix} {heading_text}\n\n")

            # Batch translate paragraphs
            raw_batch = list(paragraphs)
            translated = self.engine.translate_paragraphs(
                paragraphs, context=f"{self._title} - {self._current_heading}"
            )
            for i, tp in enumerate(translated):
                tp = tp.replace("[¶SEP¶]", "").strip()
                if tp:
                    md_parts.append(f"{tp}\n\n")
                    if i < len(raw_batch):
                        paper_parts.append(f"{raw_batch[i]}\n\n")

        self._write_paper("".join(paper_parts))
        self.engine.shutdown()
        return "".join(md_parts)

    def _parse_markdown_sections(self, md_text: str) -> list[tuple[int, str, list[str]]]:
        """Parse pymupdf4llm markdown into heading + paragraph sections."""
        lines = md_text.splitlines()
        sections: list[tuple[int, str, list[str]]] = []
        current_level = 0
        current_heading = ""
        current_paras: list[str] = []
        current_para_lines: list[str] = []
        stopped = False

        def _commit_section():
            nonlocal current_para_lines, current_paras
            if current_para_lines:
                txt = "\n".join(current_para_lines).strip()
                if txt:
                    current_paras.append(txt)
                current_para_lines = []
            if current_paras:
                sections.append((current_level, current_heading, current_paras))
                current_paras = []

        for line in lines:
            stripped = line.strip()

            # Check if this is a heading line
            heading_match = re.match(r'^(#{2,5})\s+(.+)', stripped)
            if heading_match:
                level = len(heading_match.group(1))
                heading = heading_match.group(2)

                # Clean up markdown formatting in heading
                heading = heading.replace("**", "").strip()

                if not heading:
                    continue

                heading_lower = heading.lower()
                if any(stop in heading_lower for stop in STOP_HEADINGS):
                    stopped = True
                    _commit_section()
                    break

                _commit_section()
                current_level = level
                current_heading = heading
                current_paras = []
                current_para_lines = []
                continue

            if stopped:
                continue

            # Skip metadata/navigation lines
            if not stripped:
                # Blank line = paragraph separator
                if current_para_lines:
                    txt = "\n".join(current_para_lines).strip()
                    if txt:
                        current_paras.append(txt)
                    current_para_lines = []
                continue

            # Skip image/table references
            if stripped.startswith("!["):
                continue

            # Collect paragraph lines
            current_para_lines.append(stripped)

        _commit_section()
        return sections

    def _write_paper(self, paper_text: str):
        raw_path = self.output_dir / "paper.md"
        with open(raw_path, "w", encoding="utf-8") as f:
            f.write(paper_text)
