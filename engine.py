import json
import re
import time
import os
import logging
from pathlib import Path
from openai import OpenAI
from config import OPENAI_API_KEY, OPENAI_BASE_URL, OPENAI_MODEL

logger = logging.getLogger(__name__)


TRANSLATE_SYSTEM_PROMPT = """You are a professional academic paper translator. Translate the following English academic text into Chinese (Simplified, 简体中文).

Guidelines:
1. Preserve all citation markers like [1], [2,3], [4,5,6] exactly as they appear.
2. Preserve all figure/table references like "Fig. 1", "Figure 2", "Table 1" exactly as they appear.
3. Preserve mathematical notation, scientific terms, gene/protein names (e.g., CA1, GCaMP6f, PCR, etc.) — do not translate these.
4. Do NOT translate author names, institution names, or journal names.
5. Maintain the original academic tone and precision.
6. Each paragraph (delimited by [¶SEP¶]) must be translated independently and returned delimited by the same [¶SEP¶] marker.
7. Markers like [G_0], [G_1], etc. are protected terms — keep them exactly as they appear, do NOT translate them.
8. Output ONLY the translated text, no explanations."""


SUMMARIZE_SYSTEM_PROMPT = """你是一位专业的学术论文解读专家。你的任务是对一篇已翻译为中文的学术论文进行总结。

请按以下结构输出总结：

## 研究背景与问题
简要说明该论文研究的领域、背景以及要解决的核心问题。

## 核心创新点
列出该论文的主要创新和贡献，每条用通俗易懂的语言说明。

## 研究方法
简述该论文采用的主要实验方法、技术手段或理论框架。

## 关键结论
列出该论文最重要的发现和结论。

## 局限性
指出该论文存在的不足之处或未来可改进的方向。

要求：
1. 语言要通俗易懂，但保持专业性，让非该领域读者也能理解。
2. 不要逐字翻译原文，而是要提炼精华，用你自己的话（不超过原文长度）。
3. 直接输出总结内容，不要加任何前言或后记。"""


GLOSSARY_PATH = Path(__file__).parent / "glossary.json"


def _load_glossary() -> tuple[list[tuple[str, str]], re.Pattern | None]:
    if not GLOSSARY_PATH.exists():
        return [], None
    with open(GLOSSARY_PATH, "r", encoding="utf-8") as f:
        glossary: dict[str, str] = json.load(f)
    items = sorted(glossary.items(), key=lambda x: len(x[0]), reverse=True)
    if not items:
        return [], None
    escaped = [re.escape(key) for key, _ in items]
    pattern = re.compile(r"\b(?:" + "|".join(escaped) + r")\b", re.IGNORECASE)
    return items, pattern


_GLOSSARY_ITEMS, _GLOSSARY_PATTERN = _load_glossary()


class TranslationEngine:
    def __init__(self):
        import httpx
        proxy_url = os.environ.get("HTTP_PROXY") or os.environ.get("http_proxy")
        http_client = httpx.Client(
            trust_env=False,
            proxy=proxy_url,
        )
        self.client = OpenAI(
            api_key=OPENAI_API_KEY,
            base_url=OPENAI_BASE_URL,
            http_client=http_client,
        )
        self.model = OPENAI_MODEL.strip('"')

    @staticmethod
    def _apply_glossary(text: str) -> tuple[str, dict[str, str]]:
        if not _GLOSSARY_PATTERN or not _GLOSSARY_ITEMS:
            return text, {}
        mapping: dict[str, str] = {}
        idx = 0

        def _replace(m: re.Match) -> str:
            nonlocal idx
            term = m.group(0)
            if term not in mapping:
                mapping[f"[G_{idx}]"] = _GLOSSARY_ITEMS[[k for k, _ in _GLOSSARY_ITEMS].index(term)][1]
                idx += 1
            for k, v in mapping.items():
                if v == _GLOSSARY_ITEMS[[k for k, _ in _GLOSSARY_ITEMS].index(term)][1]:
                    return k
            return term

        protected = _GLOSSARY_PATTERN.sub(_replace, text)
        rev = {v: k for k, v in mapping.items()}
        return protected, rev

    @staticmethod
    def _apply_glossary_bulk(text: str) -> tuple[str, dict[str, str]]:
        if not _GLOSSARY_PATTERN or not _GLOSSARY_ITEMS:
            return text, {}
        mapping: dict[str, str] = {}
        glossary_lower = {key.lower(): val for key, val in _GLOSSARY_ITEMS}
        idx = 0

        def _replace(m: re.Match) -> str:
            nonlocal idx
            term = m.group(0)
            replacement = glossary_lower.get(term.lower())
            if replacement is None:
                return term
            key = f"[G_{idx}]"
            mapping[key] = replacement
            idx += 1
            return key

        protected = _GLOSSARY_PATTERN.sub(_replace, text)
        return protected, mapping

    @staticmethod
    def _restore_glossary(text: str, mapping: dict[str, str]) -> str:
        for placeholder, term in mapping.items():
            text = text.replace(placeholder, term)
        return text

    def translate(self, text: str, max_retries: int = 3) -> str:
        if not text.strip():
            return text
        protected, mapping = self._apply_glossary_bulk(text)
        result = self._call_api(
            system_prompt=TRANSLATE_SYSTEM_PROMPT,
            user_message=protected,
            temperature=0.1,
            max_tokens=8192,
            max_retries=max_retries,
        )
        return self._restore_glossary(result, mapping)

    def summarize(self, content: str, max_retries: int = 3) -> str:
        if not content.strip():
            return ""
        return self._call_api(
            system_prompt=SUMMARIZE_SYSTEM_PROMPT,
            user_message=content,
            temperature=0.1,
            max_tokens=4096,
            max_retries=max_retries,
        )

    def _call_api(
        self,
        system_prompt: str,
        user_message: str,
        temperature: float,
        max_tokens: int,
        max_retries: int,
    ) -> str:
        last_error = None
        for attempt in range(max_retries):
            try:
                response = self.client.chat.completions.create(
                    model=self.model,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_message},
                    ],
                    temperature=temperature,
                    max_tokens=max_tokens,
                )
                result = response.choices[0].message.content
                if result is None:
                    raise RuntimeError("API returned empty response")
                return result.strip()
            except Exception as e:
                logger.warning(f"API call attempt {attempt + 1} failed: {e}")
                last_error = e
                if attempt < max_retries - 1:
                    time.sleep(2 ** attempt)
        raise RuntimeError(f"API call failed after {max_retries} attempts: {last_error}")

    def translate_paragraphs(self, paragraphs: list[str], sep: str = "[¶SEP¶]") -> list[str]:
        if not paragraphs:
            return []

        joined = sep.join(paragraphs)
        translated = self.translate(joined)
        translated = translated.replace(sep, sep)
        translated_parts = translated.split(sep)

        def _clean(p: str) -> str:
            p = p.replace("[¶SEP¶]", "")
            p = p.replace("[¶SEP¶", "")
            p = p.replace("¶SEP¶]", "")
            return p.strip()

        if len(translated_parts) == len(paragraphs):
            return [_clean(p) for p in translated_parts]

        logger.warning(
            f"Paragraph count mismatch: expected {len(paragraphs)}, got {len(translated_parts)}. "
            f"Falling back to individual translation."
        )
        results = []
        for para in paragraphs:
            results.append(_clean(self.translate(para)))
        return results
