---
name: bilibili-subtitle
description: 下载 Bilibili 视频 AI 字幕并保存到 org-mode vault。当用户提供 Bilibili BV 号，想要存字幕笔记时使用。
---

# Bilibili 字幕 → Org

下载 B 站视频 AI 字幕，存入 `~/org/` 作为 org-mode 笔记。

## 前置

```bash
pip install bilibili-subtitle
python -m playwright install chromium
```

## 工作流

### 1. 下载字幕

```bash
bili-subtitle BV号 --json
```

首次需要扫码：截图在 `~/.bilibili-subtitles/bilibili_qr.png`，用 `wslpath -w` 转换路径后让用户在 Windows 打开。

### 2. 读取字幕数据

字幕 JSON 保存在 `~/.bilibili-subtitles/BV号.json`。读取 `title`、`chapters`、`subtitles`。

### 3. 写入 Org

保存到 `~/org/bilibili/` 目录（不存在则创建）。文件名用 `{bvid} - {title}.org`（清理非法字符）。

**Org 格式：**

```org
:PROPERTIES:
:bvid: BV1ooDyBmE6v
:date: 2026-01-01
:tags: bilibili
:END:

* 标题

** 章节名
MM:SS 字幕内容
```

- 顶级标题 `*` 为视频标题
- 二级标题 `**` 为章节名
- 正文为 `MM:SS 内容` 格式
- 如果没章节信息，内容直接放在一级标题下

## 注意事项

- `bili-subtitle` 下载失败提示未登录 → 让用户运行 `bili-subtitle BV号 --login`
- 文件写入 `~/org/` 后自动 commit：`cd ~/org && git add -A && git commit -m "add bilibili note: {bvid}"`
- 用 `wslpath -w` 转换路径给用户看二维码时
