import os
import math
import uuid
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from flask import Flask, request, redirect, url_for, send_from_directory, render_template_string, abort, jsonify
from PIL import Image

# -----------------------------
# Config
# -----------------------------
APP_HOST = "127.0.0.1"
APP_PORT = 5000

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UPLOAD_DIR = os.path.join(BASE_DIR, ".cache/uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)

ALLOWED_EXT = {".png", ".jpg", ".jpeg", ".webp"}

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 25 * 1024 * 1024  # 25MB

# -----------------------------
# In-memory "database"
# -----------------------------
@dataclass
class Round:
    id: str
    map_filename: str
    map_size: Tuple[int, int]  # (w,h) in pixels
    answer_xy: Optional[Tuple[int, int]] = None
    guesses: Dict[str, Tuple[int, int]] = field(default_factory=dict)  # player -> (x,y)

@dataclass
class GameState:
    players: List[str] = field(default_factory=list)
    rounds: List[Round] = field(default_factory=list)
    current_round_index: int = 0

STATE = GameState()

# -----------------------------
# Helpers
# -----------------------------
def ext_ok(filename: str) -> bool:
    _, ext = os.path.splitext(filename.lower())
    return ext in ALLOWED_EXT

def normalize_player_name(name: str) -> str:
    return (name or "").strip().casefold()

def player_exists(name: str) -> bool:
    n = normalize_player_name(name)
    return any(normalize_player_name(p) == n for p in STATE.players)

def save_upload(file_storage) -> str:
    if not file_storage or file_storage.filename == "":
        raise ValueError("No file selected.")
    if not ext_ok(file_storage.filename):
        raise ValueError("Unsupported file type. Use png/jpg/jpeg/webp.")
    _, ext = os.path.splitext(file_storage.filename.lower())
    safe_name = f"{uuid.uuid4().hex}{ext}"
    path = os.path.join(UPLOAD_DIR, safe_name)
    file_storage.save(path)
    return safe_name

def pixel_distance(a: Tuple[int,int], b: Tuple[int,int]) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])

def score_from_distance(d: float, map_size: Tuple[int,int]) -> int:
    """
    Distance-based scoring using pixel distance on the uploaded map.
    Normalized by map diagonal so large images don't collapse to 0.
    Minimum 1 point for any submitted guess.
    """
    w, h = map_size
    diag = math.hypot(w, h)
    scale = max(1.0, diag / 2.0)
    raw = 1000.0 * math.exp(-d / scale)
    return max(1, int(round(raw)))

def get_round(round_id: str) -> Round:
    rd = next((x for x in STATE.rounds if x.id == round_id), None)
    if not rd:
        abort(404)
    return rd

def current_round() -> Optional[Round]:
    if not STATE.rounds:
        return None
    idx = min(max(STATE.current_round_index, 0), len(STATE.rounds) - 1)
    return STATE.rounds[idx]

# -----------------------------
# Routes: static uploads
# -----------------------------
@app.route("/uploads/<path:filename>")
def uploads(filename):
    return send_from_directory(UPLOAD_DIR, filename)

# -----------------------------
# API (AJAX)
# -----------------------------
@app.route("/api/add_player", methods=["POST"])
def api_add_player():
    data = request.get_json(force=True, silent=True) or {}
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"ok": False, "error": "Player name cannot be empty."}), 400
    if player_exists(name):
        return jsonify({"ok": False, "error": "That player name already exists."}), 400
    STATE.players.append(name)
    return jsonify({"ok": True, "players": STATE.players, "added": name})

@app.route("/api/guess", methods=["POST"])
def api_guess():
    data = request.get_json(force=True, silent=True) or {}
    round_id = data.get("round_id")
    player = data.get("player")
    x = data.get("x")
    y = data.get("y")

    if not round_id or not isinstance(round_id, str):
        return jsonify({"ok": False, "error": "Missing round_id."}), 400
    rd = get_round(round_id)

    if rd.answer_xy is None:
        return jsonify({"ok": False, "error": "Answer not set for this round."}), 400

    if not player or player not in STATE.players:
        return jsonify({"ok": False, "error": "Invalid player."}), 400

    try:
        x = int(x); y = int(y)
    except Exception:
        return jsonify({"ok": False, "error": "Invalid x/y."}), 400

    rd.guesses[player] = (x, y)
    guesses = {p: {"x": xy[0], "y": xy[1]} for p, xy in rd.guesses.items()}
    return jsonify({"ok": True, "guesses": guesses})

@app.route("/api/round_state/<round_id>", methods=["GET"])
def api_round_state(round_id):
    rd = get_round(round_id)
    guesses = {p: {"x": xy[0], "y": xy[1]} for p, xy in rd.guesses.items()}
    return jsonify({"ok": True, "players": STATE.players, "guesses": guesses})

# -----------------------------
# Pages (Flat design)
# -----------------------------
@app.route("/")
def home():
    return redirect(url_for("host"))

