import os
import re
import shutil
import subprocess
from pathlib import Path


MAX_FILENAME_LEN = 100


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
