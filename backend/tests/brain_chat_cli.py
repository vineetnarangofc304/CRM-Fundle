# Helper: run one Fundle Brain chat turn over SSE and print events. Usage: python brain_chat_cli.py <sid|new> "message"
import json
import os
import sys
import requests

BASE = os.environ["REACT_APP_BACKEND_URL"].rstrip("/")
tok = requests.post(f"{BASE}/api/auth/login", json={"email": "vineetnarangofc@gmail.com", "password": "Fundle@cf928c"}).json()["access_token"]
H = {"Authorization": f"Bearer {tok}"}
sid = sys.argv[1]
if sid == "new":
    sid = requests.post(f"{BASE}/api/brain/sessions", headers=H).json()["id"]
print("SESSION", sid)
r = requests.post(f"{BASE}/api/brain/sessions/{sid}/chat", headers=H, json={"message": sys.argv[2]}, stream=True, timeout=280)
print("STATUS", r.status_code, r.headers.get("content-type"))
text = ""
for line in r.iter_lines(decode_unicode=True):
    if not line or not line.startswith("data: "):
        continue
    ev = json.loads(line[6:])
    if ev["type"] == "text":
        text += ev["delta"] or ""
    else:
        print("EV", json.dumps(ev)[:600])
print("TEXT:\n", text)
