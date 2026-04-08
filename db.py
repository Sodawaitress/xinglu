"""
星路 · 数据层 v2 — 开放世界架构
SQLAlchemy Core — 本地 SQLite / 生产 PostgreSQL 无缝切换

三层结构：
  WORLD  层 — 客观世界（国家、签证、条件、路线、任务）
  TIME   层 — 政策事件流（append-only，世界变化日志）
  PLAYER 层 — 用户行为（旅程状态机、能力、任务进度）

切换数据库只需改环境变量：
  DATABASE_URL=sqlite:///./data/xinglu.db          ← 开发默认
  DATABASE_URL=postgresql://user:pass@host/db      ← Supabase / Railway / Render
"""

import os, json
from datetime import datetime, timezone, timedelta

from sqlalchemy import (
    create_engine, text, MetaData, Table, Column,
    Integer, String, Text, Boolean, ForeignKey, Float,
)

NZ_TZ = timezone(timedelta(hours=12))

_default_url = "sqlite:///" + os.path.join(os.path.dirname(__file__), "data", "xinglu.db")
DATABASE_URL  = os.environ.get("DATABASE_URL", _default_url)

_connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(DATABASE_URL, connect_args=_connect_args)
meta   = MetaData()


# ══════════════════════════════════════════════════════════════════════════════
# SCHEMA
# ══════════════════════════════════════════════════════════════════════════════

# ── Auth ──────────────────────────────────────────────────────────────────────

_users = Table("users", meta,
    Column("id",            Integer, primary_key=True, autoincrement=True),
    Column("email",         String(255), unique=True, nullable=False),
    Column("password_hash", String(255), nullable=False),
    Column("created_at",    String(50)),
)

_user_profiles = Table("user_profiles", meta,
    Column("id",              Integer, primary_key=True, autoincrement=True),
    Column("user_id",         Integer, ForeignKey("users.id"), unique=True),
    Column("passport",        String(100)),
    Column("current_country", String(100)),
    Column("current_visa",    String(100)),
    Column("destinations",    Text),       # JSON list e.g. '["au","nz"]'
    Column("route_type",      String(50)), # skilled/family/nomad/asylum/unknown
    Column("current_stage",   String(50)),
    Column("updated_at",      String(50)),
)

# ── WORLD layer ───────────────────────────────────────────────────────────────

_countries = Table("countries", meta,
    Column("id",     String(10), primary_key=True),   # "au", "nz", "jp"
    Column("name",   String(100)),                     # "澳大利亚"
    Column("name_en",String(100)),                     # "Australia"
    Column("coords", String(50)),                      # "[lat,lon]"
    Column("region", String(50)),                      # "oceania"
)

_visa_types = Table("visa_types", meta,
    Column("id",          String(50), primary_key=True),  # "au_189"
    Column("country_id",  String(10), ForeignKey("countries.id")),
    Column("name",        String(200)),
    Column("name_en",     String(200)),
    Column("category",    String(50)),   # skilled/family/nomad/asylum/student
    Column("description", Text),
    Column("official_url",String(500)),
)

_requirements = Table("requirements", meta,
    # Atomic, reusable requirement — shared across routes
    Column("id",          String(80), primary_key=True),  # "req_au_skills_assess"
    Column("country_id",  String(10), ForeignKey("countries.id")),
    Column("name",        String(200)),
    Column("description", Text),
    Column("req_type",    String(50)),   # document/test/application/payment/wait
    Column("typical_weeks_min", Integer),
    Column("typical_weeks_max", Integer),
    Column("typical_cost_usd",  Integer),
    Column("official_url", String(500)),
)

_routes = Table("routes", meta,
    Column("id",            String(80), primary_key=True),  # "au_189_cn"
    Column("visa_type_id",  String(50), ForeignKey("visa_types.id")),
    Column("from_country",  String(10), ForeignKey("countries.id")),
    Column("name",          String(200)),
    Column("status",        String(20)),   # open/restricted/closed/unknown
    Column("typical_months_min", Integer),
    Column("typical_months_max", Integer),
    Column("description",   Text),
    Column("risk_keywords", Text),         # JSON list
    Column("signal",        Text),
)

_route_requirements = Table("route_requirements", meta,
    # Many-to-many: route ↔ requirement, ordered
    Column("id",             Integer, primary_key=True, autoincrement=True),
    Column("route_id",       String(80), ForeignKey("routes.id")),
    Column("requirement_id", String(80), ForeignKey("requirements.id")),
    Column("order_index",    Integer),
    Column("is_optional",    Boolean, default=False),
    Column("notes",          Text),
)

_tasks = Table("tasks", meta,
    # Granular tasks hanging on requirements (not routes — reusable!)
    Column("id",             String(100), primary_key=True),  # "req_au_skills_assess_01"
    Column("requirement_id", String(80), ForeignKey("requirements.id")),
    Column("order_index",    Integer),
    Column("title",          String(300)),
    Column("description",    Text),
    Column("gotcha",         Text),   # common mistakes / traps
    Column("doc_url",        String(500)),
    Column("est_hours",      Float),
)

# ── TIME layer ────────────────────────────────────────────────────────────────
#
# 统一时空事件日志：所有发生的事都是 (when, where, what)
#
#   entity_type  entity_id       event_type 示例
#   ──────────── ─────────────── ─────────────────────────────
#   policy       au_189          status_change / suspend / reopen / quota_update
#   user         42              arrived / departed / route_selected / task_done
#   route        au_189_cn       signal_update
#   requirement  req_au_english  processing_time_change
#
# occurred_at  = 事件在真实世界发生的时间（政策生效日、用户实际抵达日）
# recorded_at  = 我们系统知道这件事的时间（可能滞后）
# lat / lon    = 事件发生的地理位置（可为空）
# country_id   = 空间索引的快捷字段（和 lat/lon 并存）

_world_events = Table("world_events", meta,
    Column("id",           Integer, primary_key=True, autoincrement=True),
    Column("occurred_at",  String(50)),   # 真实世界时间
    Column("recorded_at",  String(50)),   # 系统记录时间
    Column("lat",          Float),        # 纬度（可空）
    Column("lon",          Float),        # 经度（可空）
    Column("country_id",   String(10)),   # 空间快捷索引
    Column("entity_type",  String(30)),   # policy / user / route / requirement
    Column("entity_id",    String(100)),  # au_189 / 42 / au_189_cn
    Column("event_type",   String(60)),   # status_change / arrived / task_done …
    Column("payload",      Text),         # JSON — 任意细节
    Column("source_url",   String(500)),  # 来源（可空）
    Column("headline",     String(500)),  # 人类可读摘要
)

# 保留旧 policy_events 表向后兼容，新代码写 world_events
_policy_events = Table("policy_events", meta,
    Column("id",          Integer, primary_key=True, autoincrement=True),
    Column("country_id",  String(10)),
    Column("visa_type_id",String(50)),
    Column("route_id",    String(80)),
    Column("event_type",  String(50)),
    Column("old_value",   Text),
    Column("new_value",   Text),
    Column("source_url",  String(500)),
    Column("headline",    String(500)),
    Column("detected_at", String(50)),
)

# ── PLAYER layer ──────────────────────────────────────────────────────────────

_user_journeys = Table("user_journeys", meta,
    # State machine: every route a user ever looked at
    # States: exploring → active → paused → completed | abandoned | blocked
    Column("id",          Integer, primary_key=True, autoincrement=True),
    Column("user_id",     Integer, ForeignKey("users.id")),
    Column("route_id",    String(80), ForeignKey("routes.id")),
    Column("status",      String(20), default="exploring"),
    Column("started_at",  String(50)),
    Column("updated_at",  String(50)),
    Column("reason",      Text),   # why paused/abandoned/blocked
)

_user_capabilities = Table("user_capabilities", meta,
    # What user has: IELTS 7.5, EA skills assessment, PTE 79...
    Column("id",          Integer, primary_key=True, autoincrement=True),
    Column("user_id",     Integer, ForeignKey("users.id")),
    Column("capability",  String(100)),  # "ielts_overall", "skills_assessment_ea", "points_au_189"
    Column("value",       String(100)),  # "7.5", "positive", "75"
    Column("verified",    Boolean, default=False),
    Column("verified_at", String(50)),
    Column("expires_at",  String(50)),
    Column("notes",       Text),
    Column("created_at",  String(50)),
)

_user_task_progress = Table("user_task_progress", meta,
    Column("id",       Integer, primary_key=True, autoincrement=True),
    Column("user_id",  Integer, ForeignKey("users.id")),
    Column("task_id",  String(100), ForeignKey("tasks.id")),
    Column("done",     Boolean, default=False),
    Column("done_at",  String(50)),
    Column("note",     Text),
)

# ── Radar / Jobs (unchanged) ───────────────────────────────────────────────────

