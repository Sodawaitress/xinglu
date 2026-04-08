"""
Immigration Radar · 配置文件
修改此文件来调整数据源和过滤规则
"""

import os
from datetime import timezone, timedelta

# ── 时区 ──────────────────────────────────────────────
NZ_TZ = timezone(timedelta(hours=12))

# ── Discord ───────────────────────────────────────────
DISCORD_BOT_TOKEN  = os.environ.get("DISCORD_BOT_TOKEN", "")
DISCORD_CHANNEL_ID = os.environ.get("DISCORD_CHANNEL_ID", "")

# ── Anthropic API ──────────────────────────────────────
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

# ── RSS 数据源 ──────────────────────────────────────────
RSS_FEEDS = [
    # 官方媒体/智库
    ("UK Visas Gov",            "https://www.gov.uk/search/news-and-communications.atom?keywords=visa+immigration&organisations%5B%5D=uk-visas-and-immigration", "🇬🇧"),
    ("The Guardian Immigration","https://www.theguardian.com/uk/immigration/rss",                                "📰"),
    ("Migration Policy Inst",   "https://www.migrationpolicy.org/rss.xml",                                      "🔬"),
    ("Schengen Visa Info",      "https://www.schengenvisainfo.com/feed/?post_type=post",                        "🇪🇺"),
    ("Schengen News",           "https://schengen.news/feed/",                                                   "🇪🇺"),

    # ── 官方政策原文（via Google News 直抓官网）────────────────
    ("USCIS官网",               "https://news.google.com/rss/search?q=site:uscis.gov&hl=en&gl=US&ceid=US:en",                                  "🇺🇸"),
    ("INZ官网",                 "https://news.google.com/rss/search?q=site:immigration.govt.nz&hl=en&gl=NZ&ceid=NZ:en",                        "🇳🇿"),
    ("澳洲DHA官网",             "https://news.google.com/rss/search?q=site:homeaffairs.gov.au+visa&hl=en&gl=AU&ceid=AU:en",                    "🇦🇺"),
    ("加拿大IRCC官网",          "https://news.google.com/rss/search?q=site:canada.ca+immigration&hl=en&gl=CA&ceid=CA:en",                      "🇨🇦"),
    ("德国BAMF官网",            "https://news.google.com/rss/search?q=site:bamf.de&hl=en&gl=DE&ceid=DE:en",                                    "🇩🇪"),
]

# ── 中文社区 RSS ───────────────────────────────────────
CHINESE_FEEDS = [
    ("知乎·移民签证",  "https://news.google.com/rss/search?q=%E7%A7%BB%E6%B0%91+%E7%AD%BE%E8%AF%81+site:zhihu.com&hl=zh-CN&gl=CN&ceid=CN:zh-Hans",  "🇨🇳"),
    ("知乎·海外生活",  "https://news.google.com/rss/search?q=%E6%B5%B7%E5%A4%96%E7%94%9F%E6%B4%BB+%E5%B1%85%E7%95%99+site:zhihu.com&hl=zh-CN&gl=CN&ceid=CN:zh-Hans", "🇨🇳"),
    ("知乎·数字游民",  "https://news.google.com/rss/search?q=%E6%95%B0%E5%AD%97%E6%B8%B8%E6%B0%91+%E7%AD%BE%E8%AF%81+site:zhihu.com&hl=zh-CN&gl=CN&ceid=CN:zh-Hans",  "🇨🇳"),
]

# ── Reddit RSS（无需 API key）──────────────────────────
REDDIT_FEEDS = [
    # 英语主要目的地
    ("r/immigration",       "https://www.reddit.com/r/immigration/top/.rss?t=week",         "🇺🇸"),
    ("r/ImmigrationCanada", "https://www.reddit.com/r/ImmigrationCanada/top/.rss?t=week",   "🇨🇦"),
    ("r/ukvisa",            "https://www.reddit.com/r/ukvisa/top/.rss?t=week",              "🇬🇧"),
    ("r/newzealand",        "https://www.reddit.com/r/newzealand/search/.rss?q=visa+immigration+residency&sort=top&t=week", "🇳🇿"),
    ("r/AusVisa",           "https://www.reddit.com/r/AusVisa/top/.rss?t=week",             "🇦🇺"),
    ("r/expats",            "https://www.reddit.com/r/expats/top/.rss?t=week",              "🌍"),
    # 数字游民 / 自由目的地
    ("r/digitalnomad",      "https://www.reddit.com/r/digitalnomad/top/.rss?t=week",        "💻"),
    # 欧洲非英语国家
    ("r/portugal",          "https://www.reddit.com/r/portugal/search/.rss?q=visto+visa+residency+d7+nhr&sort=top&t=week",          "🇵🇹"),
    ("r/Portugal",          "https://www.reddit.com/r/Portugal/top/.rss?t=week",            "🇵🇹"),
    ("r/germany",           "https://www.reddit.com/r/germany/search/.rss?q=visa+aufenthaltserlaubnis+immigration&sort=top&t=week", "🇩🇪"),
    ("r/Netherlands",       "https://www.reddit.com/r/Netherlands/search/.rss?q=visa+immigration+residency+mvv&sort=top&t=week",    "🇳🇱"),
    # 亚洲
    ("r/japanlife",         "https://www.reddit.com/r/japanlife/search/.rss?q=visa+pr+residency&sort=top&t=week",                   "🇯🇵"),
    ("r/Taiwan",            "https://www.reddit.com/r/taiwan/search/.rss?q=gold+card+visa+residency&sort=top&t=week",               "🇹🇼"),
    # 中东 / 东南亚 / 拉美
    ("r/dubai",             "https://www.reddit.com/r/dubai/search/.rss?q=golden+visa+residency&sort=top&t=week",                   "🇦🇪"),
    ("r/Thailand",          "https://www.reddit.com/r/Thailand/search/.rss?q=ltr+visa+residency+elite&sort=top&t=week",             "🇹🇭"),
    ("r/Panama",            "https://www.reddit.com/r/Panama/search/.rss?q=residency+visa+immigration&sort=top&t=week",             "🇵🇦"),
]

