# 一个简单的论文翻译器

## 使用方法
```
usage: main.py [-h] [--output-dir OUTPUT_DIR] [--no-summary] [--summarize-only PATH] [-c CONCURRENCY] [input]

Translate academic papers from HTML to Chinese Markdown.

positional arguments:
  input                 URL or path to a local HTML file (if omitted, you will be prompted interactively)

options:
  -h, --help            show this help message and exit
  --output-dir OUTPUT_DIR
                        Output directory (default: output/)
  --no-summary          Skip generating summary.md
  --summarize-only PATH
                        Only generate summary for an existing translation (path to translation.md or its parent directory)
  -c, --concurrency CONCURRENCY
                        Number of concurrent translation requests (default: 10)
```

## 功能

解析、翻译并总结html格式的论文内容，所有输出内容保存到`output`文件夹中，包含4个文件：
1. `raw.html`：原始网页html
2. `paper.md`：原始英文论文内容
3. `translation.md`：翻译的中文论文内容
4. `summary.md`：总结（含论文标题的一级标题，结论附通俗解释）

翻译支持上下文感知（论文标题+章节名），提高术语一致性。支持并发翻译（`-c` 参数），可大幅加快翻译速度。

支持通过本地 HTML 文件直接翻译，也可输入 URL 自动下载（当前 URL 模式支持 `nature.com` 和 `springer.com`）。

目前支持如下网站的翻译：
1. arXiv
2. bioRxiv
3. IEEE Xplore
4. ScienceDirect
5. IOPScience
6. MDPI
7. PNAS
8. Springer Nature Link
9. Nature（default）
