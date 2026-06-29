import logging

from engine import TranslationEngine

logger = logging.getLogger(__name__)


def summarize_markdown(md_path: str) -> str:
    with open(md_path, "r", encoding="utf-8") as f:
        content = f.read()

    if not content.strip():
        logger.warning("Empty translation, skipping summary")
        return ""

    title = _extract_title_from_markdown(content)

    engine = TranslationEngine()
    logger.info(f"Calling {engine.model_summarizer} for summary...")
    summary = engine.summarize(content)

    if title:
        summary = f"# {title}\n\n{summary}"

    return summary


def _extract_title_from_markdown(content: str) -> str:
    lines = content.split("\n")
    if lines and lines[0].startswith("# "):
        return lines[0][2:].strip()
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("# "):
            return stripped[2:].strip()
    return ""
