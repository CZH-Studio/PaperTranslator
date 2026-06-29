import re
import logging
from pathlib import Path
from bs4 import BeautifulSoup, Tag
from bs4.element import NavigableString

from processors.base import BaseProcessor
from engine import TranslationEngine
from processors.pnas import _mathml_to_latex
from utils.text_utils import clean_html_entities, normalize_whitespace

logger = logging.getLogger(__name__)

STOP_HEADINGS = [
    "author contributions",
    "funding",
    "conflicts of interest",
    "conflict of interest",
    "references",
    "supplementary materials",
    "supplementary",
    "acknowledgments",
    "acknowledgements",
    "data availability",
    "data availability statement",
]


class MdpiProcessor(BaseProcessor):
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
        self._placeholders: dict[str, str] = {}

    def _make_placeholder(self, prefix: str, content: str) -> str:
        idx = len(self._placeholders)
        key = f"[{prefix}_{idx}]"
        self._placeholders[key] = content
        return key

    def _extract_meta(self):
        title_meta = self.soup.select_one("meta[name='citation_title']")
        if title_meta:
            self._title = str(title_meta.get("content", "")).strip()
        if not self._title:
            h1 = self.soup.select_one("h1.title")
            if h1:
                self._title = clean_html_entities(h1.get_text(strip=True))

        author_metas = self.soup.select("meta[name='citation_author']")
        if not author_metas:
            author_metas = self.soup.select("meta[name='dc.creator']")
        authors = []
        seen = set()
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
        date = ""
        if date_meta:
            date = str(date_meta.get("content", "")).strip()

        volume_meta = self.soup.select_one("meta[name='citation_volume']")
        volume = ""
        if volume_meta:
            volume = str(volume_meta.get("content", "")).strip()

        issue_meta = self.soup.select_one("meta[name='citation_issue']")
        issue = ""
        if issue_meta:
            issue = str(issue_meta.get("content", "")).strip()

        first_page = ""
        fp_meta = self.soup.select_one("meta[name='citation_firstpage']")
        if fp_meta:
            first_page = str(fp_meta.get("content", "")).strip()

        parts = [journal_title]
        if volume:
            parts[-1] = f"{journal_title} vol. {volume}" if journal_title else f"Vol. {volume}"
        if issue:
            parts[-1] = f"{parts[-1]}, no. {issue}"
        if date:
            parts.append(date)
        if first_page:
            parts.append(first_page)

        self._journal_info = ". ".join(parts) if parts else "MDPI"

        doi_meta = self.soup.select_one("meta[name='citation_doi']")
        if not doi_meta:
            doi_meta = self.soup.select_one("meta[name='dc.identifier']")
        if doi_meta:
            doi = str(doi_meta.get("content", "")).strip()
            if doi.startswith("doi:"):
                doi = doi[4:]
            self._doi = doi

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

        self._process_abstract(md_parts, paper_parts)

        paragraphs: list[str] = []
        plain_texts: list[str] = []

        def flush():
            nonlocal paragraphs, plain_texts
            if not paragraphs:
                return
            raw_batch = list(paragraphs)
            translated = self.engine.translate_paragraphs(paragraphs, context=f"{self._title} - {self._current_heading}")
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
                elif pt.startswith("<figure>"):
                    fig = pt[8:-9]
                    md_parts.append(f"{fig}\n\n")
                    paper_parts.append(f"{fig}\n\n")
                elif pt.startswith("<table>"):
                    tbl = pt[7:-8]
                    md_parts.append(f"{tbl}\n\n")
                    paper_parts.append(f"{tbl}\n\n")
                elif pt.startswith("<center>"):
                    md_parts.append(f"{pt}\n\n")
                    paper_parts.append(f"{pt}\n\n")
            paragraphs.clear()
            plain_texts.clear()

        html_body = self.soup.select_one("div.html-body")
        if html_body:
            self._process_sections(html_body, md_parts, paper_parts,
                                   paragraphs, plain_texts, flush, depth=0)
        flush()

        self._process_figures_and_tables(md_parts, paper_parts, flush)

        flush()

        md_text = "".join(md_parts)
        paper_text = "".join(paper_parts)
        for key, value in self._placeholders.items():
            md_text = md_text.replace(key, value)
            paper_text = paper_text.replace(key, value)

        self._write_paper(paper_text)
        self.engine.shutdown()
        return md_text

    def _write_paper(self, paper_text: str):
        raw_path = self.output_dir / "paper.md"
        with open(raw_path, "w", encoding="utf-8") as f:
            f.write(paper_text)

    def _process_abstract(self, md_parts: list[str], paper_parts: list[str]):
        abstract_section = self.soup.select_one("section.html-abstract")
        if not abstract_section:
            return

        para = abstract_section.select_one("div.html-p")
        if not para:
            return

        text = clean_html_entities(para.get_text(" ", strip=True))
        text = normalize_whitespace(text)
        if text:
            translated = self._translate(text, context="摘要")
            md_parts.append(f"## 摘要\n\n{translated}\n\n")
            paper_parts.append(f"## Abstract\n\n{text}\n\n")

    def _process_sections(self, parent: Tag, md_parts: list[str], paper_parts: list[str],
                           paragraphs: list[str], plain_texts: list[str],
                           flush_fn, depth: int = 0):
        if depth > 4:
            return

        for child in parent.children:
            if isinstance(child, NavigableString):
                continue
            if not isinstance(child, Tag):
                continue

            if child.name == "section":
                heading = child.find("h2") or child.find("h3")
                heading_text = ""
                if heading:
                    heading_text = clean_html_entities(heading.get_text(strip=True))
                    heading_text = normalize_whitespace(heading_text)

                heading_lower = heading_text.lower()
                if any(stop in heading_lower for stop in STOP_HEADINGS):
                    continue

                if heading_text and depth <= 3:
                    flush_fn()
                    self._current_heading = heading_text
                    translated_heading = self._translate(heading_text)
                    if depth == 0:
                        md_parts.append(f"## {translated_heading}\n\n")
                        paper_parts.append(f"## {heading_text}\n\n")
                    elif depth == 1:
                        md_parts.append(f"### {translated_heading}\n\n")
                        paper_parts.append(f"### {heading_text}\n\n")
                    elif depth == 2:
                        md_parts.append(f"#### {translated_heading}\n\n")
                        paper_parts.append(f"#### {heading_text}\n\n")
                    else:
                        md_parts.append(f"##### {translated_heading}\n\n")
                        paper_parts.append(f"##### {heading_text}\n\n")

                self._process_sections(child, md_parts, paper_parts,
                                       paragraphs, plain_texts, flush_fn, depth + 1)

            elif child.name == "div" and "html-p" in (child.get("class") or []):
                self._process_paragraph_div(child, paragraphs, plain_texts)

    def _process_paragraph_div(self, para_div: Tag, paragraphs: list[str], plain_texts: list[str]):
        para_div_copy = BeautifulSoup(str(para_div), "lxml")

        math_scripts = self.soup.find_all("script", type="math/mml")

        for span_math in para_div_copy.find_all("span", class_="MathJax"):
            math_id = str(span_math.get("id", ""))
            if math_id and math_id.endswith("-Frame"):
                base_id = math_id[:-6]
            else:
                base_id = math_id or ""

            mathml_tag = None
            if base_id:
                for script in math_scripts:
                    if script.get("id") == base_id:
                        mathml_tag = script
                        break

            formula_latex = None
            is_block = False
            if mathml_tag:
                try:
                    math_soup = BeautifulSoup(mathml_tag.string or "", "lxml")
                    math_elem = math_soup.find("math")
                    if math_elem:
                        display = math_elem.get("display", "inline")
                        is_block = display == "block"
                        formula_latex = _mathml_to_latex(math_elem)
                except Exception:
                    pass

            if formula_latex:
                if is_block:
                    placeholder = self._make_placeholder("EQ", f"$$\n{formula_latex}\n$$")
                else:
                    placeholder = self._make_placeholder("MATH", f"${formula_latex}$")
                span_math.replace_with(f" {placeholder} ")
            else:
                span_math.extract()

        text = clean_html_entities(para_div_copy.get_text(" ", strip=True))
        text = normalize_whitespace(text)
        if text:
            plain_texts.append(f"<p>{text}</p>")
            paragraphs.append(text)

    def _process_figures_and_tables(self, md_parts: list[str], paper_parts: list[str],
                                      flush_fn):
        figures_section = self.soup.select_one("section#FiguresandTables")
        if not figures_section:
            return

        fig_shows = figures_section.find_all("div", class_="html-fig_show")
        all_figures: list[str] = []
        for fig_show in fig_shows:
            fig_html = self._process_figure(fig_show)
            if fig_html:
                all_figures.append(fig_html)

        table_shows = figures_section.find_all("div", class_="html-table_show")
        all_tables: list[str] = []
        for tbl_show in table_shows:
            tbl_html = self._process_table(tbl_show)
            if tbl_html:
                all_tables.append(tbl_html)

        if not all_figures and not all_tables:
            return

        flush_fn()
        heading_cn = "图表"
        heading_en = "Figures and Tables"
        translated_heading = self._translate(heading_en)
        md_parts.append(f"## {translated_heading}\n\n")
        paper_parts.append(f"## {heading_en}\n\n")

        for fig in all_figures:
            md_parts.append(f"{fig}\n\n")
            paper_parts.append(f"{fig}\n\n")

        for tbl in all_tables:
            md_parts.append(f"{tbl}\n\n")
            paper_parts.append(f"{tbl}\n\n")

    def _process_figure(self, fig_show: Tag) -> str:
        caption_div = fig_show.select_one("div.html-caption")
        caption_text = ""
        if caption_div:
            caption_text = clean_html_entities(caption_div.get_text(" ", strip=True))
            caption_text = normalize_whitespace(caption_text)

        img_url = ""
        img_tag = fig_show.select_one("div.html-img img")
        if img_tag:
            src = img_tag.get("data-original") or img_tag.get("data-large") or img_tag.get("src")
            if isinstance(src, str) and not src.startswith("data:"):
                img_url = str(src)

        if caption_text:
            translated_caption = self._translate(caption_text)
        else:
            translated_caption = ""

        parts = []
        if img_url:
            parts.append(f"![{translated_caption or 'Figure'}]({img_url})")
        if translated_caption:
            parts.append(f"<center>{translated_caption}</center>")

        if not parts:
            return ""
        return "<figure>" + "\n".join(parts) + "</figure>"

    def _process_table(self, tbl_show: Tag) -> str:
        caption_div = tbl_show.select_one("div.html-caption")
        caption_text = ""
        if caption_div:
            caption_text = clean_html_entities(caption_div.get_text(" ", strip=True))
            caption_text = normalize_whitespace(caption_text)

        table_elem = tbl_show.find("table")
        if not table_elem:
            return ""

        table_html = str(table_elem)

        parts = []
        if caption_text:
            translated_caption = self._translate(caption_text)
            parts.append(f"<center>{translated_caption}</center>")
        parts.append(f"<table>{table_html}</table>")

        return "\n".join(parts)