_radar_reports = Table("radar_reports", meta,
    Column("id",         Integer, primary_key=True, autoincrement=True),
    Column("week",       String(20), unique=True),
    Column("title",      String(200)),
    Column("raw_json",   Text),
    Column("report_md",  Text),
    Column("created_at", String(50)),
)

_pipeline_jobs = Table("pipeline_jobs", meta,
    Column("id",          Integer, primary_key=True, autoincrement=True),
    Column("status",      String(20), default="pending"),
    Column("log",         Text),
    Column("created_at",  String(50)),
    Column("finished_at", String(50)),
)


# ══════════════════════════════════════════════════════════════════════════════
# SEED DATA
# ══════════════════════════════════════════════════════════════════════════════

_SEED_COUNTRIES = [
    ("au", "澳大利亚", "Australia", "[-25.27,133.78]", "oceania"),
    ("nz", "新西兰",   "New Zealand","[-40.9,174.89]", "oceania"),
    ("ca", "加拿大",   "Canada",     "[56.13,-106.35]","north_america"),
    ("us", "美国",     "United States","[37.09,-95.71]","north_america"),
    ("uk", "英国",     "United Kingdom","[55.38,-3.44]","europe"),
    ("de", "德国",     "Germany",    "[51.17,10.45]",  "europe"),
    ("pt", "葡萄牙",   "Portugal",   "[39.40,-8.22]",  "europe"),
    ("jp", "日本",     "Japan",      "[36.20,138.25]", "asia"),
    ("sg", "新加坡",   "Singapore",  "[1.35,103.82]",  "asia"),
    ("th", "泰国",     "Thailand",   "[15.87,100.99]", "asia"),
    ("ae", "阿联酋",   "UAE",        "[23.42,53.85]",  "middle_east"),
    ("cn", "中国",     "China",      "[35.86,104.19]", "asia"),
    ("hk", "香港",     "Hong Kong",  "[22.32,114.17]", "asia"),
    ("my", "马来西亚", "Malaysia",   "[4.21,101.97]",  "asia"),
    ("id", "印度尼西亚","Indonesia",  "[-0.79,113.92]", "asia"),
    ("kr", "韩国",     "South Korea","[35.91,127.77]", "asia"),
]

_SEED_VISA_TYPES = [
    ("au_189", "au", "技术独立移民 189", "Skilled Independent 189", "skilled",
     "无需雇主担保，EOI邀请制，最热门技术移民路线", "https://immi.homeaffairs.gov.au/visas/getting-a-visa/visa-listing/skilled-independent-189"),
    ("au_190", "au", "技术州担保移民 190", "Skilled Nominated 190", "skilled",
     "州政府担保，加5分，比189竞争压力小", "https://immi.homeaffairs.gov.au/visas/getting-a-visa/visa-listing/skilled-nominated-190"),
    ("au_491", "au", "偏远地区担保 491", "Skilled Work Regional 491", "skilled",
     "临时签证，偏远地区工作3年后可申PR", "https://immi.homeaffairs.gov.au/visas/getting-a-visa/visa-listing/skilled-work-regional-provisional-491"),
    ("au_820", "au", "配偶/伴侣移民 820/801", "Partner Visa 820/801", "family",
     "与澳洲公民/PR结婚或事实婚姻", "https://immi.homeaffairs.gov.au/visas/getting-a-visa/visa-listing/partner-onshore-820-801"),
    ("nz_skilled", "nz", "技术移民 Accredited Employer", "Accredited Employer Work Visa", "skilled",
     "2022年改革后的新西兰主要工签路线，雇主需认证", "https://www.immigration.govt.nz/new-zealand-visas/visa-types/accredited-employer-work-visa"),
    ("nz_residence", "nz", "居留签证 Skilled Migrant", "Skilled Migrant Category Resident Visa", "skilled",
     "积分制居留，60分以上入池", "https://www.immigration.govt.nz/new-zealand-visas/visa-types/skilled-migrant-category-resident-visa"),
    ("ca_express", "ca", "快速通道 Express Entry", "Express Entry", "skilled",
     "联邦技术移民主通道，CRS综合评分制", "https://www.canada.ca/en/immigration-refugees-citizenship/services/immigrate-canada/express-entry.html"),
    ("ca_pnp", "ca", "省提名 PNP", "Provincial Nominee Program", "skilled",
     "各省担保，走联邦快速通道或直接申请", "https://www.canada.ca/en/immigration-refugees-citizenship/services/immigrate-canada/provincial-nominees.html"),
    ("uk_skilled", "uk", "高技术工作签证", "Skilled Worker Visa", "skilled",
     "雇主担保，点数制，取代Tier 2", "https://www.gov.uk/skilled-worker-visa"),
    ("uk_hnw", "uk", "高净值人士创新签证", "Innovator Founder Visa", "nomad",
     "创业/创新路线，需背书机构认可", "https://www.gov.uk/innovator-founder-visa"),
    ("jp_engineer", "jp", "高度专门职·工程师", "Engineer/Specialist in Humanities", "skilled",
     "日本最常见技术人才签证", "https://www.moj.go.jp/isa/applications/procedures/visa_engineer.html"),
    ("jp_hsp", "jp", "高度专业人才 HSP", "Highly Skilled Professional", "skilled",
     "积分制，70分1年/80分6个月可申PR", "https://www.moj.go.jp/isa/applications/procedures/visa_hsp.html"),
    ("jp_nomad", "jp", "特定活动（数字游民）", "Specified Activities Digital Nomad", "nomad",
     "2024年新设，6个月，需月收入>100万日元", "https://www.moj.go.jp/isa/"),
    ("pt_d7", "pt", "被动收入签证 D7", "D7 Passive Income Visa", "nomad",
     "退休/被动收入路线，葡萄牙+申根", "https://vistos.mne.gov.pt/en/national-visas/specific-visas/d7-visa"),
    ("de_job", "de", "德国找工作签证", "Germany Job Seeker Visa", "skilled",
     "6个月入境找工作，到手后转工作签", "https://www.make-it-in-germany.com/en/visa-residence/types/job-seekers"),
    ("ae_gv", "ae", "黄金签证 Golden Visa", "UAE Golden Visa", "nomad",
     "10年居留，房产/投资/专业人才多路径", "https://u.ae/en/information-and-services/visa-and-emirates-id/residence-visas/golden-visa"),
]

_SEED_REQUIREMENTS = [
    # ── AU 技术移民通用 ──────────────────────────────────────────────────────────
    ("req_au_skills_assess", "au", "技能评估", "Skills Assessment",
     "application", 8, 20, 500,
     "https://immi.homeaffairs.gov.au/visas/working-in-australia/skill-occupation-list"),
    ("req_au_english", "au", "英语考试 (PTE/IELTS/TOEFL)", "English Proficiency Test",
     "test", 1, 3, 300,
     "https://immi.homeaffairs.gov.au/help-support/meeting-our-requirements/english-language"),
    ("req_au_eoi", "au", "SkillSelect EOI 提交", "Submit EOI in SkillSelect",
     "application", 0, 0, 0,
     "https://online.immi.homeaffairs.gov.au/lusc/login"),
    ("req_au_state_nom", "au", "州担保申请 (190/491)", "State Nomination Application",
     "application", 4, 24, 0,
     "https://immi.homeaffairs.gov.au/visas/working-in-australia/skill-occupation-list"),
    ("req_au_invitation", "au", "等待邀请函 ITA", "Wait for Invitation to Apply",
     "wait", 1, 52, 0,
     "https://immi.homeaffairs.gov.au/visas/working-in-australia/skillselect"),
    ("req_au_visa_app", "au", "签证申请 (主申请人)", "Lodge Visa Application",
     "application", 0, 1, 4765,
     "https://immi.homeaffairs.gov.au/visas/getting-a-visa/visa-listing/skilled-independent-189"),
    ("req_au_health", "au", "健康检查", "Health Examination",
     "document", 1, 4, 400,
     "https://immi.homeaffairs.gov.au/help-support/meeting-our-requirements/health"),
    ("req_au_police", "au", "无犯罪记录证明", "Police Clearance",
     "document", 2, 8, 50,
     "https://immi.homeaffairs.gov.au/help-support/meeting-our-requirements/character"),
    ("req_au_processing", "au", "等待签证审批", "Visa Processing",
     "wait", 4, 36, 0,
     "https://immi.homeaffairs.gov.au/visas/getting-a-visa/visa-processing-times"),

    # ── NZ ─────────────────────────────────────────────────────────────────────
    ("req_nz_employer", "nz", "获取认证雇主工签 (AEWV)", "Accredited Employer Work Visa",
     "application", 2, 8, 700,
     "https://www.immigration.govt.nz/new-zealand-visas/visa-types/accredited-employer-work-visa"),
    ("req_nz_eoi", "nz", "SMC EOI 提交", "SMC EOI Submission",
     "application", 0, 0, 0,
     "https://www.immigration.govt.nz/new-zealand-visas/visa-types/skilled-migrant-category-resident-visa"),

    # ── JP ─────────────────────────────────────────────────────────────────────
    ("req_jp_coe", "jp", "在留资格认定证明书 COE", "Certificate of Eligibility",
     "application", 4, 12, 0,
     "https://www.moj.go.jp/isa/applications/procedures/visa_engineer.html"),
    ("req_jp_visa_app", "jp", "日本大使馆签证申请", "Japan Embassy Visa Application",
     "application", 1, 3, 30,
     "https://www.mofa.go.jp/j_info/visit/visa/index.html"),
    ("req_jp_hsp_points", "jp", "HSP 积分计算（需≥70分）", "Calculate HSP Points",
     "document", 0, 0, 0,
     "https://www.moj.go.jp/isa/applications/procedures/visa_hsp.html"),

    # ── CA ─────────────────────────────────────────────────────────────────────
    ("req_ca_ielts", "ca", "语言考试 (IELTS/CELPIP)", "Language Test",
     "test", 1, 2, 280,
     "https://www.canada.ca/en/immigration-refugees-citizenship/services/immigrate-canada/express-entry/documents/language-requirements.html"),
    ("req_ca_eoi", "ca", "Express Entry 建档", "Create Express Entry Profile",
     "application", 0, 0, 0,
     "https://www.canada.ca/en/immigration-refugees-citizenship/services/immigrate-canada/express-entry/apply-permanent-residence/create-profile.html"),
    ("req_ca_ita", "ca", "等待 ITA 邀请 (CRS排名)", "Wait for ITA (CRS Score)",
     "wait", 1, 52, 0,
     "https://www.canada.ca/en/immigration-refugees-citizenship/services/immigrate-canada/express-entry/submit-profile/rounds-invitations.html"),
]

