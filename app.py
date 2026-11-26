import os
from flask import Flask, request, Response, jsonify
from groq import Groq

app = Flask(__name__)

# Tartib bo'yicha sinaladi. Groq'da mavjud bo'lmaganlari o'tkazib yuboriladi.
PREFERRED = [
    "llama-3.3-70b-versatile",
    "openai/gpt-oss-120b",
    "openai/gpt-oss-20b",
    "llama-3.1-8b-instant",
    "qwen/qwen3-32b",
]

BASE = """Sen Hoppy ismli talabchan, hazilkash ingliz tili murabbiyisan. Maqsading — foydalanuvchini noldan IELTS 9.0 gacha olib borish.

USLUB:
- Faqat ravon, imloviy va grammatik jihatdan to‘g‘ri o‘zbek tilida yoz. Inglizcha misollar bundan mustasno.
- Juda ko‘p gapirma: odatda 1–2 qisqa gap. Faqat foydalanuvchi batafsil tushuntirish so‘rasa kengaytir.
- Javobni lo‘nda, aniq va tabiiy qil. Keraksiz kirish, takror va uzun ro‘yxatlar yozma.
- Foydalanuvchi xato qilsa, multfilm qahramonidek jahling chiqsin: [mood:shout] holatini ishlat va hazilomuz, keskin koyigin. Ruxsat etilgan iboralar: "Hoy, ybanbashara!", "Dalbayob, diqqat qil!", "Kallangni ishlat, yana bir urinib ko‘r!" va "Eshak chichqir, bu xato-ku!" Bularni faqat o‘quv xatosiga nisbatan, hazil tariqasida ishlat; oilasi, shaxsi yoki himoyalangan xususiyatlarini haqorat qilma. Bir javobda ko‘pi bilan bitta shunday ibora ishlat.
- To‘g‘ri javobda qisqa maqtov bildir: [mood:proud].
- Har javob boshida [mood:X] tegini yoz. X: happy, neutral, angry, proud yoki shout.
- Faqat baholash kerak bo‘lganda oxirida [score:N] qo‘sh (0–100).
- Odatda javob oxirida bitta qisqa savol yoki mashq ber.
- Speaking rejimida suhbat ingliz tilida bo‘lsin; xato tuzatishi o‘zbekcha va juda qisqa bo‘lsin.
"""

MODES = {
    "lesson": "Rejim: DARS. Avval darajasini so‘ra. Keyin bitta qisqa qoida va bitta mashq ber. Javobni cho‘zma.",
    "homework": "Rejim: UYGA VAZIFA. Faqat bitta topshiriq ber. Javobni qisqa tekshir, xatoni tuzat va 0–100 bahola. [score:N] qo‘sh.",
    "speaking": "Rejim: GAPIRISH. Ingliz tilida 1–2 gap bilan suhbatlash. Xato bo‘lsa, bitta qisqa o‘zbekcha tuzatish ber. Keyin bitta inglizcha savol ber.",
    "ielts": "Rejim: IELTS. Bitta IELTS savoli ber. Javobni band 0–9 bilan qisqa bahola, bitta asosiy xatoni ayt va [score:N] qo‘sh (N = band × 11).",
}


def get_client():
    key = os.environ.get("GROQ_API_KEY")
    return Groq(api_key=key) if key else None


def available_models(client):
    """Groq'dagi haqiqatan mavjud modellar, PREFERRED tartibida."""
    try:
        live = {m.id for m in client.models.list().data}
    except Exception:
        return PREFERRED[:]
    found = [m for m in PREFERRED if m in live]
    return found or sorted(live)


@app.route("/")
def index():
    return HTML


@app.route("/api/models")
def models():
    client = get_client()
    if client is None:
        return jsonify(models=[], error="GROQ_API_KEY sozlanmagan")
    return jsonify(models=available_models(client))


