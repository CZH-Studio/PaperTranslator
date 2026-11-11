"""
Current Biology / Cell Press processor.

Handles: cell.com, current-biology.com, and other Cell Press journals.
Key gotcha: paragraphs use `div[role="paragraph"]` — NOT `<p>` tags.
"""
import logging
from pathlib import Path

from bs4 import BeautifulSoup, Tag
from bs4.element import NavigableString

from processors.base import BaseProcessor
from engine import TranslationEngine
from utils.text_utils import clean_html_entities, normalize_whitespace

logger = logging.getLogger(__name__)

STOP_HEADINGS = [
    "references",
    "acknowledgments",
    "acknowledgements",
    "supplementary material",
    "author contributions",
    "declaration of interests",
    "data and software availability",
    "data availability",
    "resource availability",
    "key resources table",
]

# Section IDs to skip inside bodymatter (back matter)
SKIP_SECTION_IDS = {"acknowledgments", "supplementary-material"}


class CellProcessor(BaseProcessor):
    def __init__(self, html_path: str, output_dir: Path,
                 engine: TranslationEngine | None = None, concurrency: int = 1):
        super().__init__(html_path, output_dir, concurrency=concurrency)
        self.engine = engine if engine is not None else TranslationEngine(concurrency=concurrency)
        with open(html_path, "r", encoding="utf-8", errors="ignore") as f:
            self.soup = BeautifulSoup(f.read(), "lxml")
        self._title = ""
        self._authors = ""
        self._journal_info = ""
        self._doi = ""
        self._placeholders: dict[str, str] = {}

    # ── Helpers ──

    def _make_placeholder(self, prefix: str, content: str) -> str:
        idx = len(self._placeholders)
        key = f"[{prefix}_{idx}]"
        self._placeholders[key] = content
        return key

    def _translate(self, text: str, context: str = "") -> str:
        result = self.engine.translate(text, context=context)
        result = result.replace("[¶SEP¶]", "")
        result = result.replace("[¶SEP¶", "")
        result = result.replace("¶SEP¶]", "")
        return result.strip()

    # ── Metadata ──

    def _extract_meta(self):
        title_meta = self.soup.select_one('meta[name="citation_title"]')
        if title_meta:
            self._title = str(title_meta.get("content", "")).strip()
        if not self._title:
            h1 = self.soup.find("h1")
            if h1:
                self._title = clean_html_entities(h1.get_text(strip=True))

        author_metas = self.soup.select('meta[name="citation_author"]')
        authors = []
        seen: set[str] = set()
        for a in author_metas:
            content = str(a.get("content", "")).strip()
            if content:
                parts = [p.strip() for p in content.split(",", 1)]
                if len(parts) == 2:
                    name = f"{parts[1]} {parts[0]}"
                else:
                    name = content
                if name and name not in seen:
                    seen.add(name)
                    authors.append(name)
        self._authors = ", ".join(authors)

        journal_meta = self.soup.select_one('meta[name="citation_journal_title"]')
        journal_title = str(journal_meta.get("content", "")).strip() if journal_meta else ""

        date_meta = self.soup.select_one('meta[name="citation_publication_date"]')
        date = str(date_meta.get("content", "")).strip() if date_meta else ""

        volume_meta = self.soup.select_one('meta[name="citation_volume"]')
        volume = str(volume_meta.get("content", "")).strip() if volume_meta else ""

        first_page = ""
        fp_meta = self.soup.select_one('meta[name="citation_firstpage"]')
        if fp_meta:
            first_page = str(fp_meta.get("content", "")).strip()

        parts = [journal_title]
        if volume:
            prefix = f"{journal_title} vol. {volume}" if journal_title else f"Vol. {volume}"
            parts[0] = prefix
        if date:
            parts.append(date)
        if first_page:
            parts.append(first_page)

        self._journal_info = ". ".join(parts) if parts else "Cell Press"

        doi_meta = self.soup.select_one('meta[name="citation_doi"]')
        if doi_meta:
            self._doi = str(doi_meta.get("content", "")).strip()

    def extract_title(self) -> str:
        if not self._title:
            self._extract_meta()
        return self._title

    # ── Main pipeline ──

    def extract_and_translate(self) -> str:
        self._extract_meta()
        self._placeholders = {}

        md_parts: list[str] = []
        paper_parts: list[str] = []

        # Title block
        title_cn = self._translate(self._title)
        md_parts.append(f"# {title_cn}\n\n")
        paper_parts.append(f"# {self._title}\n\n")
        md_parts.append(f"**{self._title}**\n\n")
        if self._authors:
            md_parts.append(f"**作者**: {self._authors}\n\n")
        md_parts.append(f"**{self._journal_info}**\n\n")
        if self._doi:
            md_parts.append(f"**DOI**: {self._doi}\n\n")
        md_parts.append("---\n\n")
        paper_parts.append("---\n\n")

        paragraphs: list[str] = []
        plain_texts: list[str] = []

        def flush():
            nonlocal paragraphs, plain_texts
            if not paragraphs:
                return
            raw_batch = list(paragraphs)
            translated = self.engine.translate_paragraphs(
                paragraphs, context=f"{self._title} - {self._current_heading}"
            )
            t_idx = 0
            for pt in plain_texts:
                if pt.startswith("<p>"):
                    if t_idx < len(translated):
                        md_parts.append(f"{translated[t_idx]}\n\n")
                        if t_idx < len(raw_batch):
                            paper_parts.append(f"{raw_batch[t_idx]}\n\n")
                        t_idx += 1
                elif pt.startswith("<figure>"):
                    fig = pt[8:-9]
                    md_parts.append(f"{fig}\n\n")
                    paper_parts.append(f"{fig}\n\n")
                elif pt.startswith("<center>"):
                    md_parts.append(f"{pt}\n\n")
                    paper_parts.append(f"{pt}\n\n")
            paragraphs.clear()
            plain_texts.clear()

        # ── Abstract ("Summary") ──
        self._extract_abstract(md_parts, paper_parts)

        # ── Highlights (optional) ──
        self._extract_highlights(md_parts, paper_parts)

        # ── Body sections ──
        bodymatter = self.soup.select_one("section#bodymatter")
        if bodymatter:
            core_container = bodymatter.select_one("div.core-container")
            container = core_container if core_container else bodymatter
            self._process_body_sections(container, md_parts, paper_parts,
                                        paragraphs, plain_texts, flush)
        else:
            logger.warning("Cannot find section#bodymatter — body content will be empty")

        flush()

        md_text = "".join(md_parts)
        paper_text = "".join(paper_parts)
        for key, value in self._placeholders.items():
            md_text = md_text.replace(key, value)
            paper_text = paper_text.replace(key, value)

        raw_path = self.output_dir / "paper.md"
        with open(raw_path, "w", encoding="utf-8") as f:
            f.write(paper_text)

        self.engine.shutdown()
        return md_text

    # ── Abstract ──

    def _extract_abstract(self, md_parts: list[str], paper_parts: list[str]):
        abstract_section = self.soup.select_one("section#author-abstract")
        if not abstract_section:
            abstract_section = self.soup.select_one("section#abstract")
        if not abstract_section:
            logger.warning("Cannot find abstract section")
            return

        para_divs = abstract_section.select('div[role="paragraph"]')
        abstract_texts: list[str] = []
        for div in para_divs:
            self._clean_inline_citations(div)
            txt = clean_html_entities(div.get_text(" ", strip=True))
            txt = normalize_whitespace(txt)
            if txt:
                abstract_texts.append(txt)

        if not abstract_texts:
            return

        abstract_text = " ".join(abstract_texts)
        translated = self._translate(abstract_text, context="摘要")
        heading = "## 摘要"
        heading_h2 = abstract_section.find("h2")
        abs_title = clean_html_entities(heading_h2.get_text(strip=True)) if heading_h2 else "Abstract"
        paper_heading = f"## {abs_title}"
        md_parts.append(f"{heading}\n\n{translated}\n\n")
        paper_parts.append(f"{paper_heading}\n\n{abstract_text}\n\n")

    # ── Highlights ──

    def _extract_highlights(self, md_parts: list[str], paper_parts: list[str]):
        highlights_section = self.soup.select_one("section#author-highlights-abstract")
        if not highlights_section:
            return

        items: list[str] = []
        for li in highlights_section.select("li"):
            txt = clean_html_entities(li.get_text(" ", strip=True))
            txt = normalize_whitespace(txt)
            if txt:
                items.append(txt)

        if not items:
            return

        translated = self.engine.translate_paragraphs(
            items, context=f"{self._title} - Highlights"
        )
        md_parts.append("## 亮点\n\n")
        paper_parts.append("## Highlights\n\n")
        for i, tp in enumerate(translated):
            tp = tp.replace("[¶SEP¶]", "").strip()
            md_parts.append(f"- {tp}\n")
            if i < len(items):
                paper_parts.append(f"- {items[i]}\n")
        md_parts.append("\n")
        paper_parts.append("\n")

    # ── Body sections ──

    def _process_body_sections(self, container: Tag, md_parts: list[str],
                                paper_parts: list[str], paragraphs: list[str],
                                plain_texts: list[str], flush):
        """Process direct section children of the bodymatter container."""
        for child in container.children:
            if isinstance(child, NavigableString):
                continue
            if not isinstance(child, Tag):
                continue
            if child.name != "section":
                continue

            section_id = str(child.get("id", ""))
            # Skip back-matter sections
            if section_id in SKIP_SECTION_IDS:
                break
            # Skip sections inside back matter (sec-4+, sec-5+)
            if section_id.startswith("sec-"):
                try:
                    sec_num = int(section_id.split("-")[1])
                    if sec_num >= 4:
                        break
                except (ValueError, IndexError):
                    pass

            self._process_section(child, md_parts, paper_parts,
                                  paragraphs, plain_texts, flush, level=2)

    def _process_section(self, section: Tag, md_parts: list[str],
                         paper_parts: list[str], paragraphs: list[str],
                         plain_texts: list[str], flush, level: int = 2):
        """Recursively process a section and its subsections."""
        section_id = section.get("id", "")
        # Stop at back-matter sections when reached recursively
        if section_id in SKIP_SECTION_IDS:
            return

        # Find the heading (h2 for top-level, h3/h4 for subsections)
        heading_el = section.find(["h2", "h3", "h4"], recursive=False)
        heading_text = ""
        if heading_el:
            heading_text = clean_html_entities(heading_el.get_text(strip=True))
            heading_text = normalize_whitespace(heading_text)
            heading_lower = heading_text.lower()

            if any(stop in heading_lower for stop in STOP_HEADINGS):
                return

            flush()
            self._current_heading = heading_text
            prefix = "#" * level
            translated = self._translate(heading_text)
            md_parts.append(f"{prefix} {translated}\n\n")
            paper_parts.append(f"{prefix} {heading_text}\n\n")

        # Process children in order: paragraphs, figures, subsections
        for child in section.children:
            if isinstance(child, NavigableString):
                continue
            if not isinstance(child, Tag):
                continue

            if child.name == "div" and child.get("role") == "paragraph":
                self._process_cell_paragraph(child, paragraphs, plain_texts)

            elif child.name == "div" and "figure-wrap" in (child.get("class") or []):
                flush()
                fig_html = self._process_figure(child)
                if fig_html:
                    plain_texts.append(fig_html)

            elif child.name == "section":
                # Nested subsection
                self._process_section(child, md_parts, paper_parts,
                                      paragraphs, plain_texts, flush, level=level + 1)

    # ── Paragraph ──

    def _process_cell_paragraph(self, div: Tag, paragraphs: list[str],
                                 plain_texts: list[str]):
        """Process a div[role='paragraph']. Cleans inline citations first."""
        self._clean_inline_citations(div)
        text = clean_html_entities(div.get_text(" ", strip=True))
        text = normalize_whitespace(text)
        if text:
            plain_texts.append(f"<p>{text}</p>")
            paragraphs.append(text)

    # ── Citation cleaning ──

    def _clean_inline_citations(self, element: Tag):
        """Replace span.dropBlock.reference-citations with bracketed citation text."""
        for span in element.select("span.dropBlock.reference-citations"):
            a_tags = span.find_all("a")
            citation_parts: list[str] = []
            for a in a_tags:
                a_text = a.get_text(strip=True)
                if a_text:
                    citation_parts.append(a_text)

            if citation_parts:
                replacement = "[" + ", ".join(citation_parts) + "]"
            else:
                replacement = ""

            span.replace_with(replacement)

    # ── Figure ──

    def _process_figure(self, fig_div: Tag) -> str | None:
        """Process a div.figure-wrap into a <figure> placeholder."""
        img_tag = fig_div.select_one("img")
        img_url = ""
        if img_tag:
            src = img_tag.get("src", "")
            if isinstance(src, str) and not src.startswith("data:"):
                img_url = src

        caption_el = fig_div.select_one("figcaption")
        caption_text = ""
        if caption_el:
            caption_text = clean_html_entities(caption_el.get_text(" ", strip=True))
            caption_text = normalize_whitespace(caption_text)

        if not img_url and not caption_text:
            return None

        translated_caption = self._translate(caption_text) if caption_text else ""
        parts: list[str] = []
        if img_url:
            alt_text = translated_caption or (caption_text[:80] if caption_text else "Figure")
            parts.append(f"![{alt_text}]({img_url})")
        if translated_caption:
            parts.append(f"<center>{translated_caption}</center>")

        if not parts:
            return None
        return "<figure>" + "\n".join(parts) + "</figure>"
