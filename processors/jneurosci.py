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
    "supplementary",
    "supplementary material",
    "author contributions",
    "conflict of interest",
    "footnotes",
    "data availability",
    "funding",
]


class JNeurosciProcessor(BaseProcessor):
    """Processor for Journal of Neuroscience (jneurosci.org) — HighWire Press platform.

    Handles both full articles and abstract-only partial saves.
    """

    def __init__(
        self,
        html_path: str,
        output_dir: Path,
        engine: TranslationEngine | None = None,
        concurrency: int = 1,
    ):
        super().__init__(html_path, output_dir, concurrency=concurrency)
        self.engine = engine if engine is not None else TranslationEngine(concurrency=concurrency)
        with open(html_path, "r", encoding="utf-8", errors="ignore") as f:
            self.soup = BeautifulSoup(f.read(), "lxml")
        self._title = ""
        self._authors = ""
        self._journal_info = ""
        self._doi = ""
        self._placeholders: dict[str, str] = {}

    def _make_placeholder(self, prefix: str, content: str) -> str:
        idx = len(self._placeholders)
        key = f"[{prefix}_{idx}]"
        self._placeholders[key] = content
        return key

    def _extract_meta(self) -> None:
        """Extract title, authors, DOI, and journal info from HighWire Press meta tags."""
        # Title
        title_meta = self.soup.select_one("meta[name='citation_title']")
        if title_meta:
            self._title = str(title_meta.get("content", "")).strip()
        if not self._title:
            h1 = self.soup.select_one("h1.highwire-cite-title")
            if h1:
                self._title = clean_html_entities(h1.get_text(strip=True))

        # Authors: multiple meta[name='citation_author'] tags
        author_metas = self.soup.select("meta[name='citation_author']")
        authors: list[str] = []
        seen: set[str] = set()
        for a in author_metas:
            content = str(a.get("content", "")).strip()
            if not content:
                continue
            # HighWire Press format: "LastName, FirstName"
            parts = [p.strip() for p in content.split(",", 1)]
            if len(parts) == 2:
                name = f"{parts[1]} {parts[0]}"
            else:
                name = content
            if name and name not in seen:
                seen.add(name)
                authors.append(name)
        self._authors = ", ".join(authors)

        # Journal info: journal_title + volume + issue + date + pages
        journal_title = ""
        journal_meta = self.soup.select_one("meta[name='citation_journal_title']")
        if journal_meta:
            journal_title = str(journal_meta.get("content", "")).strip()

        date = ""
        date_meta = self.soup.select_one("meta[name='citation_publication_date']")
        if date_meta:
            date = str(date_meta.get("content", "")).strip()

        volume = ""
        volume_meta = self.soup.select_one("meta[name='citation_volume']")
        if volume_meta:
            volume = str(volume_meta.get("content", "")).strip()

        issue = ""
        issue_meta = self.soup.select_one("meta[name='citation_issue']")
        if issue_meta:
            issue = str(issue_meta.get("content", "")).strip()

        first_page = ""
        fp_meta = self.soup.select_one("meta[name='citation_firstpage']")
        if fp_meta:
            first_page = str(fp_meta.get("content", "")).strip()

        info_parts: list[str] = [journal_title]
        if volume:
            info_parts[-1] = (
                f"{journal_title} vol. {volume}" if journal_title else f"Vol. {volume}"
            )
        if issue:
            info_parts[-1] = f"{info_parts[-1]}, no. {issue}"
        if date:
            info_parts.append(date)
        if first_page:
            info_parts.append(f"p. {first_page}")

        self._journal_info = ". ".join(info_parts) if info_parts else "Journal of Neuroscience"

        # DOI
        doi_meta = self.soup.select_one("meta[name='citation_doi']")
        if doi_meta:
            self._doi = str(doi_meta.get("content", "")).strip()

    def extract_title(self) -> str:
        if not self._title:
            self._extract_meta()
        return self._title

    def _translate(self, text: str, context: str = "") -> str:
        result = self.engine.translate(text, context=context)
        result = result.replace("[¶SEP¶]", "")
        result = result.replace("[¶SEP¶", "")
        result = result.replace("¶SEP¶]", "")
        return result.strip()

    def extract_and_translate(self) -> str:
        self._extract_meta()
        self._placeholders = {}

        md_parts: list[str] = []
        paper_parts: list[str] = []

        # ── Header ──
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

        # ── Abstract ──
        self._process_abstract(md_parts, paper_parts)

        # ── Body: find div.article.fulltext-view inside div.content-block-markup ──
        fulltext = self.soup.select_one("div.content-block-markup div.article.fulltext-view")
        if not fulltext:
            # Fallback: try any div.article.fulltext-view directly
            fulltext = self.soup.select_one("div.article.fulltext-view")

        if not fulltext:
            logger.warning(
                "Cannot find article fulltext-view — outputting header + abstract only (partial save?)"
            )
            self._write_paper("".join(paper_parts))
            self.engine.shutdown()
            return "".join(md_parts)

        # ── Body sections ──
        paragraphs: list[str] = []
        plain_texts: list[str] = []

        def flush() -> None:
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
                elif pt.startswith("<equation>"):
                    eq = pt[10:-11]
                    eq_block = f"$$\n{eq}\n$$\n\n"
                    md_parts.append(eq_block)
                    paper_parts.append(eq_block)
                elif pt.startswith("<center>"):
                    md_parts.append(f"{pt}\n\n")
                    paper_parts.append(f"{pt}\n\n")
                elif pt.startswith("<heading>"):
                    heading_text = pt[9:-10]
                    translated_heading = self._translate(heading_text)
                    md_parts.append(f"## {translated_heading}\n\n")
                    paper_parts.append(f"## {heading_text}\n\n")
            paragraphs.clear()
            plain_texts.clear()

        # Collect div.section elements from fulltext-view
        section_divs = fulltext.find_all("div", class_="section") or [
            child for child in fulltext.children
            if isinstance(child, Tag) and child.name == "div"
            and "section" in (child.get("class") or [])
        ]

        stopped = False
        for section in section_divs:
            if stopped:
                break
            if not isinstance(section, Tag):
                continue

            classes = section.get("class") or []

            # Skip abstract section (already processed)
            if "abstract" in classes:
                continue

            # Get heading from h2
            h2 = section.find("h2")
            if h2:
                heading_text = clean_html_entities(h2.get_text(strip=True))
                heading_text = normalize_whitespace(heading_text)
                heading_lower = heading_text.lower()

                # Stop at References, Footnotes, Acknowledgments, etc.
                if any(
                    heading_lower == stop or heading_lower.startswith(stop)
                    for stop in STOP_HEADINGS
                ):
                    stopped = True
                    break

                if heading_text:
                    flush()
                    self._current_heading = heading_text
                    translated_heading = self._translate(heading_text)
                    md_parts.append(f"## {translated_heading}\n\n")
                    paper_parts.append(f"## {heading_text}\n\n")

            # Process children: paragraphs, skip figures
            for child in section.children:
                if isinstance(child, NavigableString):
                    continue
                if not isinstance(child, Tag):
                    continue

                if child.name == "p":
                    self._process_paragraph(child, paragraphs, plain_texts)
                elif child.name == "div" and "fig" in (child.get("class") or []):
                    continue  # Skip figures
                elif child.name == "div" and any(
                    "table" in cls for cls in (child.get("class") or [])
                ):
                    flush()
                    caption = child.find("div", class_="table-caption") or child.find("caption")
                    if caption:
                        caption_text = clean_html_entities(caption.get_text(strip=True))
                        caption_text = normalize_whitespace(caption_text)
                        if caption_text:
                            translated_cap = self._translate(caption_text)
                            md_parts.append(f"<center>{translated_cap}</center>\n\n")
                elif child.name in ("ul", "ol"):
                    for item in child.find_all("p"):
                        if item:
                            self._process_paragraph(item, paragraphs, plain_texts)

        flush()

        # ── Restore placeholders ──
        md_text = "".join(md_parts)
        paper_text = "".join(paper_parts)
        for key, value in self._placeholders.items():
            md_text = md_text.replace(key, value)
            paper_text = paper_text.replace(key, value)

        self._write_paper(paper_text)
        self.engine.shutdown()
        return md_text

    def _process_abstract(
        self, md_parts: list[str], paper_parts: list[str]
    ) -> None:
        """Extract abstract body and significance statement from div.section.abstract."""
        abstract_section = self.soup.select_one("div.section.abstract")
        if not abstract_section:
            return

        abstract_paras = abstract_section.find_all("p")
        if not abstract_paras:
            return

        # First paragraph is the abstract body
        abstract_text = clean_html_entities(abstract_paras[0].get_text(" ", strip=True))
        abstract_text = normalize_whitespace(abstract_text)
        if abstract_text:
            translated = self._translate(abstract_text, context="摘要")
            md_parts.append(f"## 摘要\n\n{translated}\n\n")
            paper_parts.append(f"## Abstract\n\n{abstract_text}\n\n")

        # Second paragraph is the Significance Statement (if present)
        if len(abstract_paras) > 1:
            sig_text = clean_html_entities(abstract_paras[1].get_text(" ", strip=True))
            sig_text = normalize_whitespace(sig_text)
            if sig_text:
                translated_sig = self._translate(sig_text, context="重要性声明")
                md_parts.append(f"## 重要性声明\n\n{translated_sig}\n\n")
                paper_parts.append(f"## Significance Statement\n\n{sig_text}\n\n")

    def _write_paper(self, paper_text: str) -> None:
        raw_path = self.output_dir / "paper.md"
        with open(raw_path, "w", encoding="utf-8") as f:
            f.write(paper_text)

    def _process_paragraph(
        self, p: Tag, paragraphs: list[str], plain_texts: list[str]
    ) -> None:
        text = clean_html_entities(p.get_text(" ", strip=True))
        text = normalize_whitespace(text)
        if text:
            plain_texts.append(f"<p>{text}</p>")
            paragraphs.append(text)
