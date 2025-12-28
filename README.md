# extract-tool

# 微信公众号文章快速提取器

一个用于批量/单篇提取微信公众号文章内容的轻量 Python 工具，支持导出为 Markdown/HTML/JSON，并可选下载文章内图片。

注意：请仅在拥有合法权限或爬取公开内容且遵守目标平台服务条款的情况下使用此工具。公众号页面可能有反爬机制，工具仅提供常见场景的实用方案，遇到需要 JS 渲染或登录的页面请改用 浏览器自动化（Selenium/Playwright）。

## 功能
- 从单个 `mp.weixin.qq.com` 文章 URL 提取标题、作者、发布日期、正文 HTML 和文本（Markdown）。
- 支持批量提取（从文本文件或 CSV 列表读取 URL）。
- 可选下载文章中图片并把 src 替换为本地路径。
- 输出为 Markdown、HTML 或 JSON 文件。
- 并发提取（可配置线程数）并带进度显示。

## 依赖
推荐在虚拟环境中安装：

```
pip install requests beautifulsoup4 lxml html2text tqdm
```

（若要保存图片并处理更复杂图片格式可额外安装 Pillow，但不是必须）

## 用法
示例：
- 提取单篇并导出 Markdown：
```
python wechat_extract.py --url "https://mp.weixin.qq.com/s/XXXXXXXX" --outdir output --format md
```

- 批量从 urls.txt（每行一个 URL）提取，下载图片，输出 HTML：
```
python wechat_extract.py --input urls.txt --outdir output --format html --download-images --workers 5
```

- 从 CSV 的第一列读取 URL（示例），输出 JSON：
```
python wechat_extract.py --input urls.csv --csv --outdir output --format json
```

## 常见选项
- --url: 单个文章 URL
- --input: 包含 URL 的文件（每行一个或 CSV）
- --csv: 指明 input 是 CSV（默认取第一列为 URL）
- --outdir: 输出目录（默认 ./output）
- --format: md / html / json
- --download-images: 下载文章图片（会创建 outdir/images）
- --workers: 并发线程数（默认 4）
- --timeout: 网络超时（秒）

## 限制与建议
- 有些公众号会使用 JS 动态加载或对请求做严格校验（Referer、Cookie、UA、签名等），此脚本仅用常见请求头以提高成功率；若失败请使用浏览器自动化（Selenium / Playwright）或在浏览器中保存页面再解析。
- 请控制请求速率，尊重目标站点带宽与服务条款。
- 对于需要登录/付费/限制访问的内容，本工具无法绕过。

## 示例输出结构（JSON）
每篇文章保存为 {safe_title}.json，字段：
- url, title, author, date, content_html, content_markdown, images (列表本地或远程 URL)

