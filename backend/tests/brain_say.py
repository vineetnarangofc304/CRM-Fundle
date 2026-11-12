# Helper: send a plain chat turn to a Brain session. Usage: brain_say.py <sid> "message"
import json, os, sys
import requests

BASE = os.environ["REACT_APP_BACKEND_URL"].rstrip("/")
tok = requests.post(f"{BASE}/api/auth/login", json={"email": "vineetnarangofc@gmail.com", "password": "Fundle@cf928c"}).json()["access_token"]
H = {"Authorization": f"Bearer {tok}"}
r = requests.post(f"{BASE}/api/brain/sessions/{sys.argv[1]}/chat", headers=H, json={"message": sys.argv[2]}, stream=True, timeout=280)
text = ""
for line in r.iter_lines(decode_unicode=True):
    if line and line.startswith("data: "):
        ev = json.loads(line[6:])
        if ev["type"] == "text":
            text += ev["delta"] or ""
        else:
            print("EV", json.dumps(ev)[:600])
print("TEXT:\n", text)
