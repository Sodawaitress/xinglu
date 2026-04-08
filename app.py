#!/usr/bin/env python3
"""星路 · Flask 主应用"""

import sys, os, json, subprocess, threading, urllib.request
sys.path.insert(0, os.path.dirname(__file__))

from flask import (Flask, render_template, request, redirect,
                   url_for, session, flash, jsonify)
from flask_bcrypt import Bcrypt
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

import db

app = Flask(__name__)

# ── 节点风险关键词 ─────────────────────────────────────
# 每个 stage_key → 触发告警的词（出现在新闻标题/摘要里）
STAGE_RISK = {
    # 通用
    "eoi":        ["suspend", "pause", "halt", "freeze", "close", "EOI", "expression of interest", "stop accepting"],
    "invite":     ["no draw", "suspend", "halt", "invitation", "pool closed", "no invitation"],
    "apply":      ["fee increase", "new requirement", "requirement change", "stricter", "raise the bar"],
    "processing": ["delay", "backlog", "processing time", "longer than", "months wait", "pause processing"],
    "approved":   ["rejection", "refusal rate", "refused", "decline rate", "crackdown"],
    "landing":    ["border closed", "entry ban", "travel ban", "arrival restriction"],
    # 庇护路线
    "emergency":  ["border closure", "pushback", "turn away", "detained"],
    "unhcr":      ["UNHCR", "registration closed", "suspended registration", "overwhelmed"],
    "claim":      ["asylum suspended", "claim rejected", "backlog", "freeze"],
    "decision":   ["rejected", "appeal denied", "deportation", "removal"],
    "settlement": ["cap reduced", "quota cut", "resettlement suspended"],
    # 数字游民
    "research":   ["visa cancelled", "program ended", "discontinued"],
    "renew":      ["renewal restricted", "extension denied", "max stay"],
    # 家庭
    "sponsor":    ["sponsor requirement", "income threshold", "stricter sponsor"],
    "reunion":    ["family visa suspended", "processing halt"],
}

# 地区关键词 → 目的地代码
REGION_TO_DEST = {
    "新西兰": "nz", "nz": "nz", "inz": "nz",
    "澳大利亚": "au", "australia": "au",
    "加拿大": "ca", "canada": "ca", "ircc": "ca",
    "英国": "uk", "united kingdom": "uk",
    "美国": "us", "united states": "us", "uscis": "us",
    "葡萄牙": "pt", "portugal": "pt", "d7": "pt",
    "德国": "de", "germany": "de",
    "日本": "jp", "japan": "jp",
    "泰国": "th", "thailand": "th",
    "阿联酋": "ae", "uae": "ae", "dubai": "ae",
}


def compute_node_alerts(raw_data: dict, user_dests: list, route_type: str) -> dict:
    """
    返回 {stage_key: [headline, ...]} — 受影响的节点及触发新闻标题
    """
    alerts = {}
    items = raw_data.get("items", [])

    for item in items:
        title = (item.get("title", "") + " " + item.get("desc", "")).lower()
        regions = " ".join(item.get("regions", [])).lower()

        # 判断是否和用户目的地相关
        dest_match = not user_dests or "unknown" in user_dests
        if not dest_match:
            for dest in user_dests:
                for kw, code in REGION_TO_DEST.items():
                    if code == dest and kw in regions:
                        dest_match = True
                        break

        if not dest_match:
            continue

        # 检查每个节点的风险词
        for stage_key, keywords in STAGE_RISK.items():
            for kw in keywords:
                if kw.lower() in title:
                    alerts.setdefault(stage_key, [])
                    headline = item.get("title", "")[:80]
                    if headline not in alerts[stage_key]:
                        alerts[stage_key].append(headline)
                    break

    return alerts
app.secret_key = os.environ.get("SECRET_KEY", "xinglu-dev-secret")
bcrypt = Bcrypt(app)

@app.template_filter("from_json")
def from_json_filter(s):
    try:
        return json.loads(s or "[]")
    except Exception:
        return []


# ── Auth helper ────────────────────────────────────────
def login_required(f):
    from functools import wraps
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("user_id"):
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated


# ── Public routes ──────────────────────────────────────
@app.route("/")
def index():
    user = None
    profile = None
    if session.get("user_id"):
        user = db.get_user_by_id(session["user_id"])
        profile = db.get_profile(session["user_id"])
    latest = db.get_latest_report()
    return render_template("index.html", user=user, profile=profile, latest=latest)


