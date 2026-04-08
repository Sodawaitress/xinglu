#!/usr/bin/env python3
"""
Immigration Radar · 全流程 Pipeline
每周自动运行：抓取 → 生成带链接报告 → Bear + Discord
"""

import sys, os
sys.path.insert(0, os.path.dirname(__file__))

import json, subprocess, urllib.request, urllib.parse, ssl, time
from datetime import datetime
from config import (
    RAW_OUTPUT, REPORT_OUTPUT, NZ_TZ,
    DISCORD_BOT_TOKEN, DISCORD_CHANNEL_ID
)


# ── 1. 运行抓取脚本 ────────────────────────────────────
def run_fetch():
    script = os.path.join(os.path.dirname(__file__), "immigration_fetch.py")
    result = subprocess.run(
        [sys.executable, script],
        capture_output=True, text=True
    )
    print(result.stdout)
    if result.returncode != 0:
        print(f"⚠️ 抓取警告: {result.stderr[:500]}")
    return result.returncode == 0


# ── 2. 读取数据 ────────────────────────────────────────
def load_data():
    with open(RAW_OUTPUT, encoding="utf-8") as f:
        return json.load(f)


# ── 3. 生成带链接报告 ──────────────────────────────────
def fmt_item(item, max_title=80):
    title = item["title"][:max_title]
    link  = item["link"]
    src   = item["source"]
    return f"- [{title}]({link})（{src}）"


def generate_report(data: dict) -> str:
    week      = data["week"]
    total     = data["total"]
    fetched   = data["fetched_at"][:16].replace("T", " ")
    by_region = data["by_region"]
    items     = data["items"]

    def section(region, max_items=6):
        region_items = by_region.get(region, [])
        if not region_items:
            return ""
        lines = [fmt_item(i) for i in region_items[:max_items]]
        return "\n".join(lines)

    # 按来源类型分类
    reddit_items  = [i for i in items if i["type"] == "reddit"]
    official_items = [i for i in items if "官网" in i.get("source", "")]
    zhihu_items   = [i for i in items if i.get("source", "").startswith("知乎")]

    report = f"""# 🛂 移民雷达周报 {week}

**抓取时间**：{fetched} NZT ｜ **数据量**：{total} 条 ｜ **覆盖地区**：{len(by_region)} 个

---

## 🇳🇿 新西兰

{section("🇳🇿 新西兰", 6)}

---

## 🇺🇸 美国

{section("🇺🇸 美国", 8)}

---

## 🇨🇦 加拿大

{section("🇨🇦 加拿大", 6)}

---

## 🇬🇧 英国

{section("🇬🇧 英国", 6)}

---

## 🇦🇺 澳大利亚

{section("🇦🇺 澳大利亚", 6)}

---

## 🇵🇹 葡萄牙

{section("🇵🇹 葡萄牙", 5)}

---

## 🇩🇪 德国 / 🇳🇱 荷兰 / 🇪🇸 西班牙

{section("🇩🇪 德国", 3)}
{section("🇳🇱 荷兰", 3)}
{section("🇪🇸 西班牙", 3)}

---

## 🇯🇵 日本 / 🇹🇼 台湾

{section("🇯🇵 日本", 4)}
{section("🇹🇼 台湾", 3)}

---

## 🇦🇪 阿联酋 / 🇹🇭 泰国

{section("🇦🇪 阿联酋", 4)}
{section("🇹🇭 泰国", 3)}

---

## 🌴 数字游民

{section("🌴 数字游民", 6)}

---

## 🇪🇺 欧洲通用

{section("🇪🇺 欧洲通用", 5)}

---

## 🌍 全球趋势

{section("🌍 全球", 5)}

---

## 🇨🇳 中文社区（知乎）精选

""" + "\n".join(fmt_item(i) for i in zhihu_items[:8]) + f"""

---

*来源：INZ · USCIS · IRCC · DHA · UK Visas Gov · Guardian · MPI · Schengen News · Reddit · 知乎*
*自动生成于 {fetched} NZT*
"""
    return report.strip()


# ── 4. 存入 Bear ───────────────────────────────────────
def save_to_bear(title: str, content: str):
    url = ("bear://x-callback-url/create?"
           + "title=" + urllib.parse.quote(title)
           + "&text=" + urllib.parse.quote(content)
           + "&tags=" + urllib.parse.quote("移民,雷达"))
    subprocess.run(["open", url])
    print(f"📓 已存入 Bear：{title}")


# ── 5. Discord 发送（分块）────────────────────────────
def send_discord_chunks(content: str):
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode    = ssl.CERT_NONE

    url = f"https://discord.com/api/v10/channels/{DISCORD_CHANNEL_ID}/messages"
    headers = {
        "Authorization": f"Bot {DISCORD_BOT_TOKEN}",
        "Content-Type":  "application/json",
        "User-Agent":    "DiscordBot (https://github.com, 1.0)"
    }

    # 按 ## 分节分块，每块不超过 1900 字符
    sections = content.split("\n## ")
    chunks, current = [], sections[0]
    for sec in sections[1:]:
        candidate = current + "\n## " + sec
        if len(candidate) <= 1900:
            current = candidate
        else:
            chunks.append(current)
            current = "## " + sec
    chunks.append(current)

    sent = 0
    for chunk in chunks:
        payload = json.dumps({"content": chunk[:1990]}).encode()
        req = urllib.request.Request(url, data=payload, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, context=ctx, timeout=10) as r:
                if r.status == 200:
                    sent += 1
        except Exception as e:
            print(f"  ⚠️ Discord chunk 失败: {e}")
        time.sleep(0.5)   # 避免触发 rate limit

    print(f"📨 Discord 发送完成：{sent}/{len(chunks)} 块成功")


# ── 主流程 ─────────────────────────────────────────────
def main():
    now = datetime.now(NZ_TZ)
    print(f"\n{'='*50}")
    print(f"🛂 移民雷达 Pipeline 启动 {now.strftime('%Y-%m-%d %H:%M NZT')}")
    print(f"{'='*50}\n")

    # Step 1: 抓取
    print("📡 Step 1 / 4：抓取数据...")
    run_fetch()

    # Step 2: 生成报告
    print("\n📝 Step 2 / 4：生成带链接报告...")
    data   = load_data()
    report = generate_report(data)
    week   = data["week"]

    with open(REPORT_OUTPUT, "w", encoding="utf-8") as f:
        f.write(report)
    print(f"✅ 报告已写入 {REPORT_OUTPUT}（{len(report)} 字符）")

    # Step 3: Bear
    print("\n📓 Step 3 / 4：推送至 Bear...")
    save_to_bear(f"移民雷达 {week}", report)

    # Step 4: Discord
    print("\n📨 Step 4 / 4：推送至 Discord...")
    send_discord_chunks(report)

    print(f"\n🎉 全部完成！周报 {week} 已自动归档并推送。")


if __name__ == "__main__":
    main()
