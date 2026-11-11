import os
import tempfile
import logging
from pathlib import Path

from utils.file_utils import (
    extract_title_from_html,
    ensure_output_dir,
    download_with_wget,
    copy_file_to_output,
    sanitize_filename,
    relocate_assets,
)
from config import OPENAI_MODEL_TRANSLATOR

logger = logging.getLogger(__name__)

TRANSLATION_FILENAME = "translation.md"
SUMMARY_FILENAME = "summary.md"
PAPER_FILENAME = "paper.md"


def _make_engine(concurrency: int, skip_translation: bool):
    from engine import TranslationEngine

    return TranslationEngine(
        concurrency=concurrency,
        translate_enabled=not skip_translation,
    )


def _log_processing(skip_translation: bool):
    if skip_translation:
        logger.info("Translation disabled, extracting English markdown only...")
    else:
        logger.info(
            f"Translating paper by {OPENAI_MODEL_TRANSLATOR} (this may take a while)..."
        )


def _save_markdown(output_dir: Path, md_content: str, skip_translation: bool) -> Path:
    if skip_translation:
        md_path = output_dir / PAPER_FILENAME
        if not md_path.is_file():
            raise FileNotFoundError(
                f"English markdown was not generated: {md_path}"
            )
        logger.info(f"English markdown saved to: {md_path}")
        return md_path

    md_path = output_dir / TRANSLATION_FILENAME
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(md_content)

    logger.info(f"Translation complete: {md_path}")
    return md_path


def route(
    input_path: str, output_base: str = "output", skip_summary: bool = False,
    concurrency: int = 10, skip_translation: bool = False
) -> str:
    is_url = input_path.startswith("http://") or input_path.startswith("https://")

    if is_url:
        return _route_url(
            input_path, output_base,
            skip_summary=skip_summary,
            skip_translation=skip_translation,
            concurrency=concurrency,
        )
    else:
        return _route_file(
            input_path, output_base,
            skip_summary=skip_summary,
            skip_translation=skip_translation,
            concurrency=concurrency,
        )


def _route_url(url: str, output_base: str, skip_summary: bool,
               skip_translation: bool, concurrency: int) -> str:
    if "nature.com" not in url and "springer.com" not in url:
        raise ValueError(f"Unsupported URL (supported: nature.com, link.springer.com): {url}")

    with tempfile.TemporaryDirectory() as tmpdir:
        html_path = download_with_wget(url, tmpdir, "article.html")
        logger.info(f"Downloaded HTML to {html_path}")

        title = extract_title_from_html(html_path)
        dir_name = sanitize_filename(title)
        output_dir = ensure_output_dir(dir_name, output_base)

        html_path = copy_file_to_output(html_path, str(output_dir), "raw.html")
        logger.info(f"Moved HTML to {html_path}")

        return _translate_and_summarize(
            html_path, output_dir,
            skip_summary=skip_summary,
            skip_translation=skip_translation,
            concurrency=concurrency,
        )


def _route_file(filepath: str, output_base: str, skip_summary: bool,
                skip_translation: bool, concurrency: int) -> str:
    if not os.path.isfile(filepath):
        raise FileNotFoundError(f"File not found: {filepath}")

    # PDF files: use filename as title (no HTML extraction)
    if filepath.lower().endswith(".pdf"):
        title = Path(filepath).stem
        dir_name = sanitize_filename(title)
        output_dir = ensure_output_dir(dir_name, output_base)
        pdf_path = copy_file_to_output(filepath, str(output_dir), "raw.pdf")
        logger.info(f"Copied PDF to {pdf_path}")
        md_path = _process_pdf(
            pdf_path, output_dir,
            concurrency=concurrency,
            skip_translation=skip_translation,
        )
        if not skip_summary:
            _run_summarizer(output_dir, md_path)
        return str(md_path)

    title = extract_title_from_html(filepath)
    dir_name = sanitize_filename(title)
    output_dir = ensure_output_dir(dir_name, output_base)

    html_path = copy_file_to_output(filepath, str(output_dir), f"raw.html")
    logger.info(f"Copied HTML to {html_path}")

    md_path = _translate_and_summarize(
        html_path, output_dir,
        skip_summary=skip_summary,
        skip_translation=skip_translation,
        concurrency=concurrency,
    )
    n = relocate_assets(filepath, output_dir)
    if n > 0:
        logger.info(f"Relocated {n} asset(s) to {output_dir / 'assets'}")

    return str(md_path)


