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
)

logger = logging.getLogger(__name__)

TRANSLATION_FILENAME = "translation.md"
SUMMARY_FILENAME = "summary.md"


def route(input_path: str, output_base: str = "output", skip_summary: bool = False) -> str:
    is_url = input_path.startswith("http://") or input_path.startswith("https://")

    if is_url:
        return _route_url(input_path, output_base, skip_summary)
    else:
        return _route_file(input_path, output_base, skip_summary)


def _route_url(url: str, output_base: str, skip_summary: bool) -> str:
    if "nature.com" not in url:
        raise ValueError(f"Unsupported URL (only nature.com is supported): {url}")

    with tempfile.TemporaryDirectory() as tmpdir:
        html_path = download_with_wget(url, tmpdir, "article.html")
        logger.info(f"Downloaded HTML to {html_path}")

        title = extract_title_from_html(html_path)
        dir_name = sanitize_filename(title)
        output_dir = ensure_output_dir(dir_name, output_base)

        html_path = copy_file_to_output(html_path, str(output_dir), "raw.html")
        logger.info(f"Moved HTML to {html_path}")

        return _translate_and_summarize(html_path, output_dir, skip_summary)


def _route_file(filepath: str, output_base: str, skip_summary: bool) -> str:
    if not os.path.isfile(filepath):
        raise FileNotFoundError(f"File not found: {filepath}")

    title = extract_title_from_html(filepath)
    dir_name = sanitize_filename(title)
    output_dir = ensure_output_dir(dir_name, output_base)

    html_path = copy_file_to_output(filepath, str(output_dir), f"raw.html")
    logger.info(f"Copied HTML to {html_path}")

    return _translate_and_summarize(html_path, output_dir, skip_summary)


def _translate_and_summarize(html_path: str, output_dir: Path, skip_summary: bool) -> str:
    if _is_arxiv(html_path):
        md_path = _process_arxiv(html_path, output_dir)
    elif _is_biorxiv(html_path):
        md_path = _process_biorxiv(html_path, output_dir)
    elif _is_ieee(html_path):
        md_path = _process_ieee(html_path, output_dir)
    elif _is_sciencedirect(html_path):
        md_path = _process_sciencedirect(html_path, output_dir)
    else:
        md_path = _process_nature(html_path, output_dir)

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
        if "LaTeXML" in head or 'class="ltx_document"' in head or "ltx_page_main" in head:
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
        if 'biorxiv.org' in head or 'bioRxiv' in head:
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
        if 'meta name="citation_pii"' in head or "meta name=\"citation_pii\"" in head:
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
        if 'IEEE Xplore' in head or 'IEEE Conference Publication' in head:
            return True
    except Exception:
        pass
    return False


def _process_nature(html_path: str, output_dir: Path) -> Path:
    from processors.nature import NatureProcessor

    logger.info("Processing as Nature article...")
    processor = NatureProcessor(html_path, output_dir)

    logger.info("Translating paper (this may take a while)...")
    md_content = processor.extract_and_translate()

    md_path = output_dir / TRANSLATION_FILENAME
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(md_content)

    logger.info(f"Translation complete: {md_path}")
    return md_path


def _process_arxiv(html_path: str, output_dir: Path) -> Path:
    from processors.arxiv import ArxivProcessor

    logger.info("Processing as arXiv article...")
    processor = ArxivProcessor(html_path, output_dir)

    logger.info("Translating paper (this may take a while)...")
    md_content = processor.extract_and_translate()

    md_path = output_dir / TRANSLATION_FILENAME
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(md_content)

    logger.info(f"Translation complete: {md_path}")
    return md_path


def _process_biorxiv(html_path: str, output_dir: Path) -> Path:
    from processors.biorxiv import BiorxivProcessor

    logger.info("Processing as bioRxiv article...")
    processor = BiorxivProcessor(html_path, output_dir)

    logger.info("Translating paper (this may take a while)...")
    md_content = processor.extract_and_translate()

    md_path = output_dir / TRANSLATION_FILENAME
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(md_content)

    logger.info(f"Translation complete: {md_path}")
    return md_path


def _process_sciencedirect(html_path: str, output_dir: Path) -> Path:
    from processors.sciencedirect import ScienceDirectProcessor

    logger.info("Processing as ScienceDirect article...")
    processor = ScienceDirectProcessor(html_path, output_dir)

    logger.info("Translating paper (this may take a while)...")
    md_content = processor.extract_and_translate()

    md_path = output_dir / TRANSLATION_FILENAME
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(md_content)

    logger.info(f"Translation complete: {md_path}")
    return md_path


def _process_ieee(html_path: str, output_dir: Path) -> Path:
    from processors.ieee import IEEEXploreProcessor

    logger.info("Processing as IEEE Xplore article...")
    processor = IEEEXploreProcessor(html_path, output_dir)

    logger.info("Translating paper (this may take a while)...")
    md_content = processor.extract_and_translate()

    md_path = output_dir / TRANSLATION_FILENAME
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(md_content)

    logger.info(f"Translation complete: {md_path}")
    return md_path


def _run_summarizer(output_dir: Path, translation_path: Path):
    from summarizer import summarize_markdown

    logger.info("Generating summary...")
    summary = summarize_markdown(str(translation_path))
    summary_path = output_dir / SUMMARY_FILENAME
    with open(summary_path, "w", encoding="utf-8") as f:
        f.write(summary)
    logger.info(f"Summary saved to: {summary_path}")
