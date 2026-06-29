from processors.nature import NatureProcessor
from utils.text_utils import clean_html_entities, normalize_whitespace


class SpringerProcessor(NatureProcessor):
    def _extract_meta(self):
        super()._extract_meta()

        if not self._journal_info:
            identifiers = self.soup.select_one(".c-article-identifiers")
            if identifiers:
                self._journal_info = clean_html_entities(
                    identifiers.get_text(" ", strip=True)
                )
                self._journal_info = normalize_whitespace(self._journal_info)