_SEED_ROUTES = [
    # id, visa_type_id, from, name, status, min_mo, max_mo, desc, risk_kws, signal
    ("au_189_cn", "au_189", "cn", "中国 → 澳洲 189 技术独立移民",
     "open", 12, 48,
     "积分制，无需雇主或州担保。需技能评估+英语+EOI+ITA。",
     '["skills assessment delay","EOI pool closed","no invitation","189 suspended"]', ""),

    ("au_190_cn", "au_190", "cn", "中国 → 澳洲 190 州担保移民",
     "open", 18, 60,
     "需州提名，加5分。各州名额有限，开放时间不定。",
     '["190 nomination closed","state nomination pause","quota filled"]', ""),

    ("au_491_cn", "au_491", "cn", "中国 → 澳洲 491 偏远地区担保",
     "open", 24, 72,
     "临时签，偏远地区工作3年+满足收入要求后可转491。",
     '["491 closed","regional nomination suspended"]', ""),

    ("au_189_nz", "au_189", "nz", "新西兰 → 澳洲 189 技术独立移民",
     "open", 6, 36,
     "在新西兰已有工作经验，跨塔斯曼海直接申189。",
     '["skills assessment delay","no invitation","189 suspended"]', ""),

    ("au_820_cn", "au_820", "cn", "中国 → 澳洲 配偶/伴侣移民",
     "open", 18, 48,
     "与澳洲公民/PR结婚，在澳境内申820临时，自动转801永久。",
     '["partner visa backlog","processing halt"]', ""),

    ("nz_aewv_cn", "nz_skilled", "cn", "中国 → 新西兰 技术工签",
     "open", 3, 12,
     "雇主需持INZ认证。绿色职业清单雇主可直接招海外人才。",
     '["AEWV suspended","accreditation closed","Green List removed"]', ""),

    ("nz_smc_cn", "nz_residence", "cn", "中国 → 新西兰 SMC 技术居留",
     "open", 6, 24,
     "积分制居留，60分入池，先AEWV再EOI。",
     '["SMC suspended","pool closed","no draw"]', ""),

    ("ca_express_cn", "ca_express", "cn", "中国 → 加拿大 Express Entry",
     "open", 6, 24,
     "三项目（FSW/CEC/FSTC）积分制，CRS最低分每轮浮动。",
     '["Express Entry suspended","no draw","CRS cutoff raised"]', ""),

    ("jp_engineer_cn", "jp_engineer", "cn", "中国 → 日本 工程师签证",
     "open", 3, 8,
     "IT/理工学历或工作经验，雇主申请COE，之后大使馆签证。",
     '["visa suspended","COE delay","Japan entry ban"]', ""),

    ("jp_hsp_cn", "jp_hsp", "cn", "中国 → 日本 高度专业人才 HSP",
     "open", 4, 10,
     "70分1年可申PR，80分6个月可申PR。积分包含年龄/学历/收入。",
     '["HSP suspended","points system change"]', ""),

    ("jp_nomad_cn", "jp_nomad", "cn", "中国 → 日本 数字游民签证",
     "restricted", 2, 4,
     "2024年3月新设，6个月不可续，需月收入100万日元以上，且非日本境内受雇。",
     '["digital nomad visa ended","nomad cancelled","月收入要求"]', "⚠ 目前中国护照申请存在不确定性"),

    ("pt_d7_cn", "pt_d7", "cn", "中国 → 葡萄牙 D7 被动收入签证",
     "open", 6, 18,
     "退休金/远程工作/投资收入，月收入760€以上。申根区自由行。",
     '["D7 suspended","passive income requirement raised"]', ""),

    ("de_job_cn", "de_job", "cn", "中国 → 德国 找工作签证",
     "open", 3, 9,
     "持6个月找工作签入境，找到后在德国境内转工作居留许可。",
     '["job seeker suspended","Germany visa halt"]', ""),

    ("ae_gv_cn", "ae_gv", "cn", "中国 → 阿联酋 黄金签证",
     "open", 1, 3,
     "10年居留，多路径：200万迪拉姆房产/创业/专业人才/学者。",
     '["golden visa suspended","UAE entry restriction"]', ""),
]

# route_requirements: (route_id, req_id, order, optional, notes)
_SEED_ROUTE_REQS = [
    # AU 189
    ("au_189_cn", "req_au_english",      1, False, "需CLB 7+，推荐PTE Academic"),
    ("au_189_cn", "req_au_skills_assess",2, False, "选对评估机构很重要，IT用ACS，工程用EA"),
    ("au_189_cn", "req_au_eoi",          3, False, "填好EOI，确认职业代码和分数无误"),
    ("au_189_cn", "req_au_invitation",   4, False, "耐心等ITA，分数越高越快"),
    ("au_189_cn", "req_au_health",       5, False, "收到ITA后60天内完成"),
    ("au_189_cn", "req_au_police",       6, False, "每个住过12个月以上的国家都需要"),
    ("au_189_cn", "req_au_visa_app",     7, False, "主申+附申一起交"),
    ("au_189_cn", "req_au_processing",   8, False, "等待期保持地址、护照有效"),

    # AU 190 (共享技能评估和英语，加州担保环节)
    ("au_190_cn", "req_au_english",      1, False, ""),
    ("au_190_cn", "req_au_skills_assess",2, False, ""),
    ("au_190_cn", "req_au_eoi",          3, False, "EOI里勾选州提名意向"),
    ("au_190_cn", "req_au_state_nom",    4, False, "各州开放时间不定，需关注州移民局公告"),
    ("au_190_cn", "req_au_invitation",   5, False, "州提名后等联邦ITA"),
    ("au_190_cn", "req_au_health",       6, False, ""),
    ("au_190_cn", "req_au_police",       7, False, ""),
    ("au_190_cn", "req_au_visa_app",     8, False, "190申请费同189"),
    ("au_190_cn", "req_au_processing",   9, False, ""),

    # AU 491
    ("au_491_cn", "req_au_english",      1, False, ""),
    ("au_491_cn", "req_au_skills_assess",2, False, ""),
    ("au_491_cn", "req_au_eoi",          3, False, ""),
    ("au_491_cn", "req_au_state_nom",    4, False, "491州/地区担保，偏远地区雇主也可担保"),
    ("au_491_cn", "req_au_invitation",   5, False, ""),
    ("au_491_cn", "req_au_health",       6, False, ""),
    ("au_491_cn", "req_au_police",       7, False, ""),
    ("au_491_cn", "req_au_visa_app",     8, False, "491申请费3115澳元"),
    ("au_491_cn", "req_au_processing",   9, False, ""),

    # AU 189 from NZ
    ("au_189_nz", "req_au_english",      1, False, ""),
    ("au_189_nz", "req_au_skills_assess",2, False, ""),
    ("au_189_nz", "req_au_eoi",          3, False, ""),
    ("au_189_nz", "req_au_invitation",   4, False, ""),
    ("au_189_nz", "req_au_health",       5, False, ""),
    ("au_189_nz", "req_au_police",       6, False, ""),
    ("au_189_nz", "req_au_visa_app",     7, False, ""),
    ("au_189_nz", "req_au_processing",   8, False, ""),

    # NZ AEWV
    ("nz_aewv_cn", "req_au_english",    1, True,  "部分绿色职业清单豁免英语"),
    ("nz_aewv_cn", "req_nz_employer",   2, False, "雇主必须持INZ认证"),

    # NZ SMC
    ("nz_smc_cn", "req_au_english",     1, False, "需IELTS 6.5+ (各项6.0+)"),
    ("nz_smc_cn", "req_nz_employer",    2, True,  "有工签经验加分"),
    ("nz_smc_cn", "req_nz_eoi",         3, False, ""),

    # CA Express Entry
    ("ca_express_cn", "req_ca_ielts",   1, False, "FSW需CLB 7+，推荐IELTS General"),
    ("ca_express_cn", "req_ca_eoi",     2, False, "创建EE档案，填学历/工作/语言"),
    ("ca_express_cn", "req_ca_ita",     3, False, "等ITA，分数实时变化"),

    # JP Engineer
    ("jp_engineer_cn", "req_jp_coe",    1, False, "由日本雇主向法务省申请"),
    ("jp_engineer_cn", "req_jp_visa_app",2,False, "持COE去大使馆贴签"),

    # JP HSP
    ("jp_hsp_cn", "req_jp_hsp_points",  1, False, "先自算积分，确认够70分再继续"),
    ("jp_hsp_cn", "req_jp_coe",         2, False, "选高度人材类别"),
    ("jp_hsp_cn", "req_jp_visa_app",    3, False, ""),

    # JP Nomad
    ("jp_nomad_cn", "req_jp_visa_app",  1, False, "特定活动类别，大使馆申请"),

    # PT D7
    ("pt_d7_cn", "req_au_police",       1, False, "需中国无犯罪记录公证+海牙认证"),

    # DE Job Seeker (简化)
    ("de_job_cn", "req_au_police",      1, False, ""),

    # AE Golden Visa
    ("ae_gv_cn", "req_au_police",       1, False, ""),
]

