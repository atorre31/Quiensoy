#!/usr/bin/env python3
import json, time, random, re, os
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
import unicodedata

state = {
    "phase": "lobby",
    "currentQ": -1,
    "revealedTiles": [],
    "answers": {},
    "players": {},
    "questions": [],
    "img_data": [],
    "questionStartTime": 0,
}

def load_questions():
    try:
        with open("host.html", "r", encoding="utf-8") as f:
            content = f.read()
        pairs = re.findall(r"\{ name: '([^']+)', dataUrl: '(data:image/[^']+)'", content)
        items = [{"name": p[0].replace("\\'", "'"), "dataUrl": p[1]} for p in pairs]
        random.shuffle(items)
        state["questions"] = [i["name"] for i in items]
        state["img_data"] = [i["dataUrl"] for i in items]
        print(f"✅ {len(items)} preguntas cargadas")
    except Exception as e:
        print(f"❌ Error: {e}")

def normalize(s):
    s = s.lower().strip()
    s = unicodedata.normalize('NFD', s)
    s = ''.join(c for c in s if unicodedata.category(c) != 'Mn')
    s = re.sub(r'[^a-z0-9\s]', '', s)
    return re.sub(r'\s+', ' ', s).strip()

def public_state(player_name=None):
    players_list = [
        {"name": n, "score": p["score"]}
        for n, p in sorted(state["players"].items(), key=lambda x: -x[1]["score"])
        if time.time() - p["lastSeen"] < 30
    ]
    return {
        "phase": state["phase"],
        "currentQ": state["currentQ"],
        "totalQ": len(state["questions"]),
        "revealedTiles": state["revealedTiles"],
        "players": players_list,
        "myAnswer": state["answers"].get(player_name),
        "questionAnswer": state["questions"][state["currentQ"]] if state["phase"] == "reveal" and state["currentQ"] >= 0 else None,
        "answers": [
            {"name": n, "correct": a["correct"], "time": round(a["time"], 2), "order": a.get("order"), "pts": a.get("pts", 0)}
            for n, a in sorted(state["answers"].items(), key=lambda x: x[1]["time"])
        ],
    }

class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args): pass

    def send_json(self, data, code=200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", len(body))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Content-Security-Policy", "default-src * 'unsafe-inline' 'unsafe-eval' data: blob:;")
        self.end_headers()
        self.wfile.write(body)

    def send_file(self, path, ctype):
        try:
            with open(path, "rb") as f:
                body = f.read()
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", len(body))
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Content-Security-Policy", "default-src * 'unsafe-inline' 'unsafe-eval' data: blob:;")
            self.end_headers()
            self.wfile.write(body)
        except FileNotFoundError:
            self.send_response(404); self.end_headers()

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        qs = parse_qs(parsed.query)
        player = qs.get("player", [""])[0].strip()[:24]

        if path in ["/", "/host.html"]:
            self.send_file("host.html", "text/html; charset=utf-8")
        elif path == "/player.html":
            self.send_file("player.html", "text/html; charset=utf-8")
        elif path == "/api/state":
            if player and player in state["players"]:
                state["players"][player]["lastSeen"] = time.time()
            self.send_json(public_state(player))
        elif path == "/api/host_state":
            q = state["questions"][state["currentQ"]] if 0 <= state["currentQ"] < len(state["questions"]) else None
            self.send_json({**public_state(), "currentAnswer": q})
        elif path == "/api/order":
            # Send the shuffled question order (names only) so host can sync
            self.send_json({"names": state["questions"]})
        elif path == "/api/img":
            # Send current question image dataUrl to players
            idx = state["currentQ"]
            if 0 <= idx < len(state["img_data"]):
                self.send_json({"dataUrl": state["img_data"][idx]})
            else:
                self.send_json({"dataUrl": ""})
        else:
            self.send_response(404); self.end_headers()

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length) or b"{}")
        path = urlparse(self.path).path

        if path == "/api/join":
            name = (body.get("name") or "").strip()[:24]
            if not name: return self.send_json({"ok": False})
            if name not in state["players"]:
                state["players"][name] = {"score": 0, "lastSeen": time.time()}
            else:
                state["players"][name]["lastSeen"] = time.time()
            self.send_json({"ok": True})

        elif path == "/api/answer":
            name = (body.get("name") or "").strip()[:24]
            text = (body.get("text") or "").strip()[:60]
            if not name or name not in state["players"] or state["phase"] != "question" or name in state["answers"]:
                return self.send_json({"ok": False})
            q = state["questions"][state["currentQ"]]
            correct = normalize(text) == normalize(q)
            elapsed = time.time() - state["questionStartTime"]
            bonus = max(0, int(50 * (1 - elapsed / 30))) if correct else 0
            pts = 100 + bonus if correct else 0
            # Track correct answer order
            correct_order = len([a for a in state["answers"].values() if a["correct"]]) + 1 if correct else None
            state["answers"][name] = {"correct": correct, "pts": pts, "time": elapsed, "order": correct_order}
            if correct: state["players"][name]["score"] += pts
            self.send_json({"ok": True, "correct": correct, "pts": pts, "answer": q, "order": correct_order})

        elif path == "/api/host/start":
            load_questions()
            state.update({"phase": "question", "currentQ": 0, "revealedTiles": [], "answers": {}, "questionStartTime": time.time()})
            for p in state["players"].values(): p["score"] = 0
            self.send_json({"ok": True})

        elif path == "/api/host/reveal_one":
            hidden = [i for i in range(9) if i not in state["revealedTiles"]]
            if hidden: state["revealedTiles"].append(random.choice(hidden))
            self.send_json({"ok": True, "revealedTiles": state["revealedTiles"]})

        elif path == "/api/host/reveal_all":
            state["revealedTiles"] = list(range(9))
            state["phase"] = "reveal"
            self.send_json({"ok": True})

        elif path == "/api/host/next":
            state["currentQ"] += 1
            if state["currentQ"] >= len(state["questions"]):
                state["phase"] = "finished"
            else:
                state.update({"phase": "question", "revealedTiles": [], "answers": {}, "questionStartTime": time.time()})
            self.send_json({"ok": True})

        elif path == "/api/host/show_reveal":
            state["phase"] = "reveal"
            state["revealedTiles"] = list(range(9))
            self.send_json({"ok": True})

        else:
            self.send_response(404); self.end_headers()

if __name__ == "__main__":
    PORT = int(os.environ.get("PORT", 9090))
    load_questions()
    print(f"🎉 Servidor listo en puerto {PORT}")
    HTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
