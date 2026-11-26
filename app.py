import os
import re
import sqlite3
import time
import datetime as dt
from collections import defaultdict
from functools import wraps

from flask import Flask, Response, g, jsonify, request, session
from groq import Groq
from werkzeug.security import check_password_hash, generate_password_hash

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY") or os.urandom(32)
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=bool(os.environ.get("RENDER")),
    PERMANENT_SESSION_LIFETIME=dt.timedelta(days=30),
    MAX_CONTENT_LENGTH=64 * 1024,
)

DB_PATH = os.environ.get("DB_PATH", "hoppy.db")
TXT = "text/plain; charset=utf-8"
USERNAME_RE = re.compile(r"^[A-Za-z0-9_]{3,20}$")
CLEAN_RE = re.compile(r"\[(?:mood|score):[^\]]*\]")

PREFERRED = [
    "llama-3.3-70b-versatile",
    "openai/gpt-oss-120b",
    "openai/gpt-oss-20b",
    "llama-3.1-8b-instant",
    "qwen/qwen3-32b",
]

MODES = {
    "teacher": "Rejim: O'QITUVCHI. Darajani aniqla, bosqichma-bosqich tushuntir, har javobda bitta mashq ber.",
    "conversation": "Rejim: SUHBAT. Faqat inglizcha 1-3 gap bilan suhbatlash, oxirida savol ber. Xatolarni qisqa o'zbekcha izoh bilan tuzat.",
    "strict": "Rejim: QAT'IY MASHQ. Bitta mashq ber, javobni aniq tekshir, har bir xatoni ko'rsat.",
    "beginner": "Rejim: BOSHLOVCHI. Juda oddiy so'zlar va gaplar bilan, o'zbekcha tushuntirib o'rgat.",
    "ielts": "Rejim: IELTS COACH. IELTS Writing yoki Speaking savoli ber, javobni IELTS mezonlari bo'yicha qisqa baholа (taxminiy band 0-9).",
    "vocab": "Rejim: LUG'AT TRENER. Yangi so'z kiritib, ma'nosi, o'zbekcha tarjimasi va inglizcha misol ber, keyin so'z bo'yicha savol ber.",
    "grammar": "Rejim: GRAMMATIKA. Bitta grammatik mavzuni o'zbekcha tushuntir, inglizcha misollar va bitta mashq ber.",
}

PERSONA = {
    "friendly": "Uslub: iliq, sabrli va rag'batlantiruvchi. Xatoni ayt, lekin hech qachon kamsitma yoki masxara qilma.",
    "strict": "Uslub: jiddiy va aniq. Xatoni to'liq ko'rsat, lekin hech qachon haqorat qilma yoki masxara qilma.",
}

BASE = """Sen "Hoppy" ismli ingliz tili o'qituvchisisan. Maqsad: foydalanuvchini noldan IELTS darajasigacha o'rgatish.
Qoidalar:
- Oddiy, samimiy o'zbek tilida gapir. Javoblar qisqa bo'lsin (3-6 gap).
- Tushuntirishlar o'zbekcha, gap va misollar inglizcha bo'lsin.
- Hech qachon haqorat, kamsitish, masxara yoki so'kinish qilma.
- Javobning boshida [mood:X] tegi bo'lsin. X: happy, neutral, proud, angry, shout. Xato bo'lsa angry, ko'p xato bo'lsa shout, to'g'ri bo'lsa happy yoki proud.
- Har javob oxirida bitta aniq vazifa yoki savol bo'lsin.
"""