# tasks: (id, req_id, order, title, description, gotcha, doc_url, est_hours)
_SEED_TASKS = [
    # ── req_au_skills_assess ────────────────────────────────────────────────────
    ("req_au_skills_assess_01", "req_au_skills_assess", 1,
     "确认职业代码 (ANZSCO)",
     "在 Skills in Demand 职业清单上找到你的职位对应的 ANZSCO 代码，确认该职业可申请 189/190/491",
     "很多人误选了相近职业，比如 Software Engineer 和 ICT Business Analyst 是不同代码，评估机构不同",
     "https://immi.homeaffairs.gov.au/visas/working-in-australia/skill-occupation-list", 2.0),

    ("req_au_skills_assess_02", "req_au_skills_assess", 2,
     "选择对应评估机构",
     "根据 ANZSCO 代码确认评估机构：IT类→ACS，工程类→Engineers Australia，会计→CPA/CA/CPAA",
     "同一个人的不同职业可能属于不同机构，不要自己猜，官网有明确对应表",
     "https://immi.homeaffairs.gov.au/visas/working-in-australia/skill-occupation-list", 1.0),

    ("req_au_skills_assess_03", "req_au_skills_assess", 3,
     "准备学历文件（公证+认证）",
     "毕业证+成绩单需公证，部分机构要求海牙认证或驻华使馆认证。提前联系学校开具英文版。",
     "学校英文成绩单有时需要提前2-4周申请，且有效期限制",
     "https://www.acs.org.au/msa/about-msa.html", 8.0),

    ("req_au_skills_assess_04", "req_au_skills_assess", 4,
     "准备工作经历证明",
     "每份工作需要：劳动合同/offer letter、工资单/税单、雇主推荐信（含具体职责描述）",
     "推荐信必须描述具体技术职责，HR出具的通用证明不够用，需要直属技术上级签字",
     "https://www.acs.org.au/msa/about-msa.html", 10.0),

    ("req_au_skills_assess_05", "req_au_skills_assess", 5,
     "提交评估申请并缴费",
     "在评估机构官网在线提交，上传所有文件。ACS费用约500澳元，EA约800澳元。",
     "部分机构允许分批上传，但建议一次性提交完整材料，避免来回沟通延误",
     "https://www.acs.org.au/msa/about-msa.html", 3.0),

    ("req_au_skills_assess_06", "req_au_skills_assess", 6,
     "回复评估机构补件要求",
     "机构可能要求补充材料。通常需在规定时间内回复（一般14天），否则申请可能关闭。",
     "补件邮件发到垃圾箱的情况时有发生，评估提交后每天检查邮箱",
     "https://www.acs.org.au/msa/about-msa.html", 4.0),

    ("req_au_skills_assess_07", "req_au_skills_assess", 7,
     "获得正面技能评估结果",
     "收到评估通过信（Positive Assessment），注意结果有效期（通常3年）",
     "评估结果只对应一个职业代码，如果你要申请不同职业，需要重新评估",
     "https://www.acs.org.au/msa/about-msa.html", 0.5),

    # ── req_au_english ─────────────────────────────────────────────────────────
    ("req_au_english_01", "req_au_english", 1,
     "确认目标分数要求",
     "189/190/491 Competent English = IELTS 6.0各项，Proficient = 7.0各项，Superior = 8.0各项。分数越高EOI加分越多。",
     "加分是大幅度的：Competent+0分，Proficient+10分，Superior+20分，差别巨大",
     "https://immi.homeaffairs.gov.au/help-support/meeting-our-requirements/english-language", 1.0),

    ("req_au_english_02", "req_au_english", 2,
     "报名并参加英语考试",
     "推荐 PTE Academic（电脑阅卷，出分快2-5天）或 IELTS Academic。报名费约250-320澳元。",
     "PTE出分快且可刷分，很多人选PTE。注意考场时间和取消政策",
     "https://www.pearsonpte.com/", 40.0),

    ("req_au_english_03", "req_au_english", 3,
     "验证成绩单可用于移民申请",
     "确认成绩在有效期内（通常3年），且达到目标分数线",
     "部分州担保对英语有更高要求，先查州移民局官网",
     "https://immi.homeaffairs.gov.au/help-support/meeting-our-requirements/english-language", 0.5),

    # ── req_au_eoi ─────────────────────────────────────────────────────────────
    ("req_au_eoi_01", "req_au_eoi", 1,
     "注册 ImmiAccount",
     "在 DOHA 官网注册账号，这是所有澳洲移民申请的统一入口",
     "不要通过中介账号提交，必须是你本人的账号，否则无法直接接收官方邮件",
     "https://online.immi.homeaffairs.gov.au/lusc/login", 0.5),

    ("req_au_eoi_02", "req_au_eoi", 2,
     "在 SkillSelect 填写 EOI",
     "填写职业、技能评估、英语成绩、工作年限、学历、年龄等信息，系统自动计算EOI分数",
     "EOI分数和ITA邀请分数不完全等同，务必填写准确，虚报信息是严重签证欺诈",
     "https://online.immi.homeaffairs.gov.au/lusc/login", 3.0),

    ("req_au_eoi_03", "req_au_eoi", 3,
     "确认EOI分数并激活",
     "检查计算分数是否符合预期，激活EOI进入邀请池",
     "EOI提交后可以随时修改，每次修改会重置提交时间（影响同分排序），谨慎修改",
     "https://online.immi.homeaffairs.gov.au/lusc/login", 1.0),

    # ── req_au_invitation ──────────────────────────────────────────────────────
    ("req_au_invitation_01", "req_au_invitation", 1,
     "关注 SkillSelect 每月抽签结果",
     "移民局每月（有时每两周）进行EOI抽签，公布最低邀请分数",
     "没有固定时间，订阅移民局邮件或关注 Skills in Demand 动态",
     "https://immi.homeaffairs.gov.au/visas/working-in-australia/skillselect", 0.5),

    ("req_au_invitation_02", "req_au_invitation", 2,
     "收到 ITA 邮件，确认60天有效期",
     "收到邀请函（Invitation to Apply）后，必须在60天内提交签证申请",
     "60天是硬截止，一分钟都不能晚，建议收到后立刻开始准备体检和警察证明",
     "https://online.immi.homeaffairs.gov.au/lusc/login", 0.5),

    # ── req_au_health ──────────────────────────────────────────────────────────
    ("req_au_health_01", "req_au_health", 1,
     "在 eHealth 系统提前注册",
     "收到ITA后立刻登录 ImmiAccount 的 eHealth，获取HAP ID，预约指定体检机构",
     "HAP ID是体检机构识别你的唯一凭证，预约时必须提供",
     "https://immi.homeaffairs.gov.au/help-support/meeting-our-requirements/health", 0.5),

    ("req_au_health_02", "req_au_health", 2,
     "预约体检（所有申请人，包括随行家属）",
     "主申请人+随行家属全部需要体检。联系 Bupa/Medibank 等指定诊所，尽早预约",
     "部分城市指定诊所名额紧张，ITA拿到后第一件事就是预约体检",
     "https://www.bupahealthassessments.com.au/", 1.0),

    ("req_au_health_03", "req_au_health", 3,
     "完成体检并等待结果上传",
     "体检当天带好护照和HAP ID。医院会直接将结果上传至移民局系统，无需你操作。",
     "体检结果通常7-14天上传，X光结果可能单独上传，两个都传完才算完成",
     "https://immi.homeaffairs.gov.au/help-support/meeting-our-requirements/health", 4.0),

    # ── req_au_police ──────────────────────────────────────────────────────────
    ("req_au_police_01", "req_au_police", 1,
     "确认需要哪些国家的警察证明",
     "所有曾居住超过12个月（16岁后）的国家都需要无犯罪记录证明",
     "很多人忘记算曾经留学或工作的国家，仔细回顾居住史",
     "https://immi.homeaffairs.gov.au/help-support/meeting-our-requirements/character", 1.0),

    ("req_au_police_02", "req_au_police", 2,
     "申请中国无犯罪记录证明（公安部）",
     "在中国居住记录通过公安部网站申请，需回国办或委托家人办。建议留足4-6周。",
     "中国无犯罪记录证明有效期通常为6个月，时机要掐准，太早申请到签证下来可能过期",
     "https://immi.homeaffairs.gov.au/help-support/meeting-our-requirements/character", 4.0),

    ("req_au_police_03", "req_au_police", 3,
     "申请其他居住国警察证明",
     "例如新西兰：通过 NZ Police Vetting Service 在线申请，约10个工作日",
     "每个国家流程不同，提前查好并分别申请",
     "https://www.police.govt.nz/advice-services/personal-vetting", 2.0),

    # ── req_au_visa_app ────────────────────────────────────────────────────────
    ("req_au_visa_app_01", "req_au_visa_app", 1,
     "准备签证申请所有材料清单",
     "护照、技能评估、英语成绩、EOI记录、体检结果、警察证明、出生证明（如有随行家属）等",
     "随行家属的所有材料同样需要准备，不能少任何一个家庭成员",
     "https://immi.homeaffairs.gov.au/visas/getting-a-visa/visa-listing/skilled-independent-189", 4.0),

    ("req_au_visa_app_02", "req_au_visa_app", 2,
     "在 ImmiAccount 提交签证申请并缴费",
     "189主申请人申请费约4765澳元，随行配偶+2385澳元，每个随行子女+1195澳元",
     "缴费后不退款，确认所有信息无误再支付",
     "https://online.immi.homeaffairs.gov.au/lusc/login", 2.0),

    ("req_au_visa_app_03", "req_au_visa_app", 3,
     "完成 Section 48 声明（如在澳境外申请）",
     "签证官可能要求补充材料或回答问题，在规定时间内回复",
     "收到移民局邮件后立刻处理，不要拖",
     "https://online.immi.homeaffairs.gov.au/lusc/login", 1.0),

    # ── req_jp_hsp_points ──────────────────────────────────────────────────────
    ("req_jp_hsp_points_01", "req_jp_hsp_points", 1,
     "使用法务省积分计算器自算分数",
     "积分来自：学历（博士30/硕士20/学士10）、年收（400万+10分，800万+30分）、年龄（29岁以下15分）、职位等",
     "收入要按日元计算，且必须是来自日本雇主的收入（远程为外国公司收入不算）",
     "https://www.moj.go.jp/isa/applications/procedures/visa_hsp.html", 2.0),

    ("req_jp_hsp_points_02", "req_jp_hsp_points", 2,
     "确认是否满足70分门槛",
     "70分以上才可申请HSP在留资格，80分以上可以更快申PR（6个月）",
     "一些加分项有效期，如日本国内大学毕业加分只有5年内有效",
     "https://www.moj.go.jp/isa/applications/procedures/visa_hsp.html", 0.5),

    # ── req_jp_coe ─────────────────────────────────────────────────────────────
    ("req_jp_coe_01", "req_jp_coe", 1,
     "雇主向地方法务局申请 COE",
     "由日本公司的人事部门向所在地的出入国在留管理局提交COE申请，材料包括雇用合同、公司注册证明等",
     "COE必须由日本雇主申请，个人无法自己申请，找工作是前提",
     "https://www.moj.go.jp/isa/applications/procedures/visa_engineer.html", 1.0),

    ("req_jp_coe_02", "req_jp_coe", 2,
     "等待 COE 审批（约1-3个月）",
     "法务局审批通过后邮寄纸质COE给雇主，雇主再快递给申请人",
     "旺季（4月前）可能延长至3-4个月，入职时间需和雇主提前商量好",
     "https://www.moj.go.jp/isa/applications/procedures/visa_engineer.html", 2.0),

    ("req_jp_coe_03", "req_jp_coe", 3,
     "收到COE后持原件去大使馆贴签",
     "携带护照、COE原件、签证申请表、照片，去驻华日本大使馆或领事馆",
     "COE有效期3个月，从发行日起计算，不要拖太久去使馆",
     "https://www.cn.emb-japan.go.jp/itpr_zh/visa.html", 2.0),

    # ── req_ca_ielts ───────────────────────────────────────────────────────────
    ("req_ca_ielts_01", "req_ca_ielts", 1,
     "确认所需 CLB 级别",
     "FSW需CLB 7（IELTS G: L6.0/R6.0/W6.0/S6.0），CEC需CLB 7(speak/listen)和CLB 5(read/write)",
     "Express Entry用IELTS General Training，不是Academic",
     "https://www.canada.ca/en/immigration-refugees-citizenship/services/immigrate-canada/express-entry/documents/language-requirements.html", 1.0),

    ("req_ca_ielts_02", "req_ca_ielts", 2,
     "报名 IELTS General Training",
     "在 IDP 或 British Council 报名，费用约280加元。考试城市选择较多。",
     "注意是General Training不是Academic，报错了分数不能用于移民",
     "https://www.ielts.org/", 40.0),

    # ── req_ca_eoi ─────────────────────────────────────────────────────────────
    ("req_ca_eoi_01", "req_ca_eoi", 1,
     "创建 Express Entry 档案",
     "在 IRCC 网站创建账号，填写教育、工作、语言成绩、资金证明等，系统自动计算CRS分数",
     "学历认证（ECA）需提前准备，常用机构WES约300加元，处理6-8周",
     "https://www.canada.ca/en/immigration-refugees-citizenship/services/immigrate-canada/express-entry/apply-permanent-residence/create-profile.html", 4.0),

    ("req_ca_eoi_02", "req_ca_eoi", 2,
     "获得学历认证 ECA（WES）",
     "海外学历需通过认可机构认证。WES是最常用的，认证本科约305加元",
     "WES认证需要学校直接寄送成绩单，学校到WES的时间不稳定",
     "https://www.wes.org/ca/", 20.0),

    # ── req_ca_ita ─────────────────────────────────────────────────────────────
    ("req_ca_ita_01", "req_ca_ita", 1,
     "关注每轮 Express Entry 抽签结果",
     "IRCC定期举行抽签，公布最低CRS分数线（cut-off）。各类别分数不同。",
     "2023年起IRCC开始按职业类别抽签（STEM、医疗等），不只是综合抽签，要关注对应类别",
     "https://www.canada.ca/en/immigration-refugees-citizenship/services/immigrate-canada/express-entry/submit-profile/rounds-invitations.html", 0.5),

    ("req_ca_ita_02", "req_ca_ita", 2,
     "收到 ITA 后90天内提交PR申请",
     "ITA邀请后有90天提交完整PR申请，包括所有家庭成员材料、体检、警察证明",
     "90天看起来很多，但所有材料凑齐往往需要2-3个月，立刻行动",
     "https://www.canada.ca/en/immigration-refugees-citizenship/services/immigrate-canada/express-entry/apply-permanent-residence.html", 2.0),

    # ── req_jp_visa_app ────────────────────────────────────────────────────────
    ("req_jp_visa_app_01", "req_jp_visa_app", 1,
     "准备签证申请材料",
     "护照（有效期6个月以上）、签证申请表（在使馆官网下载）、照片（4.5×4.5cm）、COE原件（如有）",
     "照片规格日本使馆要求很严：白色背景，6个月内拍摄，不能戴眼镜，不能用手机自拍",
     "https://www.cn.emb-japan.go.jp/itpr_zh/visa.html", 2.0),

    ("req_jp_visa_app_02", "req_jp_visa_app", 2,
     "预约并前往日本使馆/领事馆",
     "持COE去最近的日本驻华大使馆或总领事馆（北京/上海/广州/成都/沈阳）递交申请",
     "部分领事馆需要网上预约，不接受walk-in，提前1-2周预约",
     "https://www.cn.emb-japan.go.jp/itpr_zh/visa.html", 3.0),

    ("req_jp_visa_app_03", "req_jp_visa_app", 3,
     "等待审核并取签",
     "标准审理约5个工作日，特殊情况可能延长。通过后回使馆取护照和贴签证。",
     "签证贴好后检查入境次数（单次/多次）和有效期是否正确",
     "https://www.cn.emb-japan.go.jp/itpr_zh/visa.html", 1.0),

    # ── req_nz_employer ────────────────────────────────────────────────────────
    ("req_nz_employer_01", "req_nz_employer", 1,
     "确认雇主是否持有 INZ 认证",
     "在 INZ 官网搜索雇主是否在 Accredited Employer 名单中。认证雇主才可为海外人才申请 AEWV。",
     "部分雇主以为自己有认证，实际上已过期，入职前务必核实",
     "https://www.immigration.govt.nz/new-zealand-visas/visa-types/accredited-employer-work-visa/for-employers/check-if-you-are-accredited", 1.0),

    ("req_nz_employer_02", "req_nz_employer", 2,
     "雇主发起 Job Check（工作核查）",
     "雇主需先在 INZ 完成 Job Check，证明无法在本地找到合适人才，才能为海外求职者申请工签。",
     "Job Check 需要雇主提交招聘证明（广告截图等），处理约10个工作日，这一步由雇主操作，你无法代劳",
     "https://www.immigration.govt.nz/new-zealand-visas/visa-types/accredited-employer-work-visa", 2.0),

    ("req_nz_employer_03", "req_nz_employer", 3,
     "提交 AEWV 申请",
     "雇主 Job Check 通过后，你在 INZ 网站提交 AEWV 申请，费用约700新西兰元",
     "申请时需填写 Job Token（由雇主提供），没有这个无法提交",
     "https://www.immigration.govt.nz/new-zealand-visas/visa-types/accredited-employer-work-visa", 3.0),

    ("req_nz_employer_04", "req_nz_employer", 4,
     "等待签证审批（约20个工作日）",
     "AEWV 标准处理时间约20个工作日，绿色职业清单职业优先处理",
     "等待期间确保护照有效期在申请到期后还有6个月以上",
     "https://www.immigration.govt.nz/new-zealand-visas/visa-types/accredited-employer-work-visa", 1.0),

    # ── req_nz_eoi ─────────────────────────────────────────────────────────────
    ("req_nz_eoi_01", "req_nz_eoi", 1,
     "计算 SMC 积分",
     "在 INZ 官网使用积分计算器自算。60分以上才可提交 EOI。积分来自：技能评估、工作经验、年龄、英语、新西兰工作经验等",
     "新西兰工作经验加分非常高，在NZ工作满1年通常能明显提升积分",
     "https://www.immigration.govt.nz/new-zealand-visas/visa-types/skilled-migrant-category-resident-visa", 2.0),

    ("req_nz_eoi_02", "req_nz_eoi", 2,
     "提交技能移民 EOI（意向书）",
     "登录 INZ 网站提交 EOI，填写个人信息、技能评估结果、工作经验等。系统自动计算分数。",
     "EOI 提交后并非立即处理，INZ 会定期从池中抽取高分申请人，分数越高被选中越快",
     "https://www.immigration.govt.nz/new-zealand-visas/visa-types/skilled-migrant-category-resident-visa", 3.0),

    ("req_nz_eoi_03", "req_nz_eoi", 3,
     "收到邀请函后提交居留申请",
     "被选中后收到邀请提交居留申请（Residence Application），需在4个月内提交，附上所有材料",
     "居留申请材料量大，建议收到邀请后立即开始准备，4个月非常紧张",
     "https://www.immigration.govt.nz/new-zealand-visas/visa-types/skilled-migrant-category-resident-visa", 20.0),
]


