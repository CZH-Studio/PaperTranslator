"""MIT Press / Silverchair processor.

Silverchair is the platform powering MIT Press journals (e.g. JOCN, NECO,
Computational Linguistics, etc.). Two HTML variants exist:

- Abstract-only (paywalled): ``.widget-ArticleFulltext_Abstract``
- Full-text (open access):    ``.widget-ArticleFulltext``

Full-text body children are **flat siblings** — not nested sections.
Pattern:  h2 → div(s), h3 → div(s), h4 → div(s), etc.
"""

import logging
from pathlib import Path

from bs4 import BeautifulSoup, Tag
from bs4.element import NavigableString

from engine import TranslationEngine
from processors.base import BaseProcessor
from utils.text_utils import clean_html_entities, normalize_whitespace

logger = logging.getLogger(__name__)

# ── stop headings (lower-cased) ────────────────────────────────────────────
STOP_HEADINGS: list[str] = [
    "references",
    "acknowledgment",
    "acknowledgements",
    "acknowledgments",
    "data and code availability",
    "author contributions",
    "declaration of competing interest",
    "supplementary materials",
    "funding",
    "notes",
    "footnotes",
    "author notes",
    "conflict of interest",
]

# CSS classes on <h2> that signal back-matter / stop point
BACK_SECTION_CLASSES: set[str] = {
    "backsection-title",
    "backacknowledgements-title",
    "backreferences-title",
    "authorNotes-section-title",
}

# heading levels that signal a new section when encountered as flat siblings
SECTION_HEADING_TAGS: set[str] = {"h2", "h3", "h4", "h5"}


