import re
import logging
from pathlib import Path
from bs4 import BeautifulSoup, Tag
from bs4.element import NavigableString, PageElement

from processors.base import BaseProcessor
from engine import TranslationEngine
from utils.text_utils import clean_html_entities, normalize_whitespace

logger = logging.getLogger(__name__)

STOP_IDS = {"backmatter", "acknowledgments", "supplementary-materials", "bibliography"}

GREEK_TO_LATEX: dict[str, str] = {
    # Lowercase
    "\u03b1": "\\alpha",
    "\u03b2": "\\beta",
    "\u03b3": "\\gamma",
    "\u03b4": "\\delta",
    "\u03b5": "\\epsilon",
    "\u03b6": "\\zeta",
    "\u03b7": "\\eta",
    "\u03b8": "\\theta",
    "\u03b9": "\\iota",
    "\u03ba": "\\kappa",
    "\u03bb": "\\lambda",
    "\u03bc": "\\mu",
    "\u03bd": "\\nu",
    "\u03be": "\\xi",
    "\u03c0": "\\pi",
    "\u03c1": "\\rho",
    "\u03c3": "\\sigma",
    "\u03c4": "\\tau",
    "\u03c5": "\\upsilon",
    "\u03c6": "\\phi",
    "\u03c7": "\\chi",
    "\u03c8": "\\psi",
    "\u03c9": "\\omega",
    # Uppercase (only those that differ from Latin)
    "\u0393": "\\Gamma",
    "\u0394": "\\Delta",
    "\u0398": "\\Theta",
    "\u039b": "\\Lambda",
    "\u039e": "\\Xi",
    "\u03a0": "\\Pi",
    "\u03a3": "\\Sigma",
    "\u03a6": "\\Phi",
    "\u03a8": "\\Psi",
    "\u03a9": "\\Omega",
    # Variant forms
    "\u03d5": "\\phi",
    "\u03b5": "\\epsilon",
    "\u03f5": "\\epsilon",
    "\u03f4": "\\Theta",
    "\u03f0": "\\varkappa",
    "\u03d1": "\\vartheta",
    "\u03f1": "\\varrho",
    "\u03d6": "\\varpi",
    "\u03c2": "\\varsigma",
}

SYMBOL_TO_LATEX: dict[str, str] = {
    "\u221e": "\\infty",
    "\u2202": "\\partial",
    "\u00d7": "\\times",
    "\u2217": "*",
    "\u2192": "\\to",
    "\u2207": "\\nabla",
    "\u222b": "\\int",
    "\u220f": "\\prod",
    "\u2211": "\\sum",
    "\u2297": "\\otimes",
    "\u2295": "\\oplus",
    "\u2205": "\\emptyset",
    "\u2208": "\\in",
    "\u2209": "\\notin",
    "\u2282": "\\subset",
    "\u2286": "\\subseteq",
    "\u2283": "\\supset",
    "\u2287": "\\supseteq",
    "\u222a": "\\cup",
    "\u2229": "\\cap",
    "\u2227": "\\land",
    "\u2228": "\\lor",
    "\u00ac": "\\neg",
    "\u2200": "\\forall",
    "\u2203": "\\exists",
    "\u223c": "\\sim",
    "\u2248": "\\approx",
    "\u2260": "\\neq",
    "\u2264": "\\le",
    "\u2265": "\\ge",
    "\u226a": "\\ll",
    "\u226b": "\\gg",
    "\u22c5": "\\cdot",
    "\u2026": "\\dots",
    "\u22ef": "\\cdots",
    "\u22ee": "\\vdots",
    "\u22f1": "\\ddots",
    "\u27e8": "\\langle",
    "\u27e9": "\\rangle",
    "\u2016": "\\|",
    "\u2113": "\\ell",
    "\u2135": "\\aleph",
    "\u210f": "\\hbar",
    "\u211c": "\\Re",
    "\u2111": "\\Im",
    "\u2032": "'",
    "\u2033": "''",
}

OPERATOR_TO_LATEX: dict[str, str] = {
    "\u2061": "",  # FUNCTION APPLICATION (invisible)
    "\u00b1": "\\pm",
    "\u2213": "\\mp",
    "\u00b7": "\\cdot",
    "\u2218": "\\circ",
    "\u2219": "\\bullet",
    "\u2025": "\\dots",
    "\u00f7": "\\div",
}