# ══════════════════════════════════════════════════════════════════════════════
# INIT & SEED
# ══════════════════════════════════════════════════════════════════════════════

def _now() -> str:
    return datetime.now(NZ_TZ).isoformat()


def init_db():
    os.makedirs(os.path.join(os.path.dirname(__file__), "data"), exist_ok=True)
    meta.create_all(engine)
    _seed_world()


def _seed_world():
    """幂等写入 WORLD 层种子数据（已存在则跳过）"""
    with engine.begin() as conn:
        # Countries
        for row in _SEED_COUNTRIES:
            exists = conn.execute(
                text("SELECT 1 FROM countries WHERE id=:id"), {"id": row[0]}
            ).fetchone()
            if not exists:
                conn.execute(
                    text("INSERT INTO countries (id,name,name_en,coords,region) VALUES (:id,:n,:ne,:c,:r)"),
                    {"id":row[0],"n":row[1],"ne":row[2],"c":row[3],"r":row[4]}
                )

        # Visa types
        for row in _SEED_VISA_TYPES:
            exists = conn.execute(
                text("SELECT 1 FROM visa_types WHERE id=:id"), {"id": row[0]}
            ).fetchone()
            if not exists:
                conn.execute(
                    text("INSERT INTO visa_types (id,country_id,name,name_en,category,description,official_url) "
                         "VALUES (:id,:c,:n,:ne,:cat,:desc,:url)"),
                    {"id":row[0],"c":row[1],"n":row[2],"ne":row[3],"cat":row[4],"desc":row[5],"url":row[6]}
                )

        # Requirements
        for row in _SEED_REQUIREMENTS:
            exists = conn.execute(
                text("SELECT 1 FROM requirements WHERE id=:id"), {"id": row[0]}
            ).fetchone()
            if not exists:
                conn.execute(
                    text("INSERT INTO requirements "
                         "(id,country_id,name,description,req_type,typical_weeks_min,typical_weeks_max,typical_cost_usd,official_url) "
                         "VALUES (:id,:c,:n,:desc,:rt,:wmin,:wmax,:cost,:url)"),
                    {"id":row[0],"c":row[1],"n":row[2],"desc":row[3],"rt":row[4],
                     "wmin":row[5],"wmax":row[6],"cost":row[7],"url":row[8]}
                )

        # Routes
        for row in _SEED_ROUTES:
            exists = conn.execute(
                text("SELECT 1 FROM routes WHERE id=:id"), {"id": row[0]}
            ).fetchone()
            if not exists:
                conn.execute(
                    text("INSERT INTO routes "
                         "(id,visa_type_id,from_country,name,status,typical_months_min,typical_months_max,description,risk_keywords,signal) "
                         "VALUES (:id,:vt,:fc,:n,:st,:mmin,:mmax,:desc,:rk,:sig)"),
                    {"id":row[0],"vt":row[1],"fc":row[2],"n":row[3],"st":row[4],
                     "mmin":row[5],"mmax":row[6],"desc":row[7],"rk":row[8],"sig":row[9]}
                )

        # Route requirements
        for row in _SEED_ROUTE_REQS:
            exists = conn.execute(
                text("SELECT 1 FROM route_requirements WHERE route_id=:rid AND requirement_id=:reqid"),
                {"rid": row[0], "reqid": row[1]}
            ).fetchone()
            if not exists:
                conn.execute(
                    text("INSERT INTO route_requirements (route_id,requirement_id,order_index,is_optional,notes) "
                         "VALUES (:rid,:reqid,:ord,:opt,:notes)"),
                    {"rid":row[0],"reqid":row[1],"ord":row[2],"opt":row[3],"notes":row[4]}
                )

        # Tasks
        for row in _SEED_TASKS:
            exists = conn.execute(
                text("SELECT 1 FROM tasks WHERE id=:id"), {"id": row[0]}
            ).fetchone()
            if not exists:
                conn.execute(
                    text("INSERT INTO tasks (id,requirement_id,order_index,title,description,gotcha,doc_url,est_hours) "
                         "VALUES (:id,:req,:ord,:title,:desc,:gotcha,:url,:hrs)"),
                    {"id":row[0],"req":row[1],"ord":row[2],"title":row[3],
                     "desc":row[4],"gotcha":row[5],"url":row[6],"hrs":row[7]}
                )


