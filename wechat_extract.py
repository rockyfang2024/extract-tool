#!/usr/bin/env python3
"""
wechat_extract.py

微信公众号文章快速提取器（批量/单篇）：
- 抓取 mp.weixin.qq.com/s/... 页面
- 提取 title / author / date / content_html / content_markdown
- 可选下载图片并替换为本地路径
- 输出为 markdown / html / json

依赖: requests, beautifulsoup4, lxml, html2text, tqdm
pip install requests beautifulsoup4 lxml html2text tqdm
"""

import argparse
import os
import re
import sys
import json
import time
from urllib.parse import urlparse, urljoin
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
from bs4 import BeautifulSoup
import html2text
from tqdm import tqdm

HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/120.0.0.0 Safari/537.36"),
    "Accept-Language": "zh-CN,zh;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    # Referer helps for some articles
    "Referer": "https://mp.weixin.qq.com/",
}

SESSION = requests.Session()
SESSION.headers.update(HEADERS)
DEFAULT_TIMEOUT = 15


def safe_filename(s: str) -> str:
    s = s.strip()
    s = re.sub(r"[\\/:*?\"<>|]+", "_", s)
    s = re.sub(r"\s+", " ", s)
    if len(s) > 120:
        s = s[:120].rstrip()
    return s or "wechat_article"


def fetch_url(url: str, timeout=DEFAULT_TIMEOUT) -> str:
    resp = SESSION.get(url, timeout=timeout)
    resp.raise_for_status()
    # mp.weixin often returns gbk/utf-8, requests handles encoding but override from headers if needed
    return resp.text


def extract_article(html: str, url: str) -> dict:
    soup = BeautifulSoup(html, "lxml")

    # Title
    title = ""
    # common patterns
    h2 = soup.find("h2", class_=re.compile(r".*rich_media_title.*"))
    if h2 and h2.get_text(strip=True):
        title = h2.get_text(strip=True)
    if not title:
        meta_title = soup.find("meta", property="og:title") or soup.find("meta", attrs={"name": "og:title"})
        if meta_title and meta_title.get("content"):
            title = meta_title["content"].strip()
    if not title:
        # fallback to <title>
        if soup.title and soup.title.string:
            title = soup.title.string.strip()

    # Author and date
    author = ""
    date = ""
    # author/date often in .rich_media_meta_list or .rich_media_meta_text
    meta_zone = soup.find(class_=re.compile(r".*rich_media_meta_list.*|.*rich_media_meta_text.*"))
    if meta_zone:
        # try common selectors
        author_tag = meta_zone.find(class_=re.compile(r".*rich_media_meta_text.*|.*rich_media_meta_nickname.*|.*nickname.*"))
        if author_tag and author_tag.get_text(strip=True):
            author = author_tag.get_text(strip=True)
        # date
        date_tag = meta_zone.find(class_=re.compile(r".*rich_media_meta_text.*|.*rich_media_meta_time.*|.*js_date.*"))
        if date_tag and date_tag.get_text(strip=True):
            date = date_tag.get_text(strip=True)
        # sometimes author and date are siblings; try all spans
        if not author or not date:
            spans = meta_zone.find_all(["span", "a"], recursive=True)
            for sp in spans:
                txt = sp.get_text(strip=True)
                if not txt:
                    continue
                # simple date detection
                if re.search(r"\d{4}[-/]\d{1,2}[-/]\d{1,2}", txt) or re.search(r"\d{4}年\d{1,2}月", txt):
                    if not date:
                        date = txt
                else:
                    if not author:
                        author = txt

    # content
    content_html = ""
    content_node = soup.find(id="js_content") or soup.find(class_=re.compile(r".*rich_media_content.*"))
    if content_node:
        # preserve inner HTML
        content_html = "".join(str(x) for x in content_node.contents)
    else:
        # fallback: whole body
        body = soup.body
        content_html = str(body) if body else ""

    # Normalize image src (take data-src if present)
    content_soup = BeautifulSoup(content_html, "lxml")
    imgs = content_soup.find_all("img")
    images = []
    for img in imgs:
        src = img.get("data-src") or img.get("data-original") or img.get("src")
        if not src:
            continue
        # full url
        src = urljoin(url, src)
        images.append(src)
        # write the resolved URL back into src attribute to make downstream replacement consistent
        img["src"] = src

    content_html_fixed = str(content_soup)

    # Convert to markdown (html2text)
    h = html2text.HTML2Text()
    h.ignore_links = False
    h.body_width = 0
    content_md = h.handle(content_html_fixed)

    result = {
        "url": url,
        "title": title,
        "author": author,
        "date": date,
        "content_html": content_html_fixed,
        "content_markdown": content_md,
        "images": images,
    }
    return result