def _translate_and_summarize(
    html_path: str, output_dir: Path, skip_summary: bool, skip_translation: bool,
    concurrency: int
) -> str:
    if _is_arxiv(html_path):
        md_path = _process_arxiv(html_path, output_dir, concurrency, skip_translation)
    elif _is_biorxiv(html_path):
        md_path = _process_biorxiv(html_path, output_dir, concurrency, skip_translation)
    elif _is_ieee(html_path):
        md_path = _process_ieee(html_path, output_dir, concurrency, skip_translation)
    elif _is_sciencedirect(html_path):
        md_path = _process_sciencedirect(html_path, output_dir, concurrency, skip_translation)
    elif _is_iopscience(html_path):
        md_path = _process_iopscience(html_path, output_dir, concurrency, skip_translation)
    elif _is_mdpi(html_path):
        md_path = _process_mdpi(html_path, output_dir, concurrency, skip_translation)
    elif _is_frontiers(html_path):
        md_path = _process_frontiers(html_path, output_dir, concurrency, skip_translation)
    elif _is_springer(html_path):
        md_path = _process_springer(html_path, output_dir, concurrency, skip_translation)
    elif _is_pnas(html_path):
        md_path = _process_pnas(html_path, output_dir, concurrency, skip_translation)
    elif _is_spie(html_path):
        md_path = _process_spie(html_path, output_dir, concurrency, skip_translation)
    elif _is_plos_one(html_path):
        md_path = _process_plos_one(html_path, output_dir, concurrency, skip_translation)
    elif _is_sage(html_path):
        md_path = _process_sage(html_path, output_dir, concurrency, skip_translation)
    elif _is_cell(html_path):
        md_path = _process_cell(html_path, output_dir, concurrency, skip_translation)
    elif _is_jneurosci(html_path):
        md_path = _process_jneurosci(html_path, output_dir, concurrency, skip_translation)
    elif _is_elife(html_path):
        md_path = _process_elife(html_path, output_dir, concurrency, skip_translation)
    elif _is_mitpress(html_path):
        md_path = _process_mitpress(html_path, output_dir, concurrency, skip_translation)
    else:
        md_path = _process_nature(html_path, output_dir, concurrency, skip_translation)

    n = relocate_assets(html_path, output_dir)
    if n > 0:
        logger.info(f"Relocated {n} asset(s) to {output_dir / 'assets'}")

    if not skip_summary:
        _run_summarizer(output_dir, md_path)

    return str(md_path)


def _is_arxiv(filepath: str) -> bool:
    basename = os.path.basename(filepath).lower()
    if "arxiv" in basename:
        return True
    try:
        with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
            head = f.read(8192)
        if (
            "LaTeXML" in head
            or 'class="ltx_document"' in head
            or "ltx_page_main" in head
        ):
            return True
    except Exception:
        pass
    return False


def _is_biorxiv(filepath: str) -> bool:
    basename = os.path.basename(filepath).lower()
    if "biorxiv" in basename:
        return True
    try:
        with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
            head = f.read(8192)
        if "biorxiv.org" in head or "bioRxiv" in head:
            return True
    except Exception:
        pass
    return False


def _is_sciencedirect(filepath: str) -> bool:
    basename = os.path.basename(filepath).lower()
    if "sciencedirect" in basename:
        return True
    try:
        with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
            head = f.read(4096)
        if "ScienceDirect" in head or "sciencedirect.com" in head:
            return True
        if 'meta name="citation_pii"' in head or 'meta name="citation_pii"' in head:
            return True
    except Exception:
        pass
    return False


def _is_ieee(filepath: str) -> bool:
    basename = os.path.basename(filepath).lower()
    keywords = ["ieee", "ieeexplore", "xplore"]
    for kw in keywords:
        if kw in basename:
            return True
    try:
        with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
            head = f.read(8192)
        if "ieeexplore.ieee.org" in head:
            return True
        if "xplGlobal" in head or "xpl-root" in head:
            return True
        if "IEEE Xplore" in head or "IEEE Conference Publication" in head:
            return True
    except Exception:
        pass
    return False