# ══════════════════════════════════════════════════════════════════════════════
# AUTH
# ══════════════════════════════════════════════════════════════════════════════

def create_user(email: str, pw_hash: str) -> int:
    with engine.begin() as conn:
        result = conn.execute(
            text("INSERT INTO users (email,password_hash,created_at) VALUES (:e,:p,:t)"),
            {"e": email, "p": pw_hash, "t": _now()}
        )
        return result.lastrowid


def get_user_by_email(email: str):
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT * FROM users WHERE email=:e"), {"e": email}
        ).fetchone()
        return dict(row._mapping) if row else None


def get_user_by_id(user_id: int):
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT * FROM users WHERE id=:id"), {"id": user_id}
        ).fetchone()
        return dict(row._mapping) if row else None


# ══════════════════════════════════════════════════════════════════════════════
# USER PROFILE
# ══════════════════════════════════════════════════════════════════════════════

def save_profile(user_id, passport, current_country, current_visa,
                 destinations, route_type, current_stage=""):
    with engine.begin() as conn:
        exists = conn.execute(
            text("SELECT id FROM user_profiles WHERE user_id=:uid"), {"uid": user_id}
        ).fetchone()
        now = _now()
        if exists:
            conn.execute(
                text("UPDATE user_profiles SET passport=:p, current_country=:cc, "
                     "current_visa=:cv, destinations=:d, route_type=:rt, "
                     "current_stage=:cs, updated_at=:t WHERE user_id=:uid"),
                {"p": passport, "cc": current_country, "cv": current_visa,
                 "d": destinations, "rt": route_type, "cs": current_stage,
                 "t": now, "uid": user_id}
            )
        else:
            conn.execute(
                text("INSERT INTO user_profiles "
                     "(user_id,passport,current_country,current_visa,destinations,route_type,current_stage,updated_at) "
                     "VALUES (:uid,:p,:cc,:cv,:d,:rt,:cs,:t)"),
                {"uid": user_id, "p": passport, "cc": current_country, "cv": current_visa,
                 "d": destinations, "rt": route_type, "cs": current_stage, "t": now}
            )


