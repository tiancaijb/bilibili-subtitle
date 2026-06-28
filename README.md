# Bilibili 字幕下载

输入 B 站 BV 号，下载 AI 字幕。

## 安装

```bash
pip install bilibili-subtitle
python -m playwright install chromium
```

## 用法

```bash
# 下载字幕为 TXT
bili-subtitle BV1ooDyBmE6v

# 输出 JSON
bili-subtitle BV1ooDyBmE6v --json

# 强制重新扫码登录
bili-subtitle BV1ooDyBmE6v --login
```

首次使用弹出二维码，用 Bilibili App 扫码登录。Cookie 缓存 24 小时。

## 本地使用

本仓库还包含一个本地脚本 `bili_summarize.py`，可以下载字幕后自动调用 LLM 总结：

```bash
LLM_API_KEY=sk-xxx python3 bili_summarize.py BV1ooDyBmE6v
```

支持 `--model` 和 `--api-base` 切换模型，`-f org/html` 切换输出格式。

## License

MIT
