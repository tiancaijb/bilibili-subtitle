# Bilibili AI 字幕总结

输入 B 站 BV 号，自动下载 AI 字幕，用 DeepSeek 总结为结构化笔记。

## 安装

```bash
git clone https://github.com/tiancaijb/bilibili-subtitle-summarize.git
cd bilibili-subtitle-summarize
pip install -r requirements.txt
python3 -m playwright install chromium
```

## 设置 DeepSeek API Key

二选一：

```bash
# 方式1：环境变量
export DEEPSEEK_KEY=sk-xxx

# 方式2：文件
echo "sk-xxx" > ~/.deepseek_key
```

## 用法

```bash
# 下载字幕 + AI 总结，输出 Markdown
python3 bili_summarize.py BV1ooDyBmE6v

# 输出 Org-mode
python3 bili_summarize.py BV1ooDyBmE6v -f org

# 输出精美 HTML
python3 bili_summarize.py BV1ooDyBmE6v -f html

# 强制重新扫码登录
python3 bili_summarize.py BV1ooDyBmE6v --login

# 只下载字幕，不总结
python3 bili_summarize.py BV1ooDyBmE6v --no-summarize
```

首次使用会弹出二维码，用 Bilibili App 扫码登录。Cookie 缓存 24 小时。

## 流程

```
BV号 → 扫码登录(首次) → 下载AI字幕 → DeepSeek总结 → Markdown/Org/HTML
```

## 文件结构

```
bili_summarize.py    ← 主入口（下载 + 总结 + 格式化输出）
download.py          ← 纯字幕下载器
requirements.txt     ← 依赖
README.md
```

## License

MIT
