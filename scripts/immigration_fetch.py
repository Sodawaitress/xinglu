#!/usr/bin/env python3
"""
Immigration Radar · 抓取层
抓取 RSS + Reddit，过滤并分类，输出 JSON
"""

import sys, os
sys.path.insert(0, os.path.dirname(__file__))

import urllib.request, json, ssl, xml.etree.ElementTree as ET
from datetime import datetime
from config import (
    RSS_FEEDS, REDDIT_FEEDS, CHINESE_FEEDS, IMMIGRATION_KEYWORDS, REGION_KEYWORDS,
    ITEMS_PER_FEED, REDDIT_ITEMS, RAW_OUTPUT, NZ_TZ
)


# ── SSL 上下文 ─────────────────────────────────────────
def make_ctx():
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


# ── 关键词过滤 ─────────────────────────────────────────
def is_immigration_related(text: str) -> bool:
    t = text.lower()
    return any(kw.lower() in t for kw in IMMIGRATION_KEYWORDS)


def classify_region(text: str) -> list[str]:
    t = text.lower()
    regions = []
    for region, keywords in REGION_KEYWORDS.items():
        if any(kw.lower() in t for kw in keywords):
            regions.append(region)
    return regions or ["🌍 全球"]


# ── RSS 解析 ───────────────────────────────────────────
def fetch_rss(name: str, url: str, emoji: str, n: int = ITEMS_PER_FEED) -> list[dict]:
    ctx = make_ctx()
    try:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "Mozilla/5.0 (compatible; ImmigrationRadar/1.0)"}
        )
        r = urllib.request.urlopen(req, context=ctx, timeout=10)
        root = ET.fromstring(r.read())
        ns = {"atom": "http://www.w3.org/2005/Atom"}
        items = []

        # RSS 2.0
        for item in root.findall(".//item")[:n]:
            title = item.findtext("title", "").strip()
            link  = item.findtext("link", "").strip()
            desc  = item.findtext("description", "").strip()[:300]
            pub   = item.findtext("pubDate", "")
            text  = f"{title} {desc}"
            if title and is_immigration_related(text):
                items.append({
                    "source": name, "emoji": emoji,
                    "title": title, "link": link,
                    "desc": desc, "pub": pub,
                    "regions": classify_region(text),
                    "type": "news"
                })

        # Atom
        if not items:
            for entry in root.findall(".//atom:entry", ns)[:n]:
                title = entry.findtext("atom:title", "", ns).strip()
                link_el = entry.find("atom:link", ns)
                link = link_el.get("href", "") if link_el is not None else ""
                summary = entry.findtext("atom:summary", "", ns).strip()[:300]
                pub = entry.findtext("atom:updated", "", ns)
                text = f"{title} {summary}"
                if title and is_immigration_related(text):
                    items.append({
                        "source": name, "emoji": emoji,
                        "title": title, "link": link,
                        "desc": summary, "pub": pub,
                        "regions": classify_region(text),
                        "type": "news"
                    })

        print(f"  {emoji} {name}: {len(items)} 条")
        return items
    except Exception as e:
        print(f"  ⚠️ {name}: {e}")
        return []


# ── Reddit RSS 解析 ────────────────────────────────────
def fetch_reddit(name: str, url: str, emoji: str, n: int = REDDIT_ITEMS) -> list[dict]:
    ctx = make_ctx()
    try:
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "ImmigrationRadar/1.0 (personal research tool)",
                "Accept": "application/rss+xml, application/xml, text/xml"
            }
        )
        r = urllib.request.urlopen(req, context=ctx, timeout=10)
        root = ET.fromstring(r.read())
        ns = {"atom": "http://www.w3.org/2005/Atom"}
        items = []

        for entry in root.findall(".//atom:entry", ns)[:n]:
            title = entry.findtext("atom:title", "", ns).strip()
            link_el = entry.find("atom:link", ns)
            link = link_el.get("href", "") if link_el is not None else ""
            content = entry.findtext("atom:content", "", ns)[:400].strip()
            pub = entry.findtext("atom:updated", "", ns)
            text = f"{title} {content}"
            if title:
                items.append({
                    "source": name, "emoji": emoji,
                    "title": title, "link": link,
                    "desc": content, "pub": pub,
                    "regions": classify_region(text),
                    "type": "reddit"
                })

        print(f"  {emoji} {name}: {len(items)} 条")
        return items
    except Exception as e:
        print(f"  ⚠️ {name}: {e}")
        return []


# ── 主逻辑 ─────────────────────────────────────────────
def main():
    now = datetime.now(NZ_TZ)
    week_str = now.strftime("%Y-W%V")
    print(f"\n🛂 Immigration Radar 抓取开始 {now.strftime('%Y-%m-%d %H:%M NZT')}")

    all_items = []

    print("\n📰 官方 & 媒体 RSS：")
    for name, url, emoji in RSS_FEEDS:
        all_items.extend(fetch_rss(name, url, emoji))

    print("\n💬 Reddit 社区：")
    for name, url, emoji in REDDIT_FEEDS:
        all_items.extend(fetch_reddit(name, url, emoji))

    print("\n🇨🇳 中文社区（知乎）：")
    for name, url, emoji in CHINESE_FEEDS:
        all_items.extend(fetch_rss(name, url, emoji, n=10))

    # 去重（按标题）
    seen_titles = set()
    unique_items = []
    for item in all_items:
        key = item["title"][:60].lower()
        if key not in seen_titles:
            seen_titles.add(key)
            unique_items.append(item)

    # 按地区分组
    by_region = {}
    for item in unique_items:
        for region in item["regions"]:
            by_region.setdefault(region, []).append(item)

    output = {
        "week": week_str,
        "fetched_at": now.isoformat(),
        "total": len(unique_items),
        "items": unique_items,
        "by_region": {k: v for k, v in sorted(by_region.items())},
    }

    with open(RAW_OUTPUT, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"\n✅ 完成：共 {len(unique_items)} 条（去重后），已保存至 {RAW_OUTPUT}")
    print(f"   地区分布：{', '.join(f'{k}({len(v)})' for k, v in by_region.items())}")


if __name__ == "__main__":
    main()
