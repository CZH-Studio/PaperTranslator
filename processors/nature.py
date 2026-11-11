import re
import logging
from pathlib import Path
from bs4 import BeautifulSoup, Tag
from bs4.element import NavigableString

from processors.base import BaseProcessor
from engine import TranslationEngine
from utils.text_utils import clean_html_entities, normalize_whitespace, clean_math_formula
from utils.file_utils import sanitize_filename

logger = logging.getLogger(__name__)


MATH_PLACEHOLDER_PREFIX = "MATHPLACEHOLDER"
FIGURE_PLACEHOLDER_PREFIX = "FIGPLACEHOLDER"
EQUATION_PLACEHOLDER_PREFIX = "EQPLACEHOLDER"


class NatureProcessor(BaseProcessor):
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
        self._placeholders: dict[str, str] = {}

    def _make_placeholder(self, prefix: str, content: str) -> str:
        idx = len(self._placeholders)
        key = f"[{prefix}_{idx}]"
        self._placeholders[key] = content
        return key

    def _extract_meta(self):
        title_tag = self.soup.select_one("h1.c-article-title")
        if title_tag:
            self._title = clean_html_entities(title_tag.get_text(strip=True))

        author_list = self.soup.select_one("ul.c-article-author-list")
        if author_list:
            author_items = author_list.select("li.c-article-author-list__item")
            authors = []
            for item in author_items:
                name_el = item.select_one("a[data-test='author-name']")
                if name_el:
                    name = name_el.get_text(strip=True)
                    name = re.sub(r'\s+', ' ', name).strip()
                    authors.append(name)
            self._authors = ", ".join(authors)

        info = self.soup.select_one("p.c-article-info-details")
        if info:
            self._journal_info = clean_html_entities(info.get_text(" ", strip=True))
            self._journal_info = normalize_whitespace(self._journal_info)

        doi_meta = self.soup.select_one("meta[name='citation_doi']")
        if doi_meta:
            self._doi = doi_meta.get("content", "")

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

        article_body = self.soup.select_one("div.c-article-body")
        if not article_body:
            raise ValueError("Cannot find article body in HTML")

        stop_sections = {"References", "Acknowledgements", "Author information",
                         "Ethics declarations", "Peer review", "Additional information",
                         "Supplementary information", "Rights and permissions",
                         "About this article"}

        paragraphs: list[str] = []
        plain_texts: list[str] = []

        def flush_paragraphs():
            nonlocal paragraphs, plain_texts
            if not paragraphs:
                return
            raw_paras = list(paragraphs)
            translated = self.engine.translate_paragraphs(paragraphs, context=f"{self._title} - {self._current_heading}")
            trans_idx = 0
            for pt in plain_texts:
                if pt.startswith("<p>"):
                    if trans_idx < len(translated):
                        md_parts.append(f"{translated[trans_idx]}\n\n")
                        if trans_idx < len(raw_paras):
                            paper_parts.append(f"{raw_paras[trans_idx]}\n\n")
                        trans_idx += 1
                elif pt.startswith("<h3>"):
                    if trans_idx < len(translated):
                        md_parts.append(f"### {translated[trans_idx]}\n\n")
                        if trans_idx < len(raw_paras):
                            paper_parts.append(f"### {raw_paras[trans_idx]}\n\n")
                        trans_idx += 1
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

        for section in article_body.find_all("section", recursive=False):
            data_title = section.get("data-title", "")
            aria_label = section.get("aria-labelledby", "")

            if data_title == "Inline Recommendations":
                continue
            if data_title in stop_sections or aria_label in ("Bib1", "author-information"):
                break

            if data_title:
                self._process_section(section, md_parts, paper_parts,
                                      paragraphs, plain_texts, flush_paragraphs)

        main_content = article_body.select_one("div.main-content")
        if main_content:
            for section in main_content.find_all("section", recursive=False):
                self._process_section(section, md_parts, paper_parts,
                                      paragraphs, plain_texts, flush_paragraphs)

        flush_paragraphs()

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

    def _process_section(self, section: Tag, md_parts: list[str], paper_parts: list[str],
                          paragraphs: list[str], plain_texts: list[str], flush_paragraphs):
        section_title_tag = section.select_one(
            ".c-article-section__title, h2[data-test='article-section-heading']"
        )
        section_title = ""
        if section_title_tag:
            section_title = clean_html_entities(section_title_tag.get_text(strip=True))
            section_title = normalize_whitespace(section_title)

        if section_title:
            flush_paragraphs()
            self._current_heading = section_title
            translated_title = self._translate(section_title)
            md_parts.append(f"## {translated_title}\n\n")
            paper_parts.append(f"## {section_title}\n\n")
        else:
            md_parts.append("## \n\n")
            paper_parts.append("## \n\n")

        content_div = section.select_one(".c-article-section__content")
        if not content_div:
            return

        for child in content_div.children:
            if isinstance(child, NavigableString):
                continue

            if not isinstance(child, Tag):
                continue

            child_classes = child.get("class")
            is_h3 = (
                child.name == "h3"
                or (isinstance(child_classes, list) and "c-article__sub-heading" in child_classes)
            )

            if is_h3:
                flush_paragraphs()
                h3_text = clean_html_entities(child.get_text(strip=True))
                h3_text = normalize_whitespace(h3_text)
                if h3_text:
                    plain_texts.append(f"<h3>{h3_text}</h3>")
                    paragraphs.append(h3_text)
                continue

            if child.name == "p":
                self._process_paragraph(child, paragraphs, plain_texts)
            elif self._is_figure(child):
                flush_paragraphs()
                fig_html = self._process_figure(child)
                plain_texts.append(fig_html)
            elif self._is_equation(child):
                flush_paragraphs()
                eq_text = self._process_equation(child)
                plain_texts.append(eq_text)
            elif self._is_equation_inline(child):
                pass
            elif child.name == "div" and child.get("class") and "c-article-section__figure" not in (child.get("class") or []):
                for sub_child in child.children:
                    if isinstance(sub_child, Tag):
                        if sub_child.name == "p":
                            self._process_paragraph(sub_child, paragraphs, plain_texts)
                        elif self._is_equation(sub_child):
                            flush_paragraphs()
                            eq_text = self._process_equation(sub_child)
                            plain_texts.append(eq_text)

    def _process_paragraph(self, p: Tag, paragraphs: list[str], plain_texts: list[str]):
        for eq_span in p.select("span.mathjax-tex"):
            eq_content = eq_span.get_text(strip=True)
            eq_content = clean_math_formula(eq_content)
            placeholder = self._make_placeholder("MATH", f"${eq_content}$")
            eq_span.replace_with(f" {placeholder} ")

        text = clean_html_entities(p.get_text(" ", strip=True))
        text = normalize_whitespace(text)
        if text:
            plain_texts.append(f"<p>{text}</p>")
            paragraphs.append(text)

    def _is_figure(self, element: Tag) -> bool:
        classes = element.get("class") or []
        return "c-article-section__figure" in classes and "js-c-reading-companion-figures-item" in classes

    def _is_equation(self, element: Tag) -> bool:
        classes = element.get("class") or []
        return "c-article-equation" in classes

    def _is_equation_inline(self, element: Tag) -> bool:
        return element.name == "span" and "mathjax-tex" in (element.get("class") or [])

    def _process_figure(self, figure: Tag) -> str:
        img_tag = figure.select_one("img")
        img_url = ""
        if img_tag:
            src = img_tag.get("src")
            if isinstance(src, str):
                img_url = src
                if img_url.startswith("//"):
                    img_url = "https:" + img_url

        caption_tag = figure.select_one("div.c-article-section__figure-description")
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
            parts.append(f"![{translated_caption or 'Figure'}]({img_url})\n")
        if translated_caption:
            parts.append(f"<center>{translated_caption}</center>")
        return "<figure>" + "\n".join(parts) + "</figure>"

    def _process_equation(self, eq_div: Tag) -> str:
        math_span = eq_div.select_one("span.mathjax-tex")
        if math_span:
            eq_content = math_span.get_text(strip=True)
            eq_content = clean_math_formula(eq_content)
            eq_content = normalize_whitespace(eq_content)
            return f"<equation>{eq_content}</equation>"
        return ""
