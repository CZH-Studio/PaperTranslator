import logging
from pathlib import Path

from bs4 import BeautifulSoup, Tag
from bs4.element import NavigableString

from engine import TranslationEngine
from processors.base import BaseProcessor
from utils.text_utils import clean_html_entities, clean_math_formula, normalize_whitespace

logger = logging.getLogger(__name__)

STOP_HEADINGS = [
    "references",
    "data availability",
    "article and author information",
    "decision letter",
    "author response",
    "acknowledgements",
    "acknowledgments",
    "funding",
    "ethics",
    "copyright",
    "supplementary material",
    "supplementary files",
    "competing interests",
    "additional information",
    "additional files",
]


class ElifeProcessor(BaseProcessor):
    """Processor for eLife journal articles."""

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
        self._journal_info = "eLife"
        self._doi = ""
        self._placeholders: dict[str, str] = {}

    def _make_placeholder(self, prefix: str, content: str) -> str:
        idx = len(self._placeholders)
        key = f"[{prefix}_{idx}]"
        self._placeholders[key] = content
        return key

    def _extract_meta(self) -> None:
        # Title: dc.title or citation_title
        title_meta = (
            self.soup.select_one("meta[name='dc.title']")
            or self.soup.select_one("meta[name='citation_title']")
        )
        if title_meta:
            self._title = str(title_meta.get("content", "")).strip()

        # Authors: dc.contributor (multiple)
        author_tags = self.soup.select("meta[name='dc.contributor']")
        authors: list[str] = []
        seen: set[str] = set()
        for tag in author_tags:
            name = str(tag.get("content", "")).strip()
            if name and name not in seen:
                seen.add(name)
                authors.append(name)
        self._authors = ", ".join(authors)

        # DOI: dc.identifier starting with "doi:"
        identifier_tags = self.soup.select("meta[name='dc.identifier']")
        for tag in identifier_tags:
            content = str(tag.get("content", "")).strip()
            if content.startswith("doi:"):
                self._doi = content[4:]  # strip "doi:" prefix
                break

        # Journal info: construct from available meta
        journal_title_tag = self.soup.select_one("meta[name='citation_journal_title']")
        journal_title = ""
        if journal_title_tag:
            journal_title = str(journal_title_tag.get("content", "")).strip()
        if not journal_title:
            journal_title = "eLife"

        date_tag = self.soup.select_one("meta[name='citation_publication_date']")
        date_str = str(date_tag.get("content", "")).strip() if date_tag else ""

        volume_tag = self.soup.select_one("meta[name='citation_volume']")
        volume_str = str(volume_tag.get("content", "")).strip() if volume_tag else ""

        parts: list[str] = []
        if journal_title:
            parts.append(journal_title)
        if volume_str:
            parts.append(f"vol. {volume_str}")
        if date_str:
            parts.append(date_str)
        self._journal_info = ". ".join(parts) if parts else "eLife"

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

    def _replace_math(self, element: Tag) -> None:
        """Replace MathJax containers/mjx with placeholders."""
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

        for eq_span in element.select("span.mathjax-tex"):
            eq_content = eq_span.get_text(strip=True)
            eq_content = clean_math_formula(eq_content)
            placeholder = self._make_placeholder("MATH", f"${eq_content}$")
            eq_span.replace_with(f" {placeholder} ")

    def _process_paragraph(
        self, p: Tag, paragraphs: list[str], plain_texts: list[str]
    ) -> None:
        self._replace_math(p)
        text = clean_html_entities(p.get_text(" ", strip=True))
        text = normalize_whitespace(text)
        if text:
            plain_texts.append(f"<p>{text}</p>")
            paragraphs.append(text)

    def _process_elife_figure(self, figure: Tag) -> str:
        img_tag = figure.select_one("img")
        img_url = ""
        if img_tag:
            src = img_tag.get("src", "")
            if isinstance(src, str) and not src.startswith("data:"):
                img_url = src

        # Caption: h6.caption-text__heading + div.caption-text__body
        caption_parts: list[str] = []
        heading_tag = figure.select_one("h6.caption-text__heading")
        if heading_tag:
            heading_text = clean_html_entities(heading_tag.get_text(strip=True))
            heading_text = normalize_whitespace(heading_text)
            if heading_text:
                caption_parts.append(heading_text)
        body_tag = figure.select_one("div.caption-text__body")
        if body_tag:
            body_text = clean_html_entities(body_tag.get_text(" ", strip=True))
            body_text = normalize_whitespace(body_text)
            if body_text:
                caption_parts.append(body_text)

        caption_text = " ".join(caption_parts)
        translated_caption = self._translate(caption_text) if caption_text else ""

        parts: list[str] = []
        if img_url:
            parts.append(f"![{translated_caption or 'Figure'}]({img_url})")
        if translated_caption:
            parts.append(f"<center>{translated_caption}</center>")

        if not parts:
            return ""
        return "<figure>" + "\n".join(parts) + "</figure>"

    def _extract_section_body(
        self,
        body: Tag,
        paragraphs: list[str],
        plain_texts: list[str],
        flush,
        md_parts: list[str],
        paper_parts: list[str],
    ) -> None:
        """Recursively extract content from an article-section__body div.

        Handles paragraphs, nested <section class='article-section'> subsections,
        figures, and lists — the key eLife gotcha is the nested section structure.
        """
        for child in body.children:
            if isinstance(child, NavigableString):
                continue
            if not isinstance(child, Tag):
                continue

            if child.name == "p":
                self._process_paragraph(child, paragraphs, plain_texts)

            elif child.name == "section" and "article-section" in (
                child.get("class") or []
            ):
                # Nested subsection (subsections inside Results / Materials and methods)
                flush()
                heading_text = ""
                h3_tag = child.select_one("h3")
                if h3_tag:
                    heading_text = clean_html_entities(h3_tag.get_text(strip=True))
                    heading_text = normalize_whitespace(heading_text)
                if not heading_text:
                    toggle_link = child.select_one(
                        "a.article-section__toggle h2"
                    )
                    if toggle_link:
                        heading_text = clean_html_entities(
                            toggle_link.get_text(strip=True)
                        )
                        heading_text = normalize_whitespace(heading_text)

                if heading_text:
                    heading_lower = heading_text.lower()
                    if any(stop in heading_lower for stop in STOP_HEADINGS):
                        continue
                    self._current_heading = heading_text
                    translated = self._translate(heading_text)
                    md_parts.append(f"### {translated}\n\n")
                    paper_parts.append(f"### {heading_text}\n\n")

                sub_body = child.select_one("div.article-section__body")
                if sub_body:
                    self._extract_section_body(
                        sub_body, paragraphs, plain_texts, flush,
                        md_parts, paper_parts,
                    )

            elif child.name == "figure" and "captioned-asset" in (
                child.get("class") or []
            ):
                flush()
                fig_html = self._process_elife_figure(child)
                if fig_html:
                    plain_texts.append(fig_html)

            elif child.name in ("ol", "ul"):
                for li in child.find_all("li", recursive=False):
                    li_text = clean_html_entities(li.get_text(" ", strip=True))
                    li_text = normalize_whitespace(li_text)
                    if li_text:
                        paragraphs.append(li_text)
                        plain_texts.append(f"<p>{li_text}</p>")

    def _write_paper(self, paper_text: str) -> None:
        raw_path = self.output_dir / "paper.md"
        with open(raw_path, "w", encoding="utf-8") as f:
            f.write(paper_text)

    def extract_and_translate(self) -> str:
        self._extract_meta()
        self._placeholders = {}

        md_parts: list[str] = []
        paper_parts: list[str] = []

        # Header
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
                elif pt.startswith("<figure>"):
                    fig = pt[8:-9]
                    md_parts.append(f"{fig}\n\n")
                    paper_parts.append(f"{fig}\n\n")
                elif pt.startswith("<center>"):
                    md_parts.append(f"{pt}\n\n")
                    paper_parts.append(f"{pt}\n\n")
            paragraphs.clear()
            plain_texts.clear()

        # Abstract
        abstract_section = self.soup.select_one("section#abstract")
        if abstract_section:
            abstract_body = abstract_section.select_one("div.article-section__body")
            if abstract_body:
                abstract_paras = abstract_body.select("p.paragraph")
                abstract_texts: list[str] = []
                for p in abstract_paras:
                    txt = clean_html_entities(p.get_text(" ", strip=True))
                    txt = normalize_whitespace(txt)
                    if txt:
                        abstract_texts.append(txt)
                abstract_text = " ".join(abstract_texts)
                if abstract_text:
                    translated = self._translate(abstract_text, context="摘要")
                    md_parts.append(f"## 摘要\n\n{translated}\n\n")
                    paper_parts.append(f"## Abstract\n\n{abstract_text}\n\n")

        # Main content grid
        main_content = self.soup.select_one("div.main-content-grid")
        if not main_content:
            logger.warning("Cannot find div.main-content-grid")
            self._write_paper("".join(paper_parts))
            self.engine.shutdown()
            return "".join(md_parts)

        stop_ids = {"data", "references", "info", "metrics"}

        for section in main_content.find_all("section", recursive=False):
            section_id = section.get("id", "")

            # Skip abstract — already processed separately
            if section_id == "abstract":
                continue

            # Stop at back-matter sections by id
            if section_id in stop_ids:
                break

            # Extract heading from <a class="article-section__toggle"><h2>...</h2></a>
            heading_tag = section.select_one(
                "a.article-section__toggle h2"
            ) or section.select_one("h2")
            heading_text = ""
            if heading_tag:
                heading_text = clean_html_entities(heading_tag.get_text(strip=True))
                heading_text = normalize_whitespace(heading_text)

            # Stop on heading text match
            if heading_text:
                heading_lower = heading_text.lower()
                if any(stop in heading_lower for stop in STOP_HEADINGS):
                    break

            if heading_text:
                flush()
                self._current_heading = heading_text
                translated_heading = self._translate(heading_text)
                md_parts.append(f"## {translated_heading}\n\n")
                paper_parts.append(f"## {heading_text}\n\n")

            # Process section body content recursively
            section_body = section.select_one("div.article-section__body")
            if section_body:
                self._extract_section_body(
                    section_body, paragraphs, plain_texts, flush,
                    md_parts, paper_parts,
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
