import json
import os
import uuid
from typing import Dict

from aiohttp import web
from config.logger import setup_logging
from core.providers.tts.dto.dto import ContentType, SentenceType, TTSMessageDTO
from core.providers.tools.device_mcp.mcp_handler import call_mcp_tool
from plugins_func.functions.play_music import _download_online_music, _find_cached_file, _get_music_dir

TAG = __name__
logger = setup_logging()

# Global registry of live ESP32 WebSocket ConnectionHandlers (device_id -> handler)
ACTIVE_CONNECTIONS: Dict[str, object] = {}

SETTINGS_FILE = os.path.abspath("./data/web_settings.json")


def _load_settings(config: dict) -> dict:
    defaults = {
        "tts_engine": "google",
        "tts_rate": 0,
        "tts_pitch": 0,
        "llm_model": config.get("LLM", {}).get("GroqLLM", {}).get("model_name", "qwen/qwen3.8-27b"),
        "groq_api_key": "",
        "prompt": config.get("prompt", ""),
        "wake_word": "Jarvis",
        "gpio_motor_l_in1": 40,
        "gpio_motor_l_in2": 42,
        "gpio_motor_r_in3": 2,
        "gpio_motor_r_in4": 1,
        "gpio_lamp": 3,
        "gpio_touch": 17,
        "touch_mode": "hold_to_talk",
        "gpio_oled_sda": 9,
        "gpio_oled_scl": 46,
    }
    try:
        if os.path.exists(SETTINGS_FILE):
            with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                saved = json.load(f)
                defaults.update(saved)
    except Exception as e:
        logger.bind(tag=TAG).warning(f"Cannot load web_settings.json: {e}")
    return defaults


def _save_settings(data: dict):
    os.makedirs(os.path.dirname(SETTINGS_FILE), exist_ok=True)
    with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _get_active_conn(device_id: str | None = None):
    if not ACTIVE_CONNECTIONS:
        return None
    if device_id and device_id in ACTIVE_CONNECTIONS:
        return ACTIVE_CONNECTIONS[device_id]
    # Return the most recently connected active device
    return list(ACTIVE_CONNECTIONS.values())[-1]


DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="vi">
<head>
<meta charset="UTF-8" />
<meta name="viewport" content="width=device-width, initial-scale=1.0" />
<title>XiaoZhi Robot — Bảng Điều Khiển & Cài Đặt</title>
<style>
  :root { --bg:#0f172a; --card:#1e293b; --border:#334155; --text:#f1f5f9; --muted:#94a3b8; --pri:#3b82f6; --ok:#22c55e; --err:#ef4444; --warn:#f59e0b; }
  * { box-sizing:border-box; font-family: system-ui, -apple-system, Segoe UI, Roboto, sans-serif; }
  body { margin:0; background:var(--bg); color:var(--text); min-height:100vh; }
  header { background:var(--card); border-bottom:1px solid var(--border); padding:14px 18px; display:flex; align-items:center; justify-content:space-between; flex-wrap:wrap; gap:10px; }
  header h1 { margin:0; font-size:18px; display:flex; align-items:center; gap:8px; }
  .badge { padding:4px 10px; border-radius:999px; font-size:12px; font-weight:600; background:#334155; color:var(--muted); }
  .badge.on { background:rgba(34,197,94,.2); color:#4ade80; border:1px solid rgba(34,197,94,.4); }
  .tabs { display:flex; gap:6px; padding:12px 16px 0; max-width:920px; margin:0 auto; flex-wrap:wrap; }
  .tab { background:var(--card); border:1px solid var(--border); color:var(--muted); padding:9px 16px; border-radius:10px 10px 0 0; cursor:pointer; font-weight:600; font-size:14px; }
  .tab.active { background:var(--pri); color:#fff; border-color:var(--pri); }
  main { max-width:920px; margin:0 auto; padding:16px; }
  .panel { display:none; }
  .panel.active { display:grid; grid-template-columns:repeat(auto-fit, minmax(290px, 1fr)); gap:16px; }
  .card { background:var(--card); border:1px solid var(--border); border-radius:14px; padding:16px; }
  .card h2 { margin:0 0 12px; font-size:15px; color:#93c5fd; }
  .dpad { display:grid; grid-template-columns:repeat(3, 1fr); gap:8px; max-width:240px; margin:10px auto; }
  .btn { background:#334155; color:#fff; border:none; border-radius:10px; padding:12px; font-size:15px; font-weight:600; cursor:pointer; transition:.15s; user-select:none; -webkit-user-select:none; touch-action:none; }
  .btn:hover { background:#475569; }
  .btn:active, .btn.holding { transform:scale(.95); filter:brightness(1.2); }
  .btn.pri { background:var(--pri); }
  .btn.pri:hover { background:#2563eb; }
  .btn.ok { background:#16a34a; }
  .btn.err { background:#dc2626; }
  .btn.warn { background:#d97706; }
  label { display:block; font-size:13px; color:var(--muted); margin:10px 0 4px; }
  input, select, textarea { width:100%; background:#0f172a; color:var(--text); border:1px solid var(--border); border-radius:8px; padding:9px 10px; font-size:14px; }
  textarea { min-height:110px; resize:vertical; }
  .row { display:flex; gap:8px; align-items:center; }
  .grid2 { display:grid; grid-template-columns:1fr 1fr; gap:10px; }
  .toast { position:fixed; bottom:18px; right:18px; background:#0f172a; border:1px solid var(--pri); color:#fff; padding:10px 16px; border-radius:10px; font-size:13px; display:none; z-index:99; box-shadow:0 8px 24px rgba(0,0,0,.4); }
  .hint { font-size:12px; color:var(--muted); margin-top:6px; line-height:1.45; }
</style>
</head>
<body>
<header>
  <h1>🤖 XiaoZhi Robot Control</h1>
  <span id="devStatus" class="badge">Đang kiểm tra kết nối...</span>
</header>

<div class="tabs">
  <button class="tab active" onclick="showTab('ctrl', this)">🎮 Điều khiển Robot</button>
  <button class="tab" onclick="showTab('ai', this)">🎙️ Giọng nói & AI</button>
  <button class="tab" onclick="showTab('hw', this)">🔌 Chân GPIO & Touch</button>
</div>

<main>
  <!-- TAB 1: CONTROL -->
  <section id="tab-ctrl" class="panel active">
    <div class="card">
      <h2>🚗 Lái Robot (Giữ phím để chạy, thả ra là dừng)</h2>
      <div class="dpad">
        <div></div>
        <button class="btn pri rc-btn" data-act="forward">⬆️ Tiến</button>
        <div></div>
        <button class="btn pri rc-btn" data-act="turn_left">⬅️ Trái</button>
        <button class="btn err" onclick="stopDrive()">⏹️ Dừng</button>
        <button class="btn pri rc-btn" data-act="turn_right">➡️ Phải</button>
        <div></div>
        <button class="btn pri rc-btn" data-act="backward">⬇️ Lùi</button>
        <div></div>
      </div>
      <p class="hint" style="text-align:center;">Hỗ trợ nhấn giữ trên màn hình cảm ứng, chuột, hoặc phím mũi tên / <b>W A S D</b> trên bàn phím!</p>
    </div>

    <div class="card">
      <h2>💡 Đèn LED & Âm lượng</h2>
      <div class="row" style="margin-bottom:14px;">
        <button class="btn ok" style="flex:1" onclick="lamp('on')">💡 Bật Đèn</button>
        <button class="btn" style="flex:1" onclick="lamp('off')">🌑 Tắt Đèn</button>
      </div>
      <label>Âm lượng loa: <b id="volVal">70</b>%</label>
      <div class="row">
        <input type="range" id="volume" min="0" max="100" step="5" value="70" oninput="document.getElementById('volVal').textContent=this.value" />
        <button class="btn pri" onclick="setVol()">Đặt</button>
      </div>

      <h2 style="margin-top:20px;">🎵 Mở nhạc trực tiếp ra loa Robot</h2>
      <div class="row">
        <input type="text" id="songInput" placeholder="VD: Nơi Này Có Anh Sơn Tùng" />
        <button class="btn ok" onclick="playMusic()">Phát</button>
      </div>
      <p class="hint">Lưu ý: Robot cần đang kết nối phiên (đã đánh thức hoặc đang trò chuyện) để nhận lệnh điều khiển tức thì.</p>
    </div>
  </section>

  <!-- TAB 2: VOICE & AI -->
  <section id="tab-ai" class="panel">
    <div class="card">
      <h2>🔊 Cài đặt Giọng nói (Cố định 1 giọng, không đổi lung tung)</h2>
      <label>Kiểu giọng nói</label>
      <select id="tts_engine">
        <option value="google">Google Tiếng Việt (Nhanh, ổn định nhất)</option>
        <option value="edge_female">Microsoft Hoài My — Nữ Tiếng Việt</option>
        <option value="edge_male">Microsoft Nam Minh — Nam Tiếng Việt</option>
      </select>
      <label>Tốc độ nói: <b id="rateVal">0</b>%</label>
      <input type="range" id="tts_rate" min="-30" max="50" step="5" value="0" oninput="document.getElementById('rateVal').textContent=this.value" />
      <label>Cao độ giọng (EdgeTTS): <b id="pitchVal">0</b> Hz</label>
      <input type="range" id="tts_pitch" min="-20" max="20" step="2" value="0" oninput="document.getElementById('pitchVal').textContent=this.value" />
    </div>

    <div class="card">
      <h2>🧠 Mô hình AI, API Key & Vai trò trợ lý</h2>
      <label>Model AI (Groq)</label>
      <select id="llm_model">
        <option value="qwen/qwen3.8-27b">Qwen 3.8 27B (Nhanh, chuẩn Tiếng Việt)</option>
        <option value="qwen/qwen3.6-27b">Qwen 3.6 27B</option>
        <option value="openai/gpt-oss-120b">OpenAI GPT-OSS 120B (Thông minh cao)</option>
        <option value="openai/gpt-oss-20b">OpenAI GPT-OSS 20B (Siêu nhanh)</option>
      </select>
      <label>Groq API Key (để trống nếu giữ nguyên key hiện tại)</label>
      <input type="password" id="groq_api_key" placeholder="gsk_..." />
      <label>Vai trò & Tính cách trợ lý (System Prompt)</label>
      <textarea id="prompt"></textarea>
      <button class="btn pri" style="width:100%;margin-top:12px;" onclick="saveSettings()">💾 Lưu & Áp dụng ngay</button>
    </div>
  </section>

  <!-- TAB 3: HARDWARE GPIO & TOUCH -->
  <section id="tab-hw" class="panel">
    <div class="card">
      <h2>🔌 Cấu hình chân GPIO (ESP32-S3)</h2>
      <div class="grid2">
        <div><label>Động cơ trái IN1 (Tiến)</label><input type="number" id="gpio_motor_l_in1" value="40" /></div>
        <div><label>Động cơ trái IN2 (Lùi)</label><input type="number" id="gpio_motor_l_in2" value="42" /></div>
        <div><label>Động cơ phải IN3 (Tiến)</label><input type="number" id="gpio_motor_r_in3" value="2" /></div>
        <div><label>Động cơ phải IN4 (Lùi)</label><input type="number" id="gpio_motor_r_in4" value="1" /></div>
        <div><label>Đèn LED (mức 1 sáng)</label><input type="number" id="gpio_lamp" value="3" /></div>
        <div><label>Cảm biến Touch (TTP223)</label><input type="number" id="gpio_touch" value="17" /></div>
        <div><label>OLED SDA</label><input type="number" id="gpio_oled_sda" value="9" /></div>
        <div><label>OLED SCL</label><input type="number" id="gpio_oled_scl" value="46" /></div>
      </div>
    </div>

    <div class="card">
      <h2>👆 Chức năng Touch & Tên đánh thức</h2>
      <label>Chức năng cảm biến Touch (GPIO 17)</label>
      <select id="touch_mode">
        <option value="hold_to_talk">Nhấn giữ để nói, thả ra để gửi</option>
        <option value="toggle_chat">Chạm 1 lần để Bật / Tắt trò chuyện</option>
        <option value="toggle_lamp">Chạm 1 lần để Bật / Tắt đèn LED</option>
      </select>
      <label>Từ đánh thức (Wake Word)</label>
      <select id="wake_word">
        <option value="Jarvis">Jarvis (Mặc định hiện tại)</option>
        <option value="Hi ESP">Hi ESP</option>
        <option value="Alexa">Alexa</option>
        <option value="Ni Hao Xiao Zhi">Ni Hao Xiao Zhi</option>
      </select>
      <p class="hint">Ghi chú: Từ đánh thức offline phụ thuộc model WakeNet đã nạp trong phân vùng assets của ESP32. Các chân GPIO & chế độ Touch sẽ được gửi xuống lưu vào NVS của ESP32 và tự khởi động lại.</p>
      <button class="btn warn" style="width:100%;margin-top:10px;" onclick="saveHardware()">⚡ Lưu & Gửi cấu hình xuống ESP32</button>
    </div>
  </section>
</main>

<div id="toast" class="toast"></div>

<script>
function toast(msg, ok=true){
  const el = document.getElementById('toast');
  el.textContent = msg;
  el.style.borderColor = ok ? '#22c55e' : '#ef4444';
  el.style.display = 'block';
  clearTimeout(window._tt);
  window._tt = setTimeout(()=> el.style.display='none', 3200);
}
function showTab(id, btn){
  document.querySelectorAll('.panel').forEach(p=>p.classList.remove('active'));
  document.querySelectorAll('.tab').forEach(t=>t.classList.remove('active'));
  document.getElementById('tab-'+id).classList.add('active');
  btn.classList.add('active');
}
async function api(path, body){
  const opt = body ? {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)} : {};
  const r = await fetch(path, opt);
  const d = await r.json();
  if(!r.ok || d.ok === false) throw new Error(d.error || 'Lỗi kết nối');
  return d;
}
async function refreshStatus(){
  try{
    const d = await api('/api/status');
    const el = document.getElementById('devStatus');
    if(d.devices && d.devices.length > 0){
      el.textContent = '🟢 Online: ' + d.devices.join(', ');
      el.classList.add('on');
    } else {
      el.textContent = '⚪ Chờ Robot kết nối (hãy đánh thức Robot)';
      el.classList.remove('on');
    }
  }catch(e){}
}
async function loadSettings(){
  try{
    const d = await api('/api/settings');
    const s = d.settings || {};
    ['tts_engine','tts_rate','tts_pitch','llm_model','prompt','wake_word','touch_mode',
     'gpio_motor_l_in1','gpio_motor_l_in2','gpio_motor_r_in3','gpio_motor_r_in4',
     'gpio_lamp','gpio_touch','gpio_oled_sda','gpio_oled_scl'].forEach(k=>{
      if(s[k] !== undefined && document.getElementById(k)) document.getElementById(k).value = s[k];
    });
    document.getElementById('rateVal').textContent = s.tts_rate || 0;
    document.getElementById('pitchVal').textContent = s.tts_pitch || 0;
  }catch(e){}
}
let activeAct = null;
let holdTimer = null;
async function sendDrive(action, duration_ms){
  try{
    await api('/api/control/motor', {action, duration_ms});
  }catch(e){ toast(e.message, false); }
}
function startDrive(action, el){
  if(activeAct === action) return;
  activeAct = action;
  if(el) el.classList.add('holding');
  clearInterval(holdTimer);
  sendDrive(action, 10000);
  holdTimer = setInterval(()=>{
    if(activeAct === action) sendDrive(action, 10000);
  }, 7000);
}
function stopDrive(el){
  clearInterval(holdTimer);
  holdTimer = null;
  document.querySelectorAll('.rc-btn.holding').forEach(b=>b.classList.remove('holding'));
  if(activeAct !== null || !el){
    activeAct = null;
    sendDrive('stop', 100);
  }
}
document.querySelectorAll('.rc-btn').forEach(btn=>{
  const act = btn.dataset.act;
  btn.addEventListener('pointerdown', (e)=>{ e.preventDefault(); btn.setPointerCapture(e.pointerId); startDrive(act, btn); });
  btn.addEventListener('pointerup', (e)=>{ e.preventDefault(); stopDrive(btn); });
  btn.addEventListener('pointercancel', (e)=>{ e.preventDefault(); stopDrive(btn); });
  btn.addEventListener('contextmenu', (e)=>e.preventDefault());
});
const keyMap = { ArrowUp:'forward', KeyW:'forward', ArrowDown:'backward', KeyS:'backward', ArrowLeft:'turn_left', KeyA:'turn_left', ArrowRight:'turn_right', KeyD:'turn_right' };
window.addEventListener('keydown', (e)=>{
  if(e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') return;
  if(keyMap[e.code] && !e.repeat){
    e.preventDefault();
    const btn = document.querySelector(`.rc-btn[data-act="${keyMap[e.code]}"]`);
    startDrive(keyMap[e.code], btn);
  }
});
window.addEventListener('keyup', (e)=>{
  if(keyMap[e.code] && activeAct === keyMap[e.code]){
    e.preventDefault();
    stopDrive();
  }
});
async function lamp(state){
  try{
    await api('/api/control/lamp', {state});
    toast(state==='on' ? '💡 Đã bật đèn LED' : '🌑 Đã tắt đèn LED');
  }catch(e){ toast(e.message, false); }
}
async function setVol(){
  try{
    const volume = parseInt(document.getElementById('volume').value);
    await api('/api/control/volume', {volume});
    toast('🔊 Đã đặt âm lượng ' + volume + '%');
  }catch(e){ toast(e.message, false); }
}
async function playMusic(){
  const song_name = document.getElementById('songInput').value.trim();
  if(!song_name) return toast('Hãy nhập tên bài hát', false);
  try{
    toast('🎵 Đang tìm & phát: ' + song_name + '...');
    await api('/api/control/music', {song_name});
    toast('🎵 Đang phát trên loa Robot!');
  }catch(e){ toast(e.message, false); }
}
async function saveSettings(){
  try{
    const payload = {
      tts_engine: document.getElementById('tts_engine').value,
      tts_rate: parseInt(document.getElementById('tts_rate').value),
      tts_pitch: parseInt(document.getElementById('tts_pitch').value),
      llm_model: document.getElementById('llm_model').value,
      prompt: document.getElementById('prompt').value,
    };
    const key = document.getElementById('groq_api_key').value.trim();
    if(key) payload.groq_api_key = key;
    await api('/api/settings', payload);
    toast('✅ Đã lưu & áp dụng cài đặt AI / Giọng nói!');
  }catch(e){ toast(e.message, false); }
}
async function saveHardware(){
  try{
    const payload = {
      gpio_motor_l_in1: parseInt(document.getElementById('gpio_motor_l_in1').value),
      gpio_motor_l_in2: parseInt(document.getElementById('gpio_motor_l_in2').value),
      gpio_motor_r_in3: parseInt(document.getElementById('gpio_motor_r_in3').value),
      gpio_motor_r_in4: parseInt(document.getElementById('gpio_motor_r_in4').value),
      gpio_lamp: parseInt(document.getElementById('gpio_lamp').value),
      gpio_touch: parseInt(document.getElementById('gpio_touch').value),
      gpio_oled_sda: parseInt(document.getElementById('gpio_oled_sda').value),
      gpio_oled_scl: parseInt(document.getElementById('gpio_oled_scl').value),
      touch_mode: document.getElementById('touch_mode').value,
      wake_word: document.getElementById('wake_word').value,
    };
    await api('/api/settings', payload);
    const res = await api('/api/control/hardware', payload);
    toast(res.message || '⚡ Đã lưu & gửi cấu hình xuống ESP32!');
  }catch(e){ toast(e.message, false); }
}
loadSettings();
refreshStatus();
setInterval(refreshStatus, 4000);
</script>
</body>
</html>
"""


class WebPanelHandler:
    def __init__(self, config: dict):
        self.config = config
        self._apply_saved_settings_to_config()

    def _apply_saved_settings_to_config(self):
        s = _load_settings(self.config)
        if s.get("prompt"):
            self.config["prompt"] = s["prompt"]
        if s.get("llm_model") and "LLM" in self.config and "GroqLLM" in self.config["LLM"]:
            self.config["LLM"]["GroqLLM"]["model_name"] = s["llm_model"]
        if s.get("groq_api_key"):
            if "LLM" in self.config and "GroqLLM" in self.config["LLM"]:
                self.config["LLM"]["GroqLLM"]["api_key"] = s["groq_api_key"]
            if "ASR" in self.config and "GroqASR" in self.config["ASR"]:
                self.config["ASR"]["GroqASR"]["api_key"] = s["groq_api_key"]

    async def handle_index(self, request: web.Request):
        return web.Response(text=DASHBOARD_HTML, content_type="text/html")

    async def handle_status(self, request: web.Request):
        return web.json_response({"ok": True, "devices": list(ACTIVE_CONNECTIONS.keys())})

    async def handle_get_settings(self, request: web.Request):
        s = _load_settings(self.config)
        s_safe = dict(s)
        s_safe["groq_api_key"] = ""  # Never expose stored key back to browser
        return web.json_response({"ok": True, "settings": s_safe})

    async def handle_post_settings(self, request: web.Request):
        try:
            body = await request.json()
            current = _load_settings(self.config)
            for k, v in body.items():
                if k == "groq_api_key" and not v:
                    continue
                current[k] = v
            _save_settings(current)
            self._apply_saved_settings_to_config()

            # Apply live to any currently connected sessions
            for conn in list(ACTIVE_CONNECTIONS.values()):
                if body.get("prompt"):
                    conn.config["prompt"] = body["prompt"]
                if body.get("llm_model") and getattr(conn, "llm", None):
                    conn.llm.model_name = body["llm_model"]
                if body.get("groq_api_key"):
                    if getattr(conn, "llm", None):
                        conn.llm.api_key = body["groq_api_key"]
                    if getattr(conn, "asr", None):
                        conn.asr.api_key = body["groq_api_key"]

            return web.json_response({"ok": True})
        except Exception as e:
            return web.json_response({"ok": False, "error": str(e)}, status=400)

    async def handle_motor(self, request: web.Request):
        conn = _get_active_conn()
        if not conn:
            return web.json_response({"ok": False, "error": "Robot chưa kết nối (hãy đánh thức Robot trước)"}, status=400)
        try:
            body = await request.json()
            action = body.get("action", "stop")
            duration_ms = int(body.get("duration_ms", 1000))
            res = await call_mcp_tool(conn, conn.mcp_client, "self_motor_drive", json.dumps({"action": action, "duration_ms": duration_ms}), timeout=10)
            return web.json_response({"ok": True, "result": res})
        except Exception as e:
            return web.json_response({"ok": False, "error": str(e)}, status=500)

    async def handle_lamp(self, request: web.Request):
        conn = _get_active_conn()
        if not conn:
            return web.json_response({"ok": False, "error": "Robot chưa kết nối (hãy đánh thức Robot trước)"}, status=400)
        try:
            body = await request.json()
            tool = "self_lamp_turn_on" if body.get("state") == "on" else "self_lamp_turn_off"
            res = await call_mcp_tool(conn, conn.mcp_client, tool, "{}", timeout=10)
            return web.json_response({"ok": True, "result": res})
        except Exception as e:
            return web.json_response({"ok": False, "error": str(e)}, status=500)

    async def handle_volume(self, request: web.Request):
        conn = _get_active_conn()
        if not conn:
            return web.json_response({"ok": False, "error": "Robot chưa kết nối (hãy đánh thức Robot trước)"}, status=400)
        try:
            body = await request.json()
            vol = max(0, min(100, int(body.get("volume", 70))))
            res = await call_mcp_tool(conn, conn.mcp_client, "self_audio_speaker_set_volume", json.dumps({"volume": vol}), timeout=10)
            return web.json_response({"ok": True, "result": res})
        except Exception as e:
            return web.json_response({"ok": False, "error": str(e)}, status=500)

    async def handle_music(self, request: web.Request):
        conn = _get_active_conn()
        if not conn:
            return web.json_response({"ok": False, "error": "Robot chưa kết nối (hãy đánh thức Robot trước)"}, status=400)
        try:
            body = await request.json()
            song_name = (body.get("song_name") or "").strip()
            if not song_name:
                return web.json_response({"ok": False, "error": "Thiếu tên bài hát"}, status=400)
            music_dir = _get_music_dir(conn)
            music_path = _find_cached_file(song_name, music_dir)
            if not music_path:
                music_path = await _download_online_music(song_name, music_dir, conn.logger)

            sid = uuid.uuid4().hex
            conn.sentence_id = sid
            conn.client_abort = False
            intro_text = f"Đang mở bài {song_name}, mời bạn cùng nghe nhé."
            conn.tts.store_tts_text(sid, intro_text)
            conn.tts.tts_text_queue.put(
                TTSMessageDTO(sentence_id=sid, sentence_type=SentenceType.FIRST, content_type=ContentType.ACTION)
            )
            conn.tts.tts_text_queue.put(
                TTSMessageDTO(sentence_id=sid, sentence_type=SentenceType.MIDDLE, content_type=ContentType.TEXT, content_detail=intro_text)
            )
            conn.tts.tts_text_queue.put(
                TTSMessageDTO(sentence_id=sid, sentence_type=SentenceType.MIDDLE, content_type=ContentType.FILE, content_file=music_path)
            )
            conn.tts.tts_text_queue.put(
                TTSMessageDTO(sentence_id=sid, sentence_type=SentenceType.LAST, content_type=ContentType.ACTION)
            )
            return web.json_response({"ok": True})
        except Exception as e:
            return web.json_response({"ok": False, "error": str(e)}, status=500)

    async def handle_hardware(self, request: web.Request):
        conn = _get_active_conn()
        if not conn:
            return web.json_response({"ok": True, "message": "Đã lưu trên Server (chưa có Robot online để gửi xuống NVS)"})
        try:
            body = await request.json()
            if conn.mcp_client.has_tool("self_hardware_set_config"):
                res = await call_mcp_tool(conn, conn.mcp_client, "self_hardware_set_config", json.dumps(body), timeout=10)
                return web.json_response({"ok": True, "message": f"Đã gửi cấu hình xuống ESP32 & đang khởi động lại! ({res})"})
            return web.json_response({"ok": True, "message": "Đã lưu trên Server! Cần nạp Phần 2 firmware ESP32 để nhận đổi GPIO từ xa."})
        except Exception as e:
            return web.json_response({"ok": False, "error": str(e)}, status=500)