def _is_iopscience(filepath: str) -> bool:
    basename = os.path.basename(filepath).lower()
    keywords = ["iopscience", "iop science", "iop_"]
    for kw in keywords:
        if kw in basename:
            return True
    try:
        with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
            head = f.read(8192)
        if "iopscience.iop.org" in head:
            return True
        if (
            'dc.publisher" content="IOP Publishing"' in head
            or "dc.publisher' content='IOP Publishing'" in head
        ):
            return True
        if (
            'name="citation_journal_abbrev"' in head
            and 'dc.publisher" content="IOP' in head
        ):
            return True
        if "wd-jnl-art-full-text" in head:
            return True
    except Exception:
        pass
    return False


def _is_mdpi(filepath: str) -> bool:
    basename = os.path.basename(filepath).lower()
    if "mdpi" in basename:
        return True
    try:
        with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
            head = f.read(8192)
        if "mdpi.com" in head or "mdpi-res.com" in head:
            return True
        if 'name="sso-service" content="mdpi"' in head:
            return True
        if 'name="citation_publisher" content="Multidisciplinary Digital Publishing Institute"' in head:
            return True
        if "html-article-content" in head or "html-body" in head:
            if "html-p" in head:
                return True
    except Exception:
        pass
    return False


def _is_frontiers(filepath: str) -> bool:
    basename = os.path.basename(filepath).lower()
    if "frontiers" in basename:
        return True
    try:
        with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
            head = f.read(8192)
        if "frontiersin.org" in head:
            return True
        if 'name="citation_publisher" content="Frontiers"' in head:
            return True
        if "ArticleContent" in head and "ArticleFigure" in head:
            return True
    except Exception:
        pass
    return False


def _is_springer(filepath: str) -> bool:
    basename = os.path.basename(filepath).lower()
    keywords = ["springer", "springerlink"]
    for kw in keywords:
        if kw in basename:
            return True
    try:
        with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
            head = f.read(8192)
        if "link.springer.com" in head:
            return True
        if 'name="dc.publisher" content="Springer"' in head:
            return True
        if 'name="citation_publisher" content="Springer US"' in head:
            return True
        if "Springer Nature Link" in head:
            return True
    except Exception:
        pass
    return False


def _is_pnas(filepath: str) -> bool:
    basename = os.path.basename(filepath).lower()
    if "pnas" in basename:
        return True
    try:
        with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
            head = f.read(8192)
        if "pnas.org" in head or "PNAS" in head:
            return True
        if 'name="pbContext"' in head and "pnas" in head:
            return True
        if "Proceedings of the National Academy of Sciences" in head:
            return True
    except Exception:
        pass
    return False


def _is_spie(filepath: str) -> bool:
    basename = os.path.basename(filepath).lower()
    if "spie" in basename or "spiedigitallibrary" in basename:
        return True
    try:
        with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
            head = f.read(8192)
        if "spiedigitallibrary.org" in head:
            return True
        if 'name="citation_publisher" content="SPIE"' in head:
            return True
        if "SPIE Digital Library" in head:
            return True
    except Exception:
        pass
    return False


def _is_plos_one(filepath: str) -> bool:
    basename = os.path.basename(filepath).lower()
    if "plos" in basename and "one" in basename:
        return True
    if "plosone" in basename or "plos_one" in basename:
        return True
    try:
        with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
            head = f.read(8192)
        if "journals.plos.org/plosone" in head:
            return True
        if 'content="PLOS ONE"' in head or 'content="PLOS ONE' in head:
            return True
        if 'name="citation_journal_title" content="PLOS ONE"' in head:
            return True
    except Exception:
        pass
    return False