@app.route("/host", methods=["GET", "POST"])
def host():
    msg = ""
    if request.method == "POST":
        action = request.form.get("action")
        try:
            if action == "add_player":
                name = (request.form.get("player_name") or "").strip()
                if not name:
                    raise ValueError("Player name cannot be empty.")
                if player_exists(name):
                    raise ValueError("That player name already exists.")
                STATE.players.append(name)

            elif action == "remove_player":
                name = request.form.get("player_name")
                if name in STATE.players:
                    STATE.players.remove(name)
                for rd in STATE.rounds:
                    rd.guesses.pop(name, None)

            elif action == "add_round":
                map_file = request.files.get("map_image")
                filename = save_upload(map_file)

                path = os.path.join(UPLOAD_DIR, filename)
                with Image.open(path) as im:
                    w, h = im.size

                rd = Round(id=uuid.uuid4().hex, map_filename=filename, map_size=(w, h))
                STATE.rounds.append(rd)
                # auto-switch to the newest round
                STATE.current_round_index = len(STATE.rounds) - 1

            elif action == "reset_game":
                STATE.players = []
                STATE.rounds = []
                STATE.current_round_index = 0

            elif action == "goto_round":
                idx = int(request.form.get("round_index"))
                if idx < 0 or idx >= len(STATE.rounds):
                    raise ValueError("Invalid round index.")
                STATE.current_round_index = idx

            else:
                raise ValueError("Unknown action.")
        except Exception as e:
            msg = str(e)

    current = current_round()

    return render_template_string("""
<!doctype html>
<html><head>
  <meta charset="utf-8" />
  <title>Host - Local Geo Party</title>
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <style>
    :root{
      --bg:#f6f7fb; --card:#ffffff; --text:#101828; --muted:#475467;
      --border:#e4e7ec; --accent:#2563eb; --accent2:#1d4ed8;
      --good:#067647; --bad:#b42318; --radius:14px;
    }
    body{ margin:0; font-family: ui-sans-serif, system-ui, -apple-system, Segoe UI, Roboto, Arial, sans-serif;
      color:var(--text); background: linear-gradient(180deg, #f6f7fb, #eef2ff); padding:24px 18px; }
    .wrap{max-width:1150px; margin:0 auto;}
    h1{margin:0 0 6px 0; font-size:26px; letter-spacing:-0.02em;}
    .muted{color:var(--muted);}
    .grid{display:grid; grid-template-columns: 1fr 1fr; gap:14px;}
    @media (max-width: 980px){ .grid{grid-template-columns:1fr;} }
    .card{ background: var(--card); border-radius: var(--radius); padding: 18px; border: 1px solid var(--border); }
    .row{display:flex; gap:10px; flex-wrap:wrap; align-items:center;}
    input, select{
      padding:10px 12px; border-radius:12px; border:1px solid var(--border);
      background: white; color: var(--text); outline:none;
    }
    input::placeholder{color:#98a2b3;}
    button{
      padding:10px 12px; border-radius:12px; border:1px solid var(--border);
      background: var(--accent); color:white; cursor:pointer; font-weight:800;
    }
    button:hover{background: var(--accent2);}
    .btn-ghost{ background:white; color:var(--text); }
    .btn-ghost:hover{ background:#f2f4f7; }
    .err{color:var(--bad); margin:10px 0 0 0; font-weight:700;}
    table{width:100%; border-collapse:collapse;}
    th, td{padding:10px 6px; border-bottom: 1px solid var(--border); text-align:left; font-size:14px;}
    code{background: #f2f4f7; padding: 2px 6px; border-radius: 8px; border: 1px solid var(--border);}
    .tag{
      display:inline-flex; padding: 4px 10px; border-radius: 999px;
      background: #eff6ff; color: #1d4ed8; font-weight: 800; font-size: 12px; border: 1px solid #dbeafe;
    }
    img{max-width:100%; border-radius:12px; border:1px solid var(--border);}
    a{color:var(--accent); text-decoration:none; font-weight:900;}
    a:hover{text-decoration:underline;}
  </style>
</head><body>
  <div class="wrap">
    <div class="row" style="justify-content:space-between; align-items:flex-end;">
      <div>
        <h1>SIMP GeoGuesser</h1>
        <div class="muted">A Customizable GeoGuessing Game made for SIMP</div>
      </div>
      <div class="row">
        <a class="tag" href="{{url_for('leaderboard')}}">Leaderboard</a>
      </div>
    </div>

    {% if msg %}<p class="err">{{msg}}</p>{% endif %}

    <div class="grid" style="margin-top:14px;">
      <div class="card">
        <div class="tag">Players</div>
        <div class="muted" style="margin-top:8px;">Add Player names(Can add during round)</div>
        <form method="post" class="row" style="margin-top:12px;">
          <input type="hidden" name="action" value="add_player" />
          <input name="player_name" placeholder="e.g. Roger" />
          <button type="submit">Add</button>
        </form>
        {% if players %}
          <table style="margin-top:10px;">
            <tr><th>Name</th><th></th></tr>
            {% for p in players %}
            <tr>
              <td>{{p}}</td>
              <td>
                <form method="post" style="margin:0">
                  <input type="hidden" name="action" value="remove_player" />
                  <input type="hidden" name="player_name" value="{{p}}" />
                  <button class="btn-ghost" type="submit">Remove</button>
                </form>
              </td>
            </tr>
            {% endfor %}
          </table>
        {% else %}
          <p class="muted" style="margin-top:10px;">No players yet.</p>
        {% endif %}
      </div>

      <div class="card">
        <div class="tag">Rounds</div>
        <div class="muted" style="margin-top:8px;">Each round only needs <b>one image</b>: the overall map.</div>
        <form method="post" enctype="multipart/form-data" class="row" style="margin-top:12px;">
          <input type="hidden" name="action" value="add_round" />
          <input type="file" name="map_image" accept=".png,.jpg,.jpeg,.webp" required />
          <button type="submit">Add round</button>
        </form>
        {% if rounds %}
          <table style="margin-top:10px;">
            <tr><th>#</th><th>Map</th><th>Answer</th><th></th></tr>
            {% for rd in rounds %}
              <tr>
                <td>{{loop.index0}}</td>
                <td><code>{{rd.map_filename}}</code></td>
                <td>
                  {% if rd.answer_xy %}
                    <span class="tag" style="background:#ecfdf3;border-color:#abefc6;color:#067647;">set ✅</span>
                  {% else %}
                    <span class="tag" style="background:#fff1f3;border-color:#fecdd6;color:#b42318;">not set</span>
                  {% endif %}
                </td>
                <td>
                  <form method="post" style="margin:0">
                    <input type="hidden" name="action" value="goto_round" />
                    <input type="hidden" name="round_index" value="{{loop.index0}}" />
                    <button class="btn-ghost" type="submit">Open</button>
                  </form>
                </td>
              </tr>
            {% endfor %}
          </table>
        {% else %}
          <p class="muted" style="margin-top:10px;">No rounds yet.</p>
        {% endif %}
      </div>
    </div>

    <div class="card" style="margin-top:14px;">
      <div class="tag">Current Round</div>
      {% if not current %}
        <p class="muted" style="margin-top:10px;">Add at least 1 round to start.</p>
      {% else %}
        <div class="row" style="justify-content:space-between; margin-top:10px;">
          <div class="muted">Round index: <code>{{round_index}}</code> · Map size: <code>{{current.map_size[0]}}×{{current.map_size[1]}}</code></div>
          <div class="row">
            <a class="tag" href="{{url_for('set_answer', round_id=current.id)}}">Set answer</a>
            <a class="tag" href="{{url_for('play_round', round_id=current.id)}}">Play (zoom/pan)</a>
          </div>
        </div>
        
<div style="margin-top:12px;">
  <div class="muted" style="margin-bottom:10px;">Map preview</div>

  <div id="answerPreviewWrap" style="position:relative; display:inline-block; max-width:100%;">
    <img id="answerPreviewImg" src="{{url_for('uploads', filename=current.map_filename)}}" />

    {# Red answer pin overlay (host only) #}
    {% if current.answer_xy %}
      <svg id="answerPreviewSvg" style="position:absolute; inset:0; width:100%; height:100%; pointer-events:none;">
        <g id="answerPin">
          <path id="answerTail" d="" fill="rgba(220,38,38,0.98)" stroke="rgba(16,24,40,0.18)" stroke-width="2"></path>
          <circle id="answerOuter" cx="0" cy="0" r="11" fill="rgba(255,255,255,0.98)" stroke="rgba(16,24,40,0.18)" stroke-width="2"></circle>
          <circle id="answerInner" cx="0" cy="0" r="4" fill="rgba(220,38,38,0.98)" stroke="rgba(16,24,40,0.18)" stroke-width="1"></circle>
        </g>
      </svg>
    {% endif %}
  </div>
</div>

{% if current.answer_xy %}
<script>
(function(){
  const img = document.getElementById("answerPreviewImg");
  const outer = document.getElementById("answerOuter");
  const inner = document.getElementById("answerInner");
  const tail  = document.getElementById("answerTail");

  const ax = {{current.answer_xy[0]}};
  const ay = {{current.answer_xy[1]}};

  function layout(){
    if(!img || !img.complete) return;

    const rect = img.getBoundingClientRect();
    const scaleX = rect.width / img.naturalWidth;
    const scaleY = rect.height / img.naturalHeight;

    const cx = ax * scaleX;
    const cy = ay * scaleY;

    outer.setAttribute("cx", cx);
    outer.setAttribute("cy", cy);
    inner.setAttribute("cx", cx);
    inner.setAttribute("cy", cy);

    // small triangle tail
    const d = `M ${cx} ${cy+11} L ${cx-6} ${cy+25} L ${cx+6} ${cy+25} Z`;
    tail.setAttribute("d", d);
  }

  img.addEventListener("load", layout);
  window.addEventListener("resize", layout);
  // in case cached
  setTimeout(layout, 0);
})();
</script>
{% endif %}

        <hr style="border:none; height:1px; background:var(--border); margin:14px 0;">
        <form method="post" class="row">
          <input type="hidden" name="action" value="reset_game" />
          <button class="btn-ghost" type="submit" onclick="return confirm('Reset players & rounds?')">Reset players & rounds</button>
        </form>
      {% endif %}
    </div>

  </div>
</body></html>
""",
    msg=msg,
    players=STATE.players,
    rounds=STATE.rounds,
    current=current,
    round_index=STATE.current_round_index)

