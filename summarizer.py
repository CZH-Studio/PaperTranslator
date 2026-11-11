import logging

from engine import TranslationEngine

logger = logging.getLogger(__name__)


def summarize_markdown(md_path: str) -> str:
    with open(md_path, "r", encoding="utf-8") as f:
        content = f.read()

    if not content.strip():
        logger.warning("Empty markdown, skipping summary")
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
    """Return (chinese_title, english_title) from translated or English markdown."""
    chinese = ""
    english = ""

    for line in content.split("\n"):
        stripped = line.strip()
        if not stripped:
            continue

        if stripped.startswith("# "):
            title = stripped[2:].strip()
            if _contains_chinese(title):
                chinese = chinese or title
            else:
                english = english or title
        elif stripped.startswith("**") and stripped.endswith("**"):
            title = stripped.strip("*").strip()
            if title:
                if _contains_chinese(title):
                    chinese = chinese or title
                else:
                    english = english or title

        if chinese and english:
            break

    return chinese, english


def _contains_chinese(text: str) -> bool:
    return any("\u4e00" <= ch <= "\u9fff" for ch in text)