def _is_sage(filepath: str) -> bool:
    """Detect SAGE Journals articles (sagepub.com). Same HTML structure as Cell Press."""
    basename = os.path.basename(filepath).lower()
    if "sage" in basename and "sagepub" not in basename:
        return True
    try:
        with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
            head = f.read(8192)
        if "sagepub.com" in head or "journals.sagepub.com" in head:
            return True
        if 'name="citation_publisher" content="SAGE' in head:
            return True
        # Structural marker: bodymatter but NOT author-abstract (Cell has author-abstract, SAGE doesn't)
        if 'section id="bodymatter"' in head and 'div role="paragraph"' in head:
            if 'section id="author-abstract"' not in head:
                if "sagepub.com" in head or "SAGE" in head:
                    return True
    except Exception:
        pass
    return False


def _process_sage(html_path: str, output_dir: Path,
                  concurrency: int, skip_translation: bool = False) -> Path:
    from processors.cell import CellProcessor

    logger.info("Processing as SAGE Journals article...")
    processor = CellProcessor(html_path, output_dir, concurrency=concurrency, engine=_make_engine(concurrency, skip_translation))

    _log_processing(skip_translation)
    md_content = processor.extract_and_translate()

    return _save_markdown(output_dir, md_content, skip_translation)


def _is_cell(filepath: str) -> bool:
    """Detect Current Biology / Cell Press articles (cell.com)."""
    basename = os.path.basename(filepath).lower()
    keywords = ["cell", "current-biology", "current_biology", "cellpress"]
    for kw in keywords:
        if kw in basename:
            return True
    try:
        with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
            head = f.read(8192)
        if "cell.com" in head:
            return True
        # Cell Press meta markers
        if ('name="citation_journal_title"' in head
                and ('Current Biology' in head or 'Cell Reports' in head
                     or 'Molecular Cell' in head or 'Neuron' in head
                     or 'Developmental Cell' in head or 'Cell' in head)):
            return True
        # Structural markers
        if 'section id="bodymatter"' in head and 'section id="author-abstract"' in head:
            return True
        if 'div role="paragraph"' in head and 'name="citation_doi"' in head:
            if 'dropBlock' in head or 'reference-citations' in head:
                return True
    except Exception:
        pass
    return False


def _is_jneurosci(filepath: str) -> bool:
    """Detect Journal of Neuroscience / HighWire Press articles (jneurosci.org)."""
    basename = os.path.basename(filepath).lower()
    if "jneurosci" in basename:
        return True
    try:
        with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
            head = f.read(8192)
        if "jneurosci.org" in head:
            return True
        if ('name="citation_journal_title" content="Journal of Neuroscience"' in head
                or "name='citation_journal_title' content='Journal of Neuroscience'" in head):
            return True
        if "highwire-cite-title" in head or "content-block-markup" in head:
            if "citation_journal_title" in head:
                return True
    except Exception:
        pass
    return False


def _is_elife(filepath: str) -> bool:
    basename = os.path.basename(filepath).lower()
    if "elife" in basename:
        return True
    try:
        with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
            head = f.read(8192)
        if "elifesciences.org" in head:
            return True
        if 'name="dc.publisher" content="eLife' in head:
            return True
        if 'content="eLife Sciences Publications"' in head:
            return True
        if 'name="dc.title"' in head and "article-section" in head:
            return True
    except Exception:
        pass
    return False


def _is_mitpress(filepath: str) -> bool:
    basename = os.path.basename(filepath).lower()
    keywords = ["mit press", "mitpress", "jocn", "jnocn"]
    for kw in keywords:
        if kw in basename:
            return True
    if "imaging neuroscience" in basename:
        return True
    try:
        with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
            head = f.read(8192)
        if 'name="citation_publisher" content="MIT Press"' in head:
            return True
        if "direct.mit.edu" in head:
            return True
        if "silverchair-cdn.com" in head and "mitp" in head:
            return True
    except Exception:
        pass
    return False


def _process_elife(html_path: str, output_dir: Path,
                   concurrency: int, skip_translation: bool = False) -> Path:
    from processors.elife import ElifeProcessor

    logger.info("Processing as eLife article...")
    processor = ElifeProcessor(html_path, output_dir, concurrency=concurrency, engine=_make_engine(concurrency, skip_translation))

    _log_processing(skip_translation)
    md_content = processor.extract_and_translate()

    return _save_markdown(output_dir, md_content, skip_translation)