# ── 关键词过滤（只保留移民相关内容）──────────────────
IMMIGRATION_KEYWORDS = [
    # 英文核心
    "visa", "immigration", "residency", "citizenship", "work permit",
    "skilled migrant", "pathway", "application", "border", "refugee",
    "asylum", "deportation", "green card", "permanent resident", "naturalization",
    "points-based", "expression of interest", "EOI", "INZ", "USCIS", "IRCC",
    "spouse visa", "student visa", "partner visa", "investor visa",
    "talent visa", "skilled worker", "labour shortage", "accredited employer",
    # 新增：宽松/非英语目的地关键词
    "d7", "nhr", "golden visa", "gold card", "chancenkarte", "aufenthaltserlaubnis",
    "blue card", "mvv", "ltr visa", "elite visa", "friendly nations",
    "digital nomad", "remote worker", "nomad visa", "retirement visa",
    "passive income", "mm2h", "second passport", "residence permit",
    # 中文
    "移民", "签证", "居留", "永居", "入籍", "技术移民", "雇主", "工签",
    "学签", "配偶签", "难民", "庇护", "边境", "居民", "公民",
    "黄金签证", "数字游民", "退休签", "投资移民",
]

# ── 地区分类关键词 ────────────────────────────────────
REGION_KEYWORDS = {
    "🇳🇿 新西兰": ["new zealand", "nz", "inz", "aotearoa", "kiwi", "wellington", "auckland",
                   "skilled migrant", "accredited employer", "green list"],
    "🇺🇸 美国":   ["united states", "us", "uscis", "green card", "h1b", "h-1b", "eb-",
                   "f1 visa", "daca", "naturalization", "american"],
    "🇨🇦 加拿大": ["canada", "ircc", "express entry", "provincial nominee", "pnp",
                   "crs score", "draw", "atlantic", "oinp", "bcpnp"],
    "🇬🇧 英国":   ["uk", "united kingdom", "british", "home office", "skilled worker visa",
                   "points-based", "ilr", "settlement", "tier"],
    "🇦🇺 澳大利亚":["australia", "australian", "dibp", "homeaffairs", "subclass",
                   "skilled independent", "189", "190", "491"],
    "🇵🇹 葡萄牙": ["portugal", "portuguese", "d7", "nhr", "non-habitual", "golden visa portugal",
                   "lisbon", "porto", "SEF", "AIMA", "passive income visa"],
    "🇩🇪 德国":   ["germany", "german", "chancenkarte", "aufenthaltserlaubnis", "fachkräfte",
                   "eu blue card germany", "niederlassungserlaubnis", "berlin", "munich"],
    "🇳🇱 荷兰":   ["netherlands", "dutch", "mvv", "highly skilled migrant", "orientation year",
                   "amsterdam", "IND", "30% ruling"],
    "🇪🇸 西班牙": ["spain", "spanish", "digital nomad visa spain", "non-lucrative",
                   "madrid", "barcelona", "arraigo"],
    "🇫🇷 法国":   ["france", "french", "talent passport", "carte de résident", "titre de séjour",
                   "paris", "long stay visa france"],
    "🇯🇵 日本":   ["japan", "japanese", "highly skilled professional", "hsp visa", "specified skilled",
                   "engineer humanties", "tokyo", "osaka", "point-based japan"],
    "🇹🇼 台湾":   ["taiwan", "taiwanese", "gold card", "employment gold card", "taipei",
                   "aprc", "arc taiwan"],
    "🇦🇪 阿联酋": ["uae", "dubai", "abu dhabi", "golden visa uae", "emirates",
                   "freelance visa dubai", "remote work visa uae"],
    "🇹🇭 泰国":   ["thailand", "thai", "ltr visa", "long term resident", "elite visa",
                   "bangkok", "thailand privilege", "retirement visa thailand"],
    "🇲🇾 马来西亚":["malaysia", "mm2h", "malaysia my second home", "kuala lumpur",
                   "premium visa", "de rantau"],
    "🇵🇦 巴拿马": ["panama", "friendly nations visa", "pensionado", "panama city",
                   "cédula", "permanent residency panama"],
    "🇬🇪 格鲁吉亚":["georgia", "georgian", "tbilisi", "remotely from georgia",
                   "virtual zone", "georgia visa free"],
    "🌴 数字游民": ["digital nomad", "nomad visa", "remote worker visa", "location independent",
                   "work remotely abroad", "nomad list", "wifi passport"],
    "🇪🇺 欧洲通用":["europe", "schengen", "eu blue card", "european union", "freedom of movement"],
}

# ── 每个来源最多抓取条数 ────────────────────────────
ITEMS_PER_FEED = 5
REDDIT_ITEMS = 8

# ── 输出文件路径 ──────────────────────────────────────
RAW_OUTPUT = "/tmp/immigration_raw.json"
REPORT_OUTPUT = "/tmp/immigration_report.md"

# ── Claude 分析模型 ──────────────────────────────────
ANALYSIS_MODEL = "claude-haiku-4-5-20251001"  # 用 Haiku 控制成本
