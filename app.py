import os
from flask import Flask, request, Response
from groq import Groq

app = Flask(__name__)
MODELS = ["llama-3.3-70b-versatile", "llama-3.1-8b-instant"]

BASE = """Sen "Hoppy" ismli ingliz tili o'qituvchisisan. Foydalanuvchini 0 dan IELTS 9.0 darajasigacha o'rgatasan.
Qoidalar:
- Tushuntirishlar o'zbek tilida, misollar va gaplar inglizcha bo'lsin.
- Har javobni [mood:X] tegi bilan boshla. X ni quyidagilardan tanla: happy, neutral, angry, proud.
- Foydalanuvchi xato qilsa, jahli chiqqan, qattiq ohangda tuzat (masalan: "Yana xato! Bu gap grammatikadan yiqildi!"). Haqorat qilma, lekin ayama.
- To'g'ri javob bo'lsa, qisqa maqta va angry emas, happy yoki proud tanla.
- Agar vazifa yoki test baholansa, javob oxirida [score:N] tegi bo'lsin, N 0 dan 100 gacha (foiz).
- Har doim oxirida bitta aniq savol yoki keyingi vazifa ber.
"""

MODES = {
    "lesson": "Rejim: DARS. Foydalanuvchi darajasini aniqlab, bosqichma-bosqich dars ber: alifbo, talaffuz, so'z turkumlari, zamonlar, gap tuzish, so'z boyligi, keyin IELTS. Har darsda qisqa nazariya va 3 ta mashq ber.",
    "homework": "Rejim: UYGA VAZIFA. Foydalanuvchiga 5 ta vazifa ber (grammatika, so'z boyligi, tarjima). Foydalanuvchi javoblarini tekshir, har birini izohla va oxirida umumiy [score:N] qo'sh.",
    "speaking": "Rejim: GAPIRISH. Foydalanuvchi bilan faqat inglizcha suhbatlash. Javobing 2-3 qisqa gap bo'lsin. Foydalanuvchi gapidagi xatolarni oxirida o'zbekcha izoh bilan ko'rsat. Talaffuz va so'z tanlashni ham tuzat. Har javobda savol ber.",
    "ielts": "Rejim: IELTS. Writing Task 2, Speaking Part 1-3 yoki Reading mashqlarini o'tkaz. Foydalanuvchi javobini IELTS band (0-9) bo'yicha baholа, nimalar yetishmasligini ayt va oxirida [score:N] qo'sh (N = band * 11, masalan 6.5 bo'lsa 72).",
}


def get_client():
    key = os.environ.get("GROQ_API_KEY")
    return Groq(api_key=key) if key else None


@app.route("/")
def index():
    return HTML


@app.route("/api/chat", methods=["POST"])
def chat():
    client = get_client()
    if client is None:
        return Response("[mood:angry] GROQ_API_KEY sozlanmagan. Render'da Environment ga qo'shing.",
                        mimetype="text/plain; charset=utf-8")

    data = request.get_json(silent=True) or {}
    message = (data.get("message") or "").strip()[:4000]
    if not message:
        return Response("[mood:angry] Bo'sh xabar! Yozing.", mimetype="text/plain; charset=utf-8")

    mode = data.get("mode") if data.get("mode") in MODES else "lesson"
    model = data.get("model") if data.get("model") in MODELS else MODELS[0]

    messages = [{"role": "system", "content": BASE + "\n" + MODES[mode]}]
    for m in (data.get("history") or [])[-30:]:
        if m.get("role") in ("user", "assistant") and isinstance(m.get("content"), str) and m["content"]:
            messages.append({"role": m["role"], "content": m["content"][:4000]})
    messages.append({"role": "user", "content": message})

    def generate():
        try:
            stream = client.chat.completions.create(
                model=model, messages=messages, max_tokens=1500, stream=True
            )
            for chunk in stream:
                delta = chunk.choices[0].delta.content
                if delta:
                    yield delta
        except Exception as e:
            yield f"\n[mood:angry] Xato yuz berdi: {e}"

    return Response(generate(), mimetype="text/plain; charset=utf-8",
                    headers={"X-Accel-Buffering": "no", "Cache-Control": "no-cache"})