ACCENT_MAP: dict[str, str] = {
    "^": "\\hat",
    "\u005e": "\\hat",
    "~": "\\tilde",
    "\u007e": "\\tilde",
    "\u00af": "\\bar",
    "\u02d9": "\\dot",
    "\u00a8": "\\ddot",
    "\u20d7": "\\vec",
    "\u2192": "\\vec",
    "\u005f": "\\bar",
    "\u203e": "\\bar",
}


def _mathml_to_latex(elem: PageElement) -> str:
    if isinstance(elem, NavigableString):
        return str(elem).strip()

    if not isinstance(elem, Tag):
        return ""

    tag = elem.name

    if tag == "mi":
        text = elem.get_text(strip=True)
        if not text:
            return ""
        variant = elem.get("mathvariant", "")
        if variant == "script":
            char = text[0] if len(text) == 1 else text
            return f"\\mathcal{{{char}}}"
        elif variant == "bold":
            char = text[0] if len(text) == 1 else text
            return f"\\mathbf{{{char}}}"
        elif variant == "normal":
            return f"\\mathrm{{{text}}}"
        else:
            result_parts = []
            for ch in text:
                result_parts.append(GREEK_TO_LATEX.get(ch, ch))
            return "".join(result_parts)

    elif tag == "mn":
        return elem.get_text(strip=True)

    elif tag == "mo":
        text = elem.get_text(strip=True)
        if not text:
            return ""
        result = []
        for ch in text:
            result.append(OPERATOR_TO_LATEX.get(ch, SYMBOL_TO_LATEX.get(ch, ch)))
        return "".join(result)

    elif tag == "mtext":
        return f"\\text{{{elem.get_text(strip=True)}}}"

    elif tag == "mrow":
        parts = []
        for child in elem.children:
            part = _mathml_to_latex(child)
            parts.append(part)
        return "".join(parts)

    elif tag == "msub":
        mathml = elem.find(recursive=False)
        if mathml is None:
            return ""
        base = _mathml_to_latex(mathml)
        sub = ""
        children = list(elem.children)
        for i, child in enumerate(children):
            if i == 0:
                continue
            result = _mathml_to_latex(child)
            if result:
                sub += result
        if len(sub) > 1:
            return f"{{{base}}}_{{{sub}}}"
        return f"{base}_{{{sub}}}"

    elif tag == "msup":
        mathml = elem.find(recursive=False)
        if mathml is None:
            return ""
        base = _mathml_to_latex(mathml)
        sup = ""
        children = list(elem.children)
        for i, child in enumerate(children):
            if i == 0:
                continue
            result = _mathml_to_latex(child)
            if result:
                sup += result
        if len(sup) > 1:
            return f"{{{base}}}^{{{sup}}}"
        return f"{base}^{{{sup}}}"

    elif tag == "msubsup":
        parts: list[str] = []
        for child in elem.children:
            result = _mathml_to_latex(child)
            if result:
                parts.append(result)
        if len(parts) == 3:
            base, sub, sup = parts
            base_wrap = f"{{{base}}}" if len(base) > 1 and base[0] != "\\" else base
            sub_wrap = f"{{{sub}}}" if len(sub) > 1 else sub
            sup_wrap = f"{{{sup}}}" if len(sup) > 1 else sup
            return f"{base_wrap}_{sub_wrap}^{sup_wrap}"
        return "".join(parts)

    elif tag == "mfrac":
        children_list = list(elem.children)
        num = ""
        den = ""
        count = 0
        for child in children_list:
            result = _mathml_to_latex(child)
            if result:
                if count == 0:
                    num = result
                elif count == 1:
                    den = result
                count += 1
        return f"\\frac{{{num}}}{{{den}}}"

    elif tag == "msqrt":
        mathml = elem.find(recursive=False)
        if mathml is None:
            return ""
        inner = _mathml_to_latex(mathml)
        return f"\\sqrt{{{inner}}}"

    elif tag == "mroot":
        children_list = list(elem.children)
        base = ""
        root = ""
        count = 0
        for child in children_list:
            result = _mathml_to_latex(child)
            if result:
                if count == 0:
                    base = result
                elif count == 1:
                    root = result
                count += 1
        return f"\\sqrt[{root}]{{{base}}}"

    elif tag == "mover":
        children_list = list(elem.children)
        base = ""
        over = ""
        for i, child in enumerate(children_list):
            result = _mathml_to_latex(child)
            if result:
                if i == 0:
                    base = result
                else:
                    over += result
        accent = elem.get("accent", "false") == "true"
        if accent and over in ACCENT_MAP:
            return f"{ACCENT_MAP[over]}{{{base}}}"
        return f"\\overline{{{base}}}"

    elif tag == "munder":
        children_list = list(elem.children)
        base = ""
        under = ""
        for i, child in enumerate(children_list):
            result = _mathml_to_latex(child)
            if result:
                if i == 0:
                    base = result
                else:
                    under += result
        return f"{{{base}}}_{{{under}}}"

    elif tag == "munderover":
        children_list = list(elem.children)
        base = ""
        under = ""
        over = ""
        for i, child in enumerate(children_list):
            result = _mathml_to_latex(child)
            if result:
                if i == 0:
                    base = result
                elif i == 1:
                    under = result
                elif i == 2:
                    over = result
        return f"{base}_{{{under}}}^{{{over}}}"

    elif tag == "mstyle":
        parts = []
        for child in elem.children:
            parts.append(_mathml_to_latex(child))
        return "".join(parts)

    elif tag == "mfenced":
        parts = []
        for child in elem.children:
            parts.append(_mathml_to_latex(child))
        inner = "".join(parts)
        open_ch = elem.get("open", "(")
        close_ch = elem.get("close", ")")
        return f"\\left{open_ch}{inner}\\right{close_ch}"

    elif tag == "mtable":
        rows = []
        for mtr in elem.find_all("mtr", recursive=False):
            cells = []
            for mtd in mtr.find_all("mtd", recursive=False):
                cells.append(_mathml_to_latex(mtd))
            rows.append(" & ".join(cells))
        return "\\begin{array}{l}" + " \\\\ ".join(rows) + "\\end{array}"

    elif tag in ("mtd", "mtr", "mpadded", "mphantom", "menclose", "merror"):
        parts = []
        for child in elem.children:
            parts.append(_mathml_to_latex(child))
        return "".join(parts)

    elif tag == "math":
        parts = []
        for child in elem.children:
            parts.append(_mathml_to_latex(child))
        return "".join(parts)

    return elem.get_text(strip=True)


