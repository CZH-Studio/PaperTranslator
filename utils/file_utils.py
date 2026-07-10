import os
import re
import shutil
import subprocess
import logging
from pathlib import Path


MAX_FILENAME_LEN = 100
logger = logging.getLogger(__name__)


def sanitize_filename(name: str, max_len: int = MAX_FILENAME_LEN) -> str:
    invalid = r'[<>:"/\\|?*]'
    sanitized = re.sub(invalid, '_', name)
    sanitized = sanitized.replace('\n', ' ').replace('\r', ' ')
    sanitized = re.sub(r'\s+', ' ', sanitized).strip()
    if max_len > 0 and len(sanitized) > max_len:
        sanitized = sanitized[:max_len].rsplit(' ', 1)[0]
    return sanitized


def extract_title_from_html(html_path: str) -> str:
    from bs4 import BeautifulSoup
    with open(html_path, "r", encoding="utf-8", errors="ignore") as f:
        soup = BeautifulSoup(f.read(), "lxml")
    meta = soup.select_one("meta[name='citation_title']")
    if meta:
        title = str(meta.get("content", "")).strip()
        if title:
            return title
    h1 = soup.select_one("h1.c-article-title, h1")
    if h1:
        title = h1.get_text(strip=True)
        if title:
            return title
    title_tag = soup.select_one("title")
    if title_tag:
        title = title_tag.get_text(strip=True)
        if title:
            return title.split(" - ")[0].strip()
    return "untitled"


def extract_article_id_from_url(url: str) -> str:
    match = re.search(r'articles?/([\w-]+)', url)
    if match:
        return match.group(1)
    basename = os.path.basename(url.rstrip('/'))
    return sanitize_filename(basename)


def ensure_output_dir(dir_name: str, base_dir: str = "output") -> Path:
    output_path = Path(base_dir) / dir_name
    output_path.mkdir(parents=True, exist_ok=True)
    return output_path


def download_with_wget(url: str, output_dir: str, filename: str) -> str:
    output_path = os.path.join(output_dir, filename)
    cmd = [
        "wget",
        "-O", output_path,
        "--user-agent", "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36",
        "--timeout", "30",
        "--tries", "3",
        "--no-verbose",
        url
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"wget failed: {result.stderr.strip()}")
    return output_path


def copy_file_to_output(src: str, output_dir: str, filename: str) -> str:
    dst = os.path.join(output_dir, filename)
    shutil.copy2(src, dst)
    return dst


def _find_sibling_assets_folder(html_path: str) -> str | None:
    parent_dir = os.path.dirname(os.path.abspath(html_path))
    basename = os.path.splitext(os.path.basename(html_path))[0]
    candidates = [
        f"{basename}_files",
        f"{basename}.files",
    ]
    for cand in candidates:
        full = os.path.join(parent_dir, cand)
        if os.path.isdir(full):
            return full
    return None


def relocate_assets(html_path: str, output_dir: Path) -> int:
    assets_src = _find_sibling_assets_folder(html_path)
    if assets_src is None:
        return 0

    assets_dst = output_dir / "assets"
    assets_dst.mkdir(parents=True, exist_ok=True)

    md_files = [
        output_dir / "paper.md",
        output_dir / "translation.md",
    ]

    image_pattern = re.compile(r'!\[(.+?)\]\(([^)]+)\)')

    relocated_count = 0
    seen_src: dict[str, str] = {}

    for md_path in md_files:
        if not md_path.exists():
            continue

        with open(md_path, "r", encoding="utf-8") as f:
            content = f.read()

        def _replace_image(m: re.Match) -> str:
            nonlocal relocated_count
            alt_text = m.group(1)
            src = m.group(2).strip()

            if src.startswith(("http://", "https://", "data:")):
                return m.group(0)

            _, ext = os.path.splitext(src)
            if not ext:
                ext = ".png"

            abs_src = os.path.normpath(
                os.path.join(os.path.dirname(os.path.abspath(html_path)), src)
            )

            if not os.path.isfile(abs_src):
                alt_src = os.path.join(assets_src, os.path.basename(src))
                if os.path.isfile(alt_src):
                    abs_src = alt_src
                else:
                    return m.group(0)

            if abs_src in seen_src:
                dst_name = seen_src[abs_src]
            else:
                base_name = sanitize_filename(
                    os.path.splitext(os.path.basename(src))[0], max_len=80
                )
                dst_name = f"{base_name}{ext}"
                dst_path = assets_dst / dst_name

                counter = 1
                while dst_path.exists():
                    dst_name = f"{base_name}_{counter}{ext}"
                    dst_path = assets_dst / dst_name
                    counter += 1

                shutil.copy2(abs_src, dst_path)
                seen_src[abs_src] = dst_name
                relocated_count += 1
                logger.debug(f"Assets: {os.path.basename(abs_src)} → assets/{dst_name}")

            return f"![{alt_text}](./assets/{dst_name})"

        new_content = image_pattern.sub(_replace_image, content)

        if new_content != content:
            with open(md_path, "w", encoding="utf-8") as f:
                f.write(new_content)

    return relocated_count
