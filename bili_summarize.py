#!/usr/bin/env python3
"""
B站视频 → AI 总结笔记

用法:
  python3 bili_summarize.py BV1ooDyBmE6v                  # 输出 Markdown
  python3 bili_summarize.py BV1ooDyBmE6v --format org     # 输出 Org-mode
  python3 bili_summarize.py BV1ooDyBmE6v --format html    # 输出 HTML
  python3 bili_summarize.py BV1ooDyBmE6v --login            # 强制重新登录
  python3 bili_summarize.py BV1ooDyBmE6v --no-summarize    # 仅下载字幕，不总结

需要设置环境变量 DEEPSEEK_KEY，或放在 ~/.deepseek_key 文件中。
"""

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

import requests

# ─── 配置 ──────────────────────────────────────────────────────────────────

SUBTITLE_DIR = Path.home() / ".bilibili-subtitles"
COOKIE_FILE = Path.home() / ".bilibili_cookies.json"
DEEPSEEK_KEY_FILE = Path.home() / ".deepseek_key"
CHUNK_SIZE = 6000  # 每段发给 LLM 的字数上限

# ─── BV 号提取 ─────────────────────────────────────────────────────────────

def extract_bvid(raw: str) -> str:
    m = re.search(r"(BV[a-zA-Z0-9]{10})", raw)
    if m:
        return m.group(1)
    raise ValueError(f"无法从 '{raw}' 中提取 BV 号")


# ─── 字幕下载 ──────────────────────────────────────────────────────────────

def _init_browser():
    from playwright.sync_api import sync_playwright
    p = sync_playwright().start()
    browser = p.chromium.launch(headless=True, args=["--no-sandbox", "--no-proxy-server"])
    context = browser.new_context(
        viewport={"width": 1280, "height": 720},
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/125.0.0.0 Safari/537.36"
        ),
        locale="zh-CN",
    )
    return p, browser, context


def _load_cookies(context) -> bool:
    if COOKIE_FILE.exists():
        context.add_cookies(json.loads(COOKIE_FILE.read_text()))
        return True
    return False


def _qr_login(page, timeout=180):
    print("🔐 正在打开登录页...", file=sys.stderr)
    page.goto("https://passport.bilibili.com/login", wait_until="domcontentloaded", timeout=30000)
    page.wait_for_timeout(2000)

    qr_path = SUBTITLE_DIR / "bilibili_qr.png"
    SUBTITLE_DIR.mkdir(parents=True, exist_ok=True)
    qr_element = page.query_selector(".bili-qrcode-img, .login-qrcode-img, img[src*='qrcode']")
    if qr_element:
        qr_element.screenshot(path=str(qr_path))
    else:
        page.screenshot(path=str(qr_path))

    win_path = os.popen(f"wslpath -w {qr_path}").read().strip()
    print(f"📱 二维码: {win_path}", file=sys.stderr)
    print(f"   请用 Bilibili App 扫码 (超时 {timeout}s)...", file=sys.stderr)

    deadline = time.time() + timeout
    while time.time() < deadline:
        cookies = page.context.cookies("https://bilibili.com")
        if any(c['name'] == 'SESSDATA' for c in cookies):
            COOKIE_FILE.write_text(json.dumps(cookies, indent=2))
            print("✅ 登录成功", file=sys.stderr)
            return True
        if int(time.time()) % 15 == 0:
            print("   ⏳ 等待扫码中...", file=sys.stderr)
        time.sleep(2)
    print("❌ 登录超时", file=sys.stderr)
    return False


