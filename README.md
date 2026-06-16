# 一个简单的论文翻译器

## 使用方法
```
usage: main.py [-h] [--output-dir OUTPUT_DIR] [--no-summary] [--summarize-only PATH] [input]

Translate academic papers (Nature, ScienceDirect, IEEE) from HTML to Chinese Markdown.

positional arguments:
  input                 URL (https://www.nature.com/articles/...) or path to a local HTML file

options:
  -h, --help            show this help message and exit
  --output-dir OUTPUT_DIR
                        Output directory (default: output/)
  --no-summary          Skip generating summary.md
  --summarize-only PATH
                        Only generate summary for an existing translation (path to translation.md or its parent directory)
```

## 功能

解析、翻译并总结html格式的论文内容，所有输出内容保存到`output`文件夹中，包含4个文件：
1. `raw.html`：原始网页html
2. `paper.md`：原始英文论文内容
3. `translation.md`：翻译的中文论文内容
4. `summary.md`：总结

目前支持如下网站的翻译：
1. Arxiv
2. Biorxiv
3. IEEE Xplore
4. Nature
5. ScienceDirect