@app.route("/set_answer/<round_id>", methods=["GET", "POST"])
def set_answer(round_id):
    rd = get_round(round_id)

    if request.method == "POST":
        x = int(request.form["x"])
        y = int(request.form["y"])
        rd.answer_xy = (x, y)
        return redirect(url_for("host"))

    return render_template_string("""
<!doctype html>
<html><head>
  <meta charset="utf-8" />
  <title>Set Answer</title>
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <style>
    :root{
      --bg:#f6f7fb; --card:#ffffff; --text:#101828; --muted:#475467;
      --border:#e4e7ec; --accent:#2563eb; --accent2:#1d4ed8;
      --good:#067647; --bad:#b42318; --radius:14px;
    }
    body{ margin:0; font-family: ui-sans-serif, system-ui, -apple-system, Segoe UI, Roboto, Arial, sans-serif;
      color:var(--text); background: linear-gradient(180deg, #f6f7fb, #eef2ff); padding:24px 18px; }
    .wrap{max-width:1200px; margin:0 auto;}
    .card{ background: var(--card); border-radius: var(--radius); padding: 18px; border: 1px solid var(--border); }
    img{max-width:100%; border-radius:12px; border:1px solid var(--border); cursor: crosshair;}
    .muted{color:var(--muted);}
    button{ padding:10px 12px; border-radius:12px; border:1px solid var(--border);
      background: var(--accent); color:white; cursor:pointer; font-weight:900; }
    button:hover{background: var(--accent2);}
    a{color:var(--accent); text-decoration:none; font-weight:900;}
    .xy{font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", monospace;}
    .tag{
      display:inline-flex; padding: 4px 10px; border-radius: 999px;
      background: #eff6ff; color: #1d4ed8; font-weight: 800; font-size: 12px; border: 1px solid #dbeafe;
    }
  </style>
</head><body>
  <div class="wrap">
    <h1 style="margin:0 0 6px 0; font-size:24px; letter-spacing:-0.02em;">Set the correct answer spot</h1>
    <p class="muted" style="margin:0 0 14px 0;">Click on the map, then save.</p>

    <div class="card">
      <div class="tag">Map</div>
      <p class="muted" style="margin:10px 0 10px 0;">Selected: <span class="xy" id="xy">(none)</span></p>
      <img id="map" src="{{url_for('uploads', filename=map_fn)}}" />
      <form method="post" style="margin-top:12px; display:flex; gap:10px; align-items:center; flex-wrap:wrap;">
        <input type="hidden" name="x" id="x" />
        <input type="hidden" name="y" id="y" />
        <button type="submit">Save answer</button>
        <a href="{{url_for('host')}}">Cancel</a>
      </form>
    </div>
  </div>

<script>
const img = document.getElementById("map");
const xInput = document.getElementById("x");
const yInput = document.getElementById("y");
const xy = document.getElementById("xy");

img.addEventListener("click", (e) => {
  const rect = img.getBoundingClientRect();
  const x = Math.round((e.clientX - rect.left) * (img.naturalWidth / rect.width));
  const y = Math.round((e.clientY - rect.top) * (img.naturalHeight / rect.height));
  xInput.value = x;
  yInput.value = y;
  xy.textContent = `(${x}, ${y})`;
});
</script>
</body></html>
""", map_fn=rd.map_filename)