def download_image(url: str, outdir: str, timeout=DEFAULT_TIMEOUT) -> str:
    try:
        resp = SESSION.get(url, stream=True, timeout=timeout)
        resp.raise_for_status()
        parsed = urlparse(url)
        # create a name from path and query
        filename = os.path.basename(parsed.path) or "image"
        # append ext if missing
        if not os.path.splitext(filename)[1]:
            # try to infer from content-type
            ct = resp.headers.get("Content-Type", "")
            if "jpeg" in ct:
                filename += ".jpg"
            elif "png" in ct:
                filename += ".png"
            elif "gif" in ct:
                filename += ".gif"
        # safe
        filename = safe_filename(filename)
        outpath = os.path.join(outdir, filename)
        # avoid overwrite collisions
        base, ext = os.path.splitext(outpath)
        i = 1
        while os.path.exists(outpath):
            outpath = f"{base}_{i}{ext}"
            i += 1
        with open(outpath, "wb") as f:
            for chunk in resp.iter_content(1024 * 8):
                if chunk:
                    f.write(chunk)
        return outpath
    except Exception as e:
        # download failed
        return ""


def save_article(item: dict, outdir: str, fmt: str = "md", download_images: bool = False):
    title = item.get("title") or "wechat_article"
    safe_title = safe_filename(title)
    os.makedirs(outdir, exist_ok=True)

    images_outdir = os.path.join(outdir, "images")
    if download_images:
        os.makedirs(images_outdir, exist_ok=True)

    content_html = item["content_html"]
    # If download_images, fetch and replace image src
    if download_images and item.get("images"):
        soup = BeautifulSoup(content_html, "lxml")
        for img in soup.find_all("img"):
            src = img.get("src")
            if not src:
                continue
            local = download_image(src, images_outdir)
            if local:
                # relative path from outdir
                rel = os.path.relpath(local, outdir)
                img["src"] = rel.replace("\\", "/")
        content_html = str(soup)
        # regenerate markdown from replaced html
        h = html2text.HTML2Text()
        h.ignore_links = False
        h.body_width = 0
        item["content_markdown"] = h.handle(content_html)
        item["content_html"] = content_html

    if fmt == "md":
        outpath = os.path.join(outdir, f"{safe_title}.md")
        with open(outpath, "w", encoding="utf-8") as f:
            f.write(f"# {item.get('title','')}\n\n")
            if item.get("author"):
                f.write(f"- 作者: {item.get('author')}\n")
            if item.get("date"):
                f.write(f"- 日期: {item.get('date')}\n")
            f.write(f"- 原文: {item.get('url')}\n\n---\n\n")
            f.write(item.get("content_markdown", ""))
        return outpath
    elif fmt == "html":
        outpath = os.path.join(outdir, f"{safe_title}.html")
        with open(outpath, "w", encoding="utf-8") as f:
            f.write("<!doctype html>\n<html><head><meta charset='utf-8'>\n")
            f.write(f"<title>{item.get('title','')}</title>\n</head><body>\n")
            f.write(f"<h1>{item.get('title','')}</h1>\n")
            if item.get("author") or item.get("date"):
                f.write("<p>")
                if item.get("author"):
                    f.write(f"作者: {item.get('author')} ")
                if item.get("date"):
                    f.write(f" 日期: {item.get('date')}")
                f.write("</p>\n")
            f.write(f"<p>原文: <a href='{item.get('url')}'>{item.get('url')}</a></p>\n<hr/>\n")
            f.write(item.get("content_html", ""))
            f.write("\n</body></html>")
        return outpath
    elif fmt == "json":
        outpath = os.path.join(outdir, f"{safe_title}.json")
        with open(outpath, "w", encoding="utf-8") as f:
            json.dump(item, f, ensure_ascii=False, indent=2)
        return outpath
    else:
        raise ValueError("unsupported format")