# ── processor ───────────────────────────────────────────────────────────────
class MitPressProcessor(BaseProcessor):
    """Extract + translate MIT Press (Silverchair) articles."""

    def __init__(
        self,
        html_path: str,
        output_dir: Path,
        engine: TranslationEngine | None = None,
        concurrency: int = 1,
    ) -> None:
        super().__init__(html_path, output_dir, concurrency=concurrency)
        self.engine = engine if engine is not None else TranslationEngine(concurrency=concurrency)
        with open(html_path, encoding="utf-8", errors="ignore") as f:
            self.soup = BeautifulSoup(f.read(), "lxml")
        self._title: str = ""
        self._authors: str = ""
        self._journal_info: str = ""
        self._doi: str = ""
        self._placeholders: dict[str, str] = {}

    # ── helpers ─────────────────────────────────────────────────────────

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

    # ── metadata extraction ─────────────────────────────────────────────

    def _extract_meta(self) -> None:
        """Pull citation metadata from <meta> tags."""
        title_meta = self.soup.select_one("meta[name='citation_title']")
        if title_meta:
            self._title = str(title_meta.get("content", "")).strip()
        if not self._title:
            h1 = self.soup.select_one("h1")
            if h1:
                self._title = clean_html_entities(h1.get_text(strip=True))

        author_metas = self.soup.select("meta[name='citation_author']")
        authors: list[str] = []
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

        journal_title = ""
        journal_meta = self.soup.select_one("meta[name='citation_journal_title']")
        if journal_meta:
            journal_title = str(journal_meta.get("content", "")).strip()

        date_meta = self.soup.select_one("meta[name='citation_publication_date']")
        date = str(date_meta.get("content", "")).strip() if date_meta else ""

        volume_meta = self.soup.select_one("meta[name='citation_volume']")
        volume = str(volume_meta.get("content", "")).strip() if volume_meta else ""

        parts = [journal_title]
        if volume and journal_title:
            parts[-1] = f"{journal_title} vol. {volume}"
        elif volume:
            parts[-1] = f"Vol. {volume}"
        if date:
            parts.append(date)

        self._journal_info = ". ".join(parts) if parts else "MIT Press"

        doi_meta = self.soup.select_one("meta[name='citation_doi']")
        if doi_meta:
            self._doi = str(doi_meta.get("content", "")).strip()

    def extract_title(self) -> str:
        if not self._title:
            self._extract_meta()
        return self._title

    # ── main extraction + translation ───────────────────────────────────

    def extract_and_translate(self) -> str:
        self._extract_meta()
        self._placeholders = {}

        md_parts: list[str] = []
        paper_parts: list[str] = []

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

        # ── abstract ────────────────────────────────────────────────────
        abstract_section = self.soup.select_one("section.abstract")
        if abstract_section:
            paras = abstract_section.select("p")
            texts: list[str] = []
            for p in paras:
                txt = clean_html_entities(p.get_text(" ", strip=True))
                txt = normalize_whitespace(txt)
                if txt:
                    texts.append(txt)
            if texts:
                abstract_text = " ".join(texts)
                translated = self._translate(abstract_text, context="摘要")
                md_parts.append(f"## 摘要\n\n{translated}\n\n")
                paper_parts.append(f"## Abstract\n\n{abstract_text}\n\n")

        # ── variant detection ───────────────────────────────────────────
        fulltext_widget = self.soup.select_one(".widget-ArticleFulltext")
        if fulltext_widget is None:
            # abstract-only variant — nothing more to do
            self._write_paper("".join(paper_parts))
            return "".join(md_parts)

        # Guard: detect abstract-only variant explicitly
        abstract_widget = self.soup.select_one(
            ".widget-ArticleFulltext_Abstract"
        )
        if abstract_widget is not None and fulltext_widget is None:
            self._write_paper("".join(paper_parts))
            return "".join(md_parts)

        widget_items = fulltext_widget.select_one(".widget-items")
        if widget_items is None:
            logger.warning("Full-text widget found but no .widget-items container")
            self._write_paper("".join(paper_parts))
            return "".join(md_parts)

        # ── body processing (flat siblings) ─────────────────────────────
        paragraphs: list[str] = []
        plain_texts: list[str] = []

        def flush() -> None:
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
                elif pt.startswith("<equation>"):
                    eq = pt[len("<equation>"):-len("</equation>")]
                    eq_block = f"$$\n{eq}\n$$\n\n"
                    md_parts.append(eq_block)
                    paper_parts.append(eq_block)
                elif pt.startswith("<figure>"):
                    fig = pt[len("<figure>"):-len("</figure>")]
                    md_parts.append(f"{fig}\n\n")
                    paper_parts.append(f"{fig}\n\n")
                elif pt.startswith("<table>"):
                    tbl = pt[len("<table>"):-len("</table>")]
                    md_parts.append(f"{tbl}\n\n")
                    paper_parts.append(f"{tbl}\n\n")
                elif pt.startswith("<center>"):
                    md_parts.append(f"{pt}\n\n")
                    paper_parts.append(f"{pt}\n\n")
            paragraphs.clear()
            plain_texts.clear()

        for child in widget_items.children:
            if isinstance(child, NavigableString):
                continue
            if not isinstance(child, Tag):
                continue

            # ── figure blocks ───────────────────────────────────────────
            if child.name == "div" and "fig-section" in (child.get("class") or []):
                flush()
                fig_html = self._process_figure_section(child)
                if fig_html:
                    plain_texts.append(f"<figure>{fig_html}</figure>")
                continue

            # ── table blocks ────────────────────────────────────────────
            if child.name == "div" and "table-wrap" in (child.get("class") or []):
                flush()
                tbl_html = self._process_table_wrap(child)
                if tbl_html:
                    plain_texts.append(f"<table>{tbl_html}</table>")
                continue

            # ── equation blocks ─────────────────────────────────────────
            if child.name == "div" and "block-child-p" in (child.get("class") or []):
                eq_text = child.get_text(" ", strip=True)
                eq_text = normalize_whitespace(eq_text)
                if eq_text:
                    plain_texts.append(f"<equation>{eq_text}</equation>")
                continue

            # ── headings (h2/h3/h4/h5) ──────────────────────────────────
            if child.name in SECTION_HEADING_TAGS:
                heading_text = clean_html_entities(child.get_text(strip=True))
                heading_text = normalize_whitespace(heading_text)
                if not heading_text:
                    continue

                heading_lower = heading_text.lower()

                # check stop by CSS class
                child_classes = set(child.get("class") or [])
                if child_classes & BACK_SECTION_CLASSES:
                    break

                # check stop by heading text
                if any(stop in heading_lower for stop in STOP_HEADINGS):
                    break

                # map h2→##, h3→###, h4→####, h5→#####
                level = int(child.name[1])
                prefix = "#" * level

                # Skip abstract heading if already handled
                if level == 2 and heading_lower == "abstract":
                    continue

                flush()
                self._current_heading = heading_text
                translated_heading = self._translate(heading_text)
                md_parts.append(f"{prefix} {translated_heading}\n\n")
                paper_parts.append(f"{prefix} {heading_text}\n\n")
                continue

            # ── content divs (following headings) ───────────────────────
            if child.name == "div":
                self._extract_content_div(child, paragraphs, plain_texts)

        flush()

        md_text = "".join(md_parts)
        paper_text = "".join(paper_parts)
        for key, value in self._placeholders.items():
            md_text = md_text.replace(key, value)
            paper_text = paper_text.replace(key, value)

        self._write_paper(paper_text)
        self.engine.shutdown()
        return md_text

    # ── internal helpers ────────────────────────────────────────────────

    def _write_paper(self, paper_text: str) -> None:
        raw_path = self.output_dir / "paper.md"
        with open(raw_path, "w", encoding="utf-8") as f:
            f.write(paper_text)

    def _extract_content_div(
        self,
        tag: Tag,
        paragraphs: list[str],
        plain_texts: list[str],
    ) -> None:
        """Extract paragraphs from a content <div> following a heading.

        Content is inside ``.article-section-wrapper > p``.
        """
        wrapper = tag.select_one(".article-section-wrapper")
        if wrapper is None:
            # fallback: direct <p> children
            for p in tag.find_all("p", recursive=False):
                self._process_paragraph(p, paragraphs, plain_texts)
            return

        for p in wrapper.select("p"):
            self._process_paragraph(p, paragraphs, plain_texts)

    def _process_paragraph(
        self,
        p: Tag,
        paragraphs: list[str],
        plain_texts: list[str],
    ) -> None:
        text = clean_html_entities(p.get_text(" ", strip=True))
        text = normalize_whitespace(text)
        if text:
            plain_texts.append(f"<p>{text}</p>")
            paragraphs.append(text)

    def _process_figure_section(self, fig_div: Tag) -> str:
        """Extract figure caption from ``div.fig.fig-section``."""
        caption_tag = fig_div.select_one(".fig-caption")
        caption_text = ""
        if caption_tag:
            caption_text = clean_html_entities(caption_tag.get_text(" ", strip=True))
            caption_text = normalize_whitespace(caption_text)

        img_tag = fig_div.select_one("img")
        img_url = ""
        if img_tag:
            src = img_tag.get("src", "")
            if isinstance(src, str) and not src.startswith("data:"):
                img_url = src

        parts: list[str] = []
        if caption_text:
            translated_caption = self._translate(caption_text)
        else:
            translated_caption = ""

        if img_url:
            parts.append(f"![{translated_caption or 'Figure'}]({img_url})")
        if translated_caption:
            parts.append(f"<center>{translated_caption}</center>")

        if not parts:
            return ""
        return "\n".join(parts)

    def _process_table_wrap(self, tbl_div: Tag) -> str:
        """Extract table content from ``div.table-wrap``."""
        caption_text = ""
        caption_tag = tbl_div.select_one(".table-caption")
        if caption_tag:
            caption_text = clean_html_entities(caption_tag.get_text(" ", strip=True))
            caption_text = normalize_whitespace(caption_text)

        table_elem = tbl_div.select_one("table")
        if table_elem is None:
            return ""

        parts: list[str] = []
        if caption_text:
            translated_caption = self._translate(caption_text)
            parts.append(f"<center>{translated_caption}</center>")

        parts.append(str(table_elem))
        return "\n".join(parts)
