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


def main():
    parser = argparse.ArgumentParser(
        description="Translate academic papers (Nature, ScienceDirect, IEEE) from HTML to Chinese Markdown."
    )
    parser.add_argument(
        "input",
        nargs="?",
        help="URL (https://www.nature.com/articles/...) or path to a local HTML file",
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
        "--summarize-only",
        metavar="PATH",
        help="Only generate summary for an existing translation (path to translation.md or its parent directory)",
    )
    args = parser.parse_args()

    if args.summarize_only:
        return cmd_summarize(args.summarize_only)

    if not args.input:
        parser.error("Either provide an input file/URL or use --summarize-only")

    try:
        md_path = route(args.input, args.output_dir, skip_summary=args.no_summary)
        print(f"Translation saved to: {md_path}")
    except Exception as e:
        logging.getLogger(__name__).error(str(e))
        sys.exit(1)


def cmd_summarize(target: str):
    from summarizer import summarize_markdown

    if os.path.isdir(target):
        md_path = os.path.join(target, "translation.md")
    else:
        md_path = target

    if not os.path.isfile(md_path):
        print(f"Error: translation.md not found at {md_path}")
        sys.exit(1)

    parent_dir = os.path.dirname(md_path)
    summary_path = os.path.join(parent_dir, "summary.md")

    logger = logging.getLogger(__name__)
    logger.info("Generating summary from existing translation...")
    summary = summarize_markdown(md_path)

    with open(summary_path, "w", encoding="utf-8") as f:
        f.write(summary)

    logger.info(f"Summary saved to: {summary_path}")
    print(f"Summary saved to: {summary_path}")


if __name__ == "__main__":
    main()