def get_profile(user_id: int):
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT * FROM user_profiles WHERE user_id=:uid"), {"uid": user_id}
        ).fetchone()
        return dict(row._mapping) if row else None


# ══════════════════════════════════════════════════════════════════════════════
# WORLD — Routes & Requirements
# ══════════════════════════════════════════════════════════════════════════════

def get_routes_by_dest(dest_country: str) -> list:
    """返回目标国家的所有路线（带基础信息 + 迷你时间线）"""
    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT r.*, vt.name as visa_name, vt.category "
                 "FROM routes r JOIN visa_types vt ON r.visa_type_id=vt.id "
                 "WHERE r.id LIKE :pat ORDER BY r.status, r.typical_months_min"),
            {"pat": f"{dest_country}_%"}
        ).fetchall()
        result = []
        for row in rows:
            d = dict(row._mapping)
            try:
                d["risk_keywords"] = json.loads(d.get("risk_keywords") or "[]")
            except Exception:
                d["risk_keywords"] = []
            # 带入迷你时间线（requirement 名称列表，按顺序）
            req_rows = conn.execute(
                text("SELECT req.name FROM route_requirements rr "
                     "JOIN requirements req ON rr.requirement_id=req.id "
                     "WHERE rr.route_id=:rid ORDER BY rr.order_index"),
                {"rid": d["id"]}
            ).fetchall()
            d["stages"] = [r[0] for r in req_rows]
            result.append(d)
        return result


def get_route_requirements(route_id: str) -> list:
    """返回路线的有序需求列表（含任务数）"""
    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT rr.order_index, rr.is_optional, rr.notes, "
                 "req.id as req_id, req.name, req.description, req.req_type, "
                 "req.typical_weeks_min, req.typical_weeks_max, req.typical_cost_usd, req.official_url "
                 "FROM route_requirements rr "
                 "JOIN requirements req ON rr.requirement_id=req.id "
                 "WHERE rr.route_id=:rid ORDER BY rr.order_index"),
            {"rid": route_id}
        ).fetchall()
        return [dict(r._mapping) for r in rows]


# ══════════════════════════════════════════════════════════════════════════════
# PLAYER — Journeys (open-world state machine)
# ══════════════════════════════════════════════════════════════════════════════

def get_or_create_journey(user_id: int, route_id: str) -> dict:
    """获取或创建用户旅程（exploring状态）"""
    with engine.begin() as conn:
        row = conn.execute(
            text("SELECT * FROM user_journeys WHERE user_id=:uid AND route_id=:rid"),
            {"uid": user_id, "rid": route_id}
        ).fetchone()
        if row:
            return dict(row._mapping)
        now = _now()
        result = conn.execute(
            text("INSERT INTO user_journeys (user_id,route_id,status,started_at,updated_at) "
                 "VALUES (:uid,:rid,'exploring',:t,:t)"),
            {"uid": user_id, "rid": route_id, "t": now}
        )
        return {"id": result.lastrowid, "user_id": user_id, "route_id": route_id,
                "status": "exploring", "started_at": now, "updated_at": now}


def set_journey_status(journey_id: int, status: str, reason: str = ""):
    """状态机转换：exploring/active/paused/completed/abandoned/blocked"""
    with engine.begin() as conn:
        conn.execute(
            text("UPDATE user_journeys SET status=:s, reason=:r, updated_at=:t WHERE id=:id"),
            {"s": status, "r": reason, "t": _now(), "id": journey_id}
        )


def get_all_journeys(user_id: int) -> list:
    """获取用户所有旅程（包括已放弃的），开放世界：历史永远保留"""
    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT uj.*, r.name as route_name, r.status as route_status, "
                 "r.typical_months_min, r.typical_months_max, vt.category "
                 "FROM user_journeys uj "
                 "JOIN routes r ON uj.route_id=r.id "
                 "JOIN visa_types vt ON r.visa_type_id=vt.id "
                 "WHERE uj.user_id=:uid ORDER BY uj.updated_at DESC"),
            {"uid": user_id}
        ).fetchall()
        return [dict(r._mapping) for r in rows]


def get_active_journey(user_id: int) -> dict | None:
    """获取当前激活的旅程"""
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT uj.*, r.name as route_name "
                 "FROM user_journeys uj JOIN routes r ON uj.route_id=r.id "
                 "WHERE uj.user_id=:uid AND uj.status='active' "
                 "ORDER BY uj.updated_at DESC LIMIT 1"),
            {"uid": user_id}
        ).fetchone()
        return dict(row._mapping) if row else None


def save_user_route_selection(user_id: int, route_id: str, dest: str):
    """向后兼容：选择路线 = 创建/激活旅程"""
    journey = get_or_create_journey(user_id, route_id)
    set_journey_status(journey["id"], "active")
    # 时空记录：用户选择了这条路线
    country_id = route_id.split("_")[0] if route_id else ""
    log_world_event(
        entity_type="user", entity_id=str(user_id),
        event_type="route_selected",
        payload={"route_id": route_id, "dest": dest},
        country_id=country_id,
        headline=f"用户选择路线 {route_id}",
    )


# ══════════════════════════════════════════════════════════════════════════════
# PLAYER — Tasks & Progress
# ══════════════════════════════════════════════════════════════════════════════

def get_tasks_by_route(route_id: str) -> list:
    """通过 route→requirements→tasks 获取任务列表"""
    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT t.id, t.title, t.description, t.gotcha, t.doc_url, t.est_hours, "
                 "t.order_index as task_order, rr.order_index as req_order, "
                 "req.name as req_name, req.id as req_id "
                 "FROM route_requirements rr "
                 "JOIN requirements req ON rr.requirement_id=req.id "
                 "JOIN tasks t ON t.requirement_id=req.id "
                 "WHERE rr.route_id=:rid "
                 "ORDER BY rr.order_index, t.order_index"),
            {"rid": route_id}
        ).fetchall()
        return [dict(r._mapping) for r in rows]


def get_user_task_progress(user_id: int, route_id: str) -> dict:
    """返回 {task_id: {done, done_at, note}} for a route"""
    tasks = get_tasks_by_route(route_id)
    if not tasks:
        return {}
    task_ids = [t["id"] for t in tasks]
    placeholders = ",".join(f":id{i}" for i in range(len(task_ids)))
    params = {"uid": user_id}
    params.update({f"id{i}": tid for i, tid in enumerate(task_ids)})
    with engine.connect() as conn:
        rows = conn.execute(
            text(f"SELECT task_id, done, done_at, note FROM user_task_progress "
                 f"WHERE user_id=:uid AND task_id IN ({placeholders})"),
            params
        ).fetchall()
    result = {}
    for r in rows:
        d = dict(r._mapping)
        result[d["task_id"]] = d
    return result


def get_route_progress_pct(user_id: int, route_id: str) -> int:
    tasks = get_tasks_by_route(route_id)
    if not tasks:
        return 0
    progress = get_user_task_progress(user_id, route_id)
    done = sum(1 for t in tasks if progress.get(t["id"], {}).get("done"))
    return int(done / len(tasks) * 100)


def get_next_task(user_id: int, route_id: str) -> dict | None:
    tasks = get_tasks_by_route(route_id)
    progress = get_user_task_progress(user_id, route_id)
    for t in tasks:
        if not progress.get(t["id"], {}).get("done"):
            return t
    return None


def set_task_done(user_id: int, task_id: str, done: bool, note: str = ""):
    with engine.begin() as conn:
        exists = conn.execute(
            text("SELECT id FROM user_task_progress WHERE user_id=:uid AND task_id=:tid"),
            {"uid": user_id, "tid": task_id}
        ).fetchone()
        now = _now() if done else None
        if exists:
            conn.execute(
                text("UPDATE user_task_progress SET done=:d, done_at=:da, note=:n "
                     "WHERE user_id=:uid AND task_id=:tid"),
                {"d": done, "da": now, "n": note, "uid": user_id, "tid": task_id}
            )
        else:
            conn.execute(
                text("INSERT INTO user_task_progress (user_id,task_id,done,done_at,note) "
                     "VALUES (:uid,:tid,:d,:da,:n)"),
                {"uid": user_id, "tid": task_id, "d": done, "da": now, "n": note}
            )
    # 时空记录：任务完成事件
    if done:
        log_world_event(
            entity_type="user", entity_id=str(user_id),
            event_type="task_done",
            payload={"task_id": task_id, "note": note},
            headline=f"完成任务 {task_id}",
        )


