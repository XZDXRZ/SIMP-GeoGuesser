import os
import math
import uuid
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from flask import Flask, request, redirect, url_for, send_from_directory, render_template, abort, jsonify
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


def pixel_distance(a: Tuple[int, int], b: Tuple[int, int]) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def score_from_distance(d: float, map_size: Tuple[int, int]) -> int:
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

    if not player or player not in STATE.players:
        return jsonify({"ok": False, "error": "Invalid player."}), 400

    if x is None or y is None:
        return jsonify({"ok": False, "error": "Missing x/y."}), 400

    try:
        x = int(x)
        y = int(y)
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
                if name:
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
                STATE.current_round_index = len(STATE.rounds) - 1

            elif action == "reset_game":
                STATE.players = []
                STATE.rounds = []
                STATE.current_round_index = 0

            elif action == "goto_round":
                idx = int(request.form.get("round_index") or "-1")
                if idx < 0 or idx >= len(STATE.rounds):
                    raise ValueError("Invalid round index.")
                STATE.current_round_index = idx

            else:
                raise ValueError("Unknown action.")
        except Exception as e:
            msg = str(e)

    current = current_round()

    return render_template(
        "host.html",
        msg=msg,
        players=STATE.players,
        rounds=STATE.rounds,
        current=current,
        round_index=STATE.current_round_index,
    )


@app.route("/set_answer/<round_id>", methods=["GET", "POST"])
def set_answer(round_id):
    rd = get_round(round_id)
    round_num = STATE.rounds.index(rd) + 1

    if request.method == "POST":
        xv = request.form.get("x", "")
        yv = request.form.get("y", "")
        if xv == "" or yv == "":
            return render_template("set_answer.html", map_fn=rd.map_filename, error="You should select a point on the map before saving.")
        x = int(request.form["x"])
        y = int(request.form["y"])
        rd.answer_xy = (x, y)

        if request.headers.get("X-Requested-With") == "fetch":
            return jsonify({"ok": True, "answer": {"x": x, "y": y}})

        return redirect(url_for("host"))

    return render_template("set_answer.html", map_fn=rd.map_filename, round_num=round_num)


@app.route("/play/<round_id>")
def play_round(round_id):
    rd = get_round(round_id)

    # Find this rounds index
    try:
        idx = STATE.rounds.index(rd)
    except ValueError:
        abort(404)

    total = len(STATE.rounds)
    round_num = idx + 1

    prev_round_id = STATE.rounds[idx - 1].id if idx > 0 else None
    next_round_id = STATE.rounds[idx + 1].id if idx < total - 1 else None

    return render_template(
        "play_round.html",
        map_fn=rd.map_filename,
        round_id=round_id,
        round_num=round_num,
        total_rounds=total,
        prev_round_id=prev_round_id,
        next_round_id=next_round_id,
        answer_set=(rd.answer_xy is not None),
    )

@app.route("/leaderboard")
def leaderboard():
    totals = {p: 0 for p in STATE.players}

    round_rows = []
    for i, rd in enumerate(STATE.rounds):
        if rd.answer_xy is None:
            continue

        guesses_obj = {p: {"x": xy[0], "y": xy[1]} for p, xy in rd.guesses.items()}

        row = {
            "index": i + 1,  # round number starting at 1
            "map": rd.map_filename,
            "answer": rd.answer_xy,
            "guesses": guesses_obj,
            "scores": {}
        }

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

    return render_template(
        "leaderboard.html",
        ranked=ranked,
        rounds=round_rows,
        players=STATE.players,
        back_round_id=back_round_id,
    )

@app.route("/r/<round_id>")
def public_round(round_id):
    rd = get_round(round_id)

    # Find this round's index
    try:
        idx = STATE.rounds.index(rd)
    except ValueError:
        abort(404)

    total = len(STATE.rounds)
    round_num = idx + 1

    prev_round_id = STATE.rounds[idx - 1].id if idx > 0 else None
    next_round_id = STATE.rounds[idx + 1].id if idx < total - 1 else None

    return render_template(
        "play_round.html",
        map_fn=rd.map_filename,
        round_id=round_id,
        round_num=round_num,
        total_rounds=total,
        prev_round_id=prev_round_id,
        next_round_id=next_round_id,
        answer_set=(rd.answer_xy is not None),
    )

if __name__ == "__main__":
    print(f"Running on http://{APP_HOST}:{APP_PORT}")
    app.run(host=APP_HOST, port=APP_PORT, debug=False)
