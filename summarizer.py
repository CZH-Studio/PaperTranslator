import logging

from engine import TranslationEngine

logger = logging.getLogger(__name__)


def summarize_markdown(md_path: str) -> str:
    with open(md_path, "r", encoding="utf-8") as f:
        content = f.read()

    if not content.strip():
        logger.warning("Empty translation, skipping summary")
        return ""

    chinese_title, english_title = _extract_titles_from_markdown(content)

    engine = TranslationEngine()
    logger.info(f"Calling {engine.model_summarizer} for summary...")
    summary = engine.summarize(content)

    header_bits = ["# 论文总结", ""]
    if english_title:
        header_bits.append(english_title)
        header_bits.append("")
    if chinese_title:
        header_bits.append(chinese_title)
        header_bits.append("")
    header = "\n".join(header_bits)

    return f"{header}\n{summary}"


def _extract_titles_from_markdown(content: str) -> tuple[str, str]:
    """Return (chinese_title, english_title)."""
    lines = content.split("\n")
    chinese = ""
    english = ""

    if lines and lines[0].startswith("# "):
        chinese = lines[0][2:].strip()

    for line in lines[1:]:
        stripped = line.strip()
        if stripped.startswith("**") and stripped.endswith("**"):
            english = stripped.strip("*").strip()
            break

    return chinese, english