def _process_mitpress(html_path: str, output_dir: Path,
                      concurrency: int, skip_translation: bool = False) -> Path:
    from processors.mitpress import MitPressProcessor

    logger.info("Processing as MIT Press article...")
    processor = MitPressProcessor(html_path, output_dir, concurrency=concurrency, engine=_make_engine(concurrency, skip_translation))

    _log_processing(skip_translation)
    md_content = processor.extract_and_translate()

    return _save_markdown(output_dir, md_content, skip_translation)


def _process_pdf(pdf_path: str, output_dir: Path, concurrency: int, skip_translation: bool = False) -> Path:
    from processors.pdf import PdfProcessor

    logger.info("Processing as PDF document...")
    processor = PdfProcessor(pdf_path, output_dir, concurrency=concurrency, engine=_make_engine(concurrency, skip_translation))

    _log_processing(skip_translation)
    md_content = processor.extract_and_translate()

    return _save_markdown(output_dir, md_content, skip_translation)


def _process_spie(html_path: str, output_dir: Path, concurrency: int, skip_translation: bool = False) -> Path:
    from processors.spie import SpieProcessor

    logger.info("Processing as SPIE Digital Library article...")
    processor = SpieProcessor(html_path, output_dir, concurrency=concurrency, engine=_make_engine(concurrency, skip_translation))

    _log_processing(skip_translation)
    md_content = processor.extract_and_translate()

    return _save_markdown(output_dir, md_content, skip_translation)


def _process_plos_one(html_path: str, output_dir: Path,
                       concurrency: int, skip_translation: bool = False) -> Path:
    from processors.plos_one import PlosOneProcessor

    logger.info("Processing as PLOS ONE article...")
    processor = PlosOneProcessor(html_path, output_dir, concurrency=concurrency, engine=_make_engine(concurrency, skip_translation))

    _log_processing(skip_translation)
    md_content = processor.extract_and_translate()

    return _save_markdown(output_dir, md_content, skip_translation)


def _process_cell(html_path: str, output_dir: Path,
                  concurrency: int, skip_translation: bool = False) -> Path:
    from processors.cell import CellProcessor

    logger.info("Processing as Current Biology / Cell Press article...")
    processor = CellProcessor(html_path, output_dir, concurrency=concurrency, engine=_make_engine(concurrency, skip_translation))

    _log_processing(skip_translation)
    md_content = processor.extract_and_translate()

    return _save_markdown(output_dir, md_content, skip_translation)


def _process_nature(html_path: str, output_dir: Path,
                    concurrency: int, skip_translation: bool = False) -> Path:
    from processors.nature import NatureProcessor

    logger.info("Processing as Nature article...")
    processor = NatureProcessor(html_path, output_dir, concurrency=concurrency, engine=_make_engine(concurrency, skip_translation))

    _log_processing(skip_translation)
    md_content = processor.extract_and_translate()

    return _save_markdown(output_dir, md_content, skip_translation)


def _process_arxiv(html_path: str, output_dir: Path, concurrency: int, skip_translation: bool = False) -> Path:
    from processors.arxiv import ArxivProcessor

    logger.info("Processing as arXiv article...")
    processor = ArxivProcessor(html_path, output_dir, concurrency=concurrency, engine=_make_engine(concurrency, skip_translation))
    

    _log_processing(skip_translation)
    md_content = processor.extract_and_translate()

    return _save_markdown(output_dir, md_content, skip_translation)


def _process_biorxiv(html_path: str, output_dir: Path, concurrency: int, skip_translation: bool = False) -> Path:
    from processors.biorxiv import BiorxivProcessor

    logger.info("Processing as bioRxiv article...")
    processor = BiorxivProcessor(html_path, output_dir, concurrency=concurrency, engine=_make_engine(concurrency, skip_translation))
    

    _log_processing(skip_translation)
    md_content = processor.extract_and_translate()

    return _save_markdown(output_dir, md_content, skip_translation)


def _process_sciencedirect(html_path: str, output_dir: Path, concurrency: int, skip_translation: bool = False) -> Path:
    from processors.sciencedirect import ScienceDirectProcessor

    logger.info("Processing as ScienceDirect article...")
    processor = ScienceDirectProcessor(html_path, output_dir, concurrency=concurrency, engine=_make_engine(concurrency, skip_translation))
    

    _log_processing(skip_translation)
    md_content = processor.extract_and_translate()

    return _save_markdown(output_dir, md_content, skip_translation)


