#!/usr/bin/env python3
"""
Immigration Radar · 分析层
读取抓取结果，调用 Claude API 深度分析，生成 Markdown 报告
"""

import sys, os
sys.path.insert(0, os.path.dirname(__file__))

import json, urllib.request, urllib.parse, subprocess
from datetime import datetime

# 加载 .env（从 scripts 的上级目录）
_env_path = os.path.join(os.path.dirname(__file__), "..", ".env")
if os.path.exists(_env_path):
    for line in open(_env_path):
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

from config import (
    RAW_OUTPUT, REPORT_OUTPUT,
    DISCORD_BOT_TOKEN, DISCORD_CHANNEL_ID, NZ_TZ
)

GROQ_API_KEY   = os.environ.get("GROQ_API_KEY", "")
ANALYSIS_MODEL = "llama-3.3-70b-versatile"

try:
    from groq import Groq as _Groq
    _groq_client = _Groq(api_key=GROQ_API_KEY) if GROQ_API_KEY else None
except ImportError:
    _groq_client = None


# ── Groq API 调用 ──────────────────────────────────────
def call_llm(prompt: str, system: str = "") -> str:
    if not GROQ_API_KEY:
        raise RuntimeError("GROQ_API_KEY 未设置")
    if not _groq_client:
        raise RuntimeError("groq 包未安装：pip install groq")

    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    r = _groq_client.chat.completions.create(
        model=ANALYSIS_MODEL,
        messages=messages,
        max_tokens=4000,
    )
    return r.choices[0].message.content


# ── 构建分析 Prompt ────────────────────────────────────
def build_prompt(data: dict) -> str:
    week = data["week"]
    items = data["items"]
    by_region = data["by_region"]

    # 按地区整理内容摘要
    region_summaries = []
    for region, region_items in by_region.items():
        if region == "🌍 全球":
            continue
        headlines = "\n".join(
            f"- [{i['source']}] {i['title'][:100]}"
            for i in region_items[:6]
        )
        region_summaries.append(f"**{region}**\n{headlines}")

    global_items = by_region.get("🌍 全球", [])
    global_headlines = "\n".join(
        f"- [{i['source']}] {i['title'][:100]}"
        for i in global_items[:5]
    )

    reddit_items = [i for i in items if i["type"] == "reddit"]
    reddit_section = "\n".join(
        f"- [{i['source']}] {i['title'][:100]}"
        for i in reddit_items[:10]
    )

    region_text = "\n\n".join(region_summaries)

    return f"""以下是本周（{week}）全球移民信息抓取结果，共 {len(items)} 条内容。

请生成一份深度分析报告，格式如下：

---

# 移民雷达 · {week}

## 🚨 本周重大变化
（分析本周最重要的政策变化、新规、重大事件，不超过5条，每条附策略影响分析）

## 🌍 分地区动态

### 🇳🇿 新西兰
（本周INZ政策动向、签证类别变化、Green List更新、Accredited Employer动态）

### 🇺🇸 美国
（USCIS更新、H1B/EB系列、绿卡排期变化、政治环境影响）

### 🇨🇦 加拿大
（Express Entry CRS分数线、各省提名、IRCC新政）

### 🇬🇧 英国
（Skilled Worker Visa门槛、薪资要求变化、ILR规则）

### 🇦🇺 澳大利亚
（各Subclass配额、State Nomination、技术移民动向）

### 🇪🇺 欧洲
（EU Blue Card、D7、数字游民签证、各国热点）

## 💬 社区热议
（Reddit高赞帖反映的移民者真实关切和经验，3-5条，附分析）

## 📊 趋势分析
（基于本周数据，分析全球移民政策的宏观走向，政策收紧/放松，哪些国家在抢人才）

## ✅ 本周策略建议
（针对不同移民目标人群的具体建议，如：考虑NZ技术移民的人、持H1B想换国家的人、寻找备选方案的人）

## 📌 下周关注点
（预告下周值得关注的政策公告日期、排期更新等）

---

以下是原始数据：

**各地区新闻：**
{region_text}

**全球/综合：**
{global_headlines}

**Reddit 社区热帖（反映移民者真实关切）：**
{reddit_section}

---

请用中文撰写，专业且实用，适合正在考虑或进行中的移民人士阅读。对于重要政策变化，请明确说明对申请人的实际影响。"""


# ── Discord 发送 ───────────────────────────────────────
def send_discord(text: str):
    url = f"https://discord.com/api/v10/channels/{DISCORD_CHANNEL_ID}/messages"
    # Discord 2000字符限制
    text = text[:1990]
    data = json.dumps({"content": text}).encode()
    req = urllib.request.Request(
        url, data=data,
        headers={
            "Authorization": f"Bot {DISCORD_BOT_TOKEN}",
            "Content-Type": "application/json",
            "User-Agent": "DiscordBot (https://discord.com, 10)",
        },
        method="POST"
    )
    with urllib.request.urlopen(req) as r:
        return r.status


# ── 存入 Bear ──────────────────────────────────────────
def save_to_bear(title: str, content: str):
    url = "bear://x-callback-url/create?title=" + urllib.parse.quote(title) + \
          "&text=" + urllib.parse.quote(content) + \
          "&tags=" + urllib.parse.quote("移民,雷达,政策")
    subprocess.run(["open", url])
    print(f"📓 已存入 Bear：{title}")


# ── 提取摘要（用于 Discord）─────────────────────────────
def extract_summary(report: str, week: str) -> str:
    lines = report.split("\n")
    summary_lines = [f"**🛂 移民雷达 · {week}** 报告已生成\n"]
    in_section = False
    count = 0
    for line in lines:
        if "## 🚨 本周重大变化" in line:
            in_section = True
            continue
        if in_section and line.startswith("## "):
            break
        if in_section and line.strip() and count < 5:
            summary_lines.append(line)
            count += 1
    summary_lines.append("\n📓 完整报告已存入 Bear")
    return "\n".join(summary_lines)


# ── 主逻辑 ─────────────────────────────────────────────
def main():
    # 读取抓取数据
    if not os.path.exists(RAW_OUTPUT):
        print(f"❌ 未找到原始数据：{RAW_OUTPUT}，请先运行 immigration_fetch.py")
        sys.exit(1)

    with open(RAW_OUTPUT, encoding="utf-8") as f:
        data = json.load(f)

    week = data["week"]
    print(f"\n🤖 开始分析 {week}（共 {data['total']} 条原始数据）...")

    # 调用 Claude 分析
    prompt = build_prompt(data)
    system = "你是一位专注全球移民政策的分析师，熟悉新西兰、美国、加拿大、英国、澳大利亚的移民体系。你的分析要具体、实用，直接告诉读者该怎么做，避免空话。"

    print("⏳ 正在调用 Claude 分析...")
    report = call_llm(prompt, system)

    # 保存报告
    with open(REPORT_OUTPUT, "w", encoding="utf-8") as f:
        f.write(report)
    print(f"✅ 报告已保存至 {REPORT_OUTPUT}")

    # 存入 Bear
    title = f"移民雷达 · {week}"
    save_to_bear(title, report)

    # Discord 推送摘要
    summary = extract_summary(report, week)
    try:
        status = send_discord(summary)
        print(f"📨 Discord 推送成功 ({status})")
    except Exception as e:
        print(f"⚠️ Discord 推送失败: {e}")

    print(f"\n🎉 完成！报告：{title}")


if __name__ == "__main__":
    main()
