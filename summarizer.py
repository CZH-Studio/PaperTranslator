import logging

from engine import TranslationEngine

logger = logging.getLogger(__name__)


def summarize_markdown(md_path: str) -> str:
    with open(md_path, "r", encoding="utf-8") as f:
        content = f.read()

    if not content.strip():
        logger.warning("Empty translation, skipping summary")
        return ""

    engine = TranslationEngine()
    logger.info("Calling LLM for summary...")
    summary = engine.summarize(content)
    return summary
