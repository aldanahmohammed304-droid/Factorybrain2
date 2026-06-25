"""
FactoryBrain - Industrial Knowledge Management System
=====================================================
Captures the expertise of workers and supervisors so it isn't lost
when they leave or retire. Built with Flask + SQLite + OpenAI GPT-4o.

Run:
    pip install -r requirements.txt
    set your key:  export OPENAI_API_KEY="sk-..."   (or edit config.py)
    python app.py
"""

import os
import json
import sqlite3
from datetime import datetime
from functools import wraps

from flask import (
    Flask, render_template, request, redirect, url_for,
    session, jsonify, g, flash
)
from werkzeug.security import generate_password_hash, check_password_hash

from config import Config
from ai_engine import extract_knowledge, ask_ai, ai_status, AIError
from translations import t as translate, LANGUAGES, DEFAULT_LANG

# --------------------------------------------------------------------------- #
#  App setup
# --------------------------------------------------------------------------- #
app = Flask(__name__)
app.config.from_object(Config)

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "factorybrain.db")


# --------------------------------------------------------------------------- #
#  Database helpers
# --------------------------------------------------------------------------- #
def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


@app.teardown_appcontext
def close_db(exception=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    """Create tables and seed the admin account + sample equipment."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    cur.executescript(
        """
        CREATE TABLE IF NOT EXISTS users (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            name          TEXT    NOT NULL,
            email         TEXT    NOT NULL UNIQUE,
            password_hash TEXT    NOT NULL,
            role          TEXT    NOT NULL DEFAULT 'user',  -- 'user' | 'admin'
            job_title     TEXT,
            created_at    TEXT    NOT NULL
        );

        CREATE TABLE IF NOT EXISTS knowledge (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id         INTEGER,
            equipment_name  TEXT    NOT NULL,
            problem_type    TEXT    NOT NULL,
            problem_cause   TEXT    NOT NULL,
            solution_steps  TEXT    NOT NULL,   -- JSON list of steps
            severity        TEXT    NOT NULL,   -- Low | Medium | High | Critical
            keywords        TEXT,               -- JSON list
            prevention      TEXT,               -- how to avoid recurrence
            raw_text        TEXT,
            created_at      TEXT    NOT NULL,
            FOREIGN KEY (user_id) REFERENCES users(id)
        );

        CREATE TABLE IF NOT EXISTS ratings (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            knowledge_id INTEGER NOT NULL,
            user_id      INTEGER,
            score        INTEGER NOT NULL,       -- 1..5
            created_at   TEXT    NOT NULL,
            FOREIGN KEY (knowledge_id) REFERENCES knowledge(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS incidents (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            equipment_name  TEXT    NOT NULL,
            description     TEXT    NOT NULL,
            downtime_minutes INTEGER DEFAULT 0,
            solution_used   TEXT,
            status          TEXT    NOT NULL DEFAULT 'open',  -- open | closed
            reported_by     INTEGER,
            created_at      TEXT    NOT NULL,
            closed_at       TEXT,
            FOREIGN KEY (reported_by) REFERENCES users(id)
        );

        CREATE TABLE IF NOT EXISTS tasks (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id      INTEGER NOT NULL,
            title        TEXT    NOT NULL,
            category     TEXT,                  -- Inspection | Training | Operation | Follow-up
            details      TEXT,
            done         INTEGER NOT NULL DEFAULT 0,
            assigned_by  INTEGER,
            created_at   TEXT    NOT NULL,
            due_date     TEXT,
            FOREIGN KEY (user_id) REFERENCES users(id)
        );
        """
    )

    # Migration: add columns introduced after the DB was first created.
    cols = {r["name"] for r in cur.execute("PRAGMA table_info(knowledge)").fetchall()}
    if "prevention" not in cols:
        cur.execute("ALTER TABLE knowledge ADD COLUMN prevention TEXT")

    # Seed admin
    admin = cur.execute(
        "SELECT id FROM users WHERE email = ?", (Config.ADMIN_EMAIL,)
    ).fetchone()
    if not admin:
        cur.execute(
            "INSERT INTO users (name, email, password_hash, role, job_title, created_at) "
            "VALUES (?,?,?,?,?,?)",
            (
                "System Admin",
                Config.ADMIN_EMAIL,
                generate_password_hash(Config.ADMIN_PASSWORD),
                "admin",
                "Administrator",
                datetime.utcnow().isoformat(),
            ),
        )

    conn.commit()
    conn.close()


# --------------------------------------------------------------------------- #
#  Auth decorators
# --------------------------------------------------------------------------- #
def login_required(f):
    @wraps(f)
    def wrapped(*args, **kwargs):
        if "user_id" not in session:
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return wrapped


def admin_required(f):
    @wraps(f)
    def wrapped(*args, **kwargs):
        if session.get("role") != "admin":
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return wrapped


def current_user():
    if "user_id" not in session:
        return None
    return get_db().execute(
        "SELECT * FROM users WHERE id = ?", (session["user_id"],)
    ).fetchone()


# --------------------------------------------------------------------------- #
#  Public / Auth routes
# --------------------------------------------------------------------------- #
@app.route("/")
def index():
    if session.get("role") == "admin":
        return redirect(url_for("admin_dashboard"))
    if "user_id" in session:
        return redirect(url_for("dashboard"))
    return redirect(url_for("login"))


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        user = get_db().execute(
            "SELECT * FROM users WHERE email = ?", (email,)
        ).fetchone()

        if user and check_password_hash(user["password_hash"], password):
            session.clear()
            session["user_id"] = user["id"]
            session["role"] = user["role"]
            session["name"] = user["name"]
            if user["role"] == "admin":
                return redirect(url_for("admin_dashboard"))
            return redirect(url_for("dashboard"))
        flash("Invalid email or password.", "error")
    return render_template("login.html")


@app.route("/signup", methods=["GET", "POST"])
def signup():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        job_title = request.form.get("job_title", "").strip()

        if not (name and email and password):
            flash("All fields are required.", "error")
            return render_template("signup.html")

        if len(password) < 6 or not any(c.isupper() for c in password):
            flash("Password must be at least 6 characters and include one uppercase letter.", "error")
            return render_template("signup.html")

        db = get_db()
        existing = db.execute(
            "SELECT id FROM users WHERE email = ?", (email,)
        ).fetchone()
        if existing:
            flash("Email already registered.", "error")
            return render_template("signup.html")

        db.execute(
            "INSERT INTO users (name, email, password_hash, role, job_title, created_at) "
            "VALUES (?,?,?,?,?,?)",
            (name, email, generate_password_hash(password), "user",
             job_title, datetime.utcnow().isoformat()),
        )
        db.commit()
        flash("Account created. Please log in.", "success")
        return redirect(url_for("login"))
    return render_template("signup.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/set-lang/<lang>")
def set_lang(lang):
    if lang in LANGUAGES:
        session["lang"] = lang
    # Return to the page the user came from, or the dashboard.
    return redirect(request.referrer or url_for("login"))


# --------------------------------------------------------------------------- #
#  USER side
# --------------------------------------------------------------------------- #
@app.route("/dashboard")
@login_required
def dashboard():
    db = get_db()
    uid = session["user_id"]
    stats = {
        "my_knowledge": db.execute(
            "SELECT COUNT(*) c FROM knowledge WHERE user_id=?", (uid,)).fetchone()["c"],
        "total_knowledge": db.execute(
            "SELECT COUNT(*) c FROM knowledge").fetchone()["c"],
        "open_tasks": db.execute(
            "SELECT COUNT(*) c FROM tasks WHERE user_id=? AND done=0", (uid,)).fetchone()["c"],
        "equipment_count": db.execute(
            "SELECT COUNT(DISTINCT equipment_name) c FROM knowledge").fetchone()["c"],
    }
    tasks = db.execute(
        "SELECT * FROM tasks WHERE user_id=? ORDER BY done ASC, created_at DESC LIMIT 6", (uid,)
    ).fetchall()
    recent = db.execute(
        "SELECT k.*, u.name author FROM knowledge k LEFT JOIN users u ON k.user_id=u.id "
        "ORDER BY k.created_at DESC LIMIT 5"
    ).fetchall()
    return render_template("dashboard.html", user=current_user(),
                           stats=stats, tasks=tasks, recent=recent)


@app.route("/add-knowledge", methods=["GET"])
@login_required
def add_knowledge_page():
    return render_template("add_knowledge.html", user=current_user())


@app.route("/api/extract", methods=["POST"])
@login_required
def api_extract():
    """Take raw text, return structured fields via GPT-4o (preview before saving)."""
    data = request.get_json(force=True)
    raw = (data.get("text") or "").strip()
    if not raw:
        return jsonify({"error": "Empty text"}), 400
    try:
        result = extract_knowledge(raw)
    except AIError as e:
        return jsonify({"error": str(e)}), 503
    return jsonify(result)


@app.route("/api/save-knowledge", methods=["POST"])
@login_required
def api_save_knowledge():
    data = request.get_json(force=True)
    db = get_db()
    db.execute(
        "INSERT INTO knowledge (user_id, equipment_name, problem_type, problem_cause, "
        "solution_steps, severity, keywords, prevention, raw_text, created_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        (
            session["user_id"],
            data.get("equipment_name", "Unknown"),
            data.get("problem_type", ""),
            data.get("problem_cause", ""),
            json.dumps(data.get("solution_steps", []), ensure_ascii=False),
            data.get("severity", "Medium"),
            json.dumps(data.get("keywords", []), ensure_ascii=False),
            data.get("prevention", ""),
            data.get("raw_text", ""),
            datetime.utcnow().isoformat(),
        ),
    )
    db.commit()
    return jsonify({"ok": True})


@app.route("/knowledge")
@login_required
def knowledge_list():
    db = get_db()
    q = request.args.get("q", "").strip()
    if q:
        like = f"%{q}%"
        rows = db.execute(
            "SELECT k.*, u.name author FROM knowledge k LEFT JOIN users u ON k.user_id=u.id "
            "WHERE k.equipment_name LIKE ? OR k.problem_type LIKE ? OR k.problem_cause LIKE ? "
            "OR k.keywords LIKE ? ORDER BY k.created_at DESC",
            (like, like, like, like),
        ).fetchall()
    else:
        rows = db.execute(
            "SELECT k.*, u.name author FROM knowledge k LEFT JOIN users u ON k.user_id=u.id "
            "ORDER BY k.created_at DESC"
        ).fetchall()
    items = [_format_knowledge(r, db) for r in rows]
    return render_template("knowledge.html", user=current_user(), items=items, q=q)


@app.route("/ask")
@login_required
def ask_page():
    return render_template("ask.html", user=current_user())


@app.route("/api/ask", methods=["POST"])
@login_required
def api_ask():
    data = request.get_json(force=True)
    question = (data.get("question") or "").strip()
    if not question:
        return jsonify({"error": "Empty question"}), 400

    db = get_db()
    # Pull candidate knowledge (simple keyword retrieval over the DB)
    words = [w for w in question.replace("?", " ").split() if len(w) > 2]
    rows = []
    seen = set()
    for w in words:
        like = f"%{w}%"
        for r in db.execute(
            "SELECT * FROM knowledge WHERE equipment_name LIKE ? OR problem_type LIKE ? "
            "OR problem_cause LIKE ? OR keywords LIKE ? OR raw_text LIKE ? LIMIT 8",
            (like, like, like, like, like),
        ).fetchall():
            if r["id"] not in seen:
                seen.add(r["id"])
                rows.append(r)
    if not rows:  # fallback: most recent
        rows = db.execute("SELECT * FROM knowledge ORDER BY created_at DESC LIMIT 8").fetchall()

    context = [_knowledge_to_dict(r) for r in rows]
    try:
        answer = ask_ai(question, context)
    except AIError as e:
        return jsonify({"error": str(e)}), 503
    return jsonify({"answer": answer, "sources": [
        {"id": r["id"], "equipment": r["equipment_name"], "problem": r["problem_type"]}
        for r in rows[:5]
    ]})


@app.route("/equipment")
@login_required
def equipment_list():
    db = get_db()
    rows = db.execute(
        "SELECT equipment_name, COUNT(*) issues, "
        "SUM(CASE WHEN severity IN ('High','Critical') THEN 1 ELSE 0 END) critical "
        "FROM knowledge GROUP BY equipment_name ORDER BY issues DESC"
    ).fetchall()
    return render_template("equipment.html", user=current_user(), equipment=rows)


@app.route("/equipment/detail")
@login_required
def equipment_detail():
    name = request.args.get("name", "")
    db = get_db()
    rows = db.execute(
        "SELECT k.*, u.name author FROM knowledge k LEFT JOIN users u ON k.user_id=u.id "
        "WHERE k.equipment_name = ? ORDER BY k.created_at DESC", (name,)
    ).fetchall()
    items = [_format_knowledge(r, db) for r in rows]
    incidents = db.execute(
        "SELECT * FROM incidents WHERE equipment_name=? ORDER BY created_at DESC", (name,)
    ).fetchall()
    return render_template("equipment_detail.html", user=current_user(),
                           name=name, items=items, incidents=incidents)


@app.route("/api/rate", methods=["POST"])
@login_required
def api_rate():
    data = request.get_json(force=True)
    kid = data.get("knowledge_id")
    score = int(data.get("score", 0))
    if not (1 <= score <= 5):
        return jsonify({"error": "Score must be 1-5"}), 400
    db = get_db()
    db.execute(
        "INSERT INTO ratings (knowledge_id, user_id, score, created_at) VALUES (?,?,?,?)",
        (kid, session["user_id"], score, datetime.utcnow().isoformat()),
    )
    db.commit()
    avg = db.execute(
        "SELECT AVG(score) a, COUNT(*) c FROM ratings WHERE knowledge_id=?", (kid,)
    ).fetchone()
    return jsonify({"ok": True, "avg": round(avg["a"], 1), "count": avg["c"]})


@app.route("/tasks")
@login_required
def my_tasks():
    db = get_db()
    rows = db.execute(
        "SELECT * FROM tasks WHERE user_id=? ORDER BY done ASC, created_at DESC",
        (session["user_id"],)
    ).fetchall()
    return render_template("tasks.html", user=current_user(), tasks=rows)


@app.route("/api/task-toggle", methods=["POST"])
@login_required
def api_task_toggle():
    data = request.get_json(force=True)
    tid = data.get("task_id")
    db = get_db()
    t = db.execute("SELECT * FROM tasks WHERE id=? AND user_id=?",
                   (tid, session["user_id"])).fetchone()
    if not t:
        return jsonify({"error": "Not found"}), 404
    new = 0 if t["done"] else 1
    db.execute("UPDATE tasks SET done=? WHERE id=?", (new, tid))
    db.commit()
    return jsonify({"ok": True, "done": new})


# --------------------------------------------------------------------------- #
#  INCIDENTS (shared)
# --------------------------------------------------------------------------- #
@app.route("/incidents")
@login_required
def incidents_page():
    db = get_db()
    rows = db.execute(
        "SELECT i.*, u.name reporter FROM incidents i LEFT JOIN users u ON i.reported_by=u.id "
        "ORDER BY i.status ASC, i.created_at DESC"
    ).fetchall()
    equipment = db.execute(
        "SELECT DISTINCT equipment_name FROM knowledge ORDER BY equipment_name"
    ).fetchall()
    return render_template("incidents.html", user=current_user(),
                           incidents=rows, equipment=equipment)


@app.route("/api/incident", methods=["POST"])
@login_required
def api_incident_create():
    data = request.get_json(force=True)
    db = get_db()
    db.execute(
        "INSERT INTO incidents (equipment_name, description, downtime_minutes, "
        "solution_used, status, reported_by, created_at) VALUES (?,?,?,?,?,?,?)",
        (
            data.get("equipment_name", "Unknown"),
            data.get("description", ""),
            int(data.get("downtime_minutes", 0) or 0),
            data.get("solution_used", ""),
            data.get("status", "open"),
            session["user_id"],
            datetime.utcnow().isoformat(),
        ),
    )
    db.commit()
    return jsonify({"ok": True})


@app.route("/api/incident-close", methods=["POST"])
@login_required
def api_incident_close():
    data = request.get_json(force=True)
    db = get_db()
    db.execute(
        "UPDATE incidents SET status='closed', solution_used=?, closed_at=? WHERE id=?",
        (data.get("solution_used", ""), datetime.utcnow().isoformat(), data.get("id")),
    )
    db.commit()
    return jsonify({"ok": True})


# --------------------------------------------------------------------------- #
#  ADMIN side
# --------------------------------------------------------------------------- #
@app.route("/admin")
@admin_required
def admin_dashboard():
    db = get_db()
    stats = {
        "users": db.execute("SELECT COUNT(*) c FROM users WHERE role='user'").fetchone()["c"],
        "knowledge": db.execute("SELECT COUNT(*) c FROM knowledge").fetchone()["c"],
        "open_incidents": db.execute(
            "SELECT COUNT(*) c FROM incidents WHERE status='open'").fetchone()["c"],
        "closed_incidents": db.execute(
            "SELECT COUNT(*) c FROM incidents WHERE status='closed'").fetchone()["c"],
    }
    top_equipment = db.execute(
        "SELECT equipment_name, COUNT(*) issues FROM knowledge "
        "GROUP BY equipment_name ORDER BY issues DESC LIMIT 6"
    ).fetchall()
    severity = db.execute(
        "SELECT severity, COUNT(*) c FROM knowledge GROUP BY severity"
    ).fetchall()
    best = db.execute(
        "SELECT k.id, k.equipment_name, k.problem_type, AVG(r.score) avg, COUNT(r.id) cnt "
        "FROM knowledge k JOIN ratings r ON r.knowledge_id=k.id "
        "GROUP BY k.id HAVING cnt>0 ORDER BY avg DESC, cnt DESC LIMIT 5"
    ).fetchall()
    return render_template("admin_dashboard.html", user=current_user(),
                           stats=stats, top_equipment=top_equipment,
                           severity=severity, best=best, ai_status=ai_status())


@app.route("/admin/users")
@admin_required
def admin_users():
    db = get_db()
    rows = db.execute(
        "SELECT u.*, "
        "(SELECT COUNT(*) FROM knowledge k WHERE k.user_id=u.id) kcount, "
        "(SELECT COUNT(*) FROM tasks t WHERE t.user_id=u.id) tcount "
        "FROM users u WHERE u.role='user' ORDER BY u.created_at DESC"
    ).fetchall()
    return render_template("admin_users.html", user=current_user(), users=rows)


@app.route("/admin/tasks", methods=["GET"])
@admin_required
def admin_tasks():
    db = get_db()
    users = db.execute(
        "SELECT id, name, email, job_title FROM users WHERE role='user' ORDER BY name"
    ).fetchall()
    tasks = db.execute(
        "SELECT t.*, u.name uname FROM tasks t JOIN users u ON t.user_id=u.id "
        "ORDER BY t.created_at DESC LIMIT 50"
    ).fetchall()
    return render_template("admin_tasks.html", user=current_user(),
                           users=users, tasks=tasks)


@app.route("/api/admin/assign-task", methods=["POST"])
@admin_required
def api_assign_task():
    data = request.get_json(force=True)
    db = get_db()
    db.execute(
        "INSERT INTO tasks (user_id, title, category, details, assigned_by, created_at, due_date) "
        "VALUES (?,?,?,?,?,?,?)",
        (
            data.get("user_id"),
            data.get("title", ""),
            data.get("category", "Inspection"),
            data.get("details", ""),
            session["user_id"],
            datetime.utcnow().isoformat(),
            data.get("due_date") or None,
        ),
    )
    db.commit()
    return jsonify({"ok": True})


@app.route("/admin/knowledge")
@admin_required
def admin_knowledge():
    db = get_db()
    rows = db.execute(
        "SELECT k.*, u.name author FROM knowledge k LEFT JOIN users u ON k.user_id=u.id "
        "ORDER BY k.created_at DESC"
    ).fetchall()
    items = [_format_knowledge(r, db) for r in rows]
    return render_template("admin_knowledge.html", user=current_user(), items=items)


@app.route("/api/admin/delete-knowledge", methods=["POST"])
@admin_required
def api_delete_knowledge():
    data = request.get_json(force=True)
    db = get_db()
    db.execute("DELETE FROM knowledge WHERE id=?", (data.get("id"),))
    db.commit()
    return jsonify({"ok": True})


# --------------------------------------------------------------------------- #
#  Helpers
# --------------------------------------------------------------------------- #
def _knowledge_to_dict(r):
    return {
        "id": r["id"],
        "equipment_name": r["equipment_name"],
        "problem_type": r["problem_type"],
        "problem_cause": r["problem_cause"],
        "solution_steps": _safe_json(r["solution_steps"]),
        "severity": r["severity"],
        "keywords": _safe_json(r["keywords"]),
        "prevention": (r["prevention"] if "prevention" in r.keys() else "") or "",
    }


def _format_knowledge(r, db):
    d = _knowledge_to_dict(r)
    d["author"] = r["author"] if "author" in r.keys() else None
    d["created_at"] = r["created_at"][:10]
    rating = db.execute(
        "SELECT AVG(score) a, COUNT(*) c FROM ratings WHERE knowledge_id=?", (r["id"],)
    ).fetchone()
    d["avg_rating"] = round(rating["a"], 1) if rating["a"] else 0
    d["rating_count"] = rating["c"]
    return d


def _safe_json(s):
    try:
        return json.loads(s) if s else []
    except (json.JSONDecodeError, TypeError):
        return []


# Make ai_status + i18n helpers available in all templates
@app.context_processor
def inject_globals():
    lang = session.get("lang", DEFAULT_LANG)
    if lang not in LANGUAGES:
        lang = DEFAULT_LANG
    return {
        "AI_READY": ai_status()["ready"],
        "LANG": lang,
        "DIR": "rtl" if lang == "ar" else "ltr",
        "t": lambda key: translate(key, lang),
    }


if __name__ == "__main__":
    init_db()
    print("=" * 60)
    print("  FactoryBrain running at http://127.0.0.1:5000")
    print(f"  Admin login : {Config.ADMIN_EMAIL} / {Config.ADMIN_PASSWORD}")
    print(f"  AI (GPT-4o) : {'ENABLED' if ai_status()['ready'] else 'NOT CONFIGURED (set OPENAI_API_KEY in .env)'}")
    print("=" * 60)
    app.run(debug=True, port=5000)
