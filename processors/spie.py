"""
SPIE Digital Library (spiedigitallibrary.org) processor.
"""
import re
import logging
from pathlib import Path
from bs4 import BeautifulSoup, Tag
from bs4.element import NavigableString

from processors.base import BaseProcessor
from engine import TranslationEngine
from utils.text_utils import clean_html_entities, normalize_whitespace, clean_math_formula

logger = logging.getLogger(__name__)

MATH_PLACEHOLDER_PREFIX = "MATHPLACEHOLDER"
STOP_HEADINGS = {
    "disclosures", "acknowledgments", "references", "citation",
    "code, data, and materials availability", "supplementary",
}


class SpieProcessor(BaseProcessor):
    def __init__(self, html_path: str, output_dir: Path,
                 engine: TranslationEngine | None = None, concurrency: int = 1):
        super().__init__(html_path, output_dir, concurrency=concurrency)
        self.engine = engine if engine is not None else TranslationEngine(concurrency=concurrency)
        with open(html_path, "r", encoding="utf-8", errors="ignore") as f:
            self.soup = BeautifulSoup(f.read(), "lxml")
        self._title = ""
        self._authors = ""
        self._journal_info = ""
        self._doi = ""
        self._placeholders: dict[str, str] = {}

    # ── Helpers ──

    def _make_placeholder(self, prefix: str, content: str) -> str:
        idx = len(self._placeholders)
        key = f"[{prefix}_{idx}]"
        self._placeholders[key] = content
        return key

    def _translate(self, text: str, context: str = "") -> str:
        result = self.engine.translate(text, context=context)
        result = result.replace("[¶SEP¶]", "")
        result = result.replace("[¶SEP¶", "")
        result = result.replace("¶SEP¶]", "")
        return result.strip()

    # ── Metadata ──

    def _extract_meta(self):
        title_meta = self.soup.select_one('meta[name="citation_title"]')
        if title_meta:
            self._title = str(title_meta.get("content", "")).strip()

        if not self._title:
            h1 = self.soup.find("h1")
            if h1:
                self._title = clean_html_entities(h1.get_text(strip=True))

        authors = []
        for meta in self.soup.find_all("meta", attrs={"name": "citation_author"}):
            name = str(meta.get("content", "")).strip()
            if name:
                authors.append(name)
        self._authors = ", ".join(authors)

        journal_meta = self.soup.select_one('meta[name="citation_journal_title"]')
        self._journal_info = str(journal_meta.get("content", "")).strip() if journal_meta else "SPIE Digital Library"

        doi_meta = self.soup.select_one('meta[name="citation_doi"]')
        if doi_meta:
            self._doi = str(doi_meta.get("content", "")).strip()

    def extract_title(self) -> str:
        if not self._title:
            self._extract_meta()
        return self._title

    # ── Main pipeline ──

    def extract_and_translate(self) -> str:
        self._extract_meta()
        self._placeholders = {}

        md_parts: list[str] = []
        paper_parts: list[str] = []

        # Title block
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
                elif pt.startswith("<h3>"):
                    if t_idx < len(translated):
                        md_parts.append(f"### {translated[t_idx]}\n\n")
                        if t_idx < len(raw_batch):
                            paper_parts.append(f"### {raw_batch[t_idx]}\n\n")
                        t_idx += 1
                elif pt.startswith("<h4>"):
                    if t_idx < len(translated):
                        md_parts.append(f"#### {translated[t_idx]}\n\n")
                        if t_idx < len(raw_batch):
                            paper_parts.append(f"#### {raw_batch[t_idx]}\n\n")
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

        # Find the React container
        react = self.soup.select_one("div#detailPageReactContainer")
        if not react:
            react = self.soup.select_one("main#main-content")
        if not react:
            raise ValueError("Cannot find SPIE article container")

        # ── Abstract ──
        self._extract_abstract(react, md_parts, paper_parts)

        # ── Sections ──
        full_text = react.select_one('section[class*="fullTextSection"]')
        if not full_text:
            full_text = react

        body_div = full_text.select_one("div.body") if full_text else None
        if body_div:
            start_sections = body_div.find_all("div", class_="section", recursive=False)
        else:
            start_sections = full_text.find_all("div", class_="section", recursive=False)

        if start_sections:
            self._process_section_tree(start_sections, md_parts, paper_parts,
                                       paragraphs, plain_texts, flush)

        flush()

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

    # ── Abstract ──

    def _extract_abstract(self, container: Tag, md_parts: list[str], paper_parts: list[str]):
        abs_h2 = container.find("h2", string=lambda s: s and "abstract" in s.strip().lower())
        if not abs_h2:
            return

        abs_parent = abs_h2.parent
        if not abs_parent:
            return

        abs_content = abs_parent.select_one('div[class*="abstractContent"]')
        if not abs_content:
            return

        paragraphs: list[str] = []
        for p in abs_content.find_all("p"):
            text = clean_html_entities(p.get_text(" ", strip=True))
            text = normalize_whitespace(text)
            if text:
                paragraphs.append(text)

        if not paragraphs:
            return

        translated = self.engine.translate_paragraphs(paragraphs, context=f"{self._title} - Abstract")

        abs_heading = clean_html_entities(abs_h2.get_text(strip=True))
        translated_heading = self._translate(abs_heading)
        md_parts.append(f"## {translated_heading}\n\n")
        paper_parts.append(f"## {abs_heading}\n\n")

        for i, tp in enumerate(translated):
            tp = tp.replace("[¶SEP¶]", "").strip()
            if tp:
                md_parts.append(f"{tp}\n\n")
                if i < len(paragraphs):
                    paper_parts.append(f"{paragraphs[i]}\n\n")

    # ── Section tree ──

    def _process_section_tree(self, sections: list[Tag], md_parts: list[str],
                               paper_parts: list[str], paragraphs: list[str],
                               plain_texts: list[str], flush):
        for section in sections:
            classes = section.get("class") or []
            if "back-section" in classes:
                return  # stop at back matter

            heading_el = section.find(["h2", "h3", "h4"], class_="section-title",
                                      recursive=False)
            heading_text = ""
            heading_level = 0
            if heading_el:
                heading_text = clean_html_entities(heading_el.get_text(strip=True))
                heading_text = normalize_whitespace(heading_text)

                lower = heading_text.lower().strip()
                if any(stop in lower for stop in STOP_HEADINGS):
                    return

                heading_level = int(heading_el.name[1])  # 2, 3, or 4

                flush()
                self._current_heading = heading_text
                translated = self._translate(heading_text)
                prefix = "#" * heading_level
                md_parts.append(f"{prefix} {translated}\n\n")
                paper_parts.append(f"{prefix} {heading_text}\n\n")

            # Process direct children: paragraphs, figures, equations, nested sections
            for child in section.children:
                if isinstance(child, NavigableString):
                    continue
                if not isinstance(child, Tag):
                    continue

                if child.name == "p":
                    self._process_spie_paragraph(child, paragraphs, plain_texts)

                elif child.name == "div":
                    child_classes = child.get("class") or []

                    if "section" in child_classes:
                        self._process_section_tree([child], md_parts, paper_parts,
                                                   paragraphs, plain_texts, flush)

                    elif any("figureContainer" in c for c in child_classes):
                        flush()
                        fig_html = self._process_spie_figure(child)
                        if fig_html:
                            plain_texts.append(fig_html)

                    elif any("MathJax_Display" in c for c in child_classes):
                        flush()
                        eq_text = self._process_display_math(child)
                        if eq_text:
                            plain_texts.append(eq_text)

                    else:
                        # Generic div: extract p children and nested sections
                        for sub in child.children:
                            if isinstance(sub, Tag):
                                sub_cls = sub.get("class") or []
                                if sub.name == "p":
                                    self._process_spie_paragraph(sub, paragraphs, plain_texts)
                                elif sub.name == "div" and "section" in sub_cls:
                                    self._process_section_tree([sub], md_parts, paper_parts,
                                                               paragraphs, plain_texts, flush)
                                elif any("figureContainer" in c for c in sub_cls):
                                    flush()
                                    fig_html = self._process_spie_figure(sub)
                                    if fig_html:
                                        plain_texts.append(fig_html)

    # ── Paragraph ──

    def _process_spie_paragraph(self, p: Tag, paragraphs: list[str], plain_texts: list[str]):
        # Replace inline math with placeholders
        for mj in p.select("span.mathjax-tex"):
            eq = clean_math_formula(mj.get_text(strip=True))
            ph = self._make_placeholder(MATH_PLACEHOLDER_PREFIX, f"${eq}$")
            mj.replace_with(f" {ph} ")

        text = clean_html_entities(p.get_text(" ", strip=True))
        text = normalize_whitespace(text)
        if text:
            plain_texts.append(f"<p>{text}</p>")
            paragraphs.append(text)

    # ── Figure ──

    def _process_spie_figure(self, container: Tag) -> str | None:
        img = container.select_one("img")
        img_url = ""
        if img:
            src = img.get("src", "")
            if isinstance(src, str):
                img_url = src
                if img_url.startswith("//"):
                    img_url = "https:" + img_url

        caption_div = container.select_one('div[class*="figureCaption"]')
        caption = ""
        if caption_div:
            caption = clean_html_entities(caption_div.get_text(" ", strip=True))
            caption = normalize_whitespace(caption)

        if not img_url and not caption:
            return None

        translated_caption = self._translate(caption) if caption else ""

        parts = []
        if img_url:
            alt_text = translated_caption or (caption[:80] if caption else "Figure")
            parts.append(f"![{alt_text}]({img_url})")
        if translated_caption:
            parts.append(f"<center>{translated_caption}</center>")

        return "<figure>" + "\n".join(parts) + "</figure>"

    # ── Equations ──

    def _process_display_math(self, eq_div: Tag) -> str | None:
        mj = eq_div.select_one("span.mathjax-tex")
        if mj:
            eq = clean_math_formula(mj.get_text(strip=True))
            eq = normalize_whitespace(eq)
            return f"<equation>{eq}</equation>"
        return None