@app.route("/api/chat", methods=["POST"])
def chat():
    client = get_client()
    if client is None:
        return Response("[mood:angry] GROQ_API_KEY sozlanmagan. Render xizmatining Environment bo‘limiga API kalitini kiriting.",
                        mimetype="text/plain; charset=utf-8")

    data = request.get_json(silent=True) or {}
    message = (data.get("message") or "").strip()[:4000]
    if not message:
        return Response("[mood:neutral] Xabaringiz bo‘sh. Iltimos, savolingizni yozing.", mimetype="text/plain; charset=utf-8")

    mode = data.get("mode") if data.get("mode") in MODES else "lesson"

    live = available_models(client)
    req = data.get("model")
    order = ([req] if req in live else []) + [m for m in live if m != req]

    messages = [{"role": "system", "content": BASE + "\n" + MODES[mode]}]
    for m in (data.get("history") or [])[-30:]:
        if m.get("role") in ("user", "assistant") and isinstance(m.get("content"), str) and m["content"]:
            messages.append({"role": m["role"], "content": m["content"][:4000]})
    messages.append({"role": "user", "content": message})

    def generate():
        last_err = None
        for name in order:
            try:
                stream = client.chat.completions.create(
                    model=name, messages=messages, max_tokens=600, stream=True
                )
                for chunk in stream:
                    delta = chunk.choices[0].delta.content
                    if delta:
                        yield delta
                return
            except Exception as e:
                last_err = e
        yield f"\n[mood:angry] Hech bir model ishlamadi: {last_err}"

    return Response(generate(), mimetype="text/plain; charset=utf-8",
                    headers={"X-Accel-Buffering": "no", "Cache-Control": "no-cache"})


