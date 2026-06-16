import re
import html as html_module


def clean_html_entities(text: str) -> str:
    text = html_module.unescape(text)
    text = text.replace('\xa0', ' ')
    return text


def normalize_whitespace(text: str) -> str:
    text = re.sub(r'\s+', ' ', text).strip()
    return text