HTML = r"""<!DOCTYPE html>
<html lang="uz">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Hoppy - Ingliz tili</title>
<style>
  * { box-sizing: border-box; }
  body { margin:0; height:100vh; display:flex; font-family: system-ui, sans-serif; background:#0f1115; color:#e8e8e8; }
  #side { width:300px; background:#171a21; border-right:1px solid #262b36; display:flex; flex-direction:column; align-items:center; padding:16px; gap:12px; overflow-y:auto; }
  #side.hidden { display:none; }
  #mascot { width:200px; height:200px; cursor:pointer; }
  #mascot .eye { transform-origin:center; transform-box:fill-box; animation: blink 4s infinite; }
  @keyframes blink { 0%,94%,100% { transform:scaleY(1); } 97% { transform:scaleY(0.1); } }
  #face.happy, #face.proud { animation: bounce 1.2s ease-in-out infinite; }
  @keyframes bounce { 0%,100% { transform:translateY(0); } 50% { transform:translateY(-8px); } }
  #face.angry { animation: shake 0.25s infinite; }
  @keyframes shake { 0%,100% { transform:translateX(0); } 25% { transform:translateX(-3px); } 75% { transform:translateX(3px); } }
  #face.angry #brows { opacity:1; }
  #brows { opacity:0; transition:opacity .2s; }
  #face.angry #pill { stroke:#ff3b3b; }
  #face.angry .eye { transform:scaleY(0.6); }
  #face.talking #pill { animation: pulse .3s infinite alternate; }
  @keyframes pulse { from { stroke-width:6; } to { stroke-width:9; } }
  #pill { transition: stroke .3s; }
  #mood-label { font-size:14px; color:#8a93a6; }
  #progressBox { width:100%; }
  #progLabel { font-size:14px; margin-bottom:6px; }
  #bar { height:12px; background:#262b36; border-radius:8px; overflow:hidden; }
  #fill { height:100%; width:0%; background:linear-gradient(90deg,#2b6cb0,#7cc4ff); transition: width .6s; }
  #tools { width:100%; display:flex; flex-direction:column; gap:8px; }
  .btn { padding:10px; border:1px solid #303746; border-radius:10px; background:#1e232d; color:#e8e8e8; cursor:pointer; font-size:14px; }
  .btn:hover { background:#262b36; }
  .btn.on { background:#2b6cb0; border-color:#2b6cb0; }
  #main { flex:1; display:flex; flex-direction:column; min-width:0; }
  header { padding:12px 16px; background:#171a21; border-bottom:1px solid #262b36; display:flex; align-items:center; gap:10px; flex-wrap:wrap; }
  header h1 { margin:0; font-size:20px; color:#7cc4ff; margin-right:auto; }
  #toggle { background:none; border:0; color:#e8e8e8; font-size:20px; cursor:pointer; }
  .tab { padding:8px 14px; border-radius:8px; border:1px solid #303746; background:#1e232d; color:#e8e8e8; cursor:pointer; font-size:14px; }
  .tab.active { background:#2b6cb0; border-color:#2b6cb0; }
  select, #clear { padding:7px 10px; border-radius:8px; border:1px solid #303746; background:#0f1115; color:#e8e8e8; cursor:pointer; }
  #chat { flex:1; overflow-y:auto; padding:20px; display:flex; flex-direction:column; gap:12px; }
  .msg { max-width:80%; padding:10px 14px; border-radius:12px; white-space:pre-wrap; line-height:1.55; word-wrap:break-word; }
  .user { align-self:flex-end; background:#2b6cb0; }
  .bot { align-self:flex-start; background:#1e232d; }
  form { display:flex; gap:8px; padding:14px; background:#171a21; border-top:1px solid #262b36; }
  textarea { flex:1; padding:12px; border-radius:10px; border:1px solid #303746; background:#0f1115; color:#e8e8e8; font-size:15px; resize:none; font-family:inherit; }
  #mic { padding:0 14px; border:1px solid #303746; border-radius:10px; background:#1e232d; color:#e8e8e8; font-size:18px; cursor:pointer; }
  #mic.rec { background:#b03030; }
  #send { padding:0 20px; border:0; border-radius:10px; background:#2b6cb0; color:#fff; font-size:15px; cursor:pointer; }
  #send:disabled { opacity:0.5; cursor:default; }
  @media (max-width:800px) { #side { position:absolute; z-index:5; height:100%; background:#171a21; } #side.hidden { display:none; } }
</style>
</head>
<body>
<div id="side">
  <svg id="mascot" viewBox="0 0 120 120">
    <g id="face">
      <rect id="pill" x="14" y="28" width="92" height="64" rx="32" fill="#fff" stroke="#ff5a4e" stroke-width="6"/>
      <ellipse class="eye" cx="45" cy="56" rx="6" ry="8" fill="#111"/>
      <ellipse class="eye" cx="75" cy="56" rx="6" ry="8" fill="#111"/>
      <g id="brows" stroke="#111" stroke-width="4" stroke-linecap="round">
        <line x1="36" y1="42" x2="50" y2="48"/>
        <line x1="84" y1="42" x2="70" y2="48"/>
      </g>
      <path id="mouth" d="M44 58 Q60 76 76 58" fill="none" stroke="#111" stroke-width="4" stroke-linecap="round"/>
    </g>
  </svg>
  <div id="mood-label">Tayyor, qachon tayyor bo'lsang</div>
  <div id="progressBox">
    <div id="progLabel">Daraja: <b id="pct">0</b>%</div>
    <div id="bar"><div id="fill"></div></div>
  </div>
  <div id="tools">
    <button class="btn" id="speakBtn">🔊 Ovoz: yoqilgan</button>
    <button class="btn" id="resetProg">Darajani nolga qaytarish</button>
  </div>
</div>

<div id="main">
  <header>
    <button id="toggle" title="Menyu">☰</button>
    <h1>Hoppy</h1>
    <button class="tab" data-mode="lesson">📘 Dars</button>
    <button class="tab" data-mode="homework">📝 Uyga vazifa</button>
    <button class="tab" data-mode="speaking">🗣 Gapirish</button>
    <button class="tab" data-mode="ielts">🎯 IELTS</button>
    <select id="model">
      <option value="llama-3.3-70b-versatile">Llama 3.3 70B</option>
      <option value="llama-3.1-8b-instant">Llama 3.1 8B (tez)</option>
    </select>
    <button id="clear">Tozalash</button>
  </header>
  <div id="chat"></div>
  <form id="f">
    <button id="mic" type="button" title="Gapirish">🎤</button>
    <textarea id="inp" rows="1" placeholder="Yozing yoki 🎤 bosib gapiring... (Enter - yuborish)"></textarea>
    <button id="send" type="submit">Yuborish</button>
  </form>
</div>

<script>
const $ = id => document.getElementById(id);
const MOUTH = {
  happy: "M44 58 Q60 76 76 58", proud: "M44 58 Q60 78 76 58",
  neutral: "M46 64 L74 64", angry: "M44 70 Q60 54 76 70",
  talk1: "M46 58 Q60 84 74 58", talk2: "M46 62 Q60 68 74 62"
};
const LABEL = {happy:"Zo'r! 😊", proud:"Ajoyib! 🌟", neutral:"Tayyor, qachon tayyor bo'lsang", angry:"Yana xato! 😡", talking:"Gapiryapman... 💬"};
const INTRO = {
  lesson: "Salom! Men Hoppy. Avval darajangizni bilaman: ingliz tilini qayerdan boshlaysiz? Hech narsa bilmasangiz ham mayli, 0 dan boshlaymiz. Lekin xato qilsangiz jahlim chiqadi!",
  homework: "Uyga vazifa bo'limi. 'Vazifa ber' deb yozing, men 5 ta mashq beraman. Javoblaringizni yozasiz, men tekshiraman. Yaxshi bajaring!",
  speaking: "Gapirish bo'limi. Mikrofonni bosing va inglizcha gapiring. Men javob beraman va xatolaringizni tuzataman. Boshlaymizmi?",
  ielts: "IELTS bo'limi. 'Writing Task 2 ber' yoki 'Speaking part 1 boshlaymiz' deb yozing. Band balingizni baholayman."
};
const MODE_NAMES = {lesson:"Dars", homework:"Uyga vazifa", speaking:"Gapirish", ielts:"IELTS"};

let mode = localStorage.getItem('hoppy_mode') || 'lesson';
let chats = JSON.parse(localStorage.getItem('hoppy_chats') || '{}');
let progress = +localStorage.getItem('hoppy_progress') || 0;
let busy = false, speakOn = true, curMood = 'neutral', talkTimer = null;

function save() {
  localStorage.setItem('hoppy_mode', mode);
  localStorage.setItem('hoppy_chats', JSON.stringify(chats));
  localStorage.setItem('hoppy_progress', progress);
}

function setMood(m) {
  curMood = m;
  $('face').setAttribute('class', m);
  if (!talkTimer) $('mouth').setAttribute('d', MOUTH[m] || MOUTH.neutral);
  $('mood-label').textContent = LABEL[m] || LABEL.neutral;
}

function startTalk() {
  $('face').setAttribute('class', 'talking');
  $('mood-label').textContent = LABEL.talking;
  let t = 0;
  clearInterval(talkTimer);
  talkTimer = setInterval(() => $('mouth').setAttribute('d', (t++ % 2) ? MOUTH.talk1 : MOUTH.talk2), 150);
}

function stopTalk() {
  clearInterval(talkTimer);
  talkTimer = null;
  setMood(curMood);
}

function updateProgress() {
  $('pct').textContent = progress;
  $('fill').style.width = progress + '%';
}

function renderAll() {
  document.querySelectorAll('.tab').forEach(b => b.classList.toggle('active', b.dataset.mode === mode));
  $('speakBtn').textContent = speakOn ? '🔊 Ovoz: yoqilgan' : '🔇 Ovoz: o\'chirilgan';
  $('model').value = $('model').value || 'llama-3.3-70b-versatile';
  const box = $('chat');
  box.innerHTML = '';
  const list = chats[mode] || [];
  if (list.length === 0) addBubble('bot', INTRO[mode]);
  list.forEach(m => addBubble(m.role === 'user' ? 'user' : 'bot', m.content));
  box.scrollTop = box.scrollHeight;
  updateProgress();
}

function addBubble(role, text) {
  const d = document.createElement('div');
  d.className = 'msg ' + (role === 'user' ? 'user' : 'bot');
  d.textContent = text;
  $('chat').appendChild(d);
  return d;
}

function parseTags(full) {
  const mood = (full.match(/\[mood:(\w+)\]/) || [])[1] || null;
  const scoreM = full.match(/\[score:(\d+)\]/);
  const score = scoreM ? Math.min(100, +scoreM[1]) : null;
  const clean = full.replace(/\[mood:\w+\]/g, '').replace(/\[score:\d+\]/g, '').trim();
  return {mood, score, clean};
}

function speak(text) {
  if (!speakOn || !('speechSynthesis' in window)) return;
  const plain = text.replace(/[*#`_]/g, '');
  const u = new SpeechSynthesisUtterance(plain);
  u.lang = 'en-US';
  u.rate = 0.95;
  u.onstart = startTalk;
  u.onend = stopTalk;
  u.onerror = stopTalk;
  speechSynthesis.cancel();
  speechSynthesis.speak(u);
}

async function send() {
  const input = $('inp');
  const text = input.value.trim();
  if (!text || busy) return;
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
      const {clean} = parseTags(full);
      el.textContent = clean;
      $('chat').scrollTop = $('chat').scrollHeight;
    }
  } catch (err) {
    full = '[mood:angry] Server bilan aloqa yo\'q.';
    el.textContent = 'Server bilan aloqa yo\'q.';
  }

  const {mood, score, clean} = parseTags(full);
  list[list.length - 1].content = clean;
  if (score !== null && score > progress) progress = score;
  if (score !== null && score < 40 && score > 0) progress = progress;
  save();
  updateProgress();
  setMood(mood || 'neutral');
  if (mode === 'speaking' || speakOn) speak(clean);
  busy = false;
  $('send').disabled = false;
  input.focus();
}

// Mikrofon
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

// Boshqaruv
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
  if (!confirm('Bu bo\'limdagi suhbat o\'chirilsinmi?')) return;
  chats[mode] = [];
  save();
  renderAll();
};
$('speakBtn').onclick = () => { speakOn = !speakOn; if (!speakOn) speechSynthesis.cancel(); renderAll(); };
$('resetProg').onclick = () => { if (confirm('Daraja nolga qaytsinmi?')) { progress = 0; save(); updateProgress(); } };
$('mascot').onclick = () => { setMood(curMood === 'angry' ? 'happy' : 'proud'); setTimeout(() => setMood('neutral'), 1500); };
$('toggle').onclick = () => $('side').classList.toggle('hidden');

setMood('neutral');
renderAll();
</script>
</body>
</html>
"""

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
