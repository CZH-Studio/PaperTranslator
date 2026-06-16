import logging
from pathlib import Path
from bs4 import BeautifulSoup, Tag
from bs4.element import NavigableString

from processors.base import BaseProcessor
from engine import TranslationEngine
from utils.text_utils import clean_html_entities, normalize_whitespace

logger = logging.getLogger(__name__)


class ArxivProcessor(BaseProcessor):
    def __init__(self, html_path: str, output_dir: Path, engine: TranslationEngine | None = None):
        super().__init__(html_path, output_dir)
        self.engine = engine if engine is not None else TranslationEngine()
        with open(html_path, "r", encoding="utf-8", errors="ignore") as f:
            self.soup = BeautifulSoup(f.read(), "lxml")
        self._title = ""
        self._authors = ""
        self._journal_info = ""
        self._placeholders: dict[str, str] = {}

    def _make_placeholder(self, prefix: str, content: str) -> str:
        idx = len(self._placeholders)
        key = f"[{prefix}_{idx}]"
        self._placeholders[key] = content
        return key

    def _extract_meta(self):
        title_tag = self.soup.select_one("h1.ltx_title_document")
        if not title_tag:
            title_tag = self.soup.select_one("h1.ltx_title")
        if title_tag:
            self._title = clean_html_entities(title_tag.get_text(strip=True))

        author_names = []
        for author_el in self.soup.select("span.ltx_creator.ltx_role_author span.ltx_personname"):
            name = clean_html_entities(author_el.get_text(strip=True))
            name = normalize_whitespace(name)
            if name:
                author_names.append(name)
        self._authors = ", ".join(author_names)

        watermark = self.soup.select_one("#watermark-tr")
        if watermark:
            self._journal_info = clean_html_entities(watermark.get_text(strip=True))
        if not self._journal_info:
            self._journal_info = "arXiv"

    def extract_title(self) -> str:
        if not self._title:
            self._extract_meta()
        return self._title

    def _translate(self, text: str) -> str:
        result = self.engine.translate(text)
        result = result.replace("[¶SEP¶]", "")
        result = result.replace("[¶SEP¶", "")
        result = result.replace("¶SEP¶]", "")
        return result.strip()

    def extract_and_translate(self) -> str:
        self._extract_meta()
        self._placeholders = {}

        md_parts = []
        paper_parts = []

        title_cn = self._translate(self._title)
        md_parts.append(f"# {title_cn}\n\n")
        paper_parts.append(f"# {self._title}\n\n")
        md_parts.append(f"**{self._title}**\n\n")
        if self._authors:
            md_parts.append(f"**作者**: {self._authors}\n\n")
        md_parts.append(f"**{self._journal_info}**\n\n")
        md_parts.append("---\n\n")
        paper_parts.append("---\n\n")

        self._process_abstract(md_parts, paper_parts)

        article = self.soup.select_one("article.ltx_document")
        if not article:
            raise ValueError("Cannot find article.ltx_document in HTML")

        paragraphs: list[str] = []
        plain_texts: list[str] = []

        def flush():
            nonlocal paragraphs, plain_texts
            if not paragraphs:
                return
            raw_batch = list(paragraphs)
            translated = self.engine.translate_paragraphs(paragraphs)
            t_idx = 0
            for pt in plain_texts:
                if pt.startswith("<p>"):
                    if t_idx < len(translated):
                        md_parts.append(f"{translated[t_idx]}\n\n")
                        if t_idx < len(raw_batch):
                            paper_parts.append(f"{raw_batch[t_idx]}\n\n")
                        t_idx += 1
                elif pt.startswith("<math_block>"):
                    formula = pt[12:-13]
                    md_parts.append(f"$$\n{formula}\n$$\n\n")
                    paper_parts.append(f"$$\n{formula}\n$$\n\n")
                elif pt.startswith("<figure>"):
                    fig = pt[8:-9]
                    md_parts.append(f"{fig}\n\n")
                    paper_parts.append(f"{fig}\n\n")
                elif pt.startswith("<center>"):
                    md_parts.append(f"{pt}\n\n")
                    paper_parts.append(f"{pt}\n\n")
            paragraphs.clear()
            plain_texts.clear()

        for child in article.children:
            if isinstance(child, NavigableString):
                continue
            if not isinstance(child, Tag):
                continue

            if child.name == "section":
                sec_id = child.get("id", "")
                sec_class = child.get("class", [])

                if sec_id == "bib" or "ltx_bibliography" in sec_class:
                    break
                if "ltx_appendix" in sec_class:
                    break

                self._process_arxiv_section(child, md_parts, paper_parts, paragraphs, plain_texts, flush)

        flush()

        md_text = "".join(md_parts)
        paper_text = "".join(paper_parts)
        for key, value in self._placeholders.items():
            md_text = md_text.replace(key, value)
            paper_text = paper_text.replace(key, value)

        self._write_paper(paper_text)
        return md_text

    def _write_paper(self, paper_text: str):
        raw_path = self.output_dir / "paper.md"
        with open(raw_path, "w", encoding="utf-8") as f:
            f.write(paper_text)

    def _process_abstract(self, md_parts: list[str], paper_parts: list[str]):
        abstract_div = self.soup.select_one("div#abstract1.ltx_abstract")
        if not abstract_div:
            abstract_div = self.soup.select_one("div.ltx_abstract")
        if not abstract_div:
            return

        p_tags = abstract_div.find_all("p", class_="ltx_p")
        if not p_tags:
            return

        texts = []
        for p in p_tags:
            for math_el in p.find_all("math", display="inline"):
                self._replace_inline_math(math_el)
            text = clean_html_entities(p.get_text(" ", strip=True))
            text = normalize_whitespace(text)
            if text:
                texts.append(text)

        if texts:
            abstract_text = " ".join(texts)
            translated = self._translate(abstract_text)
            md_parts.append(f"## 摘要\n\n{translated}\n\n")
            paper_parts.append(f"## Abstract\n\n{abstract_text}\n\n")

    def _process_arxiv_section(self, section: Tag, md_parts: list[str], paper_parts: list[str],
                                paragraphs: list[str], plain_texts: list[str], flush_fn):
        h2 = section.find("h2", class_="ltx_title_section")
        heading_text = ""
        if h2:
            heading_text = clean_html_entities(h2.get_text(strip=True))
            heading_text = normalize_whitespace(heading_text)

        if heading_text:
            flush_fn()
            translated_heading = self._translate(heading_text)
            md_parts.append(f"## {translated_heading}\n\n")
            paper_parts.append(f"## {heading_text}\n\n")

        self._process_section_children(section, md_parts, paper_parts, paragraphs, plain_texts, flush_fn)

    def _process_section_children(self, parent: Tag, md_parts: list[str], paper_parts: list[str],
                                   paragraphs: list[str], plain_texts: list[str], flush_fn, depth: int = 0):
        if depth > 3:
            return

        for child in parent.children:
            if isinstance(child, NavigableString):
                continue
            if not isinstance(child, Tag):
                continue

            child_class = child.get("class", [])

            if child.name == "section" and "ltx_subsection" in child_class:
                h3 = child.find("h3", class_="ltx_title_subsection")
                subsection_heading = ""
                if h3:
                    subsection_heading = clean_html_entities(h3.get_text(strip=True))
                    subsection_heading = normalize_whitespace(subsection_heading)

                if subsection_heading:
                    flush_fn()
                    translated_heading = self._translate(subsection_heading)
                    md_parts.append(f"### {translated_heading}\n\n")
                    paper_parts.append(f"### {subsection_heading}\n\n")

                self._process_section_children(
                    child, md_parts, paper_parts, paragraphs, plain_texts, flush_fn, depth + 1
                )

            elif child.name == "div" and "ltx_para" in child_class:
                flush_fn()
                for sub in child.children:
                    if isinstance(sub, Tag) and sub.name == "p" and "ltx_p" in (sub.get("class") or []):
                        self._process_arxiv_paragraph(sub, paragraphs, plain_texts)
                    elif isinstance(sub, Tag) and sub.name == "table" and (
                        "ltx_equation" in (sub.get("class") or [])
                        or "ltx_equationgroup" in (sub.get("class") or [])
                    ):
                        for formula in self._extract_display_formulas(sub):
                            plain_texts.append(f"<math_block>{formula}</math_block>")

            elif child.name == "table" and (
                "ltx_equation" in child_class or "ltx_equationgroup" in child_class
            ):
                flush_fn()
                for formula in self._extract_display_formulas(child):
                    plain_texts.append(f"<math_block>{formula}</math_block>")

            elif child.name == "figure" and "ltx_figure" in child_class:
                flush_fn()
                fig_html = self._process_arxiv_figure(child)
                if fig_html:
                    plain_texts.append(fig_html)

            elif child.name == "figure" and "ltx_table" in child_class:
                flush_fn()
                caption = child.find("figcaption")
                if caption:
                    caption_text = clean_html_entities(caption.get_text(strip=True))
                    caption_text = normalize_whitespace(caption_text)
                    if caption_text:
                        translated_cap = self._translate(caption_text)
                        plain_texts.append(f"<center>{translated_cap}</center>")

            elif child.name in ("ul", "ol") and any(
                c in child_class for c in ["ltx_itemize", "ltx_enumerate"]
            ):
                for li in child.find_all("li", class_="ltx_item"):
                    for li_p in li.find_all("p", class_="ltx_p"):
                        self._process_arxiv_paragraph(li_p, paragraphs, plain_texts)

    def _process_arxiv_paragraph(self, p: Tag, paragraphs: list[str], plain_texts: list[str]):
        for math_el in p.find_all("math", display="inline"):
            self._replace_inline_math(math_el)

        text = clean_html_entities(p.get_text(" ", strip=True))
        text = normalize_whitespace(text)
        if text:
            plain_texts.append(f"<p>{text}</p>")
            paragraphs.append(text)

    def _replace_inline_math(self, math_el: Tag):
        formula = self._extract_formula(math_el)
        if formula:
            placeholder = self._make_placeholder("MATH", f"${formula}$")
            math_el.replace_with(f" {placeholder} ")
        else:
            math_el.extract()

    def _extract_formula(self, math_el: Tag) -> str | None:
        annotation = math_el.find("annotation", attrs={"encoding": "application/x-tex"})
        if annotation and annotation.string:
            return annotation.string.strip()
        alttext = math_el.get("alttext", "")
        if alttext:
            return alttext.strip()
        return None

    def _extract_display_formulas(self, table: Tag) -> list[str]:
        math_els = table.find_all("math", display="block")
        if not math_els:
            math_els = table.find_all("math")

        formulas = []
        for math_el in math_els:
            formula = self._extract_formula(math_el)
            if formula:
                formulas.append(formula)
        return formulas

    def _process_arxiv_figure(self, figure: Tag) -> str:
        img_tag = figure.find("img")
        img_url = ""
        if img_tag:
            src = img_tag.get("src")
            if isinstance(src, str):
                img_url = src

        caption_tag = figure.find("figcaption")
        caption_text = ""
        if caption_tag:
            caption_text = clean_html_entities(caption_tag.get_text(" ", strip=True))
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
