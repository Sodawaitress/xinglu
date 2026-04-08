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


# ── Main ───────────────────────────────────────────────
if __name__ == "__main__":
    db.init_db()
    db.expire_stale_jobs()
    port = int(os.environ.get("PORT", 5002))
    print(f"🌟 星路 → http://127.0.0.1:{port}")
    app.run(debug=True, host="0.0.0.0", port=port)
