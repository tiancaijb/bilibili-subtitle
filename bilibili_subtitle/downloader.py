"""B站 AI 字幕下载器 —— 纯模块，不含 CLI。"""

import json
import os
import sys
import time
from pathlib import Path

import requests

COOKIE_FILE = Path.home() / ".bilibili_cookies.json"
SUBTITLE_DIR = Path.home() / ".bilibili-subtitles"


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


def _windows_login() -> bool:
    """在 Windows 上用 playwright-cli 弹出浏览器登录。"""
    import subprocess

    print("🪟 在 Windows 上打开浏览器...", file=sys.stderr)
    print("   请在弹出的浏览器中扫码登录，然后关闭浏览器", file=sys.stderr)

    # 步骤1：打开浏览器（阻塞，等用户关闭浏览器后才返回）
    subprocess.run(
        ["powershell.exe", "-NoProfile", "-Command",
         f'$auth = "$env:USERPROFILE\\.bilibili_auth.json"; '
         f'playwright-cli open https://passport.bilibili.com/login --persistent --headed; '
         f'playwright-cli state-save $auth; '
         f'playwright-cli close'],
        timeout=300,
    )

    # 步骤2：读取 auth 文件
    try:
        result = subprocess.run(
            ["powershell.exe", "-NoProfile",
             "-Command",
             f'$auth = "$env:USERPROFILE\\.bilibili_auth.json"; Get-Content $auth -Encoding UTF8'],
            capture_output=True, timeout=10,
        )
        raw = result.stdout
        if raw:
            for enc in ['utf-8-sig', 'utf-16', 'utf-8']:
                try:
                    txt = raw.decode(enc).strip()
                    if txt:
                        break
                except:
                    txt = ''
            import re
            txt = txt.lstrip('\ufeff')
            if txt.startswith('{') or txt.startswith('['):
                data = json.loads(txt)
                cookies = data.get("cookies", data if isinstance(data, list) else [])
                if any(c.get("name") == "SESSDATA" for c in cookies):
                    COOKIE_FILE.write_text(json.dumps(cookies, indent=2, ensure_ascii=False))
                    print(f"✅ 已加载 {len(cookies)} 个 cookies", file=sys.stderr)
                    return True
    except Exception as e:
        print(f"   读取失败: {e}", file=sys.stderr)

    print("❌ 登录失败", file=sys.stderr)
    return False


def _windows_fetch_subtitle(bvid: str) -> dict:
    """在 Windows 上用 playwright-cli + player/wbi/v2 API 获取字幕 URL。"""
    import subprocess, time, json, os

    url = f"https://www.bilibili.com/video/{bvid}"

    # 1. 后台打开视频页（persistent，复用登录态）
    subprocess.Popen(
        ["powershell.exe", "-NoProfile", "-Command",
         f'playwright-cli open "{url}" --persistent'],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    time.sleep(5)

    # 2. 写 .js 脚本到 Windows temp 目录
    js = (
        'async page => {'
        '  await page.waitForTimeout(2000);'
        '  const result = await page.evaluate(async () => {'
        '    const s = window.__INITIAL_STATE__;'
        '    const resp = await fetch("https://api.bilibili.com/x/player/wbi/v2?aid=" + s.aid + "&cid=" + s.cid + "&bvid=" + s.bvid, {credentials: "include"});'
        '    const data = await resp.json();'
        '    const subs = data?.data?.subtitle?.subtitles || [];'
        '    const target = subs.find(x => x.lan === "ai-zh") || subs.find(x => x.lan === "zh") || subs[0] || {};'
        '    return JSON.stringify({aid: data.data.aid || s.aid, cid: data.data.cid || s.cid, title: s.videoData.title, subtitle_url: target.subtitle_url || "", language: target.lan_doc || ""});'
        '  });'
        '  return result;'
        '}'
    )
    import tempfile
    js_file = os.path.join(tempfile.gettempdir(), "bili_fetch_wbi.js")
    with open(js_file, "w", encoding="utf-8") as f:
        f.write(js)

    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-Command",
         f'playwright-cli run-code --filename "{js_file}"'],
        capture_output=True, timeout=30,
    )

    # 3. 解析输出
    raw = result.stdout
    txt = ""
    for enc in ['utf-8-sig', 'utf-16', 'utf-8']:
        try:
            t = raw.decode(enc).strip()
            m = re.search(r'({.*})', t, re.DOTALL)
            if m: txt = m.group(1); break
        except: pass

    meta = json.loads(txt) if txt else {}
    subtitle_url = meta.get("subtitle_url", "")
    if subtitle_url and subtitle_url.startswith("//"):
        subtitle_url = "https:" + subtitle_url

    # 4. 关闭浏览器
    subprocess.run(
        ["powershell.exe", "-NoProfile", "-Command", "playwright-cli close"],
        timeout=5, capture_output=True,
    )

    return {
        "aid": meta.get("aid", 0), "cid": meta.get("cid", 0),
        "title": meta.get("title", bvid),
        "subtitle_url": subtitle_url,
        "language": meta.get("language", ""),
        "chapters": [],
    }



