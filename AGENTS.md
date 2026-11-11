# AGENTS.md

## Commands

```bash
python main.py <file_or_url>              # full pipeline: extract → translate → summarize
python main.py --no-summary <file>        # skip summary generation
python main.py --no-translation <file>    # English-only markdown, no translation
python main.py --summarize-only <path>    # regenerate summary from existing translation.md (or paper.md)
```

## Environment

- `.env` file: `OPENAI_API_KEY`, `OPENAI_BASE_URL` (default `https://api.deepseek.com`), `OPENAI_MODEL` (default `deepseek-v4-flash`)
- HTTP proxy via `HTTP_PROXY`/`http_proxy` env var (used by httpx client in engine)

## Architecture

```
main.py → router.py → processor (arxiv|biorxiv|ieee|sciencedirect|nature) → engine.py → LLM API
                                               ↕
                                         glossary.json
                                               ↕
                                         summarizer.py
```

- **router.py**: detects source type, delegates to processor, writes output files
- **processors/**: each processor extracts metadata + body from HTML, translates via engine, writes `paper.md` (English), returns translated markdown
- **engine.py**: `TranslationEngine` — wraps OpenAI-compatible API, glossary protection, batch paragraph translation with `[¶SEP¶]` separator
- **summarizer.py**: standalone LLM call to summarize `translation.md`, falling back to `paper.md` when no translation exists

## Output layout

```
output/<sanitized_title>/
├── raw.html          # original HTML copy
├── paper.md          # extracted English markdown
├── translation.md    # Chinese translation (omitted with --no-translation)
└── summary.md        # Chinese summary (5-section format)
```

## Source type detection (in order)

1. **arXiv**: filename contains `arxiv`, or content has `LaTeXML`/`ltx_document`/`ltx_page_main`
2. **bioRxiv**: filename contains `biorxiv`, or content has `biorxiv.org`/`bioRxiv`
3. **IEEE**: filename contains `ieee`/`ieeexplore`/`xplore`, or content has `xplGlobal`/`xpl-root`/`IEEE Xplore`/`IEEE Conference Publication`
4. **ScienceDirect**: filename contains `sciencedirect`, or content has `ScienceDirect` string or `citation_pii` meta tag
5. **Nature**: default fallback (URL mode only supports `nature.com`)

## Glossary

- `glossary.json`: case-insensitive matching (`re.IGNORECASE`). Keys sorted by length descending to avoid partial matches.
- Before LLM call: terms replaced with `[G_N]` placeholders via `_apply_glossary_bulk()`
- After LLM call: placeholders restored via `_restore_glossary()`

## Key gotchas

- **`paragraphs.clear()`**: nested flush functions in Nature/IEEE must use `.clear()` not `= []` — reassignment only affects outer variable, inner parameter still references stale list
- **`find_all("p")`**: IEEE `ol`/`ul` handler must search recursively (not `recursive=False`), or paragraphs inside `<li>` are missed
- **Stop markers vary**: Nature=`References`/`Acknowledgements`, ScienceDirect=`Conflict of interest`/`Acknowledgements`, IEEE=`ACKNOWLEDGMENT` h3, arXiv=`section#bib`/`ltx_appendix`, bioRxiv=stop at `Author Contributions`/`References` (after `Methods` section)
- **Math formulas**: output as `$$\nformula\n$$` (two line breaks) for Typora compatibility
- **Figure captions**: wrap translated captions in `<center>...</center>`
- **Title sanitization**: max 100 chars, removes `[<>:"/\\|?*]`, source from `meta[citation_title]` → `h1.c-article-title` → `h1` → `<title>` split on `|`
- **Paragraph count mismatch**: engine fallback translates each paragraph individually if batch separator count doesn't match
- No lint, typecheck, or test setup exists in this repo