def _process_ieee(html_path: str, output_dir: Path, concurrency: int, skip_translation: bool = False) -> Path:
    from processors.ieee import IEEEXploreProcessor

    logger.info("Processing as IEEE Xplore article...")
    processor = IEEEXploreProcessor(html_path, output_dir, concurrency=concurrency, engine=_make_engine(concurrency, skip_translation))
    

    _log_processing(skip_translation)
    md_content = processor.extract_and_translate()

    return _save_markdown(output_dir, md_content, skip_translation)


def _process_iopscience(html_path: str, output_dir: Path, concurrency: int, skip_translation: bool = False) -> Path:
    from processors.iopscience import IopscienceProcessor

    logger.info("Processing as IOPScience article...")
    processor = IopscienceProcessor(html_path, output_dir, concurrency=concurrency, engine=_make_engine(concurrency, skip_translation))
    

    _log_processing(skip_translation)
    md_content = processor.extract_and_translate()

    return _save_markdown(output_dir, md_content, skip_translation)


def _process_frontiers(html_path: str, output_dir: Path,
                       concurrency: int, skip_translation: bool = False) -> Path:
    from processors.frontiers import FrontiersProcessor

    logger.info("Processing as Frontiers article...")
    processor = FrontiersProcessor(html_path, output_dir, concurrency=concurrency, engine=_make_engine(concurrency, skip_translation))

    _log_processing(skip_translation)
    md_content = processor.extract_and_translate()

    return _save_markdown(output_dir, md_content, skip_translation)


def _process_mdpi(html_path: str, output_dir: Path, concurrency: int, skip_translation: bool = False) -> Path:
    from processors.mdpi import MdpiProcessor

    logger.info("Processing as MDPI article...")
    processor = MdpiProcessor(html_path, output_dir, concurrency=concurrency, engine=_make_engine(concurrency, skip_translation))
    

    _log_processing(skip_translation)
    md_content = processor.extract_and_translate()

    return _save_markdown(output_dir, md_content, skip_translation)


def _process_springer(html_path: str, output_dir: Path,
                      concurrency: int, skip_translation: bool = False) -> Path:
    from processors.springer import SpringerProcessor

    logger.info("Processing as Springer Nature Link article...")
    processor = SpringerProcessor(html_path, output_dir, concurrency=concurrency, engine=_make_engine(concurrency, skip_translation))

    _log_processing(skip_translation)
    md_content = processor.extract_and_translate()

    return _save_markdown(output_dir, md_content, skip_translation)


def _process_pnas(html_path: str, output_dir: Path, concurrency: int, skip_translation: bool = False) -> Path:
    from processors.pnas import PnasProcessor

    logger.info("Processing as PNAS article...")
    processor = PnasProcessor(html_path, output_dir, concurrency=concurrency, engine=_make_engine(concurrency, skip_translation))
    

    _log_processing(skip_translation)
    md_content = processor.extract_and_translate()

    return _save_markdown(output_dir, md_content, skip_translation)


def _process_jneurosci(html_path: str, output_dir: Path,
                       concurrency: int, skip_translation: bool = False) -> Path:
    from processors.jneurosci import JNeurosciProcessor

    logger.info("Processing as Journal of Neuroscience article...")
    processor = JNeurosciProcessor(html_path, output_dir, concurrency=concurrency, engine=_make_engine(concurrency, skip_translation))

    _log_processing(skip_translation)
    md_content = processor.extract_and_translate()

    return _save_markdown(output_dir, md_content, skip_translation)


def _run_summarizer(output_dir: Path, markdown_path: Path):
    from summarizer import summarize_markdown

    logger.info(f"Generating summary from {markdown_path.name}...")
    summary = summarize_markdown(str(markdown_path))
    summary_path = output_dir / SUMMARY_FILENAME
    with open(summary_path, "w", encoding="utf-8") as f:
        f.write(summary)
    logger.info(f"Summary saved to: {summary_path}")