@app.route("/play/<round_id>")
def play_round(round_id):
    rd = get_round(round_id)
    if rd.answer_xy is None:
        return redirect(url_for("set_answer", round_id=round_id))

    return render_template_string("""
<!doctype html>
<html><head>
  <meta charset="utf-8" />
  <title>Play Round</title>
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <style>
    :root{
      --bg:#f6f7fb; --card:#ffffff; --text:#101828; --muted:#475467;
      --border:#e4e7ec; --accent:#2563eb; --accent2:#1d4ed8;
      --good:#067647; --bad:#b42318; --radius:14px;
    }
    html, body { height: 100%; margin: 0; font-family: ui-sans-serif, system-ui, -apple-system, Segoe UI, Roboto, Arial, sans-serif; }
    body{ background: linear-gradient(180deg, #f6f7fb, #eef2ff); overflow:hidden; }
    .stage{ position:relative; width:100vw; height:100vh; touch-action:none; }
    #mapLayer{ position:absolute; left:0; top:0; right:0; bottom:0; overflow:hidden; }
    #mapImg{
      position:absolute; user-select:none; -webkit-user-drag:none; pointer-events:none;
      background:white; border: 1px solid var(--border); border-radius: 12px;
    }
    .overlay{ position:absolute; inset:0; pointer-events:none; }

    .hud{
      position: fixed; top: 14px; left: 14px; right: 14px;
      display: flex; gap: 12px; flex-wrap: wrap; align-items: center;
      padding: 10px 12px; background: var(--card);
      border-radius: 14px; border: 1px solid var(--border);
      z-index: 10;
    }
    .group{ display:flex; gap:8px; align-items:center; flex-wrap: wrap; }
    label{ font-size: 12px; color: var(--muted); font-weight:900; }
    select, input{
      padding: 9px 10px; border-radius: 12px; border: 1px solid var(--border);
      background: white; color: var(--text); outline:none; font-size: 14px;
    }
    input::placeholder{color:#98a2b3;}
    button{
      padding: 9px 11px; border-radius: 12px; border: 1px solid var(--border);
      background: var(--accent); color: white; cursor: pointer; font-weight: 950; font-size: 14px;
    }
    button:hover{background: var(--accent2);}
    .btn-ghost{ background: white; color: var(--text); }
    .btn-ghost:hover{ background:#f2f4f7; }
    .pill{
      display:inline-flex; gap:8px; align-items:center; padding: 7px 10px; border-radius: 999px;
      background: #f2f4f7; border: 1px solid var(--border); color: var(--muted); font-size: 13px; font-weight:900;
    }
    a { color: var(--accent); text-decoration: none; font-size: 13px; font-weight: 950; }
    a:hover { text-decoration: underline; }
    .spacer { flex: 1; }
    .hint{
      position: fixed; bottom: 14px; left: 14px; right: 14px;
      display:flex; justify-content:center; pointer-events:none; z-index: 9;
    }
    .hint .chip{
      background: var(--card); border: 1px solid var(--border); border-radius: 999px;
      padding: 8px 12px; color: var(--muted); font-size: 13px; font-weight: 900;
    }
    .toast{
      position: fixed; top: 86px; left: 50%; transform: translateX(-50%);
      padding: 10px 12px; border-radius: 12px; background: var(--card);
      border: 1px solid var(--border); font-weight: 950; color: var(--text);
      display:none; z-index: 20;
    }
  </style>
</head><body>

  <div class="stage" id="stage">
    <div id="mapLayer">
      <img id="mapImg" src="{{url_for('uploads', filename=map_fn)}}" alt="Map" />
    </div>
    <svg id="overlay" class="overlay"></svg>
  </div>

  <div class="hud">
    <div class="group">
      <label for="player">Player</label>
      <select id="player"></select>
      <span class="pill" id="guessedPill">Guessed: 0 / 0</span>
      <span class="pill" id="pinStatus">Pin: —</span>
    </div>

    <div class="group">
      <button class="btn-ghost" id="zoomOut" title="Zoom out">−</button>
      <button class="btn-ghost" id="zoomIn" title="Zoom in">+</button>
      <button class="btn-ghost" id="resetView" title="Reset view">Reset</button>
      <span class="pill" id="zoomPill">100%</span>
    </div>

    <div class="group">
      <form id="addPlayerForm" style="display:flex; gap:8px; align-items:center; margin:0;">
        <input id="newPlayer" placeholder="Add player…" />
        <button type="submit">Add</button>
      </form>
    </div>

    <div class="spacer"></div>

    <div class="group">
      <a href="{{url_for('leaderboard')}}">Leaderboard</a>
      <a href="{{url_for('host')}}">Host</a>
    </div>
  </div>

  <div class="hint">
    <div class="chip">Drag to pan · Mouse wheel / buttons to zoom · Click (no drag) to place pin</div>
  </div>

  <div class="toast" id="toast"></div>

<script>
const roundId = "{{ round_id }}";
const stateKey = "lgeoparty.selectedPlayer." + roundId;

const stage = document.getElementById("stage");
const img = document.getElementById("mapImg");
const overlay = document.getElementById("overlay");

const playerSel = document.getElementById("player");
const pinStatus = document.getElementById("pinStatus");
const guessedPill = document.getElementById("guessedPill");

const addPlayerForm = document.getElementById("addPlayerForm");
const newPlayerInput = document.getElementById("newPlayer");

const zoomInBtn = document.getElementById("zoomIn");
const zoomOutBtn = document.getElementById("zoomOut");
const resetBtn = document.getElementById("resetView");
const zoomPill = document.getElementById("zoomPill");

const toast = document.getElementById("toast");

let players = [];
let guesses = {};

// View state
let baseScale = 1; // contain scale
let zoom = 1;      // user zoom multiplier
let panX = 0;      // pixels
let panY = 0;

function clamp(v, lo, hi){ return Math.max(lo, Math.min(hi, v)); }

function showToast(text, isError=false){
  toast.textContent = text;
  toast.style.display = "block";
  toast.style.color = isError ? "var(--bad)" : "var(--text)";
  clearTimeout(showToast._t);
  showToast._t = setTimeout(() => { toast.style.display = "none"; }, 1400);
}

function clearOverlay(){ while (overlay.firstChild) overlay.removeChild(overlay.firstChild); }
function setOverlaySize(){ overlay.setAttribute("width", window.innerWidth); overlay.setAttribute("height", window.innerHeight); }

function computeBaseScale(){
  const vw = window.innerWidth;
  const vh = window.innerHeight;
  const iw = img.naturalWidth || 1;
  const ih = img.naturalHeight || 1;
  baseScale = Math.min(vw / iw, vh / ih);
}

function currentRenderSize(){
  const iw = img.naturalWidth || 1;
  const ih = img.naturalHeight || 1;
  const s = baseScale * zoom;
  return {w: iw * s, h: ih * s, s};
}

function imageTopLeft(){
  const vw = window.innerWidth;
  const vh = window.innerHeight;
  const {w, h} = currentRenderSize();
  const left = (vw - w) / 2 + panX;
  const top = (vh - h) / 2 + panY;
  return {left, top};
}

function layoutImage(){
  if(!img.naturalWidth) return;
  computeBaseScale();
  const {w, h} = currentRenderSize();
  const {left, top} = imageTopLeft();
  img.style.width = w + "px";
  img.style.height = h + "px";
  img.style.left = left + "px";
  img.style.top = top + "px";
  zoomPill.textContent = Math.round(zoom * 100) + "%";
  setOverlaySize();
  refreshPin();
}

function screenToImageCoords(clientX, clientY){
  const {left, top} = imageTopLeft();
  const {s} = currentRenderSize();
  return {x: (clientX - left) / s, y: (clientY - top) / s};
}

function imageToScreenCoords(x, y){
  const {left, top} = imageTopLeft();
  const {s} = currentRenderSize();
  return {sx: left + x * s, sy: top + y * s};
}

function drawPinForPlayer(player){
  clearOverlay();
  const g = guesses[player];
  if(!g){
    pinStatus.textContent = "Pin: —";
    return;
  }
  pinStatus.textContent = "Pin: shown";
  const p = imageToScreenCoords(g.x, g.y);

  const group = document.createElementNS("http://www.w3.org/2000/svg", "g");

  const outer = document.createElementNS("http://www.w3.org/2000/svg", "circle");
  outer.setAttribute("cx", p.sx);
  outer.setAttribute("cy", p.sy);
  outer.setAttribute("r", 11);
  outer.setAttribute("fill", "rgba(255,255,255,0.95)");
  outer.setAttribute("stroke", "rgba(16,24,40,0.25)");
  outer.setAttribute("stroke-width", 2);

  const inner = document.createElementNS("http://www.w3.org/2000/svg", "circle");
  inner.setAttribute("cx", p.sx);
  inner.setAttribute("cy", p.sy);
  inner.setAttribute("r", 4);
  inner.setAttribute("fill", "rgba(37,99,235,0.98)");
  inner.setAttribute("stroke", "rgba(16,24,40,0.18)");
  inner.setAttribute("stroke-width", 1);

  const tail = document.createElementNS("http://www.w3.org/2000/svg", "path");
  const d = `M ${p.sx} ${p.sy+11} L ${p.sx-6} ${p.sy+25} L ${p.sx+6} ${p.sy+25} Z`;
  tail.setAttribute("d", d);
  tail.setAttribute("fill", "rgba(255,255,255,0.95)");
  tail.setAttribute("stroke", "rgba(16,24,40,0.25)");
  tail.setAttribute("stroke-width", 2);

  group.appendChild(tail);
  group.appendChild(outer);
  group.appendChild(inner);
  overlay.appendChild(group);
}

function refreshPin(){ drawPinForPlayer(playerSel.value); }

function computeGuessStats(){
  const guessedCount = Object.keys(guesses).length;
  guessedPill.textContent = `Guessed: ${guessedCount} / ${players.length}`;
}

function rebuildPlayerDropdown(keepSelection){
  const prev = keepSelection ?? playerSel.value ?? "";
  playerSel.innerHTML = "";
  for(const p of players){
    const opt = document.createElement("option");
    opt.value = p;
    opt.textContent = p + (guesses[p] ? " ✅" : "");
    playerSel.appendChild(opt);
  }
  let selected = prev || localStorage.getItem(stateKey) || "";
  if(selected && players.includes(selected)){
    playerSel.value = selected;
  }else if(players.length){
    playerSel.value = players[0];
  }
  localStorage.setItem(stateKey, playerSel.value);
  computeGuessStats();
  refreshPin();
}

async function loadState(){
  const res = await fetch(`/api/round_state/${roundId}`);
  const js = await res.json();
  players = js.players || [];
  guesses = js.guesses || {};
  rebuildPlayerDropdown();
}

playerSel.addEventListener("change", () => {
  localStorage.setItem(stateKey, playerSel.value);
  refreshPin();
});

// When adding a player, switch to them immediately (prevents overwriting others)
addPlayerForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  const name = (newPlayerInput.value || "").trim();
  if(!name){
    showToast("Name cannot be empty.", true);
    return;
  }
  try{
    const res = await fetch("/api/add_player", {
      method:"POST",
      headers: {"Content-Type":"application/json"},
      body: JSON.stringify({name})
    });
    const js = await res.json();
    if(!res.ok || !js.ok){
      showToast(js.error || "Failed to add.", true);
      return;
    }
    players = js.players;
    newPlayerInput.value = "";
    rebuildPlayerDropdown(js.added);
    showToast(`Turn: ${js.added}`);
  }catch(err){
    showToast("Network error", true);
  }
});

// Zoom controls
function zoomAt(clientX, clientY, newZoom){
  newZoom = clamp(newZoom, 0.5, 6.0);
  if(!img.naturalWidth) return;

  const before = screenToImageCoords(clientX, clientY);
  zoom = newZoom;

  const vw = window.innerWidth, vh = window.innerHeight;
  computeBaseScale();
  const iw = img.naturalWidth, ih = img.naturalHeight;
  const s = baseScale * zoom;

  const desiredLeft = clientX - before.x * s;
  const desiredTop  = clientY - before.y * s;

  panX = desiredLeft - (vw - iw * s) / 2;
  panY = desiredTop  - (vh - ih * s) / 2;

  layoutImage();
}

zoomInBtn.addEventListener("click", () => zoomAt(window.innerWidth/2, window.innerHeight/2, zoom * 1.2));
zoomOutBtn.addEventListener("click", () => zoomAt(window.innerWidth/2, window.innerHeight/2, zoom / 1.2));
resetBtn.addEventListener("click", () => { zoom = 1; panX = 0; panY = 0; layoutImage(); });

stage.addEventListener("wheel", (e) => {
  e.preventDefault();
  const factor = Math.exp(-e.deltaY * 0.0012);
  zoomAt(e.clientX, e.clientY, zoom * factor);
}, {passive:false});

// Pan + click-to-guess (no drag)
let pointerDown = false;
let startX = 0, startY = 0;
let startPanX = 0, startPanY = 0;
let moved = false;

stage.addEventListener("pointerdown", (e) => {
  pointerDown = true;
  moved = false;
  startX = e.clientX;
  startY = e.clientY;
  startPanX = panX;
  startPanY = panY;
  stage.setPointerCapture(e.pointerId);
});

stage.addEventListener("pointermove", (e) => {
  if(!pointerDown) return;
  const dx = e.clientX - startX;
  const dy = e.clientY - startY;
  if(Math.hypot(dx, dy) > 6) moved = true;
  panX = startPanX + dx;
  panY = startPanY + dy;
  layoutImage();
});

stage.addEventListener("pointerup", async (e) => {
  if(!pointerDown) return;
  pointerDown = false;
  stage.releasePointerCapture(e.pointerId);

  if(moved) return;

  if(!players.length){
    showToast("Add at least one player first.", true);
    return;
  }

  const coords = screenToImageCoords(e.clientX, e.clientY);
  const iw = img.naturalWidth, ih = img.naturalHeight;
  const x = clamp(Math.round(coords.x), 0, iw);
  const y = clamp(Math.round(coords.y), 0, ih);

  const player = playerSel.value;
  localStorage.setItem(stateKey, player);

  try{
    const res = await fetch("/api/guess", {
      method:"POST",
      headers: {"Content-Type":"application/json"},
      body: JSON.stringify({round_id: roundId, player, x, y})
    });
    const js = await res.json();
    if(!res.ok || !js.ok){
      showToast(js.error || "Failed to save guess.", true);
      return;
    }
    guesses = js.guesses || guesses;
    rebuildPlayerDropdown(player);
    showToast("Saved");
  }catch(err){
    showToast("Network error", true);
  }
});

img.addEventListener("load", () => {
  zoom = 1; panX = 0; panY = 0;
  layoutImage();
  loadState();
});
window.addEventListener("resize", () => layoutImage());
</script>

</body></html>
""", map_fn=rd.map_filename, round_id=round_id)

