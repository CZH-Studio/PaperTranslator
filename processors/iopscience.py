import re
import logging
from pathlib import Path
from bs4 import BeautifulSoup, Tag
from bs4.element import NavigableString

from processors.base import BaseProcessor
from engine import TranslationEngine
from utils.text_utils import clean_html_entities, normalize_whitespace

logger = logging.getLogger(__name__)

STOP_H2_TEXTS = [
    "data availability",
    "funding",
    "conflict of interest",
    "author contributions",
    "acknowledgments",
    "acknowledgements",
    "supplementary",
    "supporting information",
    "references",
]


class IopscienceProcessor(BaseProcessor):
    def __init__(
        self, html_path: str, output_dir: Path, engine: TranslationEngine | None = None,
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

    def _extract_meta(self):
        title_meta = self.soup.select_one("meta[name='citation_title']")
        if title_meta:
            self._title = str(title_meta.get("content", "")).strip()
        if not self._title:
            h1 = self.soup.select_one("h1")
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

        date_meta = self.soup.select_one("meta[name='citation_online_date']")
        if not date_meta:
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
            parts[-1] = (
                f"{journal_title} vol. {volume}" if journal_title else f"Vol. {volume}"
            )
        if issue:
            parts[-1] = f"{parts[-1]}, no. {issue}"
        if date:
            parts.append(date)
        if first_page:
            parts.append(first_page)

        self._journal_info = ". ".join(parts) if parts else "IOPScience"

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

        body = self.soup.select_one("div.wd-jnl-art-full-text.article-text")
        if not body:
            body = self.soup.select_one("div.wd-jnl-art-full-text")
        if not body:
            logger.warning("Cannot find article full text container")
            self._write_paper("".join(paper_parts))
            return "".join(md_parts)

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
                elif pt.startswith("<table>"):
                    tbl = pt[7:-8]
                    md_parts.append(f"{tbl}\n\n")
                    paper_parts.append(f"{tbl}\n\n")
                elif pt.startswith("<equation>"):
                    eq = pt[10:-11]
                    eq_block = f"$$\n{eq}\n$$\n\n"
                    md_parts.append(eq_block)
                    paper_parts.append(eq_block)
                elif pt.startswith("<figure>"):
                    fig = pt[8:-9]
                    md_parts.append(f"{fig}\n\n")
                    paper_parts.append(f"{fig}\n\n")
                elif pt.startswith("<center>"):
                    md_parts.append(f"{pt}\n\n")
                    paper_parts.append(f"{pt}\n\n")
            paragraphs.clear()
            plain_texts.clear()

        self._process_body(body, md_parts, paper_parts, paragraphs, plain_texts, flush)

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
        abstract_div = self.soup.select_one("div.article-abstract")
        if not abstract_div:
            return

        p_tag = abstract_div.select_one("p")
        if not p_tag:
            return

        text = clean_html_entities(p_tag.get_text(" ", strip=True))
        text = normalize_whitespace(text)
        if text:
            translated = self._translate(text, context="摘要")
            md_parts.append(f"## 摘要\n\n{translated}\n\n")
            paper_parts.append(f"## Abstract\n\n{text}\n\n")

    def _process_body(
        self,
        body: Tag,
        md_parts: list[str],
        paper_parts: list[str],
        paragraphs: list[str],
        plain_texts: list[str],
        flush_fn,
    ):
        seen_figure_ids: set[str] = set()

        for child in body.children:
            if isinstance(child, NavigableString):
                continue
            if not isinstance(child, Tag):
                continue

            if child.name == "h2" and "header-anchor" in (child.get("class") or []):
                heading_text = clean_html_entities(child.get_text(strip=True))
                heading_text = normalize_whitespace(heading_text)
                heading_lower = heading_text.lower()

                if any(stop in heading_lower for stop in STOP_H2_TEXTS):
                    break

                flush_fn()
                self._current_heading = heading_text
                translated_heading = self._translate(heading_text)
                md_parts.append(f"## {translated_heading}\n\n")
                paper_parts.append(f"## {heading_text}\n\n")

            elif child.name == "div" and "article-text" in (child.get("class") or []):
                self._process_section_content(
                    child,
                    md_parts,
                    paper_parts,
                    paragraphs,
                    plain_texts,
                    flush_fn,
                    seen_figure_ids,
                )

    def _process_section_content(
        self,
        section_div: Tag,
        md_parts: list[str],
        paper_parts: list[str],
        paragraphs: list[str],
        plain_texts: list[str],
        flush_fn,
        seen_figure_ids: set[str],
    ):
        for child in section_div.children:
            if isinstance(child, NavigableString):
                continue
            if not isinstance(child, Tag):
                continue

            if child.name == "p":
                self._replace_math_in_element(child)
                text = clean_html_entities(child.get_text(" ", strip=True))
                text = normalize_whitespace(text)
                if text:
                    plain_texts.append(f"<p>{text}</p>")
                    paragraphs.append(text)

            elif child.name == "h3":
                flush_fn()
                heading_text = clean_html_entities(child.get_text(strip=True))
                heading_text = clean_html_entities(heading_text)
                heading_text = normalize_whitespace(heading_text)
                if heading_text:
                    self._current_heading = heading_text
                    translated_heading = self._translate(heading_text)
                    md_parts.append(f"### {translated_heading}\n\n")
                    paper_parts.append(f"### {heading_text}\n\n")

            elif child.name == "div" and "article-text" in (child.get("class") or []):
                self._process_section_content(
                    child,
                    md_parts,
                    paper_parts,
                    paragraphs,
                    plain_texts,
                    flush_fn,
                    seen_figure_ids,
                )

            elif child.name == "div" and "boxout" in (child.get("class") or []):
                tables = child.find_all("table")
                for tbl in tables:
                    flush_fn()
                    tbl_html = self._process_table(tbl)
                    if tbl_html:
                        plain_texts.append(tbl_html)
                figures = child.find_all("figure")
                for fig in figures:
                    flush_fn()
                    fig_html = self._process_plain_figure(fig)
                    if fig_html:
                        plain_texts.append(fig_html)

            elif child.name == "figure" and "boxout" in (child.get("class") or []):
                fig_id = child.get("id", "")
                if fig_id and fig_id in seen_figure_ids:
                    continue
                if isinstance(fig_id, str) and fig_id:
                    seen_figure_ids.add(fig_id)

                flush_fn()
                fig_html = self._process_figure(child)
                if fig_html:
                    plain_texts.append(fig_html)

            elif child.name == "table":
                flush_fn()
                tbl_html = self._process_table(child)
                if tbl_html:
                    plain_texts.append(tbl_html)

            elif child.name == "ul":
                flush_fn()
                for li in child.find_all("li"):
                    text = clean_html_entities(li.get_text(" ", strip=True))
                    text = normalize_whitespace(text)
                    if text:
                        plain_texts.append(f"<p>{text}</p>")
                        paragraphs.append(text)

    def _replace_math_in_element(self, element: Tag):
        for mjx in element.find_all("mjx-container"):
            assistive = mjx.select_one("mjx-assistive-mml")
            if assistive:
                math_elem = assistive.find("math")
                if math_elem:
                    try:
                        formula = math_elem.get_text(strip=True)
                    except Exception:
                        formula = ""
                    placeholder = self._make_placeholder("MATH", f"${formula}$")
                    mjx.replace_with(f" {placeholder} ")
                else:
                    mjx.extract()

    def _process_figure(self, figure: Tag) -> str:
        img_url = ""
        link = figure.select_one("a[href$='.jpg']")
        if not link:
            link = figure.select_one("a[href$='.jpeg']")
        if not link:
            link = figure.select_one("a[href$='.png']")
        if link:
            img_url = str(link.get("href", ""))

        if not img_url:
            img_tag = figure.select_one("img")
            if img_tag:
                src = img_tag.get("src", "")
                if isinstance(src, str) and not src.startswith("data:"):
                    img_url = src

        caption_text = ""
        parent = figure.parent
        if parent:
            caption_div = parent.select_one("div.figure-caption")
            if not caption_div:
                caption_div = parent.select_one("div.article-text.figure-caption")
            if caption_div:
                caption_text = clean_html_entities(
                    caption_div.get_text(" ", strip=True)
                )
                caption_text = normalize_whitespace(caption_text)

        if not caption_text:
            figcaption = figure.select_one("figcaption")
            if figcaption:
                caption_text = clean_html_entities(figcaption.get_text(" ", strip=True))
                caption_text = normalize_whitespace(caption_text)

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

    def _process_table(self, table: Tag) -> str:
        table_html = str(table)
        return f"<table>{table_html}</table>"

    def _process_plain_figure(self, figure: Tag) -> str:
        img_url = ""
        img_tag = figure.select_one("img")
        if img_tag:
            src = img_tag.get("src", "")
            if isinstance(src, str) and not src.startswith("data:"):
                img_url = src
        if not img_url:
            link = figure.select_one("a[href$='.jpg']")
            if link:
                img_url = str(link.get("href", ""))

        caption_text = ""
        figcaption = figure.select_one("figcaption")
        if figcaption:
            caption_text = clean_html_entities(figcaption.get_text(" ", strip=True))
            caption_text = normalize_whitespace(caption_text)

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