class PnasProcessor(BaseProcessor):
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
            h1 = self.soup.select_one("h1[property='name']")
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

        issue_meta = self.soup.select_one("meta[name='citation_issue']")
        issue = ""
        if issue_meta:
            issue = str(issue_meta.get("content", "")).strip()

        first_page = ""
        last_page = ""
        fp_meta = self.soup.select_one("meta[name='citation_firstpage']")
        lp_meta = self.soup.select_one("meta[name='citation_lastpage']")
        if fp_meta:
            first_page = str(fp_meta.get("content", "")).strip()
        if lp_meta:
            last_page = str(lp_meta.get("content", "")).strip()

        parts = [journal_title]
        if volume:
            parts[-1] = (
                f"{journal_title} vol. {volume}" if journal_title else f"Vol. {volume}"
            )
        if issue:
            parts[-1] = f"{parts[-1]}, no. {issue}"
        if date:
            parts.append(date)
        if first_page and last_page:
            parts.append(f"pp. {first_page}-{last_page}")
        elif first_page:
            parts.append(f"p. {first_page}")

        self._journal_info = ". ".join(parts) if parts else "PNAS"

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

        self._process_abstracts(md_parts, paper_parts)

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
                elif pt.startswith("<center>"):
                    md_parts.append(f"{pt}\n\n")
                    paper_parts.append(f"{pt}\n\n")
                elif pt.startswith("<heading>"):
                    heading_text = pt[9:-10]
                    translated_heading = self._translate(heading_text)
                    md_parts.append(f"## {translated_heading}\n\n")
                    paper_parts.append(f"## {heading_text}\n\n")
            paragraphs.clear()
            plain_texts.clear()

        bodymatter = self.soup.select_one("section#bodymatter")
        if bodymatter:
            core_container = bodymatter.select_one("div.core-container")
            if core_container:
                self._process_body_children(
                    core_container,
                    md_parts,
                    paper_parts,
                    paragraphs,
                    plain_texts,
                    flush,
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

    def _process_abstracts(self, md_parts: list[str], paper_parts: list[str]):
        abstracts_div = self.soup.select_one("div#abstracts")
        if not abstracts_div:
            return

        significance = abstracts_div.select_one("section#executive-summary-abstract")
        if significance:
            para = significance.select_one("div[role='paragraph']")
            if para:
                text = clean_html_entities(para.get_text(" ", strip=True))
                text = normalize_whitespace(text)
                if text:
                    translated = self._translate(text, context="重要性")
                    md_parts.append(f"## 重要性\n\n{translated}\n\n")
                    paper_parts.append(f"## Significance\n\n{text}\n\n")

        abstract_section = abstracts_div.select_one("section#abstract")
        if abstract_section:
            paras = abstract_section.select("div[role='paragraph']")
            texts = []
            for p in paras:
                text = clean_html_entities(p.get_text(" ", strip=True))
                text = normalize_whitespace(text)
                if text:
                    texts.append(text)
            if texts:
                abstract_text = " ".join(texts)
                translated = self._translate(abstract_text, context="摘要")
                md_parts.append(f"## 摘要\n\n{translated}\n\n")
                paper_parts.append(f"## Abstract\n\n{abstract_text}\n\n")

    def _process_body_children(
        self,
        parent: Tag,
        md_parts: list[str],
        paper_parts: list[str],
        paragraphs: list[str],
        plain_texts: list[str],
        flush_fn,
        depth: int = 0,
    ):
        if depth > 3:
            return

        for child in parent.children:
            if isinstance(child, NavigableString):
                continue
            if not isinstance(child, Tag):
                continue

            if isinstance(child, Tag) and child.name == "section":
                sec_id = child.get("id", "")
                if sec_id in STOP_IDS:
                    break

                h2 = child.find("h2")
                if h2:
                    flush_fn()
                    heading_text = clean_html_entities(h2.get_text(strip=True))
                    heading_text = normalize_whitespace(heading_text)
                    if heading_text:
                        self._current_heading = heading_text
                        translated_heading = self._translate(heading_text)
                        md_parts.append(f"## {translated_heading}\n\n")
                        paper_parts.append(f"## {heading_text}\n\n")

                self._process_section_content(
                    child,
                    md_parts,
                    paper_parts,
                    paragraphs,
                    plain_texts,
                    flush_fn,
                    depth + 1,
                )

            elif (
                isinstance(child, Tag)
                and child.name == "div"
                and child.get("role") == "paragraph"
            ):
                self._process_paragraph_div(child, paragraphs, plain_texts, flush_fn)

            elif (
                isinstance(child, Tag)
                and child.name == "div"
                and "figure-wrap" in (child.get("class") or [])
            ):
                flush_fn()
                fig_html = self._process_figure(child)
                if fig_html:
                    plain_texts.append(fig_html)

    def _process_section_content(
        self,
        section: Tag,
        md_parts: list[str],
        paper_parts: list[str],
        paragraphs: list[str],
        plain_texts: list[str],
        flush_fn,
        depth: int = 0,
    ):
        if depth > 3:
            return

        for child in section.children:
            if isinstance(child, NavigableString):
                continue
            if not isinstance(child, Tag):
                continue

            if isinstance(child, Tag) and child.name == "section":
                sec_id = child.get("id", "")
                if sec_id in STOP_IDS:
                    break

                h3 = child.find("h3")
                if h3:
                    flush_fn()
                    heading_text = clean_html_entities(h3.get_text(strip=True))
                    heading_text = normalize_whitespace(heading_text)
                    if heading_text:
                        self._current_heading = heading_text
                        translated_heading = self._translate(heading_text)
                        md_parts.append(f"### {translated_heading}\n\n")
                        paper_parts.append(f"### {heading_text}\n\n")

                self._process_section_content(
                    child,
                    md_parts,
                    paper_parts,
                    paragraphs,
                    plain_texts,
                    flush_fn,
                    depth + 1,
                )

            elif (
                isinstance(child, Tag)
                and child.name == "div"
                and child.get("role") == "paragraph"
            ):
                self._process_paragraph_div(child, paragraphs, plain_texts, flush_fn)

            elif (
                isinstance(child, Tag)
                and child.name == "div"
                and "figure-wrap" in (child.get("class") or [])
            ):
                flush_fn()
                fig_html = self._process_figure(child)
                if fig_html:
                    plain_texts.append(fig_html)

            elif isinstance(child, Tag) and child.name in ("h4"):
                flush_fn()
                heading_text = clean_html_entities(child.get_text(strip=True))
                heading_text = normalize_whitespace(heading_text)
                if heading_text:
                    self._current_heading = heading_text
                    translated_heading = self._translate(heading_text)
                    md_parts.append(f"#### {translated_heading}\n\n")
                    paper_parts.append(f"#### {heading_text}\n\n")

    def _process_paragraph_div(
        self, para_div: Tag, paragraphs: list[str], plain_texts: list[str], flush_fn
    ):
        has_display = para_div.select_one(
            "div.display-formula, div.equation[role='math'], div.disp-formula"
        )
        if not has_display:
            self._replace_math_in_element(para_div)
            text = clean_html_entities(para_div.get_text(" ", strip=True))
            text = normalize_whitespace(text)
            if text:
                plain_texts.append(f"<p>{text}</p>")
                paragraphs.append(text)
            return

        text_parts: list[str] = []

        def flush_text():
            nonlocal text_parts
            if text_parts:
                text = " ".join(text_parts)
                text = normalize_whitespace(text)
                if text:
                    plain_texts.append(f"<p>{text}</p>")
                    paragraphs.append(text)
                text_parts = []

        for child in para_div.children:
            if isinstance(child, NavigableString):
                t = str(child).strip()
                if t:
                    text_parts.append(t)
                continue

            if not isinstance(child, Tag):
                continue

            classes = child.get("class") or []

            if child.name == "div" and (
                "display-formula" in classes
                or "disp-formula" in classes
                or (child.get("role") == "math" and "equation" in classes)
            ):
                flush_text()
                flush_fn()
                formula_latex = self._extract_display_formula(child)
                if formula_latex:
                    plain_texts.append(f"<equation>{formula_latex}</equation>")

            elif child.name == "span" and child.get("role") == "math":
                formula_latex = self._extract_math_formula(child)
                if formula_latex:
                    placeholder = self._make_placeholder("MATH", f"${formula_latex}$")
                    text_parts.append(f" {placeholder} ")

            elif child.name in ("a", "i", "b", "em", "strong", "sub", "sup"):
                text_parts.append(child.get_text(strip=True))

            else:
                t = child.get_text(strip=True)
                if t:
                    text_parts.append(t)

        flush_text()

    def _replace_math_in_element(self, element: Tag):
        for span_math in element.find_all("span", role="math"):
            formula_latex = self._extract_math_formula(span_math)
            if formula_latex:
                placeholder = self._make_placeholder("MATH", f"${formula_latex}$")
                span_math.replace_with(f" {placeholder} ")
            else:
                span_math.extract()

    def _extract_math_formula(self, math_span: Tag) -> str | None:
        assistive = math_span.select_one("mjx-assistive-mml")
        if not assistive:
            return None
        math_elem = assistive.find("math")
        if not math_elem:
            return None
        try:
            return _mathml_to_latex(math_elem)
        except Exception:
            return None

    def _extract_display_formula(self, container: Tag) -> str | None:
        assistive = container.select_one("mjx-assistive-mml[display='block']")
        if not assistive:
            assistive = container.select_one("mjx-assistive-mml")
        if not assistive:
            return None
        math_elem = assistive.find("math")
        if not math_elem:
            return None
        try:
            return _mathml_to_latex(math_elem)
        except Exception:
            return None

    def _process_figure(self, figure_wrap: Tag) -> str:
        img_tag = figure_wrap.select_one("figure.graphic img")
        img_url = ""
        if img_tag:
            src = img_tag.get("src")
            if isinstance(src, str):
                img_url = src

        caption_tag = figure_wrap.select_one("figure.graphic figcaption")
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
