# Helper: upload a file to a Brain session then run a chat turn with the '[Attached file #N ...]' note. Usage: brain_upload_cli.py <sid|new> <file> "message"
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
with open(sys.argv[2], "rb") as f:
    up = requests.post(f"{BASE}/api/brain/sessions/{sid}/upload", headers=H, files={"file": (os.path.basename(sys.argv[2]), f)}).json()
print("UPLOAD", up)
q = f"[Attached file #{up['id']}: {up['filename']} — {up['rows']} rows, columns: {', '.join(up['columns'])}]\n{sys.argv[3]}"
r = requests.post(f"{BASE}/api/brain/sessions/{sid}/chat", headers=H, json={"message": q}, stream=True, timeout=280)
text = ""
for line in r.iter_lines(decode_unicode=True):
    if line and line.startswith("data: "):
        ev = json.loads(line[6:])
        if ev["type"] == "text":
            text += ev["delta"] or ""
        else:
            print("EV", json.dumps(ev)[:700])
print("TEXT:\n", text)
