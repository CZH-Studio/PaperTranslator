import re
import logging
from pathlib import Path
from bs4 import BeautifulSoup, Tag
from bs4.element import NavigableString

from processors.base import BaseProcessor
from engine import TranslationEngine
from utils.text_utils import clean_html_entities, normalize_whitespace

logger = logging.getLogger(__name__)

STOP_HEADINGS = [
    "statements",
    "data availability",
    "ethics statement",
    "author contributions",
    "funding",
    "acknowledgments",
    "acknowledgements",
    "conflict of interest",
    "generative ai statement",
    "publisher's note",
    "publishers note",
    "supplementary material",
    "references",
    "summary",
]


class FrontiersProcessor(BaseProcessor):
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
            h1 = self.soup.select_one("h1.ArticleDetailsV4__main__title")
            if h1:
                self._title = clean_html_entities(h1.get_text(strip=True))

        author_metas = self.soup.select("meta[name='citation_author']")
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

        first_page = ""
        fp_meta = self.soup.select_one("meta[name='citation_firstpage']")
        if fp_meta:
            first_page = str(fp_meta.get("content", "")).strip()

        article_type = ""
        type_tag = self.soup.select_one("h2.ArticleLayoutHeader__info__title")
        if type_tag:
            article_type = clean_html_entities(type_tag.get_text(strip=True))

        parts = [journal_title]
        if volume:
            parts[-1] = f"{journal_title} vol. {volume}" if journal_title else f"Vol. {volume}"
        if article_type:
            parts.append(article_type)
        if date:
            parts.append(date)
        if first_page:
            parts.append(first_page)

        self._journal_info = ". ".join(parts) if parts else "Frontiers"

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

        article_content = self.soup.select_one("div.ArticleContent")
        if not article_content:
            logger.warning("Cannot find div.ArticleContent")
            self._write_paper("".join(paper_parts))
            return "".join(md_parts)

        abstract_done = False
        for child in article_content.children:
            if isinstance(child, NavigableString):
                continue
            if not isinstance(child, Tag):
                continue

            if child.name == "div" and "ArticleFigure" in (child.get("class") or []):
                flush()
                self._current_heading = child.select_one("p.ArticleFigure__title")
                self._current_heading = child.get("id", "Figure")
                fig_html = self._process_figure(child)
                if fig_html:
                    plain_texts.append(fig_html)
                continue

            if child.name == "div" and "ArticleTable" in (child.get("class") or []):
                flush()
                self._current_heading = child.get("id", "Table")
                tbl_html = self._process_table(child)
                if tbl_html:
                    plain_texts.append(tbl_html)
                continue

            if child.name == "div" and not abstract_done:
                abstract_done = True
                h2 = child.find("h2")
                if h2:
                    # Collect all abstract paragraphs, not just the first one
                    all_paras = child.find_all("p")
                    abstract_texts = []
                    for p in all_paras:
                        txt = clean_html_entities(p.get_text(" ", strip=True))
                        txt = normalize_whitespace(txt)
                        if txt:
                            abstract_texts.append(txt)
                    abstract_text = " ".join(abstract_texts)
                    if abstract_text:
                        translated = self._translate(abstract_text, context="摘要")
                        md_parts.append(f"## 摘要\n\n{translated}\n\n")
                        paper_parts.append(f"## Abstract\n\n{abstract_text}\n\n")
                continue

            heading = child.find("h2") or child.find("h3")
            if heading:
                heading_text = clean_html_entities(heading.get_text(strip=True))
                heading_text = normalize_whitespace(heading_text)
                heading_lower = heading_text.lower()

                if any(stop in heading_lower for stop in STOP_HEADINGS):
                    break

                if heading.name == "h2":
                    flush()
                    self._current_heading = heading_text
                    translated_heading = self._translate(heading_text)
                    md_parts.append(f"## {translated_heading}\n\n")
                    paper_parts.append(f"## {heading_text}\n\n")
                elif heading.name == "h3":
                    flush()
                    self._current_heading = heading_text
                    translated_heading = self._translate(heading_text)
                    md_parts.append(f"### {translated_heading}\n\n")
                    paper_parts.append(f"### {heading_text}\n\n")

                # Process section children sequentially
                for sub in child.children:
                    if isinstance(sub, NavigableString):
                        continue
                    if not isinstance(sub, Tag):
                        continue

                    if sub.name == "p":
                        self._process_paragraph(sub, paragraphs, plain_texts)
                    elif sub.name == "h3":
                        flush()
                        sub_text = clean_html_entities(sub.get_text(strip=True))
                        sub_text = normalize_whitespace(sub_text)
                        if sub_text:
                            self._current_heading = sub_text
                            translated = self._translate(sub_text)
                            md_parts.append(f"### {translated}\n\n")
                            paper_parts.append(f"### {sub_text}\n\n")
                    elif sub.name == "div" and "ArticleFigure" in (sub.get("class") or []):
                        flush()
                        fig_id = sub.get("id", "Figure")
                        self._current_heading = fig_id
                        fig_html = self._process_figure(sub)
                        if fig_html:
                            plain_texts.append(fig_html)
                    elif sub.name == "div" and "ArticleTable" in (sub.get("class") or []):
                        flush()
                        self._current_heading = sub.get("id", "Table")
                        tbl_html = self._process_table(sub)
                        if tbl_html:
                            plain_texts.append(tbl_html)
                    elif sub.name == "ul":
                        for li in sub.find_all("li", recursive=False):
                            li_text = clean_html_entities(li.get_text(" ", strip=True))
                            li_text = normalize_whitespace(li_text)
                            if li_text:
                                paragraphs.append(li_text)
                                plain_texts.append(f"<p>{li_text}</p>")

            if child.name == "p" and not heading:
                self._process_paragraph(child, paragraphs, plain_texts)

            if child.name == "div" and "Statement__" in (child.get("class") or [""])[0]:
                break

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

    def _process_paragraph(self, p: Tag, paragraphs: list[str], plain_texts: list[str]):
        self._replace_citations(p)
        self._replace_math(p)
        text = clean_html_entities(p.get_text(" ", strip=True))
        text = normalize_whitespace(text)
        if text:
            plain_texts.append(f"<p>{text}</p>")
            paragraphs.append(text)

    def _replace_citations(self, element: Tag):
        for btn in element.find_all("button", class_="ArticleReference"):
            ref_text = btn.get_text(strip=True)
            btn.replace_with(f"[{ref_text}]")

    def _replace_math(self, element: Tag):
        for mjx in element.find_all("mjx-container"):
            assistive = mjx.select_one("mjx-assistive-mml")
            if assistive:
                math_elem = assistive.find("math")
                if math_elem:
                    try:
                        from processors.pnas import _mathml_to_latex
                        formula = _mathml_to_latex(math_elem)
                    except Exception:
                        formula = mjx.get_text(strip=True)
                    placeholder = self._make_placeholder("MATH", f"${formula}$")
                    mjx.replace_with(f" {placeholder} ")
                else:
                    mjx.extract()

    def _process_figure(self, fig_div: Tag) -> str:
        img_tag = fig_div.select_one("img")
        img_url = ""
        caption_alt = ""
        if img_tag:
            src = img_tag.get("src", "")
            if isinstance(src, str) and not src.startswith("data:"):
                img_url = src
            alt = img_tag.get("alt", "")
            if isinstance(alt, str):
                caption_alt = clean_html_entities(alt)
                caption_alt = normalize_whitespace(caption_alt)

        title_tag = fig_div.select_one("p.ArticleFigure__title")
        title_text = ""
        if title_tag:
            title_text = clean_html_entities(title_tag.get_text(strip=True))

        caption_text = caption_alt or title_text
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

    def _process_table(self, tbl_div: Tag) -> str:
        caption_div = tbl_div.select_one("div.ArticleTable__description")
        caption_text = ""
        if caption_div:
            caption_text = clean_html_entities(caption_div.get_text(" ", strip=True))
            caption_text = normalize_whitespace(caption_text)

        foot_div = tbl_div.select_one("div.ArticleTable__foot")
        foot_text = ""
        if foot_div:
            foot_text = clean_html_entities(foot_div.get_text(" ", strip=True))
            foot_text = normalize_whitespace(foot_text)

        table_elem = tbl_div.select_one("div.ArticleTable__content table")
        if not table_elem:
            table_elem = tbl_div.find("table")
        if not table_elem:
            return ""

        table_html = str(table_elem)

        parts = []
        if caption_text:
            translated_caption = self._translate(caption_text)
            parts.append(f"<center>{translated_caption}</center>")
        parts.append(f"<table>{table_html}</table>")
        if foot_text:
            translated_foot = self._translate(foot_text)
            parts.append(f"<center>{translated_foot}</center>")

        return "\n".join(parts)
