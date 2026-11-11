import re
import logging
from pathlib import Path
from bs4 import BeautifulSoup, Tag
from bs4.element import NavigableString

from processors.base import BaseProcessor
from engine import TranslationEngine
from utils.text_utils import clean_html_entities, normalize_whitespace

logger = logging.getLogger(__name__)

STOP_SECTIONS = [
    "conflict of interest",
    "conflict of interest statement",
    "acknowledgements",
    "acknowledgments",
    "references",
    "recommended articles",
    "cited by",
    "special issue articles",
]


class ScienceDirectProcessor(BaseProcessor):
    def __init__(self, html_path: str, output_dir: Path, engine: TranslationEngine | None = None,
                 concurrency: int = 1):
        super().__init__(html_path, output_dir, concurrency=concurrency)
        self.engine = engine if engine is not None else TranslationEngine(concurrency=concurrency)
        with open(html_path, "r", encoding="utf-8") as f:
            self.soup = BeautifulSoup(f.read(), "lxml")
        self._title = ""
        self._authors = ""
        self._journal_info = ""
        self._doi = ""

    def _extract_meta(self):
        title_meta = self.soup.select_one("meta[name='citation_title']")
        if title_meta:
            self._title = str(title_meta.get("content", "")).strip()

        author_metas = self.soup.select("meta[name='citation_author']")
        authors = []
        for a in author_metas:
            name = str(a.get("content", "")).strip()
            if name:
                authors.append(name)

        if not authors:
            main = self.soup.find("main", id="main")
            if main:
                h1 = main.find("h1")
                if h1:
                    author_div = h1.find_next_sibling("div")
                    if author_div:
                        raw = author_div.get_text(" ", strip=True)
                        raw = raw.replace("Author links open overlay panel", "")
                        raw = clean_html_entities(raw)
                        raw = re.sub(r"\bShow\s*more\b", "", raw, flags=re.IGNORECASE)
                        raw = raw.strip()
                        if raw:
                            names = re.split(r",\s*", raw)
                            cleaned_names = []
                            for n in names:
                                n = n.strip()
                                n = re.sub(r"\d+$", "", n)
                                n = re.sub(r"(\w)([A-Z])", r"\1 \2", n)
                                n = n.strip()
                                if n and len(n) > 1:
                                    cleaned_names.append(n)
                            if cleaned_names:
                                authors = cleaned_names

        self._authors = ", ".join(authors)

        journal = self.soup.select_one("meta[name='citation_journal_title']")
        volume = self.soup.select_one("meta[name='citation_volume']")
        firstpage = self.soup.select_one("meta[name='citation_firstpage']")
        lastpage = self.soup.select_one("meta[name='citation_lastpage']")
        date = self.soup.select_one("meta[name='citation_publication_date']")

        parts = []
        if journal:
            parts.append(str(journal.get("content", "")))
        info_parts = []
        if volume:
            info_parts.append(f"volume {str(volume.get('content', ''))}")
        if firstpage:
            fp = str(firstpage.get("content", ""))
            lp = str(lastpage.get("content", "")) if lastpage else ""
            if lp:
                info_parts.append(f"{fp}–{lp}")
            else:
                info_parts.append(fp)
        if date:
            info_parts.append(str(date.get("content", "")))
        if info_parts:
            parts.append(", ".join(info_parts))
        self._journal_info = " ".join(parts)

        doi_meta = self.soup.select_one("meta[name='citation_doi']")
        if doi_meta:
            self._doi = str(doi_meta.get("content", "")).strip()

    def _translate(self, text: str, context: str = "") -> str:
        result = self.engine.translate(text, context=context)
        result = result.replace("[¶SEP¶]", "")
        result = result.replace("[¶SEP¶", "")
        result = result.replace("¶SEP¶]", "")
        return result.strip()

    def extract_title(self) -> str:
        if not self._title:
            self._extract_meta()
        return self._title

    def extract_and_translate(self) -> str:
        self._extract_meta()

        md_parts = []
        paper_parts = []

        title_cn = self._translate(self._title)
        md_parts.append(f"# {title_cn}\n\n")
        paper_parts.append(f"# {self._title}\n\n")
        md_parts.append(f"**{self._title}**\n\n")
        md_parts.append(f"**作者**: {self._authors}\n\n")
        md_parts.append(f"**{self._journal_info}**\n\n")
        if self._doi:
            md_parts.append(f"**DOI**: {self._doi}\n\n")
        md_parts.append("---\n\n")
        paper_parts.append("---\n\n")

        self._process_abstract(md_parts, paper_parts)
        self._process_body(md_parts, paper_parts)

        raw_path = self.output_dir / "paper.md"
        with open(raw_path, "w", encoding="utf-8") as f:
            f.write("".join(paper_parts))

        self.engine.shutdown()
        return "".join(md_parts)

    def _process_abstract(self, md_parts: list[str], paper_parts: list[str]):
        abstracts_div = self.soup.find("div", id="abstracts")
        if not abstracts_div:
            return

        abstract_author = abstracts_div.select_one("div.abstract.author")
        if not abstract_author:
            return

        abstract_text = clean_html_entities(abstract_author.get_text(" ", strip=True))
        abstract_text = normalize_whitespace(abstract_text)
        if not abstract_text:
            return

        translated = self._translate(abstract_text, context="摘要")
        md_parts.append(f"## 摘要\n\n{translated}\n\n")
        paper_parts.append(f"## Abstract\n\n{abstract_text}\n\n")

    def _process_body(self, md_parts: list[str], paper_parts: list[str]):
        body = self.soup.find("div", id="body")
        if not body:
            logger.warning("Cannot find div#body")
            return

        sections = body.find_all("section")
        if not sections:
            logger.warning("No <section> elements found in div#body")
            return

        for section in sections:
            h2 = section.find("h2")
            if not h2:
                continue
            heading_text = clean_html_entities(h2.get_text(strip=True))
            heading_text = normalize_whitespace(heading_text)
            if not heading_text:
                continue

            heading_lower = heading_text.lower()
            if any(stop in heading_lower for stop in STOP_SECTIONS):
                break

            translated_heading = self._translate(heading_text)
            self._current_heading = heading_text
            md_parts.append(f"## {translated_heading}\n\n")
            paper_parts.append(f"## {heading_text}\n\n")

            paragraphs: list[str] = []

            for para_div in section.find_all("div", class_="u-margin-s-bottom"):
                text = clean_html_entities(para_div.get_text(" ", strip=True))
                text = normalize_whitespace(text)
                if text:
                    paragraphs.append(text)

            if paragraphs:
                translated_paras = self.engine.translate_paragraphs(paragraphs, context=f"{self._title} - {self._current_heading}")
                for i, tp in enumerate(translated_paras):
                    tp = tp.replace("[¶SEP¶]", "")
                    tp = tp.replace("[¶SEP¶", "")
                    tp = tp.replace("¶SEP¶]", "")
                    tp = tp.strip()
                    if tp:
                        md_parts.append(f"{tp}\n\n")
                        if i < len(paragraphs):
                            paper_parts.append(f"{paragraphs[i]}\n\n")
