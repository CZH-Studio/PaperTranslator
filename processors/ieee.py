import re
import json
import logging
from pathlib import Path
from bs4 import BeautifulSoup, Tag
from bs4.element import NavigableString

from processors.base import BaseProcessor
from engine import TranslationEngine
from utils.text_utils import clean_html_entities, normalize_whitespace, clean_math_formula

logger = logging.getLogger(__name__)


class IEEEXploreProcessor(BaseProcessor):
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
        title_tag = self.soup.select_one("title")
        if title_tag:
            raw_title = title_tag.get_text(strip=True)
            self._title = raw_title.split("|")[0].strip()

        meta_title = self.soup.select_one("meta[property='og:title']")
        if meta_title:
            _title = meta_title.get("content", "")
            if not isinstance(_title, str):
                self._title = ""
            else:
                self._title = _title.strip() or self._title

        self._authors = self._extract_authors_from_js()

        meta_doi = self.soup.select_one("meta[name='citation_doi']")
        if meta_doi:
            self._doi = str(meta_doi.get("content", "")).strip()

        meta_desc = self.soup.select_one("meta[name='citation_conference']")
        meta_date = self.soup.select_one("meta[name='citation_publication_date']")
        if meta_desc:
            self._journal_info = str(meta_desc.get("content", "")).strip()
            if meta_date:
                self._journal_info += ", " + str(meta_date.get("content", "")).strip()

        if not self._journal_info:
            self._journal_info = "IEEE Xplore"

    def _extract_authors_from_js(self) -> str:
        for script in self.soup.find_all("script"):
            text = script.string
            if not text or "xplGlobal.document.metadata" not in text:
                continue

            authors_str = self._extract_json_array(text, "authors")
            if authors_str:
                try:
                    data = json.loads(f'{{"authors":{authors_str}}}')
                    names = [
                        a["name"].strip()
                        for a in data.get("authors", [])
                        if isinstance(a, dict) and a.get("name")
                    ]
                    return ", ".join(names)
                except json.JSONDecodeError:
                    pass

            idx = text.find('"authors"')
            if idx > 0:
                nearby = text[idx : idx + 2000]
                names = re.findall(r'"name"\s*:\s*"([^"]+)"', nearby)
                seen = set()
                result = []
                for n in names:
                    if n not in seen and len(n) > 2:
                        seen.add(n)
                        result.append(n)
                        if len(result) >= 10:
                            break
                return ", ".join(result)

        return ""

    def _extract_json_array(self, text: str, field_name: str) -> str | None:
        idx = text.find(f'"{field_name}"')
        if idx < 0:
            return None

        after_field = text[idx + len(field_name) + 2 :]
        after_field = after_field.lstrip()
        if not after_field.startswith(":"):
            return None
        after_field = after_field[1:].lstrip()
        if not after_field.startswith("["):
            return None

        bracket_count = 0
        for i, ch in enumerate(after_field):
            if ch == "[":
                bracket_count += 1
            elif ch == "]":
                bracket_count -= 1
                if bracket_count == 0:
                    return after_field[: i + 1]

        return None

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
        if self._doi:
            md_parts.append(f"**DOI**: {self._doi}\n\n")
        md_parts.append("---\n\n")
        paper_parts.append("---\n\n")

        body_wrapper = self.soup.find("div", id="BodyWrapper")
        if not body_wrapper:
            logger.warning("Cannot find BodyWrapper")
            self._write_paper("".join(paper_parts))
            return "".join(md_parts)

        article = body_wrapper.find("div", id="article")
        if not article:
            logger.warning("Cannot find article div in BodyWrapper")
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
                elif pt.startswith("<equation>"):
                    eq = pt[10:-11]
                    eq = normalize_whitespace(eq)
                    eq_block = f"$$\n{eq}\n$$\n\n"
                    md_parts.append(eq_block)
                    paper_parts.append(eq_block)
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

            classes = child.get("class") or []

            if "section" in classes:
                self._process_ieee_section(
                    child, paragraphs, plain_texts, md_parts, paper_parts, flush
                )
            elif "section_2" in classes:
                self._process_ieee_subsection(
                    child, paragraphs, plain_texts, md_parts, paper_parts, flush
                )
            elif "figure" in classes:
                flush()
                fig_html = self._process_ieee_figure(child)
                plain_texts.append(fig_html)
            elif (
                child.name == "h3"
                and "ACKNOWLEDGMENT" in child.get_text(strip=True).upper()
            ):
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

    def _process_ieee_section(
        self, section: Tag, paragraphs, plain_texts, md_parts, paper_parts, flush_fn
    ):
        header = section.find("div", class_="header")
        h2 = section.find("h2")
        if h2:
            heading_text = clean_html_entities(h2.get_text(strip=True))
            heading_text = normalize_whitespace(heading_text)
            if heading_text:
                flush_fn()
                self._current_heading = heading_text
                translated_heading = self._translate(heading_text)
                md_parts.append(f"## {translated_heading}\n\n")
                paper_parts.append(f"## {heading_text}\n\n")

        self._process_section_children(
            section, paragraphs, plain_texts, md_parts, paper_parts, flush_fn
        )

    def _process_ieee_subsection(
        self, subsection: Tag, paragraphs, plain_texts, md_parts, paper_parts, flush_fn
    ):
        h3 = subsection.find("h3")
        if h3:
            heading_text = clean_html_entities(h3.get_text(strip=True))
            heading_text = normalize_whitespace(heading_text)
            if heading_text:
                flush_fn()
                self._current_heading = heading_text
                translated_heading = self._translate(heading_text)
                md_parts.append(f"### {translated_heading}\n\n")
                paper_parts.append(f"### {heading_text}\n\n")

        self._process_section_children(
            subsection, paragraphs, plain_texts, md_parts, paper_parts, flush_fn
        )

    def _process_section_children(
        self,
        parent: Tag,
        paragraphs,
        plain_texts,
        md_parts,
        paper_parts,
        flush_fn,
        depth: int = 0,
    ):
        if depth > 2:
            return
        for child in parent.children:
            if isinstance(child, NavigableString):
                continue
            if not isinstance(child, Tag):
                continue

            classes = child.get("class") or []

            if child.name == "p":
                self._process_ieee_paragraph(child, paragraphs, plain_texts)
            elif "section_2" in classes and depth == 0:
                self._process_ieee_subsection(
                    child, paragraphs, plain_texts, md_parts, paper_parts, flush_fn
                )
            elif "figure" in classes:
                flush_fn()
                fig_html = self._process_ieee_figure(child)
                plain_texts.append(fig_html)
            elif "algorithm" in classes:
                flush_fn()
            elif "header" in classes:
                continue
            elif "table" in classes or (
                child.name == "div"
                and child.get("id")
                and "table" in str(child.get("id", ""))
            ):
                flush_fn()
                caption = child.find("div", class_="table-caption")
                if caption:
                    caption_text = clean_html_entities(caption.get_text(strip=True))
                    caption_text = normalize_whitespace(caption_text)
                    if caption_text:
                        translated_cap = self._translate(caption_text)
                        md_parts.append(f"<center>{translated_cap}</center>\n\n")
            elif child.name in ("ol", "ul"):
                for item in child.find_all("p"):
                    if item:
                        self._process_ieee_paragraph(item, paragraphs, plain_texts)

    def _process_ieee_paragraph(
        self, p: Tag, paragraphs: list[str], plain_texts: list[str]
    ):
        self._replace_inline_formulas(p)

        text = clean_html_entities(p.get_text(" ", strip=True))
        text = normalize_whitespace(text)
        if text:
            plain_texts.append(f"<p>{text}</p>")
            paragraphs.append(text)

    def _replace_inline_formulas(self, element: Tag):
        for inline_formula in element.find_all("inline-formula"):
            script = inline_formula.find("script", attrs={"type": "math/tex"})
            if script and script.string:
                formula = clean_math_formula(script.string)
                placeholder = self._make_placeholder("MATH", f"${formula}$")
                inline_formula.replace_with(f" {placeholder} ")
            else:
                inline_formula.extract()

    def _process_ieee_figure(self, figure: Tag) -> str:
        img_tag = figure.find("img", class_="document-ft-image")
        img_url = ""
        if img_tag:
            src = img_tag.get("src")
            if isinstance(src, str):
                img_url = src

        caption_div = figure.find("div", class_="figcaption")
        caption_text = ""
        if caption_div:
            caption_bold = caption_div.find("b", class_="title")
            fig_parts = []
            if caption_bold:
                fig_parts.append(caption_bold.get_text(strip=True))
            fig_tag = caption_div.find("fig")
            if fig_tag:
                fig_p = fig_tag.find("p")
                if fig_p:
                    fig_parts.append(fig_p.get_text(strip=True))
            caption_text = " ".join(fig_parts)
            caption_text = clean_html_entities(caption_text)
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
        return "\n".join(parts)