@app.route("/register", methods=["GET", "POST"])
def register():
    if session.get("user_id"):
        return redirect(url_for("index"))
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        pw    = request.form.get("password", "")
        if not email or not pw:
            flash("请填写邮箱和密码", "error")
            return render_template("register.html")
        if db.get_user_by_email(email):
            flash("该邮箱已注册", "error")
            return render_template("register.html")
        pw_hash = bcrypt.generate_password_hash(pw).decode()
        user_id = db.create_user(email, pw_hash)
        session["user_id"] = user_id
        session["email"] = email
        return redirect(url_for("onboarding"))
    return render_template("register.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if session.get("user_id"):
        return redirect(url_for("index"))
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        pw    = request.form.get("password", "")
        user  = db.get_user_by_email(email)
        if not user or not bcrypt.check_password_hash(user["password_hash"], pw):
            flash("邮箱或密码不正确", "error")
            return render_template("login.html")
        session["user_id"] = user["id"]
        session["email"] = user["email"]
        return redirect(url_for("map_page"))
    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("index"))


# ── Onboarding ─────────────────────────────────────────
@app.route("/onboarding", methods=["GET", "POST"])
@login_required
def onboarding():
    if request.method == "POST":
        passport        = request.form.get("passport", "")
        current_country = request.form.get("current_country", "")
        current_visa    = request.form.get("current_visa", "")
        destinations    = json.dumps(request.form.getlist("destinations"))
        route_type      = request.form.get("route_type", "unknown")
        db.save_profile(
            session["user_id"], passport, current_country,
            current_visa, destinations, route_type
        )
        # 时空记录：用户「现在在这里」
        db.log_world_event(
            entity_type="user", entity_id=str(session["user_id"]),
            event_type="arrived",
            payload={"passport": passport, "visa": current_visa},
            country_id=current_country.lower()[:2],
            headline=f"用户当前所在：{current_country}（{current_visa}）",
        )
        return redirect(url_for("map_page"))
    profile = db.get_profile(session["user_id"])
    existing_dests = json.loads(profile["destinations"] or "[]") if profile else []
    return render_template("onboarding.html", profile=profile, existing_dests=existing_dests)


# ── Map ────────────────────────────────────────────────
@app.route("/map")
@login_required
def map_page():
    profile = db.get_profile(session["user_id"])
    if not profile:
        return redirect(url_for("onboarding"))
    destinations = json.loads(profile["destinations"] or "[]")
    return render_template("map.html", profile=profile, destinations=destinations)


# ── Node alerts API ───────────────────────────────────
@app.route("/api/map/alerts")
@login_required
def map_alerts():
    profile = db.get_profile(session["user_id"])
    if not profile:
        return jsonify({})
    latest = db.get_latest_report()
    if not latest or not latest["raw_json"]:
        return jsonify({})
    try:
        raw_data = json.loads(latest["raw_json"])
    except Exception:
        return jsonify({})

    dests_raw = profile["destinations"] or "[]"
    dests = [d.strip().strip('"') for d in
             dests_raw.strip("[]").split(",") if d.strip().strip('"')]
    alerts = compute_node_alerts(raw_data, dests, profile["route_type"] or "")
    return jsonify(alerts)


# ── Routes API ───────────────────────────────────────
@app.route("/api/map/routes")
@login_required
def map_routes():
    dest = request.args.get("dest", "").strip().lower()
    if not dest:
        return jsonify([])
    routes = db.get_routes_by_dest(dest)
    # 按用户护照排序：route_id 以 passport 结尾的排前面（如 cn 护照优先 au_189_cn）
    profile = db.get_profile(session["user_id"])
    passport = (profile.get("passport") or "").lower()[:2]
    if passport:
        routes.sort(key=lambda r: (0 if r["id"].endswith(f"_{passport}") else 1,
                                    r.get("typical_months_min") or 99))
    # 叠加雷达告警：如果路线的 risk_keywords 命中最新新闻，status 降为 restricted
    latest = db.get_latest_report()
    if latest and latest.get("raw_json"):
        try:
            raw = json.loads(latest["raw_json"])
            all_titles = " ".join(
                (i.get("title","") + " " + i.get("desc","")).lower()
                for i in raw.get("items", [])
            )
            for r in routes:
                for kw in r.get("risk_keywords", []):
                    if kw.lower() in all_titles:
                        if r["status"] == "open":
                            r["status"] = "restricted"
                        r["signal"] = r.get("signal") or f"⚠ 检测到相关新闻：{kw}"
                        break
        except Exception:
            pass
    return jsonify(routes)


@app.route("/api/map/select-route", methods=["POST"])
@login_required
def select_route():
    data = request.get_json() or {}
    route_id = data.get("route_id", "")
    dest     = data.get("dest", "")
    if not route_id or not dest:
        return jsonify({"error": "missing params"}), 400
    # 把其他 active 旅程设为 paused
    all_journeys = db.get_all_journeys(session["user_id"])
    for j in all_journeys:
        if j["status"] == "active" and j["route_id"] != route_id:
            db.set_journey_status(j["id"], "paused", "用户切换到其他路线")
    db.save_user_route_selection(session["user_id"], route_id, dest)
    return jsonify({"ok": True})