def download_subtitle(bvid: str, force_login: bool = False) -> dict:
    """返回 {bvid, title, cid, aid, chapters, subtitles, language}。"""
    SUBTITLE_DIR.mkdir(parents=True, exist_ok=True)

    p, browser, context = _init_browser()
    try:
        page = context.new_page()

        # 登录
        if not force_login:
            _load_cookies(context)
        if force_login or not COOKIE_FILE.exists():
            if not _qr_login(page):
                raise RuntimeError("登录失败")

        # 获取字幕元数据
        print("🔍 获取字幕列表...", file=sys.stderr)
        page.goto(f"https://www.bilibili.com/video/{bvid}", wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(4000)

        result = page.evaluate(
            """
            async (bvid) => {
                const r = await fetch(
                    'https://api.bilibili.com/x/player/wbi/v2?bvid=' + bvid + '&cid=37424595579',
                    {credentials: 'include'}
                );
                return await r.json();
            }
            """,
            bvid,
        )
        data = result.get("data", {})
        cid = data.get("cid", 0)

        result = page.evaluate(
            """
            async ([bvid, cid]) => {
                const r = await fetch(
                    'https://api.bilibili.com/x/player/wbi/v2?bvid=' + bvid + '&cid=' + cid,
                    {credentials: 'include'}
                );
                return await r.json();
            }
            """,
            [bvid, str(cid)],
        )

        data = result.get("data", {})
        aid = data.get("aid", 0)
        subtitle_info = data.get("subtitle", {})
        subtitles = subtitle_info.get("subtitles", [])
        title = data.get("title", bvid)

        # 章节信息
        chapters = []
        for vp in data.get("view_points", []):
            chapters.append({
                "from": vp.get("from", 0),
                "title": vp.get("content", ""),
            })

        if not subtitles:
            logged = any('SESSDATA' in c['name'] for c in page.context.cookies())
            if not logged:
                raise RuntimeError("未登录，AI 字幕需要登录。请加 --login 重试。")
            raise RuntimeError("该视频没有字幕")

        # 优先中文字幕
        chosen = subtitles[0]
        for s in subtitles:
            if "zh" in s.get("lan", ""):
                chosen = s
                break

        subtitle_url = chosen.get("subtitle_url", "")
        if subtitle_url.startswith("//"):
            subtitle_url = "https:" + subtitle_url

        print(f"📺 {title[:60]}", file=sys.stderr)
        print(f"⬇ 下载 {chosen.get('lan_doc', '')} 字幕...", file=sys.stderr)

        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Referer": "https://www.bilibili.com/",
        }
        resp = requests.get(subtitle_url, headers=headers, timeout=30)
        resp.raise_for_status()
        body = resp.json().get("body", [])

        # 保存原始数据
        cache_file = SUBTITLE_DIR / f"{bvid}.json"
        cache_file.write_text(
            json.dumps({
                "bvid": bvid, "title": title, "cid": cid, "aid": aid,
                "language": chosen.get("lan_doc", ""), "subtitles": body,
                "chapters": chapters,
            }, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        print(f"✅ {len(body)} 行字幕已缓存", file=sys.stderr)
        return {
            "bvid": bvid, "title": title, "cid": cid, "aid": aid,
            "language": chosen.get("lan_doc", ""),
            "subtitles": body, "chapters": chapters,
        }

    finally:
        browser.close()
        p.stop()


# ─── AI 总结 ───────────────────────────────────────────────────────────────

def _get_deepseek_key() -> str:
    """从环境变量或文件读取 DeepSeek API key。"""
    key = os.environ.get("DEEPSEEK_KEY", "")
    if key:
        return key
    if DEEPSEEK_KEY_FILE.exists():
        return DEEPSEEK_KEY_FILE.read_text().strip()

    # 尝试从 n8n 数据库解密
    try:
        import sqlite3, base64, hashlib
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

        config_path = Path.home() / ".n8n/config"
        if config_path.exists():
            with open(config_path) as f:
                cfg = json.load(f)
            key_bytes = cfg.get("encryptionKey", "").encode()
            conn = sqlite3.connect(str(Path.home() / ".n8n/database.sqlite"))
            cursor = conn.execute(
                "SELECT data FROM credentials_entity WHERE type = 'deepSeekApi' LIMIT 1"
            )
            row = cursor.fetchone()
            conn.close()
            if row:
                enc = base64.b64decode(row[0])
                salt, ct = enc[8:16], enc[16:]
                dk = b""
                while len(dk) < 48:
                    dk += hashlib.md5(dk[-16:] + key_bytes + salt if dk else key_bytes + salt).digest()
                cipher = Cipher(algorithms.AES(dk[:32]), modes.CBC(dk[32:48]))
                decryptor = cipher.decryptor()
                plain = decryptor.update(ct) + decryptor.finalize()
                plain = plain[:-plain[-1]]
                return json.loads(plain)["apiKey"]
    except Exception:
        pass

    raise RuntimeError(
        "未找到 DeepSeek API key。请设置环境变量 DEEPSEEK_KEY 或创建 ~/.deepseek_key 文件。"
    )


def _format_subtitle_text(subtitle_data: dict) -> str:
    """将字幕转为 LLM 可读的纯文本。"""
    subs = subtitle_data["subtitles"]
    chapters = subtitle_data.get("chapters", [])

    lines = [f"标题: {subtitle_data['title']}", f"语言: {subtitle_data['language']}", ""]

    # 章节结构
    if chapters:
        lines.append("## 章节")
        for ch in chapters:
            ts = int(ch["from"])
            lines.append(f"- {ts//60:02d}:{ts%60:02d} {ch['title']}")
        lines.append("")

    lines.append("## 字幕全文")
    lines.append("")

    current_chapter_idx = 0
    for item in subs:
        t = int(item.get("from", 0))
        content = item.get("content", "")

        # 插入章节标记
        while current_chapter_idx < len(chapters) and t >= chapters[current_chapter_idx]["from"]:
            lines.append(f"\n### {chapters[current_chapter_idx]['title']}")
            current_chapter_idx += 1

        lines.append(f"[{t//60:02d}:{t%60:02d}] {content}")

    return "\n".join(lines)


def _chunk_text(text: str, max_chars: int = CHUNK_SIZE) -> list:
    """将长文本按字数分块，尽量在句号处断开。"""
    if len(text) <= max_chars:
        return [text]

    chunks = []
    while len(text) > max_chars:
        split_at = text.rfind("。", 0, max_chars)
        if split_at == -1:
            split_at = text.rfind("\n", 0, max_chars)
        if split_at == -1:
            split_at = max_chars
        chunks.append(text[:split_at + 1])
        text = text[split_at + 1:].strip()
    if text:
        chunks.append(text)
    return chunks


def _call_deepseek(prompt: str, api_key: str, max_tokens: int = 4096) -> str:
    """调用 DeepSeek API。"""
    resp = requests.post(
        "https://api.deepseek.com/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json={
            "model": "deepseek-chat",
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.3,
            "max_tokens": max_tokens,
        },
        timeout=180,
    )
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"]


def _summarize_chunk(chunk_text: str, chunk_idx: int, total: int, api_key: str) -> str:
    """总结一个字幕分块。"""
    prompt = f"""请将以下B站视频字幕（第 {chunk_idx + 1}/{total} 部分）整理为结构化笔记：

{chunk_text}

要求：
- 保留关键的技术细节和具体数据
- 用标题组织层次结构
- 提取核心观点和可操作要点
- 直接用中文输出，不要解释、不要寒暄"""

    print(f"  🤖 总结第 {chunk_idx + 1}/{total} 部分...", file=sys.stderr)
    return _call_deepseek(prompt, api_key)


def _merge_summaries(summaries: list, title: str, api_key: str) -> str:
    """合并多个分块总结为最终笔记。"""
    if len(summaries) == 1:
        return summaries[0]

    combined = "\n\n---\n\n".join(
        f"## 第 {i+1} 部分\n\n{s}" for i, s in enumerate(summaries)
    )
    prompt = f"""请将以下多段视频笔记合并整理为一份完整的结构化笔记。

视频标题: {title}

{combined}

要求：
- 去除重复内容
- 统一标题层级
- 保留所有关键信息
- 输出完整的结构化笔记（Markdown 格式）
- 直接用中文输出"""

    print(f"  🔗 合并 {len(summaries)} 段总结...", file=sys.stderr)
    return _call_deepseek(prompt, api_key, max_tokens=4096)


def summarize(subtitle_data: dict, api_key: str = "") -> str:
    """总结字幕内容，返回 Markdown 格式的笔记。"""
    if not api_key:
        api_key = _get_deepseek_key()

    title = subtitle_data["title"]
    text = _format_subtitle_text(subtitle_data)
    chunks = _chunk_text(text)

    print(f"📝 字幕共 {len(text)} 字，分 {len(chunks)} 段处理", file=sys.stderr)

    summaries = []
    for i, chunk in enumerate(chunks):
        s = _summarize_chunk(chunk, i, len(chunks), api_key)
        summaries.append(s)

    result = _merge_summaries(summaries, title, api_key)
    return result


# ─── 格式化输出 ────────────────────────────────────────────────────────────

def _to_org(markdown: str, title: str) -> str:
    """Markdown → Org-mode 格式。"""
    lines = [f"#+TITLE: {title}", ""]
    for line in markdown.split("\n"):
        if line.startswith("### "):
            lines.append(f"*** {line[4:]}")
        elif line.startswith("## "):
            lines.append(f"** {line[3:]}")
        elif line.startswith("# "):
            lines.append(f"* {line[2:]}")
        elif line.startswith("- "):
            lines.append(f"- {line[2:]}")
        elif re.match(r"^\d+\. ", line):
            lines.append(f"1. {line.split('. ', 1)[1]}")
        else:
            lines.append(line)
    return "\n".join(lines)


def _to_html(markdown: str, title: str) -> str:
    """Markdown → 精美 HTML。"""
    # 简单的 Markdown → HTML 转换
    html = []
    in_list = False
    for line in markdown.split("\n"):
        line = line.strip()
        if not line:
            if in_list:
                html.append("</ul>")
                in_list = False
            html.append("")
            continue

        if line.startswith("### "):
            if in_list:
                html.append("</ul>")
                in_list = False
            html.append(f"<h3>{line[4:]}</h3>")
        elif line.startswith("## "):
            if in_list:
                html.append("</ul>")
                in_list = False
            html.append(f"<h2>{line[3:]}</h2>")
        elif line.startswith("# "):
            if in_list:
                html.append("</ul>")
                in_list = False
            html.append(f"<h1>{line[2:]}</h1>")
        elif line.startswith("- "):
            if not in_list:
                html.append("<ul>")
                in_list = True
            html.append(f"<li>{line[2:]}</li>")
        else:
            if in_list:
                html.append("</ul>")
                in_list = False
            # 加粗处理
            line = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", line)
            html.append(f"<p>{line}</p>")

    if in_list:
        html.append("</ul>")

    body = "\n".join(html)
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{title}</title>
<style>
  body {{ font-family: -apple-system, sans-serif; max-width: 720px; margin: 0 auto; padding: 40px 20px; line-height: 1.7; color: #1a1a1a; }}
  h1 {{ font-size: 1.8rem; border-bottom: 2px solid #2563eb; padding-bottom: 8px; }}
  h2 {{ font-size: 1.3rem; margin-top: 32px; color: #2563eb; }}
  h3 {{ font-size: 1.1rem; margin-top: 24px; }}
  ul {{ padding-left: 20px; }}
  li {{ margin: 4px 0; }}
  p {{ margin: 12px 0; }}
  strong {{ color: #2563eb; }}
</style>
</head>
<body>
<h1>{title}</h1>
{body}
</body>
</html>"""


def format_output(markdown: str, title: str, fmt: str) -> str:
    if fmt == "org":
        return _to_org(markdown, title)
    elif fmt == "html":
        return _to_html(markdown, title)
    else:
        return f"# {title}\n\n{markdown}"


# ─── CLI ────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="B站视频 → AI 总结笔记")
    parser.add_argument("video", help="BV 号或视频 URL")
    parser.add_argument("--format", "-f", choices=["md", "org", "html"], default="md",
                        help="输出格式 (默认: md)")
    parser.add_argument("--login", action="store_true", help="强制重新扫码登录")
    parser.add_argument("--no-summarize", action="store_true", help="只下载字幕，不总结")
    parser.add_argument("--output", "-o", type=str, default="",
                        help="输出文件路径")
    args = parser.parse_args()

    bvid = extract_bvid(args.video)
    print(f"🎬 BV: {bvid}", file=sys.stderr)

    # 1. 下载字幕
    subtitle_data = download_subtitle(bvid, force_login=args.login)

    if args.no_summarize:
        print("⏹ 跳过总结（--no-summarize）", file=sys.stderr)
        return

    # 2. AI 总结
    print(f"\n🤖 开始 AI 总结...", file=sys.stderr)
    markdown = summarize(subtitle_data)

    # 3. 格式化输出
    title = subtitle_data["title"]
    output = format_output(markdown, title, args.format)

    if args.output:
        Path(args.output).write_text(output, encoding="utf-8")
        print(f"✅ 已保存: {args.output}", file=sys.stderr)
    else:
        ext = {"md": "md", "org": "org", "html": "html"}[args.format]
        outpath = SUBTITLE_DIR / f"{bvid}_summary.{ext}"
        outpath.write_text(output, encoding="utf-8")
        print(f"✅ 已保存: {outpath}", file=sys.stderr)

    print(f"\n{output[:200]}", file=sys.stderr if len(output) > 200 else sys.stdout)


if __name__ == "__main__":
    main()
