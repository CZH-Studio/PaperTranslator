#!/usr/bin/env python3
import argparse
import logging
import os
import sys

from router import route

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


def main():
    parser = argparse.ArgumentParser(
        description="Translate academic papers (Nature, ScienceDirect, IEEE) from HTML to Chinese Markdown."
    )
    parser.add_argument(
        "input",
        nargs="?",
        help="URL or path to a local HTML file",
    )
    parser.add_argument(
        "--output-dir",
        default="output",
        help="Output directory (default: output/)",
    )
    parser.add_argument(
        "--no-summary",
        action="store_true",
        help="Skip generating summary.md",
    )
    parser.add_argument(
        "--no-translation",
        action="store_true",
        help="Skip translation and only write the English paper.md",
    )
    parser.add_argument(
        "--summarize-only",
        metavar="PATH",
        help="Only generate summary from an existing translation.md or paper.md (path to the file or its parent directory)",
    )
    parser.add_argument(
        "-c",
        "--concurrency",
        type=int,
        default=10,
        help="Number of concurrent translation requests (default: 10)",
    )
    args = parser.parse_args()

    if args.summarize_only:
        return cmd_summarize(args.summarize_only)

    if not args.input:
        print("请输入论文 URL 或本地 HTML 文件路径：", end=" ", flush=True)
        args.input = input().strip()
        if args.input.startswith('"') and args.input.endswith('"'):
            args.input = args.input[1:-1]
        if not args.input:
            logger.error("No input provided, exiting.")
            sys.exit(1)

    try:
        md_path = route(
            args.input,
            args.output_dir,
            skip_summary=args.no_summary,
            skip_translation=args.no_translation,
            concurrency=args.concurrency,
        )
        label = "English markdown" if args.no_translation else "Translation"
        logger.info(f"{label} saved to: {md_path}")
    except Exception as e:
        logger.error(str(e))
        sys.exit(1)


def cmd_summarize(target: str):
    from summarizer import summarize_markdown

    if os.path.isdir(target):
        translation_path = os.path.join(target, "translation.md")
        paper_path = os.path.join(target, "paper.md")
        if os.path.isfile(translation_path):
            md_path = translation_path
        elif os.path.isfile(paper_path):
            md_path = paper_path
        else:
            logger.error(f"Error: neither translation.md nor paper.md found in {target}")
            sys.exit(1)
    else:
        md_path = target

    if not os.path.isfile(md_path):
        logger.error(f"Error: markdown file not found at {md_path}")
        sys.exit(1)

    parent_dir = os.path.dirname(md_path)
    summary_path = os.path.join(parent_dir, "summary.md")

    logger.info(f"Generating summary from {os.path.basename(md_path)}...")
    summary = summarize_markdown(md_path)

    with open(summary_path, "w", encoding="utf-8") as f:
        f.write(summary)

    logger.info(f"Summary saved to: {summary_path}")


if __name__ == "__main__":
    main()