# ── Complete journey ──────────────────────────────────
@app.route("/api/map/complete-journey", methods=["POST"])
@login_required
def complete_journey():
    data     = request.get_json() or {}
    route_id = data.get("route_id", "")
    if not route_id:
        return jsonify({"error": "missing route_id"}), 400
    journeys = db.get_all_journeys(session["user_id"])
    for j in journeys:
        if j["route_id"] == route_id and j["status"] in ("active", "paused"):
            db.set_journey_status(j["id"], "completed")
            db.log_world_event(
                entity_type="user", entity_id=str(session["user_id"]),
                event_type="route_completed",
                payload={"route_id": route_id},
                headline=f"完成路线 {route_id} 所有任务",
            )
            break
    return jsonify({"ok": True})


# ── Set stage ─────────────────────────────────────────
@app.route("/map/stage", methods=["POST"])
@login_required
def set_stage():
    stage = request.form.get("stage", "")
    profile = db.get_profile(session["user_id"])
    if profile and stage:
        db.save_profile(
            session["user_id"],
            profile["passport"], profile["current_country"],
            profile["current_visa"], profile["destinations"],
            profile["route_type"], stage
        )
    return redirect(url_for("map_page"))


# ── Radar ──────────────────────────────────────────────
@app.route("/radar")
@login_required
def radar_page():
    profile = db.get_profile(session["user_id"])
    destinations = json.loads(profile["destinations"] or "[]") if profile else []
    latest  = db.get_latest_report()
    reports = db.get_all_reports()
    raw_data = None
    if latest and latest["raw_json"]:
        try:
            raw_data = json.loads(latest["raw_json"])
        except Exception:
            pass
    return render_template("radar.html",
                           profile=profile,
                           destinations=destinations,
                           latest=latest,
                           raw_data=raw_data,
                           reports=reports)


@app.route("/radar/report/<int:report_id>")
@login_required
def report_detail(report_id):
    report = db.get_report_by_id(report_id)
    if not report:
        flash("报告不存在", "error")
        return redirect(url_for("radar_page"))
    return render_template("report_detail.html", report=report)


# ── Tasks API ─────────────────────────────────────────
@app.route("/api/tasks/<route_id>")
@login_required
def get_tasks(route_id):
    tasks    = db.get_tasks_by_route(route_id)
    progress = db.get_user_task_progress(session["user_id"], route_id)
    pct      = db.get_route_progress_pct(session["user_id"], route_id)
    for t in tasks:
        p = progress.get(t["id"])
        t["done"]    = bool(p and p["done"])
        t["done_at"] = p["done_at"] if p else None
    return jsonify({"tasks": tasks, "progress_pct": pct})


@app.route("/api/tasks/next/<route_id>")
@login_required
def next_task(route_id):
    task = db.get_next_task(session["user_id"], route_id)
    pct  = db.get_route_progress_pct(session["user_id"], route_id)
    return jsonify({"task": task, "progress_pct": pct})


@app.route("/api/tasks/done", methods=["POST"])
@login_required
def mark_task_done():
    data    = request.get_json() or {}
    task_id = data.get("task_id", "")
    done    = bool(data.get("done", True))
    note    = data.get("note", "")
    if not task_id:
        return jsonify({"error": "missing task_id"}), 400
    db.set_task_done(session["user_id"], task_id, done, note)
    route_id = data.get("route_id", "")
    pct = db.get_route_progress_pct(session["user_id"], route_id) if route_id else 0
    return jsonify({"ok": True, "progress_pct": pct})


