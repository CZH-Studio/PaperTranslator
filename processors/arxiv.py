import html as html_module
import logging
from pathlib import Path
from bs4 import BeautifulSoup, Tag
from bs4.element import NavigableString

from processors.base import BaseProcessor
from engine import TranslationEngine
from utils.text_utils import clean_html_entities, normalize_whitespace, clean_math_formula

logger = logging.getLogger(__name__)


SECTION_HEADING_LEVELS = {
    "ltx_section": 2,
    "ltx_subsection": 3,
    "ltx_subsubsection": 4,
    "ltx_paragraph": 5,
}


class ArxivProcessor(BaseProcessor):
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
        for author_el in self.soup.select(
            "span.ltx_creator.ltx_role_author span.ltx_personname"
        ):
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
            if not plain_texts:
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
                elif pt.startswith("<table"):
                    md_parts.append(f"{pt}\n\n")
                    paper_parts.append(f"{pt}\n\n")
                elif pt.startswith("<pre"):
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
                sec_class = child.get("class", None)
                if sec_class is None:
                    continue

                if sec_id == "bib" or "ltx_bibliography" in sec_class:
                    break
                if "ltx_appendix" in sec_class:
                    break

                self._process_arxiv_section(
                    child, md_parts, paper_parts, paragraphs, plain_texts, flush
                )

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
            translated = self._translate(abstract_text, context="摘要")
            md_parts.append(f"## 摘要\n\n{translated}\n\n")
            paper_parts.append(f"## Abstract\n\n{abstract_text}\n\n")

    def _process_arxiv_section(
        self,
        section: Tag,
        md_parts: list[str],
        paper_parts: list[str],
        paragraphs: list[str],
        plain_texts: list[str],
        flush_fn,
        depth: int = 0,
    ):
        level = self._section_level(section)
        if level is None or depth > 8:
            return

        heading_text = self._section_heading_text(section)
        if heading_text:
            flush_fn()
            self._current_heading = heading_text
            translated_heading = self._translate(heading_text)
            prefix = "#" * level
            md_parts.append(f"{prefix} {translated_heading}\n\n")
            paper_parts.append(f"{prefix} {heading_text}\n\n")

        self._process_section_children(
            section, md_parts, paper_parts, paragraphs, plain_texts, flush_fn, depth
        )

    def _section_level(self, section: Tag) -> int | None:
        classes = section.get("class") or []
        for class_name, level in SECTION_HEADING_LEVELS.items():
            if class_name in classes:
                return level
        return None

    def _section_heading_text(self, section: Tag) -> str:
        heading = section.find(["h2", "h3", "h4", "h5", "h6"], recursive=False)
        if not heading:
            return ""
        text = clean_html_entities(heading.get_text(" ", strip=True))
        return normalize_whitespace(text)

    def _process_section_children(
        self,
        parent: Tag,
        md_parts: list[str],
        paper_parts: list[str],
        paragraphs: list[str],
        plain_texts: list[str],
        flush_fn,
        depth: int = 0,
    ):
        for child in parent.children:
            if not isinstance(child, Tag):
                continue

            if child.name in ("h1", "h2", "h3", "h4", "h5", "h6"):
                continue

            child_classes = child.get("class") or []

            if child.name == "section":
                self._process_arxiv_section(
                    child,
                    md_parts,
                    paper_parts,
                    paragraphs,
                    plain_texts,
                    flush_fn,
                    depth + 1,
                )
                continue

            if child.name == "div" and "ltx_para" in child_classes:
                flush_fn()
                self._process_para_container(child, paragraphs, plain_texts)
                continue

            if child.name == "figure":
                if "ltx_figure" in child_classes:
                    flush_fn()
                    fig_html = self._process_arxiv_figure(child)
                    if fig_html:
                        plain_texts.append(fig_html)
                    continue
                if "ltx_table" in child_classes:
                    flush_fn()
                    table_html = self._process_arxiv_table(child)
                    if table_html:
                        plain_texts.append(table_html)
                    continue

            if child.name == "table" and (
                "ltx_equation" in child_classes
                or "ltx_equationgroup" in child_classes
            ):
                flush_fn()
                for formula in self._extract_display_formulas(child):
                    plain_texts.append(f"<math_block>{formula}</math_block>")
                continue

            if child.name in ("ul", "ol") and any(
                c in child_classes for c in ("ltx_itemize", "ltx_enumerate")
            ):
                flush_fn()
                self._process_list(child, paragraphs, plain_texts)

    def _process_para_container(
        self,
        container: Tag,
        paragraphs: list[str],
        plain_texts: list[str],
    ):
        for child in container.children:
            if not isinstance(child, Tag):
                continue
            if child.name == "p":
                self._process_arxiv_paragraph(child, paragraphs, plain_texts)
            elif child.name in ("ul", "ol"):
                self._process_list(child, paragraphs, plain_texts)
            elif child.name == "pre":
                text = clean_html_entities(child.get_text(" ", strip=True))
                text = normalize_whitespace(text)
                if text:
                    plain_texts.append(f"<pre>{text}</pre>")

    def _process_list(
        self,
        list_tag: Tag,
        paragraphs: list[str],
        plain_texts: list[str],
    ):
        ordered = list_tag.name == "ol"
        for index, item in enumerate(list_tag.find_all("li", recursive=False), 1):
            item_texts = [
                self._extract_paragraph_text(p)
                for p in item.find_all("p")
            ]
            item_texts = [text for text in item_texts if text]
            if not item_texts:
                fallback = clean_html_entities(item.get_text(" ", strip=True))
                fallback = normalize_whitespace(fallback)
                if fallback:
                    item_texts = [fallback]
            if not item_texts:
                continue

            prefix = f"{index}. " if ordered else "- "
            text = prefix + " ".join(item_texts)
            plain_texts.append(f"<p>{text}</p>")
            paragraphs.append(text)

    def _extract_paragraph_text(self, p: Tag) -> str:
        for math_el in p.find_all("math", display="inline"):
            self._replace_inline_math(math_el)

        text = clean_html_entities(p.get_text(" ", strip=True))
        return normalize_whitespace(text)

    def _process_arxiv_paragraph(
        self, p: Tag, paragraphs: list[str], plain_texts: list[str]
    ):
        text = self._extract_paragraph_text(p)
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
            return clean_math_formula(annotation.string)
        alttext = math_el.get("alttext", "")
        if alttext and isinstance(alttext, str):
            return clean_math_formula(alttext)
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

    def _process_arxiv_table(self, figure: Tag) -> str:
        parts = []

        caption = figure.find("figcaption")
        if caption:
            caption_text = clean_html_entities(caption.get_text(" ", strip=True))
            caption_text = normalize_whitespace(caption_text)
            if caption_text:
                translated_cap = self._translate(caption_text)
                parts.append(f"<center>{translated_cap}</center>")

        table = figure.find("table")
        if table:
            table_html = self._render_table_html(table)
            if table_html:
                parts.append(table_html)

        return "\n".join(parts)

    def _render_table_html(self, table: Tag) -> str:
        rows = []
        for row in table.find_all("tr"):
            if row.find_parent("table") is not table:
                continue

            cells = []
            for cell in row.find_all(["td", "th"], recursive=False):
                for math_el in cell.find_all("math"):
                    self._replace_inline_math(math_el)

                text = clean_html_entities(cell.get_text(" ", strip=True))
                text = normalize_whitespace(text)
                text = html_module.escape(text, quote=False)

                attrs = []
                for attr in ("colspan", "rowspan"):
                    value = cell.get(attr)
                    if value:
                        attrs.append(
                            f' {attr}="{html_module.escape(str(value), quote=True)}"'
                        )

                tag = "th" if cell.name == "th" else "td"
                cells.append(f"<{tag}{''.join(attrs)}>{text}</{tag}>")

            if cells:
                rows.append("<tr>" + "".join(cells) + "</tr>")

        if not rows:
            return ""
        return "<table>\n<tbody>\n" + "\n".join(rows) + "\n</tbody>\n</table>"

    def _process_arxiv_figure(self, figure: Tag) -> str:
        media_kind, media_value = self._figure_media(figure)

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
        if media_kind == "src" and media_value:
            parts.append(f"![{translated_caption or 'Figure'}]({media_value})")
        elif media_kind == "svg" and media_value:
            parts.append(media_value)
        if translated_caption:
            parts.append(f"<center>{translated_caption}</center>")

        if not parts:
            return ""
        return "<figure>" + "\n".join(parts) + "</figure>"

    def _figure_media(self, figure: Tag) -> tuple[str, str]:
        """Return ('src', url) for img/object media or ('svg', markup) for inline SVG."""
        for obj in figure.find_all("object"):
            data = obj.get("data")
            obj_type = str(obj.get("type", "")).lower()
            if not isinstance(data, str) or not data.strip():
                continue
            data = data.strip()
            clean_path = data.split("?", 1)[0].split("#", 1)[0].lower()
            if obj_type.startswith("image/") or clean_path.endswith(
                (".svg", ".png", ".jpg", ".jpeg", ".gif", ".webp")
            ):
                return "src", data

        img_tag = figure.find("img")
        if img_tag:
            src = img_tag.get("src")
            if isinstance(src, str) and src.strip():
                return "src", src.strip()

        svg = figure.find("svg")
        if svg:
            return "svg", str(svg)

        return "", ""
