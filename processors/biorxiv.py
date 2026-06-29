import logging
from pathlib import Path
from bs4 import BeautifulSoup, Tag
from bs4.element import NavigableString

from processors.base import BaseProcessor
from engine import TranslationEngine
from utils.text_utils import clean_html_entities, normalize_whitespace

logger = logging.getLogger(__name__)

STOP_HEADINGS = [
    "author contributions",
    "ethical statements",
    "declaration of interests",
    "code and data availability",
    "acknowledgements",
    "acknowledgments",
    "footnotes",
    "references",
    "supplementary material",
    "supplementary",
    "supporting information",
]


class BiorxivProcessor(BaseProcessor):
    def __init__(self, html_path: str, output_dir: Path, engine: TranslationEngine | None = None,
                 concurrency: int = 1):
        super().__init__(html_path, output_dir, concurrency=concurrency)
        self.engine = engine if engine is not None else TranslationEngine(concurrency=concurrency)
        with open(html_path, "r", encoding="utf-8", errors="ignore") as f:
            self.soup = BeautifulSoup(f.read(), "lxml")
        self._title = ""
        self._authors = ""
        self._journal_info = ""
        self._doi = ""

    def _extract_meta(self):
        title_meta = self.soup.select_one("meta[name='citation_title']")
        if title_meta:
            self._title = str(title_meta.get("content", "")).strip()
        if not self._title:
            h1 = self.soup.select_one("h1#page-title")
            if h1:
                self._title = clean_html_entities(h1.get_text(strip=True))

        author_names = []
        seen = set()
        for author_el in self.soup.select("span.highwire-citation-author"):
            given = author_el.select_one("span.nlm-given-names")
            surname = author_el.select_one("span.nlm-surname")
            if given and surname:
                name = f"{given.get_text(strip=True)} {surname.get_text(strip=True)}"
                name = normalize_whitespace(name)
                if name and name not in seen:
                    seen.add(name)
                    author_names.append(name)
        self._authors = ", ".join(author_names)

        journal_meta = self.soup.select_one("meta[name='citation_journal_title']")
        journal = ""
        if journal_meta:
            journal = str(journal_meta.get("content", "")).strip()

        date_meta = self.soup.select_one("meta[name='citation_publication_date']")
        date = ""
        if date_meta:
            date = str(date_meta.get("content", "")).strip()

        parts = []
        if journal:
            parts.append(journal)
        if date:
            parts.append(date)
        self._journal_info = " ".join(parts) if parts else "bioRxiv"

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

        md_parts = []
        paper_parts = []

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

        article = self.soup.select_one("div.article.fulltext-view")
        if not article:
            article = self.soup.find("div", class_="article")
        if not article:
            article = self.soup.select_one("div.highwire-markup")
        if not article:
            raise ValueError("Cannot find article content in HTML")

        sections = article.find_all("div", class_="section")
        if not sections:
            sections = article.find_all("div", class_=True)

        for section in sections:
            h2 = section.find("h2")
            heading = ""
            if h2:
                heading = clean_html_entities(h2.get_text(strip=True))
                heading = normalize_whitespace(heading)

            if heading and heading.lower() in STOP_HEADINGS:
                break

            if heading == "Abstract":
                self._process_abstract(section, md_parts, paper_parts)
            elif heading:
                self._process_content_section(section, "h2", heading, md_parts, paper_parts)
            else:
                h3 = section.find("h3")
                if h3:
                    sub_heading = clean_html_entities(h3.get_text(strip=True))
                    sub_heading = normalize_whitespace(sub_heading)
                    if sub_heading:
                        self._process_content_section(section, "h3", sub_heading, md_parts, paper_parts)

        raw_path = self.output_dir / "paper.md"
        with open(raw_path, "w", encoding="utf-8") as f:
            f.write("".join(paper_parts))

        self.engine.shutdown()
        return "".join(md_parts)

    def _write_paper(self, paper_text: str):
        raw_path = self.output_dir / "paper.md"
        with open(raw_path, "w", encoding="utf-8") as f:
            f.write(paper_text)

    def _process_abstract(self, section: Tag, md_parts: list[str], paper_parts: list[str]):
        paragraphs = section.find_all("p")
        texts = []
        for p in paragraphs:
            self._strip_formulas(p)
            text = clean_html_entities(p.get_text(" ", strip=True))
            text = normalize_whitespace(text)
            if text:
                texts.append(text)

        if texts:
            abstract_text = " ".join(texts)
            translated = self._translate(abstract_text, context="摘要")
            md_parts.append(f"## 摘要\n\n{translated}\n\n")
            paper_parts.append(f"## Abstract\n\n{abstract_text}\n\n")

    def _process_content_section(self, section: Tag, heading_level: str, heading: str,
                                  md_parts: list[str], paper_parts: list[str]):
        self._current_heading = heading
        translated_heading = self._translate(heading)
        if heading_level == "h2":
            md_parts.append(f"## {translated_heading}\n\n")
            paper_parts.append(f"## {heading}\n\n")
        else:
            md_parts.append(f"### {translated_heading}\n\n")
            paper_parts.append(f"### {heading}\n\n")

        paragraphs = []

        for child in section.children:
            if isinstance(child, NavigableString):
                continue
            if not isinstance(child, Tag):
                continue

            if child.name in ("h3",):
                if heading_level == "h3":
                    continue
                if paragraphs:
                    translated_paras = self.engine.translate_paragraphs(paragraphs, context=f"{self._title} - {self._current_heading}")
                    self._append_paragraphs(translated_paras, paragraphs, md_parts, paper_parts)
                    paragraphs.clear()

                sub_heading = clean_html_entities(child.get_text(strip=True))
                sub_heading = normalize_whitespace(sub_heading)
                if sub_heading:
                    self._current_heading = sub_heading
                    translated_sub = self._translate(sub_heading)
                    md_parts.append(f"### {translated_sub}\n\n")
                    paper_parts.append(f"### {sub_heading}\n\n")

            elif child.name == "p":
                self._strip_formulas(child)
                text = clean_html_entities(child.get_text(" ", strip=True))
                text = normalize_whitespace(text)
                if text:
                    paragraphs.append(text)

            elif child.name == "div" and "section" in (child.get("class") or []):
                pass

        if paragraphs:
            translated_paras = self.engine.translate_paragraphs(paragraphs)
            self._append_paragraphs(translated_paras, paragraphs, md_parts, paper_parts)
            paragraphs.clear()

    def _append_paragraphs(self, translated: list[str], original: list[str],
                           md_parts: list[str], paper_parts: list[str]):
        for i, tp in enumerate(translated):
            tp = tp.replace("[¶SEP¶]", "").replace("[¶SEP¶", "").replace("¶SEP¶]", "").strip()
            if tp:
                md_parts.append(f"{tp}\n\n")
                if i < len(original):
                    paper_parts.append(f"{original[i]}\n\n")

    def _strip_formulas(self, p: Tag):
        for span in p.find_all("span", class_="disp-formula"):
            span.extract()
        for span in p.find_all("span", class_="inline-formula"):
            span.extract()