# ── Advisor API (LLM 中介分析) ────────────────────────
@app.route("/api/advisor", methods=["POST"])
@login_required
def advisor():
    profile  = db.get_profile(session["user_id"])
    if not profile:
        return jsonify({"error": "no profile"}), 400

    data     = request.get_json() or {}
    dest     = data.get("dest", "")
    extra    = data.get("extra", {})   # 用户补充信息：英语分数、职业、学历等

    # 拉路线数据作为上下文
    routes   = db.get_routes_by_dest(dest) if dest else []
    latest   = db.get_latest_report()
    radar_summary = ""
    if latest and latest.get("report_md"):
        radar_summary = latest["report_md"][:1500]  # 不超 token 限制

    # 构建 prompt
    passport = profile.get("passport", "")
    current  = profile.get("current_country", "")
    visa     = profile.get("current_visa", "")
    route_ctx = "\n".join(
        f"- {r['name']} ({r['status']}, {r['typical_months_min']}–{r['typical_months_max']}个月): {r['description']}"
        for r in routes[:6]
    )

    prompt = f"""你是一位专业的移民顾问。用户情况如下：
护照国：{passport}
现居地：{current}（{visa}）
目标国：{dest}
用户补充信息：{json.dumps(extra, ensure_ascii=False)}

当前可用路线：
{route_ctx}

最新政策动态（摘要）：
{radar_summary}

请用中文给出：
1. 根据用户情况，推荐哪条路线，为什么（2-3句）
2. 现在最应该做的第一步是什么（具体，像顾问告诉客户的那种）
3. 需要注意的最大风险或坑（1-2个）

格式：直接输出，不要加标题编号，用自然对话语气，控制在150字以内。"""

    groq_key = os.environ.get("GROQ_API_KEY", "")
    if not groq_key:
        return jsonify({"advice": "（未配置 GROQ_API_KEY，顾问功能暂不可用）"})

    try:
        payload = json.dumps({
            "model": "llama-3.3-70b-versatile",
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 300,
            "temperature": 0.7,
        }).encode()
        req = urllib.request.Request(
            "https://api.groq.com/openai/v1/chat/completions",
            data=payload,
            headers={"Authorization": f"Bearer {groq_key}",
                     "Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=20) as resp:
            result = json.loads(resp.read())
        advice = result["choices"][0]["message"]["content"].strip()
        return jsonify({"advice": advice})
    except Exception as e:
        return jsonify({"advice": f"顾问暂时不可用：{e}"}), 500


# ── Trajectory API ───────────────────────────────────
@app.route("/api/me/trajectory")
@login_required
def my_trajectory():
    events = db.get_user_trajectory(session["user_id"])
    return jsonify(events)


@app.route("/api/world/events")
@login_required
def world_events():
    country = request.args.get("country", "")
    since   = request.args.get("since", "")
    events  = db.get_world_events(country_id=country, since=since, limit=100)
    return jsonify(events)


# ── Journeys API ─────────────────────────────────────
@app.route("/api/map/journeys")
@login_required
def map_journeys():
    journeys = db.get_all_journeys(session["user_id"])
    # 给每条旅程加上进度百分比
    for j in journeys:
        j["progress_pct"] = db.get_route_progress_pct(session["user_id"], j["route_id"])
    return jsonify(journeys)


# ── Pipeline API ───────────────────────────────────────
@app.route("/api/run-pipeline", methods=["POST"])
@login_required
def run_pipeline():
    job_id = db.create_job()

    def _run(jid):
        log = ""
        try:
            scripts_dir = os.path.join(os.path.dirname(__file__), "scripts")
            python = sys.executable

            # Step 1: fetch
            db.update_job(jid, "running", "📡 抓取资讯中…")
            r1 = subprocess.run(
                [python, os.path.join(scripts_dir, "immigration_fetch.py")],
                capture_output=True, text=True, timeout=120
            )
            log += r1.stdout + r1.stderr

            # Step 2: analyze
            db.update_job(jid, "running", log + "\n🤖 Claude 分析中…")
            r2 = subprocess.run(
                [python, os.path.join(scripts_dir, "immigration_analyze.py")],
                capture_output=True, text=True, timeout=180
            )
            log += r2.stdout + r2.stderr

            # Step 3: save report to DB
            raw_path    = "/tmp/immigration_raw.json"
            report_path = "/tmp/immigration_report.md"
            raw_json, report_md, week = "", "", ""
            if os.path.exists(raw_path):
                with open(raw_path, encoding="utf-8") as f:
                    raw_content = f.read()
                    raw_json = raw_content
                    week = json.loads(raw_content).get("week", "")
            if os.path.exists(report_path):
                with open(report_path, encoding="utf-8") as f:
                    report_md = f.read()

            if week:
                db.save_report(week, "", raw_json, report_md)

            db.update_job(jid, "done", log + "\n✅ 完成", finished=True)

        except Exception as e:
            db.update_job(jid, "failed", log + f"\n❌ {e}", finished=True)

    threading.Thread(target=_run, args=(job_id,), daemon=True).start()
    return jsonify({"ok": True, "job_id": job_id})


@app.route("/api/job/<int:job_id>")
@login_required
def job_status(job_id):
    job = db.get_job(job_id)
    if not job:
        return jsonify({"error": "not found"}), 404
    return jsonify({
        "status": job["status"],
        "log":    job["log"] or "",
        "done":   job["status"] in ("done", "failed")
    })


# ══════════════════════════════════════════════════════════════════════════════
# ADMIN — 维护面板 (密码保护，不依赖用户 session)
# ══════════════════════════════════════════════════════════════════════════════

ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "xinglu-admin")

def admin_required(f):
    from functools import wraps
    @wraps(f)
    def decorated(*args, **kwargs):
        if session.get("admin_authed"):
            return f(*args, **kwargs)
        return redirect(url_for("admin_login"))
    return decorated

@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    error = ""
    if request.method == "POST":
        if request.form.get("password") == ADMIN_PASSWORD:
            session["admin_authed"] = True
            return redirect(url_for("admin_dashboard"))
        error = "密码错误"
    return render_template("admin_login.html", error=error)

@app.route("/admin/logout")
def admin_logout():
    session.pop("admin_authed", None)
    return redirect(url_for("admin_login"))

@app.route("/admin")
@admin_required
def admin_dashboard():
    with db.engine.connect() as conn:
        from sqlalchemy import text as _t
        users = conn.execute(_t(
            "SELECT u.id, u.email, u.created_at, "
            "p.passport, p.current_country, p.destinations, p.current_visa "
            "FROM users u LEFT JOIN user_profiles p ON p.user_id=u.id "
            "ORDER BY u.id DESC LIMIT 50"
        )).fetchall()
        users = [dict(r._mapping) for r in users]

        events = conn.execute(_t(
            "SELECT * FROM world_events ORDER BY recorded_at DESC LIMIT 100"
        )).fetchall()
        events = [dict(r._mapping) for r in events]

        journeys = conn.execute(_t(
            "SELECT j.*, u.email FROM user_journeys j "
            "JOIN users u ON u.id=j.user_id "
            "ORDER BY j.updated_at DESC LIMIT 50"
        )).fetchall()
        journeys = [dict(r._mapping) for r in journeys]

        counts = {}
        for tbl in ["users", "routes", "tasks", "route_requirements", "world_events", "user_journeys", "user_task_progress"]:
            row = conn.execute(_t(f"SELECT COUNT(*) FROM {tbl}")).fetchone()
            counts[tbl] = row[0]

    return render_template("admin.html", users=users, events=events,
                           journeys=journeys, counts=counts)

@app.route("/admin/reset-user/<int:uid>", methods=["POST"])
@admin_required
def admin_reset_user(uid):
    """清空用户的旅程和任务进度（保留账号和 profile）"""
    with db.engine.begin() as conn:
        from sqlalchemy import text as _t
        conn.execute(_t("DELETE FROM user_journeys WHERE user_id=:uid"), {"uid": uid})
        conn.execute(_t("DELETE FROM user_task_progress WHERE user_id=:uid"), {"uid": uid})
        conn.execute(_t("DELETE FROM world_events WHERE entity_id=:uid AND entity_type='user'"), {"uid": str(uid)})
    return redirect(url_for("admin_dashboard"))


# ── Country Profiles ─────────────────────────────────
COUNTRY_PROFILES = {
    "nz": {
        "name": "新西兰", "emoji": "🇳🇿",
        "authority": "移民局 INZ (Immigration New Zealand)",
        "authority_url": "https://www.immigration.govt.nz",
        "tagline": "绿色移民，积分制，雇主签证快速通道",
        "main_routes": [
            {"name": "技术移民（SMC）", "type": "skilled", "desc": "积分制，EOI→邀请→申请", "months": "12–18"},
            {"name": "雇主担保（AEWV）", "type": "skilled", "desc": "雇主认证后可递签", "months": "3–6"},
            {"name": "绿色清单（Green List）", "type": "skilled", "desc": "特定职业直接获批", "months": "3–8"},
            {"name": "数字游民签证", "type": "nomad", "desc": "海外收入证明，最长9个月", "months": "1–2"},
            {"name": "家庭类（配偶/伴侣）", "type": "family", "desc": "与公民/永居伴侣", "months": "6–12"},
        ],
        "key_facts": [
            "永居（Resident Visa）→ 5年后可申请公民",
            "EOI 积分池定期开放抽签，需关注 EOI 公告",
            "Green List 职业直接落地无需积分，含护士、教师、工程师",
            "AEW 雇主需官网认证，求职时确认雇主资质",
        ],
        "processing_note": "SMC EOI 积分达 160 分通常可获邀，2024年门槛有所上升",
        "region_key": "🇳🇿 新西兰",
    },
    "au": {
        "name": "澳大利亚", "emoji": "🇦🇺",
        "authority": "内政部 DHA (Department of Home Affairs)",
        "authority_url": "https://immi.homeaffairs.gov.au",
        "tagline": "多通道移民大国，积分制+州担保",
        "main_routes": [
            {"name": "189 独立技术移民", "type": "skilled", "desc": "积分制，无需担保", "months": "12–24"},
            {"name": "190 州担保技术移民", "type": "skilled", "desc": "州政府担保+5分加成", "months": "9–18"},
            {"name": "491 区域担保技术移民", "type": "skilled", "desc": "偏远地区州担保", "months": "9–15"},
            {"name": "482 临时技术移民", "type": "skilled", "desc": "雇主担保工签，可续居", "months": "2–4"},
            {"name": "数字游民（非官方）", "type": "nomad", "desc": "旅游签+远程办公，无专项签证", "months": "—"},
        ],
        "key_facts": [
            "EOI 通过 SkillSelect 系统提交，积分达线等待邀请",
            "189分数线历史低点约65分，近年涨至80+",
            "职业需在 MLTSSL / STSOL 清单内",
            "技能评估（RPL/TRA等）通常需 2–6 个月，提前准备",
        ],
        "processing_note": "2024 年引入 SIQ（Skills in Demand）签替换部分 482 类别",
        "region_key": "🇦🇺 澳大利亚",
    },
    "ca": {
        "name": "加拿大", "emoji": "🇨🇦",
        "authority": "移民部 IRCC (Immigration, Refugees and Citizenship Canada)",
        "authority_url": "https://www.canada.ca/en/immigration-refugees-citizenship.html",
        "tagline": "Express Entry + 省提名，全球移民首选",
        "main_routes": [
            {"name": "Federal Skilled Worker (FSW)", "type": "skilled", "desc": "Express Entry主力流", "months": "6–12"},
            {"name": "Canadian Experience Class (CEC)", "type": "skilled", "desc": "在加工作经验", "months": "4–8"},
            {"name": "省提名计划 (PNP)", "type": "skilled", "desc": "各省独立配额", "months": "12–24"},
            {"name": "Start-up Visa", "type": "investment", "desc": "创业签，需指定机构支持", "months": "18–36"},
        ],
        "key_facts": [
            "CRS 综合评分系统，定期从池中抽取邀请（Draw）",
            "FSW 最低 CRS 约 480–520 分（波动较大）",
            "PNP 额外 600 分加成，中签率极高",
            "LMIA 雇主劳工市场影响评估可大幅提升评分",
        ],
        "processing_note": "2024 年 IRCC 宣布新移民配额调整，总量约 485,000/年",
        "region_key": "🇨🇦 加拿大",
    },
    "uk": {
        "name": "英国", "emoji": "🇬🇧",
        "authority": "内政部 / UK Visas and Immigration (UKVI)",
        "authority_url": "https://www.gov.uk/government/organisations/uk-visas-and-immigration",
        "tagline": "积分制工签，高技能人才优先",
        "main_routes": [
            {"name": "Skilled Worker Visa", "type": "skilled", "desc": "雇主担保+积分达70分", "months": "3–8"},
            {"name": "High Potential Individual (HPI)", "type": "skilled", "desc": "顶尖大学毕业生2年免担保工签", "months": "2–4"},
            {"name": "Global Talent Visa", "type": "skilled", "desc": "顶级人才，需机构背书", "months": "3–6"},
            {"name": "Graduate Visa", "type": "student", "desc": "英国毕业后2年工签", "months": "1–2"},
            {"name": "Family Visa", "type": "family", "desc": "配偶/伴侣/父母", "months": "8–12"},
        ],
        "key_facts": [
            "ILR（无限期居留）需合法居英 5 年",
            "2024年最低工资门槛大幅提升至 38,700 英镑/年",
            "Health Surcharge 每年约 1,035 英镑",
            "英语要求 B1 级别（CEFR）",
        ],
        "processing_note": "2024年起配偶签收入门槛分阶段提升，最终至 38,700 英镑",
        "region_key": "🇬🇧 英国",
    },
    "us": {
        "name": "美国", "emoji": "🇺🇸",
        "authority": "移民局 USCIS + 国务院 DOS",
        "authority_url": "https://www.uscis.gov",
        "tagline": "绿卡配额制，H-1B抽签，路长但值",
        "main_routes": [
            {"name": "H-1B 工作签证", "type": "skilled", "desc": "雇主担保+每年4月抽签", "months": "6–12（抽中后）"},
            {"name": "EB-1A/EB-1B 杰出人才", "type": "skilled", "desc": "无需雇主，绿卡优先", "months": "12–36"},
            {"name": "EB-2 NIW 国家利益豁免", "type": "skilled", "desc": "自请愿，无雇主担保", "months": "24–48"},
            {"name": "O-1 杰出能力签证", "type": "skilled", "desc": "文艺/体育/科学杰出人才", "months": "3–6"},
            {"name": "F-1 学生签证", "type": "student", "desc": "留学+OPT工作1–3年", "months": "1–3"},
        ],
        "key_facts": [
            "H-1B 每年 4 月电子抽签，中签率约 25%（近年下降）",
            "中国/印度出生绿卡优先级积压严重，等待时间可达 10–30 年",
            "NIW 申请无配额限制，但审理时间不稳定",
            "2025年 H-1B 规则更新，选择性注册期间有变化",
        ],
        "processing_note": "EB 类别中国出生等候时间极长，建议优先考虑 O-1 或 NIW",
        "region_key": "🇺🇸 美国",
    },
    "pt": {
        "name": "葡萄牙", "emoji": "🇵🇹",
        "authority": "移民与边境局 AIMA (ex-SEF)",
        "authority_url": "https://www.aima.gov.pt",
        "tagline": "D7被动收入签证，欧洲落脚首选",
        "main_routes": [
            {"name": "D7 被动收入签证", "type": "nomad", "desc": "月收入≥760€，家庭可降", "months": "2–4"},
            {"name": "D8 数字游民签证", "type": "nomad", "desc": "远程收入≥3,040€/月", "months": "2–4"},
            {"name": "黄金签证（正在收缩）", "type": "investment", "desc": "投资类，2023年大幅限制", "months": "12–24"},
            {"name": "Tech Visa", "type": "skilled", "desc": "科技行业雇主担保", "months": "2–4"},
        ],
        "key_facts": [
            "D7/D8 需在葡持有 NHR 税务身份，享受 10 年税收优惠（已更新为 IFICI）",
            "申根区自由行，5年可申请永居",
            "2024年 AIMA 更名替代 SEF，预约积压情况有改善",
            "里斯本/波尔图房价上涨，偏远地区更具性价比",
        ],
        "processing_note": "D7 签证在使领馆办理，落地后 4 个月内在 AIMA 转为居留许可",
        "region_key": "🇵🇹 葡萄牙",
    },
    "de": {
        "name": "德国", "emoji": "🇩🇪",
        "authority": "联邦移民与难民局 BAMF",
        "authority_url": "https://www.bamf.de",
        "tagline": "机遇卡（Chancenkarte）开放，技术移民加速",
        "main_routes": [
            {"name": "Chancenkarte 机遇卡", "type": "skilled", "desc": "积分制找工签证，1年有效", "months": "2–4"},
            {"name": "Fachkräftezuwanderungsgesetz 技术移民法", "type": "skilled", "desc": "雇主担保工签", "months": "3–6"},
            {"name": "EU 蓝卡 (Blue Card EU)", "type": "skilled", "desc": "高学历+高薪，快速永居", "months": "2–4"},
            {"name": "Niederlassungserlaubnis 定居许可", "type": "skilled", "desc": "永居，通常需 2–5 年合法居德", "months": "—"},
        ],
        "key_facts": [
            "机遇卡 2024 年正式实施，持有者可入德找工作",
            "EU 蓝卡月薪门槛约 3,909 EUR（部分职业更低）",
            "德语 B1 可加分，不强制要求但大幅提升机会",
            "获蓝卡后 21 个月（B1 德语）或 33 个月可申永居",
        ],
        "processing_note": "Chancenkarte 需至少 6 分（学历/经验/语言/年龄积分），入德后找工作3个月内需提交合同",
        "region_key": "🇩🇪 德国",
    },
    "jp": {
        "name": "日本", "emoji": "🇯🇵",
        "authority": "出入国在留管理厅 (Immigration Services Agency)",
        "authority_url": "https://www.isa.go.jp",
        "tagline": "高度人才积分制，特定技能扩大开放",
        "main_routes": [
            {"name": "高度専門職（积分制）", "type": "skilled", "desc": "积分≥70分，快速永居通道", "months": "1–3"},
            {"name": "特定技能 1 号 / 2 号", "type": "skilled", "desc": "特定行业，2号可永居", "months": "3–6"},
            {"name": "技术・人文知识・国际业务", "type": "skilled", "desc": "主流工签类别", "months": "1–3"},
            {"name": "数字游民（J-Find）", "type": "nomad", "desc": "顶尖大学毕业，6个月找工签", "months": "1–2"},
            {"name": "配偶签证", "type": "family", "desc": "与日本公民/永居结婚", "months": "1–3"},
        ],
        "key_facts": [
            "高度人才 80 分以上可 1 年后申请永居，70 分为 3 年",
            "特定技能 2 号 2024 年新增 11 个行业，含制造、建筑",
            "10 年合法居住一般可申请永居",
            "日语 N2 以上在积分系统中大幅加分",
        ],
        "processing_note": "2024年新设 J-Find 签证吸引海外人才来日，仅限特定大学毕业",
        "region_key": "🇯🇵 日本",
    },
    "th": {
        "name": "泰国", "emoji": "🇹🇭",
        "authority": "移民局 (Immigration Bureau of Thailand)",
        "authority_url": "https://www.immigration.go.th",
        "tagline": "LTR 长期居留签证，数字游民热门目的地",
        "main_routes": [
            {"name": "LTR 长期居留签证 (10年)", "type": "investment", "desc": "富裕人士/被动收入/远程工作者", "months": "1–3"},
            {"name": "Thailand Privilege (旧 Elite)", "type": "investment", "desc": "付费会员制，5–20年居留", "months": "1–2"},
            {"name": "退休签证 (Non-O-A)", "type": "investment", "desc": "50岁以上，存款80万泰铢", "months": "1–2"},
            {"name": "SMART Visa", "type": "skilled", "desc": "科技/高技能人才，4年", "months": "1–3"},
            {"name": "旅游签多次往返", "type": "nomad", "desc": "非正式数字游民方式", "months": "—"},
        ],
        "key_facts": [
            "LTR 远程工作者需海外收入≥80,000 USD/年，持有至少 1 年雇主合同",
            "Thailand Privilege 会籍费 60–2,500 万泰铢（约 1.6–70 万 USD）",
            "泰国无永居路径（永久居留 PR 极难获批）",
            "2024 年推出 90 天远程工作签证试点",
        ],
        "processing_note": "LTR 签证通过 BOI 网站申请，审批效率较传统移民局好",
        "region_key": "🇹🇭 泰国",
    },
    "ae": {
        "name": "阿联酋", "emoji": "🇦🇪",
        "authority": "联邦身份与公民局 ICP + GDRFA 迪拜",
        "authority_url": "https://icp.gov.ae",
        "tagline": "黄金签证10年居留，免税天堂",
        "main_routes": [
            {"name": "黄金签证 (10年)", "type": "investment", "desc": "房产≥200万迪拉姆/杰出人才/企业家", "months": "1–2"},
            {"name": "绿卡签证 (5年)", "type": "skilled", "desc": "技术工人/自由职业者", "months": "1–2"},
            {"name": "雇主工签", "type": "skilled", "desc": "公司担保，标准工作签", "months": "1–2"},
            {"name": "远程工作签证 (1年)", "type": "nomad", "desc": "海外雇主收入证明，可续签", "months": "1–2"},
        ],
        "key_facts": [
            "阿联酋无个人所得税",
            "黄金签证 2022 年扩展：博士/科学家/文化体育人才可申请",
            "迪拜 vs 阿布扎比：生活方式差异明显，迪拜更国际化",
            "无国籍转换路径，签证≠永居，长期规划需注意",
        ],
        "processing_note": "黄金签证房产路径：需持有完工房产，off-plan 不满足条件",
        "region_key": "🇦🇪 阿联酋",
    },
    "sg": {
        "name": "新加坡", "emoji": "🇸🇬",
        "authority": "移民与关卡局 ICA + 人力部 MOM",
        "authority_url": "https://www.ica.gov.sg",
        "tagline": "EP工作准证，亚洲金融中心，难但有路",
        "main_routes": [
            {"name": "Employment Pass (EP)", "type": "skilled", "desc": "最低月薪 5,000 SGD，雇主担保", "months": "3–8"},
            {"name": "ONE Pass (杰出人才)", "type": "skilled", "desc": "月薪≥30,000 SGD 或顶尖企业高管", "months": "2–4"},
            {"name": "Tech.Pass", "type": "skilled", "desc": "科技行业高薪人才，无需本地雇主", "months": "2–3"},
            {"name": "永久居民 (PR)", "type": "skilled", "desc": "合法居新 2 年以上可申请，竞争激烈", "months": "12–24"},
        ],
        "key_facts": [
            "PR 申请无明确标准，政府自由裁量，通过率约 30–40%",
            "新加坡公民资格需持有 PR 2 年以上",
            "2023 年大幅提升 EP 门槛，金融业最低月薪 5,500 SGD",
            "CECA 协议影响已收紧，印度籍申请人审查更严",
        ],
        "processing_note": "EP 被拒一般无正式原因，可重新申请但需调整薪资/职位",
        "region_key": "🇸🇬 新加坡",
    },
    "my": {
        "name": "马来西亚", "emoji": "🇲🇾",
        "authority": "移民厅 Jabatan Imigresen Malaysia",
        "authority_url": "https://www.imi.gov.my",
        "tagline": "MM2H 第二家园，低成本高生活质量",
        "main_routes": [
            {"name": "MM2H 第二家园计划", "type": "investment", "desc": "定期存款+收入要求，10年多次入境签", "months": "3–6"},
            {"name": "DE Rantau 数字游民", "type": "nomad", "desc": "月收入≥24,000 MYR，12个月可续签", "months": "1–3"},
            {"name": "工作准证（Employment Pass）", "type": "skilled", "desc": "雇主担保，月薪≥5,000 MYR", "months": "1–3"},
        ],
        "key_facts": [
            "MM2H 2021 年收紧：定期存款 150–200 万 MYR，年收入 4 万 MYR",
            "生活成本约新加坡 30–40%",
            "无永居→公民路径（华裔特殊情况除外）",
            "DE Rantau 在 MDEC 申请，吸引科技自由职业者",
        ],
        "processing_note": "MM2H 2024年再度修订，建议通过认证代理申请以提高通过率",
        "region_key": None,
    },
}


@app.route("/country/<code>")
@login_required
def country_page(code):
    code = code.lower()
    profile_data = COUNTRY_PROFILES.get(code)
    if not profile_data:
        flash("暂无该国家档案", "error")
        return redirect(url_for("map_page"))
    profile = db.get_profile(session["user_id"])
    # 从雷达数据提取该国家的最新新闻
    latest = db.get_latest_report()
    recent_news = []
    if latest and latest.get("raw_json"):
        try:
            raw = json.loads(latest["raw_json"])
            region_key = profile_data.get("region_key")
            if region_key:
                items = raw.get("by_region", {}).get(region_key, [])
                recent_news = items[:5]
        except Exception:
            pass
    return render_template("country.html",
                           code=code,
                           country=profile_data,
                           profile=profile,
                           recent_news=recent_news)


# ── Main ───────────────────────────────────────────────
if __name__ == "__main__":
    db.init_db()
    db.expire_stale_jobs()
    port = int(os.environ.get("PORT", 5002))
    print(f"🌟 星路 → http://127.0.0.1:{port}")
    app.run(debug=True, host="0.0.0.0", port=port)