def process_url(url: str, outdir: str, fmt: str, download_images: bool, timeout=DEFAULT_TIMEOUT):
    try:
        html = fetch_url(url, timeout=timeout)
        item = extract_article(html, url)
        saved = save_article(item, outdir, fmt=fmt, download_images=download_images)
        return {"url": url, "ok": True, "path": saved, "title": item.get("title")}
    except Exception as e:
        return {"url": url, "ok": False, "error": str(e)}


def read_urls_from_file(path: str, csv_mode: bool = False):
    urls = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            if csv_mode:
                # take first column
                parts = re.split(r",|\t", line)
                if parts:
                    urls.append(parts[0].strip())
            else:
                urls.append(line)
    return urls


def main():
    parser = argparse.ArgumentParser(description="微信公众号文章快速提取器")
    parser.add_argument("--url", help="单个文章 URL (mp.weixin.qq.com/s/...)")
    parser.add_argument("--input", help="输入文件，包含 URL 列表（每行一个），或 CSV（配合 --csv）")
    parser.add_argument("--csv", action="store_true", help="输入文件为 CSV，取第一列为 URL")
    parser.add_argument("--outdir", default="output", help="输出目录（默认 ./output）")
    parser.add_argument("--format", default="md", choices=["md", "html", "json"], help="输出格式")
    parser.add_argument("--download-images", action="store_true", help="下载文章图片并替换为本地路径")
    parser.add_argument("--workers", type=int, default=4, help="并发线程数（默认 4）")
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT, help="网络超时（秒）")
    args = parser.parse_args()

    urls = []
    if args.url:
        urls.append(args.url.strip())
    if args.input:
        if not os.path.exists(args.input):
            print("input file not found:", args.input, file=sys.stderr)
            sys.exit(1)
        urls.extend(read_urls_from_file(args.input, csv_mode=args.csv))

    if not urls:
        print("请通过 --url 或 --input 提供一个或多个文章 URL", file=sys.stderr)
        parser.print_help()
        sys.exit(1)

    os.makedirs(args.outdir, exist_ok=True)

    results = []
    if len(urls) == 1 or args.workers <= 1:
        for u in urls:
            res = process_url(u, args.outdir, args.format, args.download_images, timeout=args.timeout)
            results.append(res)
            if res.get("ok"):
                print(f"[OK] {u} -> {res.get('path')}")
            else:
                print(f"[ERR] {u} -> {res.get('error')}")
    else:
        with ThreadPoolExecutor(max_workers=args.workers) as ex:
            futures = {ex.submit(process_url, u, args.outdir, args.format, args.download_images, args.timeout): u for u in urls}
            for f in tqdm(as_completed(futures), total=len(futures), desc="processing"):
                res = f.result()
                results.append(res)
                if res.get("ok"):
                    tqdm.write(f"[OK] {res.get('url')} -> {res.get('path')}")
                else:
                    tqdm.write(f"[ERR] {res.get('url')} -> {res.get('error')}")

    # summary
    ok = sum(1 for r in results if r.get("ok"))
    err = len(results) - ok
    print(f"完成: 成功 {ok}，失败 {err}，输出目录: {os.path.abspath(args.outdir)}")


if __name__ == "__main__":
    main()