def _fetch_subtitle_meta(page, bvid: str) -> list:
    """调用 player/wbi/v2 + subtitle/web/view，返回字幕列表及内容。"""
    page.goto(f"https://www.bilibili.com/video/{bvid}", wait_until="domcontentloaded", timeout=30000)
    page.wait_for_timeout(4000)

    # 从 __INITIAL_STATE__ 取 aid、cid
    state = page.evaluate("() => window.__INITIAL_STATE__")
    aid = state.get("aid", 0)
    cid = state.get("cid", 0)
    title = state.get("videoData", {}).get("title", bvid)
    subtitle_list = state.get("videoData", {}).get("subtitle", {}).get("list", [])

    # 章节
    chapters = []
    vps = state.get("videoData", {}).get("view_points", []) or \
          state.get("videoData", {}).get("ugc_season", {}).get("view_points", []) or []
    for vp in vps:
        t = vp.get("from", 0)
        chapters.append({"from": t, "title": vp.get("content", "")})

    if not subtitle_list:
        return {"aid": aid, "cid": cid, "title": title, "subtitles": [], "chapters": chapters}

    # 优先中文 AI 字幕
    chosen = subtitle_list[0]
    for s in subtitle_list:
        lan = s.get("lan", "")
        if "ai" in lan and "zh" in lan:
            chosen = s
            break
        if "zh" in lan:
            chosen = s

    subtitle_url = chosen.get("subtitle_url", "")

    # 如果 subtitle_url 为空，等待页面自动加载字幕（拦截 aisubtitle.hdslb.com 请求）
    if not subtitle_url:
        print(f"  📡 等待字幕加载...", file=sys.stderr)
        with page.expect_response(
            lambda r: "aisubtitle.hdslb.com/bfs/subtitle" in r.url,
            timeout=20000,
        ) as resp_info:
            # 等待页面自动触发字幕下载
            page.wait_for_timeout(8000)
        resp = resp_info.value
        subtitle_url = resp.url

    return {
        "aid": aid, "cid": cid, "title": title,
        "subtitle_url": subtitle_url,
        "language": chosen.get("lan_doc", ""),
        "chapters": chapters,
    }
    

def _download_subtitle_json(subtitle_url: str) -> list:
    """从 CDN URL 下载字幕 JSON。"""
    if subtitle_url.startswith("//"):
        subtitle_url = "https:" + subtitle_url
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Referer": "https://www.bilibili.com/",
    }
    resp = requests.get(subtitle_url, headers=headers, timeout=30)
    resp.raise_for_status()
    return resp.json().get("body", [])


def download_subtitle(bvid: str, force_login: bool = False) -> dict:
    """下载 B 站视频 AI 字幕，返回结构化数据。"""
    SUBTITLE_DIR.mkdir(parents=True, exist_ok=True)

    # --- 获取字幕 URL ---
    if os.path.exists("/usr/bin/wslpath"):
        # WSL2 → 用 Windows playwright-cli
        if force_login or not COOKIE_FILE.exists():
            if not _windows_login():
                raise RuntimeError("登录失败")
        print("🔍 获取字幕列表...", file=sys.stderr)
        meta = _windows_fetch_subtitle(bvid)
    else:
        # 非 WSL2 → 用 Python Playwright
        p, browser, context = _init_browser()
        page = context.new_page()
        try:
            if not force_login:
                _load_cookies(context)
            if force_login or not COOKIE_FILE.exists():
                if not _qr_login(page, context):
                    raise RuntimeError("登录失败")
            print("🔍 获取字幕列表...", file=sys.stderr)
            meta = _fetch_subtitle_meta(page, bvid)
        finally:
            browser.close()
            p.stop()

    # --- 提取数据 ---
    aid = meta["aid"]
    cid = meta["cid"]
    title = meta["title"]
    chapters = meta.get("chapters", [])
    subtitle_url = meta["subtitle_url"]
    language = meta.get("language", "")

    if not subtitle_url:
        raise RuntimeError("该视频没有字幕")

    print(f"📺 {title[:60]}", file=sys.stderr)
    print(f"⬇ 下载 {language} 字幕...", file=sys.stderr)

    # --- 下载字幕 JSON ---
    body = _download_subtitle_json(subtitle_url)

    # --- 缓存 ---
    cache_file = SUBTITLE_DIR / f"{bvid}.json"
    cache_file.write_text(
        json.dumps({
            "bvid": bvid, "title": title, "cid": cid, "aid": aid,
            "language": language, "subtitles": body,
            "chapters": chapters,
        }, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"✅ {len(body)} 行字幕已缓存", file=sys.stderr)

    return {
        "bvid": bvid, "title": title, "cid": cid, "aid": aid,
        "language": language,
        "subtitles": body, "chapters": chapters,
    }
