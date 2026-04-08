---
name: immigration-radar
description: Weekly global immigration news, policy changes, and strategy analysis. Fetches from RSS, Reddit, and official government sources, then generates a deep analysis report in Bear and pushes a summary to Discord.
---

# Immigration Radar · 移民信息雷达

每周自动抓取全球移民资讯，分析政策变化，生成策略报告存入 Bear，Discord 推送摘要。

## 触发方式

用户说以下任意一种时触发此 skill：
- `/immigration-radar`
- "跑移民雷达"
- "更新移民信息"
- "生成移民报告"

## 工作流程

```
1. 运行 scripts/immigration_fetch.py  → 抓取所有来源，输出 JSON
2. 运行 scripts/immigration_analyze.py → 调用 Claude API 深度分析
3. 创建 Bear 笔记（完整报告）
4. 推送摘要到 Discord
```

## 执行步骤

### Step 1：运行抓取脚本

```bash
/opt/homebrew/bin/python3 ~/.claude/skills/immigration-radar/scripts/immigration_fetch.py
```

输出文件：`/tmp/immigration_raw.json`

### Step 2：运行分析脚本

```bash
/opt/homebrew/bin/python3 ~/.claude/skills/immigration-radar/scripts/immigration_analyze.py
```

输出文件：`/tmp/immigration_report.md`

### Step 3：存入 Bear

读取 `/tmp/immigration_report.md`，用 Bear URL scheme 创建笔记：
- 标题格式：`移民雷达 · YYYY-WW周`
- 标签：`#移民 #政策`

### Step 4：Discord 推送

读取报告中的 `## 本周要点` 部分，发送到 Discord。

---

## 数据源配置（在 scripts/config.py 里修改）

### RSS 来源

| 来源 | 地区 | 说明 |
|------|------|------|
| Immigration NZ | 🇳🇿 NZ | 新西兰移民局官方新闻 |
| USCIS News | 🇺🇸 US | 美国公民及移民服务局 |
| IRCC Canada | 🇨🇦 CA | 加拿大移民难民公民部 |
| UK Visas & Immigration | 🇬🇧 UK | 英国内政部移民动态 |
| Schengen Visa Info | 🇪🇺 EU | 欧盟申根签证资讯 |
| The Guardian · Immigration | 全球 | 移民新闻深度报道 |
| Migration Policy Institute | 全球 | 移民政策研究机构 |

### Reddit 来源（RSS，无需 API key）

| Subreddit | 覆盖内容 |
|-----------|--------|
| r/immigration | 美国移民综合 |
| r/newzealand | NZ 本地讨论 |
| r/ukvisa | 英国签证经验 |
| r/ImmigrationCanada | 加拿大移民专版 |
| r/AusVisa | 澳大利亚签证 |
| r/expats | 全球外籍人士 |
| r/digitalnomad | 数字游民签证策略 |
| r/portugal / r/Portugal | 葡萄牙 D7 / NHR |
| r/germany | 德国 Chancenkarte / 蓝卡 |
| r/Netherlands | 荷兰高技能移民 |
| r/japanlife | 日本高级人才签证 |
| r/Taiwan | 台湾黄金签证 |
| r/dubai | 阿联酋黄金签证 |
| r/Thailand | 泰国 LTR / Elite 签证 |
| r/Panama | 巴拿马友好国家签证 |

### 官方政策页面（HTML 抓取）

| 页面 | 内容 |
|------|------|
| INZ Residence Visa | NZ 居留签证政策 |
| USCIS Policy Manual | 美国政策手册更新 |

---

## 报告结构

```markdown
# 移民雷达 · YYYY-WW周

## 🚨 本周重大变化（政策/法规）
## 🌍 分地区动态
  ### 🇳🇿 新西兰
  ### 🇺🇸 美国
  ### 🇨🇦 加拿大
  ### 🇬🇧 英国
  ### 🇦🇺 澳大利亚
  ### 🇵🇹 葡萄牙（D7 / NHR）
  ### 🇩🇪 德国（Chancenkarte）
  ### 🇳🇱 荷兰 / 🇪🇸 西班牙 / 🇫🇷 法国
  ### 🇯🇵 日本 / 🇹🇼 台湾
  ### 🇦🇪 阿联酋 / 🇹🇭 泰国 / 🇲🇾 马来西亚
  ### 🇵🇦 巴拿马 / 🇬🇪 格鲁吉亚
  ### 💻 数字游民目的地综合
## 💬 社区热议（Reddit 高赞帖）
## 📊 趋势分析（语言要求 / 收入门槛 / 通过率对比）
## ✅ 本周策略建议
## 📌 下周关注点
```

---

## 自动化设置

每周日 18:00 NZT 自动运行（launchd）：

```bash
# 加载
launchctl load ~/Library/LaunchAgents/immigration.radar.plist

# 手动触发测试
launchctl start immigration.radar
```

plist 文件：`~/Library/LaunchAgents/immigration.radar.plist`

---

## 依赖

- Python 3（`/opt/homebrew/bin/python3`）
- `ANTHROPIC_API_KEY` 环境变量（用于分析层）
- Chrome CDP（可选，若需抓取 JS 渲染页面）
- Bear App（macOS）
- Discord Bot Token（已配置在脚本中）