@app.route("/leaderboard")
def leaderboard():
    totals = {p: 0 for p in STATE.players}

    round_rows = []
    for i, rd in enumerate(STATE.rounds):
        if rd.answer_xy is None:
            continue
        row = {"index": i, "map": rd.map_filename, "answer": rd.answer_xy, "guesses": rd.guesses, "scores": {}}
        for p in STATE.players:
            if p in rd.guesses:
                d = pixel_distance(rd.guesses[p], rd.answer_xy)
                s = score_from_distance(d, rd.map_size)
                totals[p] += s
                row["scores"][p] = (s, int(round(d)))
            else:
                row["scores"][p] = None
        round_rows.append(row)

    ranked = sorted(totals.items(), key=lambda kv: kv[1], reverse=True)

    cr = current_round()
    back_round_id = cr.id if cr else None

    return render_template_string("""
<!doctype html>
<html><head>
  <meta charset="utf-8" />
  <title>Leaderboard</title>
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <style>
    :root{
      --bg:#f6f7fb; --card:#ffffff; --text:#101828; --muted:#475467;
      --border:#e4e7ec; --accent:#2563eb; --accent2:#1d4ed8;
      --good:#067647; --bad:#b42318; --radius:14px;
    }
    body{ margin:0; font-family: ui-sans-serif, system-ui, -apple-system, Segoe UI, Roboto, Arial, sans-serif;
      color:var(--text); background: linear-gradient(180deg, #f6f7fb, #eef2ff); padding:24px 18px; }
    .wrap{max-width:1150px; margin:0 auto;}
    h1{margin:0; font-size:26px; letter-spacing:-0.02em;}
    a{color:var(--accent); text-decoration:none; font-weight:950;}
    a:hover{text-decoration:underline;}
    .card{ background: var(--card); border-radius: var(--radius); padding: 18px; border: 1px solid var(--border); margin: 12px 0; }
    table{width:100%; border-collapse:collapse;}
    th, td{padding:10px 6px; border-bottom: 1px solid var(--border); text-align:left; font-size:14px;}
    .muted{color:var(--muted);}
    img{max-width:420px; border-radius:12px; border:1px solid var(--border);}
    code{background: #f2f4f7; padding: 2px 6px; border-radius: 8px; border: 1px solid var(--border);}
    .row{display:flex; gap:12px; align-items:center; flex-wrap:wrap; justify-content:space-between;}
    .btn{
      display:inline-flex;
      align-items:center;
      gap:8px;
      padding:10px 12px;
      border-radius:12px;
      border:1px solid var(--border);
      background: var(--accent);
      color:white;
      font-weight:900;
    }
    .btn:hover{background: var(--accent2); text-decoration:none;}
    .btn-ghost{ background:white; color:var(--text); }
    .btn-ghost:hover{background:#f2f4f7;}
  </style>
</head><body>
  <div class="wrap">
    <div class="row">
      <h1>Leaderboard</h1>
      <div style="display:flex; gap:10px; align-items:center; flex-wrap:wrap;">
        {% if back_round_id %}
          <a class="btn btn-ghost" href="{{url_for('play_round', round_id=back_round_id)}}">Back to round</a>
        {% endif %}
        <a class="btn btn-ghost" href="{{url_for('host')}}">Back to host</a>
      </div>
    </div>

    <div class="card">
      <h3 style="margin:0 0 10px 0;">Total scores</h3>
      {% if not ranked %}
        <p class="muted">No players.</p>
      {% else %}
        <table>
          <tr><th>Rank</th><th>Player</th><th>Total</th></tr>
          {% for (p, s) in ranked %}
            <tr><td>{{loop.index}}</td><td>{{p}}</td><td>{{s}}</td></tr>
          {% endfor %}
        </table>
      {% endif %}
      <p class="muted" style="margin:10px 0 0 0;">Scoring is normalized by map size</p>
    </div>

    <div class="card">
      <h3 style="margin:0 0 10px 0;">Round breakdown</h3>
      {% if not rounds %}
        <p class="muted">No rounds scored yet (did you set answers and submit guesses?).</p>
      {% else %}
        {% for rd in rounds %}
          <h4 style="margin:10px 0 8px 0;">Round {{rd.index}} — <code>{{rd.map}}</code></h4>
          <div class="pinwrap" data-round="{{rd.index}}" data-answer-x="{{rd.answer[0]}}" data-answer-y="{{rd.answer[1]}}" style="position:relative; display:inline-block; max-width:420px;">
            <img class="pinmap" src="{{url_for('uploads', filename=rd.map)}}" />
            <svg class="pinsvg" style="position:absolute; inset:0; width:100%; height:100%;"></svg>
            <div class="pintooltip" style="position:absolute; display:none; z-index:5; padding:6px 8px; border-radius:10px; background: var(--card); border: 1px solid var(--border); color: var(--text); font-size: 12px; font-weight: 950; box-shadow: 0 6px 20px rgba(16,24,40,0.12); pointer-events:none;"></div>
          </div>
          <script type="application/json" class="pindata" data-round="{{rd.index}}">{{ rd.guesses | tojson }}</script>
          <table style="margin-top:10px;">
            <tr>
              <th>Player</th><th>Score</th><th>Distance (px)</th>
            </tr>
            {% for p in players %}
              {% set v = rd.scores[p] %}
              <tr>
                <td>{{p}}</td>
                {% if v %}
                  <td>{{v[0]}}</td>
                  <td>{{v[1]}}</td>
                {% else %}
                  <td class="muted">—</td>
                  <td class="muted">no guess</td>
                {% endif %}
              </tr>
            {% endfor %}
          </table>
        {% endfor %}
      {% endif %}
    </div>

<script>
(function(){
  function clamp(v, lo, hi){ return Math.max(lo, Math.min(hi, v)); }

  function makePin(svg, cx, cy, color, label){
    const ns = "http://www.w3.org/2000/svg";
    const g = document.createElementNS(ns, "g");
    g.classList.add("pin");
    g.dataset.label = label;
    g.style.cursor = "default";

    // tail
    const tail = document.createElementNS(ns, "path");
    tail.setAttribute("d", `M ${cx} ${cy+11} L ${cx-6} ${cy+25} L ${cx+6} ${cy+25} Z`);
    tail.setAttribute("fill", color);
    tail.setAttribute("stroke", "rgba(16,24,40,0.18)");
    tail.setAttribute("stroke-width", "2");

    // outer ring
    const outer = document.createElementNS(ns, "circle");
    outer.setAttribute("cx", cx);
    outer.setAttribute("cy", cy);
    outer.setAttribute("r", "11");
    outer.setAttribute("fill", "rgba(255,255,255,0.98)");
    outer.setAttribute("stroke", "rgba(16,24,40,0.18)");
    outer.setAttribute("stroke-width", "2");

    const inner = document.createElementNS(ns, "circle");
    inner.setAttribute("cx", cx);
    inner.setAttribute("cy", cy);
    inner.setAttribute("r", "4");
    inner.setAttribute("fill", color);
    inner.setAttribute("stroke", "rgba(16,24,40,0.18)");
    inner.setAttribute("stroke-width", "1");

    // Native browser tooltip (fallback)
    const title = document.createElementNS(ns, "title");
    title.textContent = label;

    g.appendChild(title);
    g.appendChild(tail);
    g.appendChild(outer);
    g.appendChild(inner);
    svg.appendChild(g);
    return g;
  }

  function parseRoundGuesses(roundIndex){
    const script = document.querySelector(`script.pindata[data-round="${roundIndex}"]`);
    if(!script) return {};
    try { return JSON.parse(script.textContent || "{}"); } catch { return {}; }
  }

  function layoutOne(wrap){
    const img = wrap.querySelector("img.pinmap");
    const svg = wrap.querySelector("svg.pinsvg");
    const tooltip = wrap.querySelector(".pintooltip");
    if(!img || !svg) return;
    if(!img.naturalWidth || !img.naturalHeight) return;

    // size svg to image displayed size
    const rect = img.getBoundingClientRect();
    const w = rect.width;
    const h = rect.height;
    svg.setAttribute("width", w);
    svg.setAttribute("height", h);
    svg.setAttribute("viewBox", `0 0 ${w} ${h}`);

    // clear old pins
    while(svg.firstChild) svg.removeChild(svg.firstChild);

    const roundIndex = wrap.dataset.round;
    const guesses = parseRoundGuesses(roundIndex);

    const scaleX = w / img.naturalWidth;
    const scaleY = h / img.naturalHeight;

    // player pins (blue)
    for(const [name, pt] of Object.entries(guesses)){
      if(!pt) continue;
      const cx = clamp(Math.round(pt.x * scaleX), 0, w);
      const cy = clamp(Math.round(pt.y * scaleY), 0, h);
      makePin(svg, cx, cy, "rgba(37,99,235,0.98)", name);
    }

    // answer pin (red)
    const ax = Number(wrap.dataset.answerX);
    const ay = Number(wrap.dataset.answerY);
    if(Number.isFinite(ax) && Number.isFinite(ay)){
      const cx = clamp(Math.round(ax * scaleX), 0, w);
      const cy = clamp(Math.round(ay * scaleY), 0, h);
      makePin(svg, cx, cy, "rgba(220,38,38,0.98)", "Answer");
    }

    // Hover label (custom tooltip)
    if(tooltip){
      const show = (e, label) => {
        tooltip.textContent = label;
        tooltip.style.display = "block";
        const pad = 10;
        const x = clamp(e.offsetX + 12, pad, w - tooltip.offsetWidth - pad);
        const y = clamp(e.offsetY + 12, pad, h - tooltip.offsetHeight - pad);
        tooltip.style.left = x + "px";
        tooltip.style.top = y + "px";
      };
      const hide = () => { tooltip.style.display = "none"; };

      svg.onmousemove = (e) => {
        const pin = e.target && e.target.closest ? e.target.closest(".pin") : null;
        if(pin && pin.dataset && pin.dataset.label){
          show(e, pin.dataset.label);
        }else{
          hide();
        }
      };
      svg.onmouseleave = hide;
    }
  }

  function layoutAll(){
    document.querySelectorAll(".pinwrap").forEach(layoutOne);
  }

  window.addEventListener("resize", () => layoutAll());
  document.addEventListener("DOMContentLoaded", () => {
    // wait a tick for images to size
    setTimeout(() => layoutAll(), 0);
    // also layout after each image loads
    document.querySelectorAll(".pinwrap img.pinmap").forEach(img => {
      img.addEventListener("load", () => layoutAll());
    });
  });
})();
</script>

  </div>
</body></html>
""", ranked=ranked, rounds=round_rows, players=STATE.players, back_round_id=back_round_id)

if __name__ == "__main__":
    print(f"Running on http://{APP_HOST}:{APP_PORT}")
    app.run(host=APP_HOST, port=APP_PORT, debug=False)