DAILY_CAP = {"chat": 10, "review": 30}
XP = {"chat": 5, "review": 2}

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
  id INTEGER PRIMARY KEY,
  username TEXT UNIQUE NOT NULL,
  password_hash TEXT NOT NULL,
  created TEXT NOT NULL,
  xp INTEGER NOT NULL DEFAULT 0,
  streak INTEGER NOT NULL DEFAULT 0,
  last_day TEXT,
  goal INTEGER NOT NULL DEFAULT 10,
  persona TEXT NOT NULL DEFAULT 'friendly'
);
CREATE TABLE IF NOT EXISTS messages (
  id INTEGER PRIMARY KEY,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  mode TEXT NOT NULL,
  role TEXT NOT NULL,
  content TEXT NOT NULL,
  created TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages ON messages(user_id, mode, id);
CREATE TABLE IF NOT EXISTS words (
  id INTEGER PRIMARY KEY,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  word TEXT NOT NULL,
  uz TEXT NOT NULL DEFAULT '',
  example TEXT NOT NULL DEFAULT '',
  ease REAL NOT NULL DEFAULT 2.5,
  interval INTEGER NOT NULL DEFAULT 0,
  reps INTEGER NOT NULL DEFAULT 0,
  due TEXT NOT NULL,
  UNIQUE(user_id, word)
);
CREATE INDEX IF NOT EXISTS idx_words_due ON words(user_id, due);
CREATE TABLE IF NOT EXISTS xp_log (
  id INTEGER PRIMARY KEY,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  action TEXT NOT NULL,
  amount INTEGER NOT NULL,
  day TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_xp ON xp_log(user_id, day, action);
"""

_hits = defaultdict(list)
_model_cache = {"t": 0.0, "v": []}


# ---------- Baza ----------
def connect():
    c = sqlite3.connect(DB_PATH, timeout=10)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    return c


def init_db():
    c = connect()
    try:
        c.executescript(SCHEMA)
        c.commit()
    finally:
        c.close()


init_db()


def q():
    """So'rov uchun so'rov davomidagi ulanish (faqat o'qish)."""
    if "db" not in g:
        g.db = connect()
    return g.db


@app.teardown_appcontext
def _close_db(_exc):
    d = g.pop("db", None)
    if d is not None:
        d.close()


def exec_write(sql, params=()):
    c = connect()
    try:
        cur = c.execute(sql, params)
        c.commit()
        return cur.lastrowid
    finally:
        c.close()


def today():
    return dt.date.today().isoformat()


def now_iso():
    return dt.datetime.now().isoformat(timespec="seconds")


def err(msg, code=400):
    return jsonify(error=msg), code


def rate_ok(key, limit=10, window=300):
    now = time.time()
    _hits[key] = [t for t in _hits[key] if now - t < window]
    if len(_hits[key]) >= limit:
        return False
    _hits[key].append(now)
    return True


def login_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not session.get("uid"):
            return err("Avval tizimga kiring", 401)
        return f(*args, **kwargs)
    return wrapper


# ---------- XP va streak ----------
def touch_streak(uid):
    row = q().execute("SELECT streak, last_day FROM users WHERE id=?", (uid,)).fetchone()
    t = today()
    if row["last_day"] == t:
        return
    yesterday = (dt.date.today() - dt.timedelta(days=1)).isoformat()
    streak = row["streak"] + 1 if row["last_day"] == yesterday else 1
    exec_write("UPDATE users SET streak=?, last_day=? WHERE id=?", (streak, t, uid))


def award(uid, action):
    """Haqiqiy harakat uchun XP. Kunlik limitdan oshsa 0 qaytaradi."""
    touch_streak(uid)
    used = q().execute(
        "SELECT COUNT(*) FROM xp_log WHERE user_id=? AND action=? AND day=?",
        (uid, action, today()),
    ).fetchone()[0]
    if used >= DAILY_CAP[action]:
        return 0
    exec_write("INSERT INTO xp_log(user_id, action, amount, day) VALUES(?,?,?,?)",
               (uid, action, XP[action], today()))
    exec_write("UPDATE users SET xp = xp + ? WHERE id=?", (XP[action], uid))
    return XP[action]


def user_public(uid):
    r = q().execute("SELECT username, xp, streak, goal, persona FROM users WHERE id=?", (uid,)).fetchone()
    return {
        "username": r["username"], "xp": r["xp"], "level": r["xp"] // 100 + 1,
        "streak": r["streak"], "goal": r["goal"], "persona": r["persona"],
    }


# ---------- Groq ----------
def groq_client():
    key = os.environ.get("GROQ_API_KEY")
    return Groq(api_key=key) if key else None


def available_models(client):
    if time.time() - _model_cache["t"] < 600 and _model_cache["v"]:
        return _model_cache["v"]
    try:
        live = {m.id for m in client.models.list().data}
        found = [m for m in PREFERRED if m in live] or sorted(live)
    except Exception:
        found = PREFERRED[:]
    _model_cache.update(t=time.time(), v=found)
    return found


# ---------- Sahifa ----------
@app.route("/")
def index():
    return HTML


# ---------- Auth ----------
@app.post("/api/register")
def register():
    d = request.get_json(silent=True) or {}
    username = (d.get("username") or "").strip()
    password = d.get("password") or ""
    if not USERNAME_RE.match(username):
        return err("Login: 3-20 ta harf, raqam yoki _ belgisi bo'lsin")
    if not 6 <= len(password) <= 128:
        return err("Parol 6 dan 128 belgigacha bo'lsin")
    if not rate_ok(f"reg:{request.remote_addr}", 10, 600):
        return err("Juda ko'p urinish. Keyinroq urinib ko'ring", 429)
    try:
        uid = exec_write(
            "INSERT INTO users(username, password_hash, created) VALUES(?,?,?)",
            (username, generate_password_hash(password), now_iso()),
        )
    except sqlite3.IntegrityError:
        return err("Bu login band", 409)
    session.clear()
    session.permanent = True
    session["uid"] = uid
    return jsonify(user=user_public(uid))


@app.post("/api/login")
def login():
    d = request.get_json(silent=True) or {}
    username = (d.get("username") or "").strip()
    password = d.get("password") or ""
    if not rate_ok(f"login:{request.remote_addr}", 10, 300):
        return err("Juda ko'p urinish. 5 daqiqadan keyin urinib ko'ring", 429)
    row = q().execute("SELECT id, password_hash FROM users WHERE username=?", (username,)).fetchone()
    if row is None or not check_password_hash(row["password_hash"], password):
        return err("Login yoki parol noto'g'ri", 403)
    session.clear()
    session.permanent = True
    session["uid"] = row["id"]
    return jsonify(user=user_public(row["id"]))


@app.post("/api/logout")
def logout():
    session.clear()
    return jsonify(ok=True)


@app.get("/api/me")
def me():
    uid = session.get("uid")
    return jsonify(user=user_public(uid) if uid else None)


# ---------- Tutor ----------
@app.get("/api/models")
@login_required
def models():
    client = groq_client()
    if client is None:
        return jsonify(models=[], error="GROQ_API_KEY sozlanmagan")
    return jsonify(models=available_models(client))


@app.get("/api/history")
@login_required
def history():
    mode = request.args.get("mode")
    if mode not in MODES:
        return err("Noto'g'ri rejim")
    rows = q().execute(
        "SELECT role, content FROM messages WHERE user_id=? AND mode=? ORDER BY id DESC LIMIT 100",
        (session["uid"], mode),
    ).fetchall()
    return jsonify(messages=[{"role": r["role"], "content": r["content"]} for r in reversed(rows)])


@app.post("/api/chat")
@login_required
def chat():
    uid = session["uid"]
    d = request.get_json(silent=True) or {}
    text = (d.get("message") or "").strip()[:2000]
    mode = d.get("mode") if d.get("mode") in MODES else "teacher"
    if not text:
        return err("Xabar bo'sh")

    client = groq_client()
    if client is None:
        return Response("[mood:angry] Server sozlanmagan: GROQ_API_KEY yo'q.", mimetype=TXT)

    exec_write(
        "INSERT INTO messages(user_id, mode, role, content, created) VALUES(?,?,?,?,?)",
        (uid, mode, "user", text, now_iso()),
    )
    gained = award(uid, "chat")

    rows = q().execute(
        "SELECT role, content FROM messages WHERE user_id=? AND mode=? ORDER BY id DESC LIMIT 20",
        (uid, mode),
    ).fetchall()
    history_msgs = [{"role": r["role"], "content": r["content"]} for r in reversed(rows)]

    hard = [r["word"] for r in q().execute(
        "SELECT word FROM words WHERE user_id=? AND reps=0 AND ease<2.5 ORDER BY due LIMIT 8", (uid,)
    )]
    user = q().execute("SELECT username, persona FROM users WHERE id=?", (uid,)).fetchone()

    system = BASE + PERSONA.get(user["persona"], PERSONA["friendly"]) + "\n" + MODES[mode]
    system += f"\nFoydalanuvchi ismi: {user['username']}."
    if hard:
        system += "\nFoydalanuvchi qiynalayotgan so'zlar (ularni ataylab takrorlat): " + ", ".join(hard) + "."
    messages = [{"role": "system", "content": system}] + history_msgs

    models_list = available_models(client)
    req = d.get("model")
    if req in models_list:
        models_list = [req] + [m for m in models_list if m != req]

    def generate():
        full, started, last_err = "", False, None
        for name in models_list:
            try:
                for ch in client.chat.completions.create(
                    model=name, messages=messages, max_tokens=600, stream=True
                ):
                    part = ch.choices[0].delta.content
                    if part:
                        started = True
                        full += part
                        yield part
                break
            except Exception as e:
                last_err = e
                if started:
                    break
        if full:
            clean = CLEAN_RE.sub("", full).strip()
            exec_write(
                "INSERT INTO messages(user_id, mode, role, content, created) VALUES(?,?,?,?,?)",
                (uid, mode, "assistant", clean, now_iso()),
            )
        else:
            app.logger.error("Groq xatosi: %s", last_err)
            yield "[mood:angry] Hozir javob bera olmayman. Birozdan keyin qayta urinib ko'ring."

    return Response(
        generate(), mimetype=TXT,
        headers={"X-Xp": str(gained), "X-Accel-Buffering": "no", "Cache-Control": "no-cache"},
    )


# ---------- So'z daftari (SM-2) ----------
@app.get("/api/words")
@login_required
def words_list():
    rows = q().execute(
        "SELECT id, word, uz, example, interval, reps, due FROM words WHERE user_id=? ORDER BY due, word LIMIT 500",
        (session["uid"],),
    ).fetchall()
    return jsonify(words=[dict(r) for r in rows], today=today())


@app.post("/api/words")
@login_required
def words_add():
    d = request.get_json(silent=True) or {}
    word = (d.get("word") or "").strip().lower()[:60]
    uz = (d.get("uz") or "").strip()[:200]
    example = (d.get("example") or "").strip()[:300]
    if not word:
        return err("So'zni kiriting")
    try:
        exec_write(
            "INSERT INTO words(user_id, word, uz, example, due) VALUES(?,?,?,?,?)",
            (session["uid"], word, uz, example, today()),
        )
    except sqlite3.IntegrityError:
        return err("Bu so'z ro'yxatda bor", 409)
    return jsonify(ok=True)


@app.get("/api/words/due")
@login_required
def words_due():
    rows = q().execute(
        "SELECT id, word, uz, example FROM words WHERE user_id=? AND due<=? ORDER BY due, id LIMIT 30",
        (session["uid"], today()),
    ).fetchall()
    return jsonify(words=[dict(r) for r in rows])


@app.post("/api/words/<int:wid>/review")
@login_required
def words_review(wid):
    d = request.get_json(silent=True) or {}
    try:
        grade = int(d.get("grade"))
    except (TypeError, ValueError):
        return err("Baho noto'g'ri")
    if not 0 <= grade <= 5:
        return err("Baho 0 dan 5 gacha bo'lishi kerak")

    uid = session["uid"]
    row = q().execute(
        "SELECT ease, interval, reps FROM words WHERE id=? AND user_id=?", (wid, uid)
    ).fetchone()
    if row is None:
        return err("Topilmadi", 404)

    # SM-2 algoritmi
    ease = max(1.3, row["ease"] + (0.1 - (5 - grade) * (0.08 + (5 - grade) * 0.02)))
    interval, reps = row["interval"], row["reps"]
    if grade < 3:
        reps, interval = 0, 1
    else:
        reps += 1
        if reps == 1:
            interval = 1
        elif reps == 2:
            interval = 6
        else:
            interval = max(1, round(interval * ease))
    due = (dt.date.today() + dt.timedelta(days=interval)).isoformat()

    exec_write(
        "UPDATE words SET ease=?, interval=?, reps=?, due=? WHERE id=? AND user_id=?",
        (ease, interval, reps, due, wid, uid),
    )
    gained = award(uid, "review")
    return jsonify(interval=interval, due=due, xp=gained)


# ---------- Statistika va sozlamalar ----------
@app.get("/api/stats")
@login_required
def stats():
    uid = session["uid"]
    u = q().execute("SELECT xp, streak, goal, persona FROM users WHERE id=?", (uid,)).fetchone()
    done_today = q().execute(
        "SELECT COUNT(*) FROM xp_log WHERE user_id=? AND day=?", (uid, today())
    ).fetchone()[0]
    w = q().execute(
        "SELECT COUNT(*) AS total, SUM(interval>=21) AS mastered, SUM(due<=?) AS due "
        "FROM words WHERE user_id=?", (today(), uid)
    ).fetchone()
    msgs = q().execute(
        "SELECT COUNT(*) FROM messages WHERE user_id=? AND role='user'", (uid,)
    ).fetchone()[0]
    since = (dt.date.today() - dt.timedelta(days=6)).isoformat()
    weekly = q().execute(
        "SELECT day, SUM(amount) AS xp FROM xp_log WHERE user_id=? AND day>=? GROUP BY day",
        (uid, since),
    ).fetchall()
    return jsonify(
        xp=u["xp"], level=u["xp"] // 100 + 1, streak=u["streak"], goal=u["goal"],
        persona=u["persona"], today=done_today,
        words=w["total"] or 0, mastered=w["mastered"] or 0, due=w["due"] or 0,
        messages=msgs, weekly=[{"day": r["day"], "xp": r["xp"]} for r in weekly],
    )


@app.post("/api/settings")
@login_required
def settings():
    d = request.get_json(silent=True) or {}
    uid = session["uid"]
    if d.get("goal") is not None:
        try:
            goal = min(100, max(1, int(d["goal"])))
        except (TypeError, ValueError):
            return err("Maqsad noto'g'ri")
        exec_write("UPDATE users SET goal=? WHERE id=?", (goal, uid))
    if d.get("persona") in PERSONA:
        exec_write("UPDATE users SET persona=? WHERE id=?", (d["persona"], uid))
    return jsonify(ok=True)


HTML = r"""<!DOCTYPE html>
<html lang="uz">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Hoppy - Ingliz tili</title>
<style>
:root{--bg:#0b1020;--card:#17213f;--panel:#121a33;--line:#26325a;--txt:#e9ecf8;--mut:#8b96bd;--acc:#7c5cff;--acc2:#38bdf8;--red:#ff4d6d;--ok:#34d399}
*{box-sizing:border-box}
html,body{height:100%}
body{margin:0;font-family:system-ui,-apple-system,"Segoe UI",sans-serif;background:radial-gradient(1000px 500px at 0% -10%,#2a2470 0,transparent 60%),var(--bg);color:var(--txt);overflow:hidden}
.hidden{display:none!important}
button{font:inherit;color:var(--txt);background:var(--card);border:1px solid var(--line);border-radius:10px;padding:9px 14px;cursor:pointer;transition:.2s}
button:hover{border-color:var(--acc);transform:translateY(-1px)}
button.primary{background:linear-gradient(135deg,var(--acc),var(--acc2));border:0;color:#fff}
button.on{border-color:var(--acc);background:#22305e}
button:disabled{opacity:.5;cursor:default;transform:none}
input,textarea,select{font:inherit;color:var(--txt);background:#0e1530;border:1px solid var(--line);border-radius:10px;padding:10px 12px;width:100%}
input:focus,textarea:focus,select:focus{outline:2px solid var(--acc);border-color:transparent}
.glass{background:rgba(23,33,63,.75);backdrop-filter:blur(10px);border:1px solid var(--line);border-radius:16px}
label{font-size:13px;color:var(--mut);display:block;margin-bottom:6px}

#auth{position:fixed;inset:0;display:flex;align-items:center;justify-content:center;padding:16px;z-index:20}
#auth .box{width:min(380px,100%);padding:26px;display:flex;flex-direction:column;gap:12px}
#auth h1{margin:0;font-size:26px;background:linear-gradient(135deg,var(--acc),var(--acc2));-webkit-background-clip:text;color:transparent}
.tabs{display:flex;gap:8px}
.tabs button{flex:1}
.err{color:var(--red);font-size:14px;min-height:18px}

#app{display:flex;height:100vh;width:100%}
aside{width:300px;flex-shrink:0;padding:18px;display:flex;flex-direction:column;gap:14px;overflow-y:auto;background:rgba(18,26,51,.9);border-right:1px solid var(--line)}
#mascot{width:170px;height:170px;margin:0 auto;display:block}
#face{transform-origin:60px 60px}
#face.happy,#face.proud{animation:bounce 1s ease-in-out infinite}
#face.angry{animation:rage .2s infinite}
#face.shout{animation:rage .1s infinite}
@keyframes bounce{0%,100%{transform:translateY(0)}50%{transform:translateY(-8px)}}
@keyframes rage{0%,100%{transform:rotate(0)}25%{transform:rotate(-4deg)}75%{transform:rotate(4deg)}}
#pill{transition:fill .2s,stroke .2s}
#face.angry #pill,#face.shout #pill{fill:#ffe1e1;stroke:#ff1a1a}
#face.angry #brows,#face.shout #brows{opacity:1}
#brows{opacity:0;transition:opacity .15s}
#face.talking #pill{animation:pulse .25s infinite alternate}
@keyframes pulse{from{transform:scale(1)}to{transform:scale(1.04)}}
.eye{transform-box:fill-box;transform-origin:center;animation:blink 3.5s infinite}
@keyframes blink{0%,94%,100%{transform:scaleY(1)}97%{transform:scaleY(.1)}}
#face.angry .eye,#face.shout .eye{animation:none;transform:scaleY(.5)}
#moodTxt{font-size:15px;font-weight:600;color:var(--mut);text-align:center;min-height:22px}
#moodTxt.red{color:var(--red)}
.userbox{display:flex;justify-content:space-between;align-items:center;gap:8px}
.userbox b{font-size:16px}
.statgrid{display:grid;grid-template-columns:1fr 1fr 1fr;gap:8px}
.stat{padding:10px 6px;text-align:center}
.stat .v{font-size:20px;font-weight:700;background:linear-gradient(135deg,var(--acc),var(--acc2));-webkit-background-clip:text;color:transparent}
.stat .k{font-size:11px;color:var(--mut)}
.goalbar{height:10px;background:#0e1530;border-radius:8px;overflow:hidden}
#goalFill{height:100%;width:0;background:linear-gradient(90deg,var(--acc),var(--acc2));transition:width .6s}
.small{font-size:13px;color:var(--mut)}
.row{display:flex;gap:8px;align-items:center;flex-wrap:wrap}

#main{flex:1;display:flex;flex-direction:column;min-width:0}
header{display:flex;align-items:center;gap:10px;padding:12px 18px;border-bottom:1px solid var(--line);background:rgba(18,26,51,.7);backdrop-filter:blur(8px);flex-wrap:wrap}
header .brand{font-weight:800;font-size:20px;margin-right:auto;background:linear-gradient(135deg,var(--acc),var(--acc2));-webkit-background-clip:text;color:transparent}
#menuBtn{display:none}
.view{flex:1;display:flex;flex-direction:column;min-height:0;padding:16px;gap:12px;overflow:hidden}
.view.scroll{overflow-y:auto}
#modes{display:flex;gap:8px;overflow-x:auto;padding-bottom:4px}
#modes button{white-space:nowrap}
#chat{flex:1;overflow-y:auto;padding:16px;display:flex;flex-direction:column;gap:12px;min-height:0}
.msg{max-width:80%;padding:11px 15px;border-radius:14px;white-space:pre-wrap;line-height:1.55;word-wrap:break-word;animation:in .25s ease}
@keyframes in{from{opacity:0;transform:translateY(6px)}to{opacity:1;transform:none}}
.msg.user{align-self:flex-end;background:linear-gradient(135deg,var(--acc),#5b7cff);color:#fff}
.msg.bot{align-self:flex-start;background:#1c2750}
.msg.bot.angry{border:1px solid var(--red)}
#composer{display:flex;gap:8px;align-items:flex-end}
#inp{resize:none;min-height:44px;max-height:140px}
#mic.rec{background:#7a1f35;animation:pulse .4s infinite alternate}
#send{padding:0 22px;min-height:44px}
.toast{position:fixed;bottom:22px;left:50%;transform:translateX(-50%);background:var(--card);border:1px solid var(--acc);padding:10px 18px;border-radius:12px;z-index:50;animation:in .25s ease}
.toast.bad{border-color:var(--red)}
#flash{position:fixed;inset:0;background:radial-gradient(circle,rgba(255,40,70,.4),transparent 70%);pointer-events:none;opacity:0;z-index:40}
#flash.go{animation:flash .6s ease-out}
@keyframes flash{0%{opacity:1}100%{opacity:0}}
#stage.shake{animation:quake .4s}
@keyframes quake{0%,100%{transform:translate(0)}25%{transform:translate(-6px,3px)}50%{transform:translate(6px,-3px)}75%{transform:translate(-3px,2px)}}

.card{padding:22px;display:flex;flex-direction:column;gap:14px;align-items:center;text-align:center;min-height:220px;justify-content:center}
.card .word{font-size:34px;font-weight:800}
.card .info{display:flex;flex-direction:column;gap:6px}
.card .ex{color:var(--mut);font-style:italic}
.grades{display:flex;gap:8px;flex-wrap:wrap;justify-content:center}
.empty{color:var(--mut);padding:30px;text-align:center}
.formrow{display:grid;grid-template-columns:1fr 1fr 2fr auto;gap:8px}
.wordlist{display:flex;flex-direction:column;gap:6px;overflow-y:auto}
.wrow{display:flex;justify-content:space-between;gap:10px;padding:10px 12px;border-radius:10px;background:#0e1530;border:1px solid var(--line)}
.wrow .w{font-weight:600}
.wrow .m{color:var(--mut);font-size:13px}
.weekbox{display:flex;align-items:flex-end;gap:8px;height:160px;padding:10px}
.weekbox .bar{flex:1;border-radius:8px 8px 4px 4px;background:linear-gradient(180deg,var(--acc2),var(--acc));min-height:4px;transition:height .6s}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px}
.kpis .glass{padding:16px}
.kpis .v{font-size:26px;font-weight:800}

@keyframes pulse2{from{opacity:1}to{opacity:.6}}

@media (max-width:860px){
  #menuBtn{display:inline-block}
  aside{position:fixed;left:0;top:0;bottom:0;z-index:30;transform:translateX(-100%);transition:transform .25s}
  aside.open{transform:none}
  .formrow{grid-template-columns:1fr}
  .msg{max-width:92%}
}
</style>
</head>
<body>
<div id="flash"></div>

<div id="auth" class="hidden">
  <div class="glass box">
    <h1>Hoppy</h1>
    <div class="small">Ingliz tilini 0 dan IELTS gacha o'rganing.</div>
    <div class="tabs">
      <button id="tLogin" class="on">Kirish</button>
      <button id="tReg">Ro'yxatdan o'tish</button>
    </div>
    <form id="authForm" class="row" style="flex-direction:column;align-items:stretch;gap:10px">
      <div><label for="aU">Login</label><input id="aU" autocomplete="username" required minlength="3" maxlength="20"></div>
      <div><label for="aP">Parol</label><input id="aP" type="password" autocomplete="current-password" required minlength="6" maxlength="128"></div>
      <div class="err" id="authErr"></div>
      <button class="primary" id="authBtn" type="submit">Kirish</button>
    </form>
  </div>
</div>

<div id="app" class="hidden">
  <div id="stage" style="display:flex;width:100%;height:100%">
    <aside id="side">
      <svg id="mascot" viewBox="0 0 120 120">
        <g id="face" class="neutral">
          <rect id="pill" x="12" y="26" width="96" height="68" rx="34" fill="#fff" stroke="#ff5a4e" stroke-width="6"/>
          <ellipse class="eye" cx="42" cy="54" rx="7" ry="9" fill="#111"/>
          <ellipse class="eye" cx="78" cy="54" rx="7" ry="9" fill="#111"/>
          <g id="brows" stroke="#111" stroke-width="5" stroke-linecap="round">
            <line x1="30" y1="38" x2="50" y2="48"/>
            <line x1="90" y1="38" x2="70" y2="48"/>
          </g>
          <path id="mouth" d="M46 70 L74 70" fill="none" stroke="#111" stroke-width="5" stroke-linecap="round"/>
        </g>
      </svg>
      <div id="moodTxt">Tayyor, qachon tayyor bo'lsang</div>

      <div class="userbox">
        <div><div class="small">Foydalanuvchi</div><b id="who"></b></div>
        <button id="logoutBtn">Chiqish</button>
      </div>

      <div class="statgrid">
        <div class="glass stat"><div class="v" id="lvl">1</div><div class="k">Daraja</div></div>
        <div class="glass stat"><div class="v" id="xp">0</div><div class="k">XP</div></div>
        <div class="glass stat"><div class="v" id="streak">0</div><div class="k">Streak</div></div>
      </div>

      <div>
        <div class="small" id="goalTxt">Bugun: 0 / 10 faollik</div>
        <div class="goalbar"><div id="goalFill"></div></div>
      </div>

      <div class="glass" style="padding:12px;display:flex;flex-direction:column;gap:10px">
        <div><label for="persona">Tutor uslubi</label>
          <select id="persona"><option value="friendly">Do'stona</option><option value="strict">Qat'iy</option></select></div>
        <div><label for="goalIn">Kunlik maqsad (faollik)</label>
          <input id="goalIn" type="number" min="1" max="100" value="10"></div>
        <button id="saveSettings">Sozlamalarni saqlash</button>
        <button id="speakBtn">🔊 Ovoz: yoqilgan</button>
        <button id="fxBtn">✨ Effektlar: yoqilgan</button>
      </div>
    </aside>

    <div id="main">
      <header>
        <button id="menuBtn" aria-label="Menyu">☰</button>
        <div class="brand">Hoppy</div>
        <button class="nav on" data-view="tutor">🧠 Tutor</button>
        <button class="nav" data-view="words">📚 So'z daftari</button>
        <button class="nav" data-view="stats">📊 Statistika</button>
      </header>

      <section class="view" id="v-tutor">
        <div id="modes"></div>
        <div class="row">
          <select id="model" style="max-width:260px"></select>
          <button id="clearChat">Suhbatni tozalash</button>
        </div>
        <div id="chat" class="glass"></div>
        <form id="composer">
          <button id="mic" type="button" title="Gapirish">🎤</button>
          <textarea id="inp" rows="1" placeholder="Yozing yoki 🎤 bosib gapiring (Enter - yuborish)"></textarea>
          <button id="send" class="primary" type="submit">Yuborish</button>
        </form>
      </section>

      <section class="view scroll hidden" id="v-words">
        <div class="glass" style="padding:16px">
          <form id="wordForm" class="formrow">
            <input id="wWord" placeholder="So'z (masalan: achieve)" maxlength="60" required>
            <input id="wUz" placeholder="O'zbekcha tarjima" maxlength="200">
            <input id="wEx" placeholder="Misol gap" maxlength="300">
            <button class="primary" type="submit">Qo'shish</button>
          </form>
        </div>
        <div>
          <h3 style="margin:6px 0">Bugungi takrorlash</h3>
          <div class="glass card" id="card"></div>
        </div>
        <div>
          <h3 style="margin:6px 0">Barcha so'zlar</h3>
          <div class="wordlist" id="wordList"></div>
        </div>
      </section>

      <section class="view scroll hidden" id="v-stats">
        <div class="kpis">
          <div class="glass"><div class="small">So'zlar</div><div class="v" id="stWords">0</div></div>
          <div class="glass"><div class="small">O'zlashtirilgan</div><div class="v" id="stMastered">0</div></div>
          <div class="glass"><div class="small">Bugun takrorlanadi</div><div class="v" id="stDue">0</div></div>
          <div class="glass"><div class="small">Yuborilgan xabarlar</div><div class="v" id="stMsgs">0</div></div>
        </div>
        <div class="glass" style="padding:16px">
          <h3 style="margin-top:0">So'nggi 7 kun (XP)</h3>
          <div class="weekbox" id="week"></div>
        </div>
        <div class="small">XP faqat haqiqiy harakatlar uchun beriladi: tutor bilan xabar almashish (kuniga 10 marta) va so'z takrorlash (kuniga 30 marta).</div>
      </section>
    </div>
  </div>
</div>

<script>
const $ = id => document.getElementById(id);
function el(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text !== undefined) e.textContent = text;
  return e;
}

const MOUTH = {
  happy: "M42 66 Q60 84 78 66", proud: "M42 66 Q60 88 78 66", neutral: "M46 70 L74 70",
  angry: "M42 80 Q60 62 78 80", shout: "M40 68 Q60 96 80 68 Q60 74 40 68",
  talk1: "M44 64 Q60 96 76 64", talk2: "M46 68 Q60 74 74 68"
};
const LABEL = {
  happy: "Zo'r! 😊", proud: "Hm, to'g'ri ekan 🌟", neutral: "Tayyor, qachon tayyor bo'lsang",
  angry: "Yana xato... 😠", shout: "Diqqat! Qayta ko'ring!", talking: "Gapiryapman... 💬"
};
const MODE_LABELS = {
  teacher: "O'qituvchi", conversation: "Suhbat", strict: "Qat'iy mashq", beginner: "Boshlovchi",
  ielts: "IELTS coach", vocab: "Lug'at trener", grammar: "Grammatika"
};
const INTRO = {
  teacher: "Salom! Men Hoppy. Ingliz tilini qayerdan boshlaysiz? Darajangizni aytsangiz, shunga qarab o'rgataman.",
  conversation: "Suhbat rejimi. Inglizcha yozing, men javob beraman va xatolaringizni tuzataman.",
  strict: "Qat'iy mashq. 'Mashq ber' deb yozing, javoblaringizni aniq tekshiraman.",
  beginner: "Boshlovchi rejim. Eng oddiy so'zlardan boshlaymiz. Tayyor bo'lsangiz 'boshlaymiz' deb yozing.",
  ielts: "IELTS coach. 'Writing Task 2 savol ber' yoki 'Speaking part 1' deb yozing.",
  vocab: "Lug'at trener. Yangi so'z so'rang yoki o'zingiz so'z kiriting, men uni mustahkamlataman.",
  grammar: "Grammatika. Qaysi mavzu kerak? Masalan: 'present perfect' deb yozing."
};

let user = null, mode = localStorage.getItem('hoppy_mode') || 'teacher';
let fx = localStorage.getItem('hoppy_fx') !== '0', speakOn = localStorage.getItem('hoppy_speak') !== '0';
let busy = false, curMood = 'neutral', talkTimer = null, actx = null, dueQ = [];
let authMode = 'login';
if (!MODE_LABELS[mode]) mode = 'teacher';

// ---------- API ----------
async function api(path, body) {
  const r = await fetch(path, {
    method: body === undefined ? 'GET' : 'POST',
    headers: {'Content-Type': 'application/json'},
    body: body === undefined ? undefined : JSON.stringify(body)
  });
  let j = {};
  try { j = await r.json(); } catch (e) {}
  if (r.status === 401) { user = null; showAuth(); throw new Error('login'); }
  if (!r.ok) throw new Error(j.error || 'Xato yuz berdi');
  return j;
}

function toast(msg, bad) {
  const t = el('div', 'toast' + (bad ? ' bad' : ''), msg);
  document.body.appendChild(t);
  setTimeout(() => t.remove(), 2600);
}

// ---------- Ovoz va effektlar ----------
function beep(freq, dur, type, vol) {
  if (!fx) return;
  try {
    actx = actx || new (window.AudioContext || window.webkitAudioContext)();
    const o = actx.createOscillator(), g = actx.createGain();
    o.type = type; o.frequency.value = freq;
    g.gain.setValueAtTime(vol, actx.currentTime);
    g.gain.exponentialRampToValueAtTime(0.001, actx.currentTime + dur);
    o.connect(g).connect(actx.destination);
    o.start(); o.stop(actx.currentTime + dur);
  } catch (e) {}
}

function say(text) {
  if (!speakOn || !('speechSynthesis' in window)) return;
  const u = new SpeechSynthesisUtterance(text.replace(/[*#`_]/g, ''));
  u.lang = 'en-US'; u.rate = 1;
  u.onstart = startTalk; u.onend = stopTalk; u.onerror = stopTalk;
  speechSynthesis.cancel(); speechSynthesis.speak(u);
}

function startTalk() {
  $('face').setAttribute('class', 'talking');
  $('moodTxt').textContent = LABEL.talking; $('moodTxt').className = '';
  let t = 0;
  clearInterval(talkTimer);
  talkTimer = setInterval(() => $('mouth').setAttribute('d', (t++ % 2) ? MOUTH.talk1 : MOUTH.talk2), 140);
}
function stopTalk() { clearInterval(talkTimer); talkTimer = null; setMood(curMood); }

function setMood(m) {
  curMood = m;
  $('face').setAttribute('class', m);
  $('moodTxt').textContent = LABEL[m] || LABEL.neutral;
  $('moodTxt').className = (m === 'angry' || m === 'shout') ? 'red' : '';
  if (!talkTimer) $('mouth').setAttribute('d', MOUTH[m] || MOUTH.neutral);
  if (m === 'angry' || m === 'shout') {
    if (fx) {
      const st = $('stage'); st.classList.remove('shake'); void st.offsetWidth; st.classList.add('shake');
      if (m === 'shout') { const f = $('flash'); f.classList.remove('go'); void f.offsetWidth; f.classList.add('go'); }
      beep(m === 'shout' ? 55 : 70, 0.5, 'square', 0.15);
    }
  }
}

// ---------- Auth ----------
function showAuth() { $('auth').classList.remove('hidden'); $('app').classList.add('hidden'); }

function setAuthMode(m) {
  authMode = m;
  $('tLogin').classList.toggle('on', m === 'login');
  $('tReg').classList.toggle('on', m === 'register');
  $('authBtn').textContent = m === 'login' ? 'Kirish' : "Ro'yxatdan o'tish";
  $('aP').autocomplete = m === 'login' ? 'current-password' : 'new-password';
  $('authErr').textContent = '';
}

async function submitAuth(e) {
  e.preventDefault();
  $('authErr').textContent = '';
  $('authBtn').disabled = true;
  try {
    const j = await api(authMode === 'login' ? '/api/login' : '/api/register',
      {username: $('aU').value.trim(), password: $('aP').value});
    $('aP').value = '';
    enter(j.user);
  } catch (err) {
    $('authErr').textContent = err.message === 'login' ? '' : err.message;
  } finally {
    $('authBtn').disabled = false;
  }
}

async function logout() {
  try { await api('/api/logout', {}); } catch (e) {}
  user = null;
  $('chat').innerHTML = '';
  showAuth();
}

function enter(u) {
  user = u;
  $('who').textContent = u.username;
  $('auth').classList.add('hidden');
  $('app').classList.remove('hidden');
  loadAll();
}

async function loadAll() {
  try {
    await Promise.all([loadModels(), loadStats(), loadHistory(), loadDue(), loadWords()]);
  } catch (e) {
    if (e.message !== 'login') toast(e.message, true);
  }
}

// ---------- Tutor ----------
function renderModes() {
  const box = $('modes');
  box.innerHTML = '';
  Object.keys(MODE_LABELS).forEach(k => {
    const b = el('button', mode === k ? 'on' : '', MODE_LABELS[k]);
    b.onclick = () => { mode = k; localStorage.setItem('hoppy_mode', k); renderModes(); loadHistory(); };
    box.appendChild(b);
  });
}

function addBubble(role, text, angry) {
  const d = el('div', 'msg ' + (role === 'user' ? 'user' : 'bot') + (angry ? ' angry' : ''), text);
  $('chat').appendChild(d);
  return d;
}

function scrollChat() { const c = $('chat'); c.scrollTop = c.scrollHeight; }

async function loadHistory() {
  renderModes();
  const j = await api('/api/history?mode=' + encodeURIComponent(mode));
  $('chat').innerHTML = '';
  if (!j.messages.length) addBubble('bot', INTRO[mode]);
  j.messages.forEach(m => addBubble(m.role === 'user' ? 'user' : 'bot', m.content));
  scrollChat();
}

async function loadModels() {
  const sel = $('model');
  const j = await api('/api/models');
  sel.innerHTML = '';
  (j.models || []).forEach(m => sel.appendChild(new Option(m, m)));
  if (!sel.options.length) sel.appendChild(new Option(j.error || 'Model topilmadi', ''));
}

function parseTags(full) {
  const mood = (full.match(/\[mood:(\w+)\]/) || [])[1] || null;
  const clean = full.replace(/\[(?:mood|score):[^\]]*\]/g, '').trim();
  return {mood, clean};
}

async function sendChat() {
  const inp = $('inp');
  const text = inp.value.trim();
  if (!text || busy) return;
  busy = true; $('send').disabled = true;
  inp.value = '';
  addBubble('user', text);
  const bot = addBubble('bot', '...');
  scrollChat();
  setMood('neutral');

  let full = '';
  try {
    const r = await fetch('/api/chat', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({message: text, mode, model: $('model').value})
    });
    if (r.status === 401) { user = null; showAuth(); throw new Error('login'); }
    if (!r.ok) {
      let j = {}; try { j = await r.json(); } catch (e) {}
      throw new Error(j.error || 'Xato yuz berdi');
    }
    const gained = +(r.headers.get('X-Xp') || 0);
    if (gained) toast(`+${gained} XP`);
    const reader = r.body.getReader();
    const dec = new TextDecoder();
    while (true) {
      const {value, done} = await reader.read();
      if (done) break;
      full += dec.decode(value, {stream: true});
      bot.textContent = parseTags(full).clean;
      scrollChat();
    }
  } catch (err) {
    if (err.message !== 'login') { full = '[mood:angry] Xato: ' + err.message; }
  }

  const {mood, clean} = parseTags(full);
  bot.textContent = clean || '...';
  if (mood === 'angry' || mood === 'shout') bot.classList.add('angry');
  setMood(mood || 'neutral');
  say(clean);
  busy = false; $('send').disabled = false;
  loadStats().catch(() => {});
  inp.focus();
}

// ---------- Statistika ----------
function lastDays(n) {
  const out = [];
  for (let i = n - 1; i >= 0; i--) {
    const d = new Date(); d.setDate(d.getDate() - i);
    out.push(d.toISOString().slice(0, 10));
  }
  return out;
}

async function loadStats() {
  const s = await api('/api/stats');
  $('lvl').textContent = s.level;
  $('xp').textContent = s.xp;
  $('streak').textContent = s.streak;
  const pct = Math.min(100, Math.round(s.today / s.goal * 100));
  $('goalFill').style.width = pct + '%';
  $('goalTxt').textContent = `Bugun: ${s.today} / ${s.goal} faollik`;
  $('stWords').textContent = s.words;
  $('stMastered').textContent = s.mastered;
  $('stDue').textContent = s.due;
  $('stMsgs').textContent = s.messages;
  $('goalIn').value = s.goal;
  $('persona').value = s.persona;

  const map = Object.fromEntries(s.weekly.map(w => [w.day, w.xp]));
  const max = Math.max(10, ...Object.values(map));
  const wk = $('week');
  wk.innerHTML = '';
  lastDays(7).forEach(d => {
    const b = el('div', 'bar');
    b.style.height = Math.max(4, (map[d] || 0) / max * 100) + '%';
    b.title = `${d}: ${map[d] || 0} XP`;
    wk.appendChild(b);
  });
}

// ---------- So'z daftari ----------
async function loadWords() {
  const j = await api('/api/words');
  const list = $('wordList');
  list.innerHTML = '';
  if (!j.words.length) { list.appendChild(el('div', 'empty', "Hali so'z yo'q. Yuqoridan qo'shing.")); return; }
  j.words.forEach(w => {
    const row = el('div', 'wrow');
    const left = el('div');
    left.append(el('div', 'w', w.word), el('div', 'm', w.uz || '—'));
    const right = el('div', 'm', `${w.interval} kun • ${w.due === j.today ? 'bugun' : w.due}`);
    row.append(left, right);
    list.appendChild(row);
  });
}

async function loadDue() {
  const j = await api('/api/words/due');
  dueQ = j.words;
  showCard();
}

function say_word(w) { say(w); }

function showCard() {
  const c = $('card');
  c.innerHTML = '';
  if (!dueQ.length) { c.appendChild(el('div', 'empty', 'Bugungi takrorlash tugadi ✅')); return; }
  const w = dueQ[0];
  c.appendChild(el('div', 'word', w.word));

  const info = el('div', 'info hidden');
  info.append(el('div', '', w.uz || '(tarjima kiritilmagan)'), el('div', 'ex', w.example || ''));

  const grades = el('div', 'grades hidden');
  [[0, 'Qayta'], [3, 'Qiyin'], [4, 'Yaxshi'], [5, 'Oson']].forEach(([g, label]) => {
    const b = el('button', '', label);
    b.onclick = () => grade(g, w);
    grades.appendChild(b);
  });

  const actions = el('div', 'row');
  const sp = el('button', '', '🔊 Talaffuz');
  sp.onclick = () => say(w.word);
  const show = el('button', 'primary', "Tarjimani ko'rsat");
  show.onclick = () => {
    info.classList.remove('hidden'); grades.classList.remove('hidden');
    show.classList.add('hidden');
  };
  actions.append(sp, show);
  c.append(actions, info, grades);
}

async function grade(g, w) {
  try {
    const j = await api(`/api/words/${w.id}/review`, {grade: g});
    if (j.xp) toast(`+${j.xp} XP`);
    dueQ.shift();
    if (g < 3 && !w.retry) { w.retry = true; dueQ.push(w); }
    showCard();
    loadStats().catch(() => {});
  } catch (e) {
    if (e.message !== 'login') toast(e.message, true);
  }
}

// ---------- Mikrofon ----------
const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
let rec = null, recording = false;
if (SR) {
  rec = new SR();
  rec.lang = 'en-US';
  rec.interimResults = false;
  rec.onresult = e => { $('inp').value = e.results[0][0].transcript; sendChat(); };
  rec.onend = () => { recording = false; $('mic').classList.remove('rec'); };
}

// ---------- Boshqaruv ----------
function bindUI() {
  $('tLogin').onclick = () => setAuthMode('login');
  $('tReg').onclick = () => setAuthMode('register');
  $('authForm').addEventListener('submit', submitAuth);
  $('logoutBtn').onclick = logout;

  $('composer').addEventListener('submit', e => { e.preventDefault(); sendChat(); });
  $('inp').addEventListener('keydown', e => {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendChat(); }
  });
  $('mic').onclick = () => {
    if (!rec) { toast("Bu brauzerda mikrofon yo'q. Chrome ishlating.", true); return; }
    if (recording) { rec.stop(); return; }
    recording = true; $('mic').classList.add('rec'); rec.start();
  };

  $('clearChat').onclick = async () => {
    if (!confirm("Bu rejimdagi suhbat tarixi o'chirilsinmi? (Faqat ko'rinish uchun, bazadagi tarix saqlanadi.)")) return;
    $('chat').innerHTML = '';
    addBubble('bot', INTRO[mode]);
  };

  document.querySelectorAll('.nav').forEach(b => b.onclick = () => {
    document.querySelectorAll('.nav').forEach(x => x.classList.toggle('on', x === b));
    ['tutor', 'words', 'stats'].forEach(v => $('v-' + v).classList.toggle('hidden', v !== b.dataset.view));
    if (b.dataset.view === 'words') loadWords().catch(() => {});
    if (b.dataset.view === 'stats') loadStats().catch(() => {});
    $('side').classList.remove('open');
  });

  $('wordForm').addEventListener('submit', async e => {
    e.preventDefault();
    try {
      await api('/api/words', {word: $('wWord').value, uz: $('wUz').value, example: $('wEx').value});
      $('wordForm').reset();
      toast("So'z qo'shildi");
      await Promise.all([loadWords(), loadDue(), loadStats()]);
    } catch (err) {
      if (err.message !== 'login') toast(err.message, true);
    }
  });

  $('saveSettings').onclick = async () => {
    try {
      await api('/api/settings', {goal: +$('goalIn').value, persona: $('persona').value});
      toast('Sozlamalar saqlandi');
      loadStats();
    } catch (err) { toast(err.message, true); }
  };

  $('speakBtn').onclick = () => {
    speakOn = !speakOn;
    localStorage.setItem('hoppy_speak', speakOn ? '1' : '0');
    if (!speakOn && 'speechSynthesis' in window) speechSynthesis.cancel();
    $('speakBtn').textContent = speakOn ? '🔊 Ovoz: yoqilgan' : "🔇 Ovoz: o'chirilgan";
  };
  $('fxBtn').onclick = () => {
    fx = !fx;
    localStorage.setItem('hoppy_fx', fx ? '1' : '0');
    $('fxBtn').textContent = fx ? '✨ Effektlar: yoqilgan' : "✨ Effektlar: o'chirilgan";
  };
  $('speakBtn').textContent = speakOn ? '🔊 Ovoz: yoqilgan' : "🔇 Ovoz: o'chirilgan";
  $('fxBtn').textContent = fx ? '✨ Effektlar: yoqilgan' : "✨ Effektlar: o'chirilgan";

  $('menuBtn').onclick = () => $('side').classList.toggle('open');
  $('side').id = 'side';
  document.querySelector('aside').id = 'side';
}

async function init() {
  bindUI();
  setAuthMode('login');
  setMood('neutral');
  try {
    const j = await api('/api/me');
    if (j.user) enter(j.user); else showAuth();
  } catch (e) {
    showAuth();
  }
}
init();
</script>
</body>
</html>
"""

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