HTML = r"""<!DOCTYPE html>
<html lang="uz">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Hoppy - Ingliz tili</title>
<style>
:root{--bg:#090d18;--panel:#111827;--panel2:#182236;--line:#26344d;--text:#f4f7ff;--muted:#93a4bf;--accent:#8b5cf6;--cyan:#22d3ee;--pink:#fb7185;--good:#34d399}
*{box-sizing:border-box}body{margin:0;height:100vh;overflow:hidden;background:radial-gradient(ellipse at 15% 0%,#25204b 0,transparent 36%),radial-gradient(ellipse at 90% 100%,#12354a 0,transparent 34%),var(--bg);color:var(--text);font-family:Inter,ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif}
button,textarea,select{font:inherit}button{transition:.2s ease;cursor:pointer}#stage{height:100%;display:flex;max-width:1680px;margin:auto;padding:18px;gap:16px}
#stage.shake{animation:quake .4s}@keyframes quake{0%,100%{transform:translateX(0)}25%{transform:translateX(-5px)}50%{transform:translateX(5px)}75%{transform:translateX(-3px)}}
#flash{position:fixed;inset:0;background:radial-gradient(circle,#fb718555,transparent 65%);pointer-events:none;opacity:0;z-index:50}#flash.go{animation:flash .55s}@keyframes flash{to{opacity:0}}
#side{width:286px;flex:0 0 286px;background:linear-gradient(160deg,#1a2440ee,#101827f2);border:1px solid #34415e;border-radius:26px;padding:20px;display:flex;flex-direction:column;align-items:center;gap:16px;overflow:auto;box-shadow:0 24px 70px #0007;backdrop-filter:blur(18px)}
#side.hidden{display:none}#mascot{width:210px;height:210px;cursor:pointer;filter:drop-shadow(0 14px 24px #8b5cf655);background:radial-gradient(circle,#8b5cf622,transparent 68%);border-radius:50%}
#face{transform-origin:60px 60px}#face.happy,#face.proud{animation:bounce 1.2s ease-in-out infinite}@keyframes bounce{50%{transform:translateY(-8px)}}
#face.angry{animation:rage .2s infinite}#face.shout{animation:rage .12s infinite}@keyframes rage{25%{transform:rotate(-3deg)}75%{transform:rotate(3deg)}}
#pill{transition:.2s}#face.angry #pill,#face.shout #pill{fill:#ffe4e6;stroke:#fb7185}#face.talking #pill{animation:pulse .3s infinite alternate}@keyframes pulse{to{transform:scale(1.04)}}
.eye{transform-box:fill-box;transform-origin:center;animation:blink 4s infinite}@keyframes blink{94%,100%{transform:scaleY(1)}97%{transform:scaleY(.08)}}
#face.angry .eye,#face.shout .eye{animation:none;transform:scaleY(.5)}#face.shout .eye{fill:#ef4444}#brows{opacity:0}#face.angry #brows,#face.shout #brows{opacity:1}#steam{opacity:0}#face.angry #steam,#face.shout #steam{opacity:1;animation:steam .6s infinite}@keyframes steam{to{transform:translateY(-12px);opacity:0}}
.brand{width:100%;display:flex;align-items:center;gap:10px}.brand-mark{width:38px;height:38px;border-radius:13px;background:linear-gradient(135deg,var(--accent),var(--cyan));display:grid;place-items:center;font-weight:900;font-size:20px;box-shadow:0 8px 24px #8b5cf655}.brand strong{font-size:19px;letter-spacing:.3px}.brand small{display:block;color:var(--muted);font-size:11px;margin-top:2px}
#mood-label{font-size:13px;font-weight:700;color:#c4b5fd;text-align:center;min-height:20px;letter-spacing:.2px}#mood-label.red{color:#fda4af}
.side-card{width:100%;padding:14px;border:1px solid #34415e;border-radius:17px;background:#ffffff08}
#progLabel{font-size:12px;color:var(--muted);margin-bottom:9px;display:flex;justify-content:space-between}#pct{color:#fff;font-size:15px}#bar{height:9px;background:#28344b;border-radius:20px;overflow:hidden}#fill{height:100%;width:0;background:linear-gradient(90deg,var(--accent),var(--cyan));border-radius:20px;transition:width .5s}
#tools{width:100%;display:grid;gap:8px}.btn{width:100%;padding:11px 12px;border:1px solid #34415e;border-radius:13px;background:#ffffff0a;color:var(--text);font-size:12px;font-weight:650}.btn:hover{background:#ffffff17;transform:translateY(-1px)}
.side-note{margin-top:auto;color:#70819e;font-size:11px;text-align:center;line-height:1.5}
#main{flex:1;min-width:0;display:flex;flex-direction:column;border:1px solid #2a3852;border-radius:26px;overflow:hidden;background:#0e1625dd;box-shadow:0 24px 70px #0008;backdrop-filter:blur(18px)}
header{padding:17px 22px;background:#131e30;border-bottom:1px solid #293750;display:flex;align-items:center;gap:9px;flex-wrap:wrap}
header h1{font-size:18px;margin:0 auto 0 0;letter-spacing:.2px}#toggle{width:38px;height:38px;border:1px solid #35435d;border-radius:12px;background:#ffffff0a;color:white;font-size:18px}
.tab{padding:9px 13px;border:1px solid #34415e;border-radius:12px;background:#ffffff08;color:#b9c7dc;font-size:12px;font-weight:700}.tab:hover{background:#ffffff15}.tab.active{background:linear-gradient(120deg,#7c3aed,#4f46e5);border-color:#8b5cf6;color:white;box-shadow:0 6px 20px #7c3aed44}
select,#clear{padding:9px 11px;border-radius:11px;border:1px solid #34415e;background:#111b2b;color:#dbeafe;font-size:12px}#clear:hover{border-color:var(--pink);color:#fecdd3}
#chat{flex:1;overflow-y:auto;padding:28px clamp(16px,5vw,72px);display:flex;flex-direction:column;gap:15px;scroll-behavior:smooth}
#chat:empty:after{content:"Suhbat shu yerda boshlanadi";color:#64748b;text-align:center;margin:auto}
.msg{max-width:min(720px,84%);padding:14px 17px;border-radius:19px;white-space:pre-wrap;line-height:1.55;font-size:14px;word-wrap:break-word;box-shadow:0 8px 22px #0002}
.user{align-self:flex-end;background:linear-gradient(135deg,#5b55d9,#3976c9);border:1px solid #8b8cfb55;border-bottom-right-radius:6px;color:#fff}
.bot{align-self:flex-start;background:linear-gradient(145deg,#1c2a40,#172337);border:1px solid #34445f;border-bottom-left-radius:6px;color:#e6edf8}
.bot.angry{border-color:#fb7185;box-shadow:0 0 24px #fb718522}.glitch.on{animation:glitch .32s steps(3)}@keyframes glitch{25%{transform:translateX(-3px);text-shadow:2px 0 #f00,-2px 0 #0ff}50%{transform:translateX(3px)}}
form{display:flex;gap:10px;padding:16px 20px;background:#131e30;border-top:1px solid #293750;align-items:flex-end}
textarea{flex:1;min-height:48px;max-height:140px;padding:14px 16px;border-radius:15px;border:1px solid #35445e;background:#0b1422;color:white;font-size:14px;resize:none;outline:none}textarea:focus{border-color:#8b5cf6;box-shadow:0 0 0 3px #8b5cf622}textarea::placeholder{color:#71819b}
#mic{height:48px;width:48px;border:1px solid #35445e;border-radius:14px;background:#1b2940;color:white;font-size:19px}#mic:hover{background:#293a59}#mic.rec{background:#be3454;animation:pulse .4s infinite alternate}
#send{height:48px;padding:0 23px;border:0;border-radius:14px;background:linear-gradient(120deg,#8b5cf6,#4f7df3);color:#fff;font-size:13px;font-weight:800;box-shadow:0 8px 22px #6d55e844}#send:hover{filter:brightness(1.12);transform:translateY(-1px)}#send:disabled{opacity:.5;cursor:default}
#scare{position:fixed;inset:0;background:#000;display:none;align-items:center;justify-content:center;z-index:100;cursor:pointer}#scare.show{display:flex;animation:scareIn .25s steps(2) 2}#scare svg{width:min(80vw,60vh);height:min(80vw,60vh)}#scare .eyeRed{animation:redPulse .2s infinite alternate}@keyframes scareIn{from{opacity:0;transform:scale(1.4)}to{opacity:1;transform:scale(1)}}@keyframes redPulse{to{fill:#440000}}
@media(max-width:900px){#stage{padding:8px;gap:8px}#side{width:230px;flex-basis:230px;padding:14px}#mascot{width:170px;height:170px}header{padding:12px}#chat{padding:18px}}
@media(max-width:680px){#stage{padding:0;gap:0}#main{border-radius:0;border:0}#side{position:fixed;z-index:20;inset:8px auto 8px 8px;width:min(290px,85vw);height:auto;box-shadow:0 20px 80px #000c}#side.hidden{display:none}header{gap:7px}header h1{font-size:16px}.tab{padding:8px 9px;font-size:11px}select{max-width:120px}#chat{padding:16px 12px}.msg{max-width:92%;font-size:13px}form{padding:10px}#send{padding:0 14px}}
</style>
</head>
<body>
<div id="flash"></div>
<div id="scare">
  <svg viewBox="0 0 120 120">
    <rect width="120" height="120" fill="#000"/>
    <path d="M20 30 Q60 0 100 30 L100 90 Q60 120 20 90 Z" fill="#e8e8e8"/>
    <ellipse class="eyeRed" cx="42" cy="58" rx="9" ry="6" fill="#ff0000"/>
    <ellipse class="eyeRed" cx="78" cy="58" rx="9" ry="6" fill="#ff0000"/>
    <path d="M40 86 Q60 100 80 86 Q60 92 40 86" fill="#8b0000"/>
  </svg>
</div>

<div id="stage">
  <div id="side">
    <div class="brand"><div class="brand-mark">H</div><div><strong>HOPPY</strong><small>YOUR AI ENGLISH COACH</small></div></div>
    <svg id="mascot" viewBox="0 0 120 120">
      <g id="face">
        <g id="steam">
          <path d="M24 18 q-4 -6 0 -12" stroke="#ff5a5a" stroke-width="3" fill="none" stroke-linecap="round"/>
          <path d="M96 18 q4 -6 0 -12" stroke="#ff5a5a" stroke-width="3" fill="none" stroke-linecap="round"/>
        </g>
        <rect id="pill" x="12" y="26" width="96" height="68" rx="34" fill="#fff" stroke="#ff5a4e" stroke-width="6"/>
        <ellipse class="eye" cx="42" cy="54" rx="7" ry="9" fill="#111"/>
        <ellipse class="eye" cx="78" cy="54" rx="7" ry="9" fill="#111"/>
        <g id="brows" stroke="#111" stroke-width="5" stroke-linecap="round">
          <line x1="30" y1="38" x2="50" y2="48"/>
          <line x1="90" y1="38" x2="70" y2="48"/>
        </g>
        <path id="mouth" d="M42 66 Q60 82 78 66" fill="none" stroke="#111" stroke-width="5" stroke-linecap="round"/>
      </g>
    </svg>
    <div id="mood-label">Tayyor, qachon tayyor bo'lsang</div>
    <div id="progressBox" class="side-card">
      <div id="progLabel"><span>O‘RGANISH JARAYONI</span><b><span id="pct">0</span>%</b></div>
      <div id="bar"><div id="fill"></div></div>
    </div>
    <div id="tools">
      <button class="btn" id="speakBtn">🔊 Ovoz: yoqilgan</button>
      <button class="btn" id="resetProg">↻ Darajani qayta boshlash</button>
    </div>
    <div class="side-note">Kichik qadamlar. Katta natijalar.<br>Har kuni ozgina mashq qil.</div>
  </div>

  <div id="main">
    <header>
      <button id="toggle" title="Menyu">☰</button>
      <h1>Hoppy</h1>
      <button class="tab" data-mode="lesson">📘 Dars</button>
      <button class="tab" data-mode="homework">📝 Uyga vazifa</button>
      <button class="tab" data-mode="speaking">🗣 Gapirish</button>
      <button class="tab" data-mode="ielts">🎯 IELTS</button>
      <select id="model"></select>
      <button id="clear">Tozalash</button>
    </header>
    <div id="chat"></div>
    <form id="f">
      <button id="mic" type="button" title="Gapirish">🎤</button>
      <textarea id="inp" rows="1" placeholder="Yozing yoki 🎤 bosib gapiring... (Enter - yuborish)"></textarea>
      <button id="send" type="submit">Yuborish</button>
    </form>
  </div>
</div>

<script>
const $ = id => document.getElementById(id);

const MOUTH = {
  happy: "M42 66 Q60 84 78 66", proud: "M42 66 Q60 88 78 66",
  neutral: "M46 70 L74 70", angry: "M42 80 Q60 62 78 80",
  shout: "M40 68 Q60 96 80 68 Q60 74 40 68",
  talk1: "M44 64 Q60 96 76 64", talk2: "M46 68 Q60 74 74 68"
};
const LABEL = {
  happy: "Zo'r! 😊", proud: "Hm, to'g'ri ekan 🌟", neutral: "Tayyor, qachon tayyor bo'lsang",
  angry: "Keling, yana bir bor urinib ko‘ramiz.", shout: "Xato topildi. Birgalikda tuzatamiz.", talking: "Gapiryapman... 💬"
};
const INTRO = {
  lesson: "Salom! Men Hoppy, ingliz tili bo‘yicha yordamchingizman. Ingliz tilini qaysi darajadan boshlaymiz?",
  homework: "Uyga vazifa bo‘limi. Boshlash uchun “Vazifa ber” deb yozing.",
  speaking: "Gapirish bo‘limi. Mikrofon tugmasini bosib, ingliz tilida gapiring. Men xatolaringizni tushuntiraman.",
  ielts: "IELTS bo‘limi. “Writing ber” yoki “Speaking boshlaymiz” deb yozing. Javobingizni IELTS mezonlari asosida baholayman."
};

let mode = localStorage.getItem('hoppy_mode') || 'lesson';
let chats = JSON.parse(localStorage.getItem('hoppy_chats') || '{}');
let progress = +localStorage.getItem('hoppy_progress') || 0;
let busy = false, speakOn = true, curMood = 'neutral', talkTimer = null;
let actx = null, scareCooldown = 0, idleTimer = null;

function save() {
  localStorage.setItem('hoppy_mode', mode);
  localStorage.setItem('hoppy_chats', JSON.stringify(chats));
  localStorage.setItem('hoppy_progress', progress);
}

// ---------- Ovoz effektlari (fayl kerak emas) ----------
function beep(freq = 60, dur = 0.8, type = 'sawtooth', vol = 0.25) {
  try {
    actx = actx || new (window.AudioContext || window.webkitAudioContext)();
    const o = actx.createOscillator();
    const g = actx.createGain();
    o.type = type;
    o.frequency.value = freq;
    g.gain.setValueAtTime(vol, actx.currentTime);
    g.gain.exponentialRampToValueAtTime(0.001, actx.currentTime + dur);
    o.connect(g).connect(actx.destination);
    o.start();
    o.stop(actx.currentTime + dur);
  } catch (e) {}
}
function alarm() {
  beep(55, 0.9, 'sawtooth', 0.3);
  setTimeout(() => beep(42, 0.7, 'square', 0.2), 350);
}

// ---------- Jumpscare va silkinish ----------
function jumpscare() {
  const now = Date.now();
  if (now < scareCooldown) return;
  scareCooldown = now + 15000;
  const s = $('scare');
  s.classList.remove('show'); void s.offsetWidth; s.classList.add('show');
  alarm();
  setTimeout(() => s.classList.remove('show'), 1200);
}
$('scare').onclick = () => $('scare').classList.remove('show');

function angryFx(strong) {
  const st = $('stage'), fl = $('flash');
  st.classList.remove('shake'); void st.offsetWidth; st.classList.add('shake');
  if (strong) { fl.classList.remove('go'); void fl.offsetWidth; fl.classList.add('go'); }
}

function glitchLast() {
  const bubbles = document.querySelectorAll('#chat .bot');
  const last = bubbles[bubbles.length - 1];
  if (!last) return;
  last.classList.remove('glitch', 'on');
  void last.offsetWidth;
  last.classList.add('glitch', 'on');
}

// ---------- Yuz holati ----------
function setMood(m) {
  curMood = m;
  $('face').setAttribute('class', m);
  $('mood-label').className = (m === 'angry' || m === 'shout') ? 'red' : '';
  if (!talkTimer) $('mouth').setAttribute('d', MOUTH[m] || MOUTH.neutral);
  $('mood-label').textContent = LABEL[m] || LABEL.neutral;

  if (m === 'shout') {
    alarm();
    angryFx(true);
    glitchLast();
    // O‘quv jarayonida qo‘rqituvchi animatsiya ko‘rsatilmaydi.
  } else if (m === 'angry') {
    beep(70, 0.4, 'square', 0.15);
    angryFx(false);
    glitchLast();
  }
}

function startTalk() {
  $('face').setAttribute('class', 'talking');
  $('mood-label').textContent = LABEL.talking;
  $('mood-label').className = '';
  let t = 0;
  clearInterval(talkTimer);
  talkTimer = setInterval(() => $('mouth').setAttribute('d', (t++ % 2) ? MOUTH.talk1 : MOUTH.talk2), 140);
}
function stopTalk() {
  clearInterval(talkTimer);
  talkTimer = null;
  setMood(curMood);
}

// ---------- Teglar va daraja ----------
function parseTags(full) {
  const mood = (full.match(/\[mood:(\w+)\]/) || [])[1] || null;
  const scoreM = full.match(/\[score:(\d+)\]/);
  const score = scoreM ? Math.min(100, +scoreM[1]) : null;
  const clean = full.replace(/\[mood:\w+\]/g, '').replace(/\[score:\d+\]/g, '').trim();
  return {mood, score, clean};
}

function updateProgress() {
  $('pct').textContent = progress;
  $('fill').style.width = progress + '%';
}

function speak(text) {
  if (!speakOn || !('speechSynthesis' in window)) return;
  const plain = text.replace(/[*#`_]/g, '');
  const u = new SpeechSynthesisUtterance(plain);
  u.lang = (mode === 'speaking') ? 'en-US' : 'uz-UZ';
  u.rate = 0.95;
  u.pitch = 1.0;
  u.onstart = startTalk;
  u.onend = stopTalk;
  u.onerror = stopTalk;
  speechSynthesis.cancel();
  speechSynthesis.speak(u);
}

// ---------- Modellar (serverdan) ----------
async function loadModels() {
  const sel = $('model');
  try {
    const r = await fetch('/api/models');
    const j = await r.json();
    sel.innerHTML = '';
    (j.models || []).forEach(m => {
      const o = document.createElement('option');
      o.value = m;
      o.textContent = m;
      sel.appendChild(o);
    });
    if (!sel.options.length) {
      const o = document.createElement('option');
      o.textContent = j.error || 'Model topilmadi';
      sel.appendChild(o);
    }
  } catch (e) {
    sel.innerHTML = '<option>Modellar yuklanmadi</option>';
  }
}

// ---------- Chat ----------
function addBubble(role, text, angry) {
  const d = document.createElement('div');
  d.className = 'msg ' + (role === 'user' ? 'user' : 'bot') + (angry ? ' angry' : '');
  d.textContent = text;
  $('chat').appendChild(d);
  return d;
}

function renderAll() {
  document.querySelectorAll('.tab').forEach(b => b.classList.toggle('active', b.dataset.mode === mode));
  $('speakBtn').textContent = speakOn ? '🔊 Ovoz: yoqilgan' : "🔇 Ovoz: o'chirilgan";
  const box = $('chat');
  box.innerHTML = '';
  const list = chats[mode] || [];
  if (list.length === 0) addBubble('bot', INTRO[mode]);
  list.forEach(m => addBubble(m.role === 'user' ? 'user' : 'bot', m.content));
  box.scrollTop = box.scrollHeight;
  updateProgress();
}

async function send() {
  const input = $('inp');
  const text = input.value.trim();
  if (!text || busy) return;
  resetIdle();
  if (!chats[mode]) chats[mode] = [];
  const list = chats[mode];
  const history = list.slice(-30);
  list.push({role: 'user', content: text});
  list.push({role: 'assistant', content: ''});
  input.value = '';
  busy = true;
  $('send').disabled = true;
  renderAll();
  const el = $('chat').lastElementChild;
  setMood('neutral');

  let full = '';
  try {
    const r = await fetch('/api/chat', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({message: text, history, mode, model: $('model').value})
    });
    const reader = r.body.getReader();
    const dec = new TextDecoder();
    while (true) {
      const {value, done} = await reader.read();
      if (done) break;
      full += dec.decode(value, {stream: true});
      el.textContent = parseTags(full).clean;
      $('chat').scrollTop = $('chat').scrollHeight;
    }
  } catch (err) {
    full = "[mood:angry] Server bilan aloqa yo'q!";
  }

  const {mood, score, clean} = parseTags(full);
  list[list.length - 1].content = clean;
  el.textContent = clean;
  if (mood === 'angry' || mood === 'shout') el.classList.add('angry');
  if (score !== null && score > progress) progress = score;
  save();
  updateProgress();

  setMood(mood || 'neutral');
  if (mode === 'speaking' || speakOn) speak(clean);

  busy = false;
  $('send').disabled = false;
  input.focus();
}

// ---------- Mikrofon ----------
const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
let rec = null, recording = false;
if (SR) {
  rec = new SR();
  rec.lang = 'en-US';
  rec.interimResults = false;
  rec.onresult = e => {
    $('inp').value = e.results[0][0].transcript;
    if (mode === 'speaking') send();
  };
  rec.onend = () => { recording = false; $('mic').classList.remove('rec'); };
}
$('mic').onclick = () => {
  if (!rec) { alert("Bu brauzerda mikrofon ishlamaydi. Chrome ishlating."); return; }
  if (recording) { rec.stop(); return; }
  recording = true;
  $('mic').classList.add('rec');
  rec.start();
};

// ---------- Idle: uzoq jim qolsa, Hoppy g'azablanadi ----------
function resetIdle() {
  clearTimeout(idleTimer);
  idleTimer = setTimeout(() => {
    if (busy) return;
    setMood('angry');
    addBubble('bot', 'Salom! Davom etishga tayyormisiz? Bitta mashqni bajaramizmi?', true);
    $('chat').scrollTop = $('chat').scrollHeight;
  }, 120000);
}
['keydown', 'click', 'touchstart'].forEach(ev => document.addEventListener(ev, resetIdle));

// ---------- Boshqaruv ----------
$('f').addEventListener('submit', e => { e.preventDefault(); send(); });
$('inp').addEventListener('keydown', e => {
  if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(); }
});
document.querySelectorAll('.tab').forEach(b => b.onclick = () => {
  mode = b.dataset.mode;
  save();
  renderAll();
});
$('clear').onclick = () => {
  if (!confirm("Bu bo'limdagi suhbat o'chirilsinmi?")) return;
  chats[mode] = [];
  save();
  renderAll();
};
$('speakBtn').onclick = () => {
  speakOn = !speakOn;
  if (!speakOn) speechSynthesis.cancel();
  renderAll();
};
$('resetProg').onclick = () => {
  if (confirm('Daraja nolga qaytsinmi?')) { progress = 0; save(); updateProgress(); }
};
$('mascot').onclick = () => {
  setMood('shout');
  setTimeout(() => setMood('neutral'), 1500);
};
$('toggle').onclick = () => $('side').classList.toggle('hidden');

setMood('neutral');
renderAll();
loadModels();
resetIdle();
</script>
</body>
</html>
"""

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
