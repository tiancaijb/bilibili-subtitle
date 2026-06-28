"""B站视频字幕下载命令行工具"""

import argparse
import sys
from pathlib import Path

from .downloader import download_subtitle


def main():
    parser = argparse.ArgumentParser(
        description="B站视频 AI 字幕下载器",
        usage="bili-subtitle <BV号> [options]",
    )
    parser.add_argument("video", help="BV 号或视频 URL")
    parser.add_argument("--login", action="store_true", help="强制重新扫码登录")
    parser.add_argument("-o", "--output", type=str, default="",
                        help="输出文件路径")
    parser.add_argument("--json", action="store_true", help="输出 JSON 格式（默认 TXT）")
    args = parser.parse_args()

    import re
    m = re.search(r"(BV[a-zA-Z0-9]{10})", args.video)
    if not m:
        print(f"错误: 无法从 '{args.video}' 提取 BV 号", file=sys.stderr)
        sys.exit(1)
    bvid = m.group(1)

    print(f"🎬 BV: {bvid}", file=sys.stderr)

    subtitle_data = download_subtitle(bvid, force_login=args.login)

    out_dir = Path.home() / ".bilibili-subtitles"
    out_dir.mkdir(parents=True, exist_ok=True)

    if args.json:
        import json
        outpath = args.output or str(out_dir / f"{bvid}.json")
        Path(outpath).write_text(
            json.dumps(subtitle_data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    else:
        outpath = args.output or str(out_dir / f"{bvid}.txt")
        lines = []
        for item in subtitle_data["subtitles"]:
            t = int(item.get("from", 0))
            lines.append(f"{t//60:02d}:{t%60:02d} {item.get('content', '')}")
        Path(outpath).write_text("\n".join(lines), encoding="utf-8")

    print(f"✅ {outpath}", file=sys.stderr)


if __name__ == "__main__":
    main()
