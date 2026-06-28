#!/usr/bin/env python3
"""
B站 AI 字幕下载器

用法:
  python3 download.py BV1ooDyBmE6v           # 下载字幕（已登录则直接下）
  python3 download.py BV1ooDyBmE6v --login    # 强制重新扫码登录
"""

import argparse
import json
import os
import re
import requests
import sys
import time
from pathlib import Path

COOKIE_FILE = os.path.expanduser("~/.bilibili_cookies.json")


def extract_bvid(raw: str) -> str:
    m = re.search(r"(BV[a-zA-Z0-9]{10})", raw)
    if m:
        return m.group(1)
    raise ValueError(f"无法从 '{raw}' 中提取 BV 号")


def init_browser(headless: bool = True):
    from playwright.sync_api import sync_playwright
    p = sync_playwright().start()
    browser = p.chromium.launch(
        headless=headless,
        args=["--no-sandbox", "--no-proxy-server"],
    )
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


def load_saved_cookies(context) -> bool:
    if Path(COOKIE_FILE).exists():
        context.add_cookies(json.loads(Path(COOKIE_FILE).read_text()))
        return True
    return False


def wait_for_login(page, timeout: int = 180):
    """打开登录页，截图二维码让用户扫。"""
    print("🔐 正在打开登录页...", file=sys.stderr)
    page.goto("https://passport.bilibili.com/login", wait_until="domcontentloaded", timeout=30000)
    page.wait_for_timeout(2000)

    qr_path = os.path.expanduser("~/.bilibili_qr.png")
    qr_element = page.query_selector(".bili-qrcode-img, .login-qrcode-img, img[src*='qrcode']")
    if qr_element:
        qr_element.screenshot(path=qr_path)
    else:
        page.screenshot(path=qr_path)

    win_path = os.popen(f"wslpath -w {qr_path}").read().strip()
    print(f"📱 二维码: {win_path}", file=sys.stderr)
    print(f"   请用 Bilibili App 扫码 (超时 {timeout}s)...", file=sys.stderr)

    deadline = time.time() + timeout
    while time.time() < deadline:
        cookies = page.context.cookies("https://bilibili.com")
        cookie_names = [c['name'] for c in cookies]
        if 'SESSDATA' in cookie_names:
            Path(COOKIE_FILE).write_text(json.dumps(cookies, indent=2))
            print("✅ 登录成功", file=sys.stderr)
            return True
        if int(time.time()) % 15 == 0:
            print("   ⏳ 等待扫码中...", file=sys.stderr)
        time.sleep(2)
    print("❌ 登录超时", file=sys.stderr)
    return False


def fetch_subtitle_urls(page, bvid: str) -> tuple:
    """调用 player/wbi/v2，返回 (cid, aid, subtitle_list, title)。"""
    page.goto(f"https://www.bilibili.com/video/{bvid}", wait_until="domcontentloaded", timeout=30000)
    page.wait_for_timeout(4000)

    result = page.evaluate(
        """
        async (bvid) => {
            const r = await fetch(
                'https://api.bilibili.com/x/player/wbi/v2?bvid=' + bvid +
                '&cid=' + '37424595579',  // 先不带 cid
                {credentials: 'include'}
            );
            return await r.json();
        }
        """,
        bvid,
    )
    data = result.get("data", {})
    cid = data.get("cid", 0)

    # 带 cid 再调一次
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

    return cid, aid, subtitles, title


def download_subtitle(subtitle_url: str) -> list:
    """下载字幕 JSON 文件。"""
    if subtitle_url.startswith("//"):
        subtitle_url = "https:" + subtitle_url

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Referer": "https://www.bilibili.com/",
    }
    resp = requests.get(subtitle_url, headers=headers, timeout=30)
    resp.raise_for_status()
    return resp.json().get("body", [])


def main():
    parser = argparse.ArgumentParser(description="B站 AI 字幕下载器")
    parser.add_argument("video", help="BV 号或视频 URL")
    parser.add_argument("--login", action="store_true", help="强制重新扫码登录")
    parser.add_argument("--output", "-o", type=str, default="", help="输出文件路径")
    args = parser.parse_args()

    bvid = extract_bvid(args.video)
    print(f"🎬 BV: {bvid}", file=sys.stderr)

    p, browser, context = init_browser(headless=True)
    try:
        page = context.new_page()

        # 1. 加载 Cookie / 登录
        if not args.login:
            load_saved_cookies(context)
        if args.login or not Path(COOKIE_FILE).exists():
            if not wait_for_login(page):
                sys.exit(1)

        # 2. 获取字幕列表
        print("🔍 获取字幕列表...", file=sys.stderr)
        cid, aid, subtitles, title = fetch_subtitle_urls(page, bvid)

        if not subtitles:
            logged = any('SESSDATA' in c['name'] for c in page.context.cookies())
            if not logged:
                print("❌ 未登录，AI 字幕需要登录", file=sys.stderr)
                print("   请加 --login 参数重试", file=sys.stderr)
            else:
                print("❌ 该视频没有字幕", file=sys.stderr)
            sys.exit(1)

        print(f"📺 标题: {title[:60]}", file=sys.stderr)
        print(f"   找到 {len(subtitles)} 个字幕:", file=sys.stderr)
        lang_names = [s.get("lan_doc", s.get("lan", "?")) for s in subtitles]
        print(f"   {', '.join(lang_names)}", file=sys.stderr)

        # 3. 优先下载中文字幕
        chosen = subtitles[0]
        for s in subtitles:
            lan = s.get("lan", "")
            if "zh" in lan:
                chosen = s
                break

        subtitle_url = chosen.get("subtitle_url", "")
        if not subtitle_url:
            print("❌ 字幕 URL 为空", file=sys.stderr)
            sys.exit(1)

        print(f"⬇ 下载 {chosen.get('lan_doc', '')} 字幕...", file=sys.stderr)
        body = download_subtitle(subtitle_url)

        # 4. 保存
        output = args.output or f"{bvid}_subtitle.json"
        result = {
            "bvid": bvid,
            "title": title,
            "cid": cid,
            "aid": aid,
            "language": chosen.get("lan_doc", ""),
            "subtitles": body,
        }
        Path(output).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

        # 纯文本版
        lines = []
        for item in body:
            t = int(item.get("from", 0))
            lines.append(f"{t//60:02d}:{t%60:02d} {item.get('content', '')}")
        txt_path = Path(output).with_suffix(".txt")
        txt_path.write_text("\n".join(lines), encoding="utf-8")

        print(f"✅ JSON: {output} ({len(body)} 行)", file=sys.stderr)
        print(f"✅ TXT:  {txt_path}", file=sys.stderr)

    finally:
        browser.close()
        p.stop()


if __name__ == "__main__":
    main()
