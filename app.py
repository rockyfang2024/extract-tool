#!/usr/bin/env python3
"""
Flask web app wrapping wechat_extract.py 的核心函数，提供可视化界面并支持设置默认保存路径。

功能:
- 在 Web UI 输入单个 URL 或多行 URL 批量提取
- 选择输出格式 (md/html/json) 和是否下载图片
- 指定/使用默认保存路径（可在 Settings 页面修改并持久化到 config.json）
- 显示处理结果并提供下载链接 (从服务器上的输出目录)
"""
import os
import json
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from flask import Flask, render_template, request, redirect, url_for, send_from_directory, flash

# 导入 wechat_extract 中的 process_url 函数
import wechat_extract as we

APP_DIR = Path(__file__).parent.resolve()
CONFIG_PATH = APP_DIR / "config.json"
STATIC_OUTPUT_BASE = APP_DIR / "outputs"  # 安全起见，把所有导出放到 app 的 outputs 子目录

os.makedirs(STATIC_OUTPUT_BASE, exist_ok=True)

def load_config():
    if CONFIG_PATH.exists():
        try:
            return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    # 默认配置
    cfg = {"default_outdir": str(STATIC_OUTPUT_BASE / "default")}
    save_config(cfg)
    return cfg

def save_config(cfg):
    CONFIG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")

cfg = load_config()
os.makedirs(cfg["default_outdir"], exist_ok=True)

app = Flask(__name__)
app.secret_key = "change-me-in-production"

@app.route("/", methods=["GET"])
def index():
    cfg = load_config()
    return render_template("index.html", default_outdir=cfg.get("default_outdir", ""))

@app.route("/extract", methods=["POST"])
def extract():
    urls_input = request.form.get("urls", "")
    single_url = request.form.get("url", "").strip()
    outdir = request.form.get("outdir", "").strip() or load_config().get("default_outdir")
    fmt = request.form.get("format", "md")
    download_images = bool(request.form.get("download_images"))
    workers = int(request.form.get("workers") or 4)
    timeout = int(request.form.get("timeout") or 15)

    # Normalize outdir: if it's not absolute, place it under STATIC_OUTPUT_BASE
    outdir_path = Path(outdir)
    if not outdir_path.is_absolute():
        outdir_path = STATIC_OUTPUT_BASE / outdir_path
    outdir_path = outdir_path.resolve()
    # Ensure outdir is inside STATIC_OUTPUT_BASE for security
    try:
        outdir_path.relative_to(STATIC_OUTPUT_BASE.resolve())
    except Exception:
        flash("输出目录必须在服务器允许的 outputs 目录下（限制为安全考虑）。", "error")
        return redirect(url_for("index"))

    os.makedirs(outdir_path, exist_ok=True)

    urls = []
    if single_url:
        urls.append(single_url)
    if urls_input:
        for line in urls_input.splitlines():
            line = line.strip()
            if line:
                urls.append(line)

    if not urls:
        flash("请提供至少一个 URL（单个 URL 或多行 URL）。", "error")
        return redirect(url_for("index"))

    results = []

    # Use ThreadPoolExecutor to parallelize without blocking Flask main thread too long
    def run_tasks():
        nonlocal results
        with ThreadPoolExecutor(max_workers=min(workers, len(urls))) as ex:
            futures = {ex.submit(we.process_url, u, str(outdir_path), fmt, download_images, timeout): u for u in urls}
            for f in as_completed(futures):
                res = f.result()
                results.append(res)
        # Persist a small results.json in outdir for reference
        try:
            (outdir_path / "results.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass

    thread = threading.Thread(target=run_tasks)
    thread.start()
    thread.join()  # for simplicity we wait — for longer tasks you may want to dispatch and poll

    # Build list of output files to show (successful entries)
    saved_files = []
    for r in results:
        if r.get("ok") and r.get("path"):
            # convert absolute path to relative path under STATIC_OUTPUT_BASE for download route
            try:
                rel = Path(r["path"]).resolve().relative_to(STATIC_OUTPUT_BASE.resolve())
                saved_files.append(str(rel).replace("\\", "/"))
            except Exception:
                # If path is outside base, skip exposing as download
                saved_files.append(r["path"])

    return render_template("result.html", results=results, saved_files=saved_files, outdir=str(outdir_path))

@app.route("/download/<path:filepath>", methods=["GET"])
def download(filepath):
    # ensure filepath is within STATIC_OUTPUT_BASE
    safe_base = STATIC_OUTPUT_BASE.resolve()
    target = (safe_base / filepath).resolve()
    try:
        target.relative_to(safe_base)
    except Exception:
        flash("非法的下载路径", "error")
        return redirect(url_for("index"))
    directory = str(target.parent)
    filename = str(target.name)
    return send_from_directory(directory, filename, as_attachment=True)

@app.route("/settings", methods=["GET", "POST"])
def settings():
    cfg = load_config()
    if request.method == "POST":
        new_default = request.form.get("default_outdir", "").strip()
        if new_default:
            # normalize to inside STATIC_OUTPUT_BASE
            p = Path(new_default)
            if not p.is_absolute():
                p = STATIC_OUTPUT_BASE / p
            p = p.resolve()
            try:
                p.relative_to(STATIC_OUTPUT_BASE.resolve())
            except Exception:
                flash("默认路径必须在服务器允许的 outputs 目录下。", "error")
                return redirect(url_for("settings"))
            os.makedirs(p, exist_ok=True)
            cfg["default_outdir"] = str(p)
            save_config(cfg)
            flash("默认保存路径已更新。", "success")
        else:
            flash("默认路径不能为空。", "error")
        return redirect(url_for("settings"))
    return render_template("settings.html", config=cfg)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8873, debug=True)