# ══════════════════════════════════════════════════════════════════════════════
# PLAYER — Capabilities
# ══════════════════════════════════════════════════════════════════════════════

def save_capability(user_id: int, capability: str, value: str,
                    verified: bool = False, expires_at: str = "", notes: str = ""):
    with engine.begin() as conn:
        exists = conn.execute(
            text("SELECT id FROM user_capabilities WHERE user_id=:uid AND capability=:cap"),
            {"uid": user_id, "cap": capability}
        ).fetchone()
        now = _now()
        if exists:
            conn.execute(
                text("UPDATE user_capabilities SET value=:v, verified=:vf, "
                     "verified_at=:vat, expires_at=:exp, notes=:n WHERE id=:id"),
                {"v": value, "vf": verified, "vat": now if verified else None,
                 "exp": expires_at, "n": notes, "id": exists[0]}
            )
        else:
            conn.execute(
                text("INSERT INTO user_capabilities "
                     "(user_id,capability,value,verified,verified_at,expires_at,notes,created_at) "
                     "VALUES (:uid,:cap,:v,:vf,:vat,:exp,:n,:t)"),
                {"uid": user_id, "cap": capability, "v": value, "vf": verified,
                 "vat": now if verified else None, "exp": expires_at, "n": notes, "t": now}
            )


def get_capabilities(user_id: int) -> dict:
    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT capability, value, verified, expires_at FROM user_capabilities WHERE user_id=:uid"),
            {"uid": user_id}
        ).fetchall()
        return {r["capability"]: dict(r._mapping) for r in rows}


# ══════════════════════════════════════════════════════════════════════════════
# TIME — Policy Events (append-only)
# ══════════════════════════════════════════════════════════════════════════════

def log_world_event(entity_type: str, entity_id: str, event_type: str,
                    payload: dict = None, country_id: str = "",
                    lat: float = None, lon: float = None,
                    occurred_at: str = "", source_url: str = "", headline: str = ""):
    """
    统一时空事件写入。
    occurred_at 可指定真实世界时间；空则与 recorded_at 相同（「刚发生」）。
    lat/lon 可从 countries 表自动填充（传入 country_id 即可）。
    """
    now = _now()
    occ = occurred_at or now
    # 若未传经纬度，但有 country_id，从 countries 表查坐标
    if lat is None and country_id:
        with engine.connect() as conn:
            row = conn.execute(
                text("SELECT coords FROM countries WHERE id=:cid"), {"cid": country_id}
            ).fetchone()
            if row and row[0]:
                try:
                    coords = json.loads(row[0])
                    lat, lon = coords[0], coords[1]
                except Exception:
                    pass
    with engine.begin() as conn:
        conn.execute(
            text("INSERT INTO world_events "
                 "(occurred_at,recorded_at,lat,lon,country_id,entity_type,entity_id,event_type,payload,source_url,headline) "
                 "VALUES (:occ,:rec,:lat,:lon,:cid,:et,:eid,:evt,:pay,:src,:hdl)"),
            {"occ": occ, "rec": now, "lat": lat, "lon": lon, "cid": country_id,
             "et": entity_type, "eid": entity_id, "evt": event_type,
             "pay": json.dumps(payload or {}), "src": source_url, "hdl": headline}
        )


def get_world_events(entity_type: str = "", entity_id: str = "",
                     country_id: str = "", event_type: str = "",
                     since: str = "", limit: int = 50) -> list:
    """时空查询：按任意维度过滤事件流"""
    clauses = []
    params = {"lim": limit}
    if entity_type:
        clauses.append("entity_type=:et"); params["et"] = entity_type
    if entity_id:
        clauses.append("entity_id=:eid"); params["eid"] = entity_id
    if country_id:
        clauses.append("country_id=:cid"); params["cid"] = country_id
    if event_type:
        clauses.append("event_type=:evt"); params["evt"] = event_type
    if since:
        clauses.append("occurred_at>=:since"); params["since"] = since
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    with engine.connect() as conn:
        rows = conn.execute(
            text(f"SELECT * FROM world_events {where} ORDER BY occurred_at DESC LIMIT :lim"),
            params
        ).fetchall()
        result = []
        for r in rows:
            d = dict(r._mapping)
            try:
                d["payload"] = json.loads(d["payload"] or "{}")
            except Exception:
                pass
            result.append(d)
        return result


def get_user_trajectory(user_id: int) -> list:
    """用户的时空轨迹：他走过的每一步，按时间顺序"""
    return get_world_events(entity_type="user", entity_id=str(user_id), limit=200)


def get_country_timeline(country_id: str, since: str = "") -> list:
    """某个国家的政策事件时间线"""
    return get_world_events(country_id=country_id, entity_type="policy", since=since)


# 旧接口保留（向后兼容）
def log_policy_event(country_id="", visa_type_id="", route_id="",
                     event_type="", old_value="", new_value="",
                     source_url="", headline=""):
    # 同时写入新的 world_events 表
    log_world_event(
        entity_type="policy",
        entity_id=route_id or visa_type_id or country_id,
        event_type=event_type,
        payload={"old": old_value, "new": new_value, "visa_type_id": visa_type_id},
        country_id=country_id,
        source_url=source_url,
        headline=headline,
    )
    with engine.begin() as conn:
        conn.execute(
            text("INSERT INTO policy_events "
                 "(country_id,visa_type_id,route_id,event_type,old_value,new_value,source_url,headline,detected_at) "
                 "VALUES (:c,:vt,:r,:et,:ov,:nv,:su,:h,:t)"),
            {"c": country_id, "vt": visa_type_id, "r": route_id, "et": event_type,
             "ov": old_value, "nv": new_value, "su": source_url, "h": headline, "t": _now()}
        )


def get_recent_policy_events(limit: int = 20) -> list:
    return get_world_events(entity_type="policy", limit=limit)


# ══════════════════════════════════════════════════════════════════════════════
# RADAR REPORTS
# ══════════════════════════════════════════════════════════════════════════════

def save_report(week: str, title: str, raw_json: str, report_md: str):
    with engine.begin() as conn:
        exists = conn.execute(
            text("SELECT id FROM radar_reports WHERE week=:w"), {"w": week}
        ).fetchone()
        now = _now()
        if exists:
            conn.execute(
                text("UPDATE radar_reports SET title=:t, raw_json=:rj, report_md=:rm WHERE week=:w"),
                {"t": title, "rj": raw_json, "rm": report_md, "w": week}
            )
        else:
            conn.execute(
                text("INSERT INTO radar_reports (week,title,raw_json,report_md,created_at) "
                     "VALUES (:w,:t,:rj,:rm,:n)"),
                {"w": week, "t": title, "rj": raw_json, "rm": report_md, "n": now}
            )


def get_latest_report():
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT * FROM radar_reports ORDER BY created_at DESC LIMIT 1")
        ).fetchone()
        return dict(row._mapping) if row else None


def get_all_reports():
    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT id, week, title, created_at FROM radar_reports ORDER BY created_at DESC")
        ).fetchall()
        return [dict(r._mapping) for r in rows]


def get_report_by_id(report_id: int):
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT * FROM radar_reports WHERE id=:id"), {"id": report_id}
        ).fetchone()
        return dict(row._mapping) if row else None


# ══════════════════════════════════════════════════════════════════════════════
# PIPELINE JOBS
# ══════════════════════════════════════════════════════════════════════════════

def create_job() -> int:
    with engine.begin() as conn:
        result = conn.execute(
            text("INSERT INTO pipeline_jobs (status,log,created_at) VALUES ('pending','',:t)"),
            {"t": _now()}
        )
        return result.lastrowid


def update_job(job_id: int, status: str, log: str, finished: bool = False):
    with engine.begin() as conn:
        conn.execute(
            text("UPDATE pipeline_jobs SET status=:s, log=:l, finished_at=:f WHERE id=:id"),
            {"s": status, "l": log, "f": _now() if finished else None, "id": job_id}
        )


def get_job(job_id: int):
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT * FROM pipeline_jobs WHERE id=:id"), {"id": job_id}
        ).fetchone()
        return dict(row._mapping) if row else None


def expire_stale_jobs():
    """启动时把遗留的 running 状态改成 failed"""
    with engine.begin() as conn:
        conn.execute(
            text("UPDATE pipeline_jobs SET status='failed', log=log||'\n[expired on restart]' "
                 "WHERE status IN ('pending','running')")
        )
