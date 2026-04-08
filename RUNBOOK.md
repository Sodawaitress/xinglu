# 星路 · 运维手册

> 这里记录的是开发和维护中发现的坑、工作流和注意事项。  
> PRODUCT.md 管「要做什么」，这里管「怎么不出错」。

---

## 启动 App

```bash
cd /Users/poluovoila/.claude/skills/immigration-radar
python3 app.py
# → http://127.0.0.1:5002
```

Flask 日志输出到终端。如果后台运行：
```bash
python3 app.py &>/tmp/xinglu.log &
tail -f /tmp/xinglu.log   # 实时看日志
```

---

## Admin 维护面板

**地址：** `http://localhost:5002/admin`  
**密码：** `xinglu-admin`（开发默认，生产改 `.env` 里的 `ADMIN_PASSWORD`）

面板可以看到：
- **DB 概览**：各表行数（users / routes / tasks / world_events / user_journeys…）
- **用户列表**：所有注册用户的 profile 数据（护照、居住地、目的地、签证状态）
- **旅程状态**：每个用户在走哪条路线，状态是 exploring / active / paused / completed
- **事件流**：最近 100 条 world_events（含经纬度、事件类型、摘要）
- **重置进度**：清空某用户的旅程和任务进度（保留账号和 profile）

---

## 调试工作流

```
用户报告问题
   ↓
1. 浏览器 F12 → Console tab    ← JS 报错、变量值
                → Network tab  ← 哪个 API 请求失败、返回了什么
   ↓
2. Flask 日志 /tmp/xinglu.log  ← 服务端 500 / Python traceback
   ↓
3. /admin 面板                 ← 用户数据格式是否正确、旅程状态是否符合预期
   ↓
4. 定位根因 → 修代码 → 重启 → 重新测试同一路径
```

---

## 数据库

**位置：** `data/xinglu.db`（SQLite，开发环境）  
**生产：** 设置环境变量 `DATABASE_URL=postgresql://...`，代码无需改动

### 重建数据库（会清空所有数据）

```bash
rm data/xinglu.db
python3 -c "import db; db.init_db()"
```

⚠️ **重建后所有账号消失**，用户需要重新注册。开发期间常做，但要提前告知测试用户。

### 直接查库（调试用）

```bash
python3 -c "
import sqlite3, json
conn = sqlite3.connect('data/xinglu.db')
c = conn.cursor()
c.execute('SELECT * FROM users')
print(c.fetchall())
conn.close()
"
```

---

## 已知坑

### 1. Profile 字段存中文自由文本

Onboarding 的 `passport` 和 `current_country` 是 **自由输入框**，用户填「中国」存「中国」，不是 `cn`。

地球仪的 COORDS 表 (`map.html:767`) 兼容了这两种格式：
```javascript
'中国': [35, 105], 'cn': [35, 105]
```

但未来如果加了新国家、改了查询逻辑，需要同时维护中文名和代码两个 key。  
**长期解法：** Onboarding 改成下拉选择（发送 country code），不用自由输入。

### 2. 路线 ID 有国籍后缀

DB 里的路线 ID 是 `au_189_cn`（中国护照 → 澳洲 189），不是 `au_189`。  
前端点击地球仪 → `get_routes_by_dest('au')` 返回的 `r.id` 就是带后缀的完整 ID。  
所有 API 调用（select-route、tasks/next、complete-journey）都用完整 ID。

调试时如果任务加载不出来，先确认传的 route_id 是 `au_189_cn` 而不是 `au_189`。

### 3. CDN 依赖（在中国可能加载慢）

地球仪依赖三个 jsdelivr CDN：
```
three@0.160        ← Three.js 本体
d3@7               ← 地球纹理渲染
world-atlas@2      ← 国家边界地图数据（在 initGlobe 里异步拉取）
```

如果 CDN 加载失败：
- **Three.js 挂**：地球整个不渲染，Console 报 `THREE is not defined`
- **world-atlas 挂**：地球变纯色球（无大陆纹理），但点和弧线正常，try/catch 已处理
- **现象**：globe-wrap 区域变黑 / 显示「地球仪加载失败 · 错误信息」

**解法（如果 CDN 经常挂）：** 把三个库下载到 `static/` 本地引用。

### 4. 地球仪加载失败现在有可见提示

`map.html` 已有顶层 try/catch：
```javascript
requestAnimationFrame(async () => {
  try { await initGlobe(); }
  catch(e) {
    // 在 globe-wrap 区域显示错误文字
  }
});
```

看到「地球仪加载失败 · XXX」就去 Console 看具体报错。

### 5. 切换路线的状态机

用户选新路线时，旧的 `active` 旅程会变 `paused`（不删除）。  
如果发现用户有多条 `active` 旅程（不正常），去 `/admin` 用「重置进度」清空，让用户重新选。

---

## 环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `SECRET_KEY` | `xinglu-dev-secret` | Flask session 加密，生产必须改 |
| `ADMIN_PASSWORD` | `xinglu-admin` | Admin 面板密码，生产必须改 |
| `DATABASE_URL` | `sqlite:///data/xinglu.db` | 生产改成 PostgreSQL |
| `GROQ_API_KEY` | — | LLM 路线顾问，没有则 advisor 降级静默 |
| `PORT` | `5002` | Flask 监听端口 |

`.env` 文件放在项目根目录，已在 `.gitignore` 里（不要提交）。

---

## 部署检查清单

上线前确认：
- [ ] `SECRET_KEY` 改成随机字符串（`python3 -c "import secrets; print(secrets.token_hex(32))"`)
- [ ] `ADMIN_PASSWORD` 改成强密码
- [ ] `DATABASE_URL` 指向 PostgreSQL（Supabase / Railway / Render）
- [ ] CDN 在目标用户网络环境可访问（或改本地引用）
- [ ] `GROQ_API_KEY` 设置（否则 advisor 功能不可用）
- [ ] `/admin` 路由在生产考虑加 IP 白名单或改用更强的 auth
