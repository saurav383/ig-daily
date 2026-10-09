"""Find a Gemini model that reliably answers on this key."""
import os
import time

import requests

KEY = os.environ["GEMINI_API_KEY"]
BASE = "https://generativelanguage.googleapis.com/v1beta"
H = {"x-goog-api-key": KEY, "Content-Type": "application/json"}

r = requests.get(f"{BASE}/models", headers=H, params={"pageSize": 100}, timeout=45)
all_models = [m["name"].split("/")[-1] for m in r.json().get("models", [])]
print(f"=== {len(all_models)} models visible to this key ===")
for n in sorted(all_models):
    print(f"   {n}")

CANDIDATES = [
    "gemini-3.8-flash",
    "gemini-2.5-flash",
    "gemini-3.1-flash-lite",
    "gemini-2.5-flash-lite",
    "gemini-3-flash-preview",
    "gemini-3.1-flash-lite-preview",
    "gemini-2.5-pro",
]

print("\n=== generateContent probe (3 tries each, 5s apart) ===")
print(f"{'model':<34} {'verdict':<10} {'ms':>6}  detail")
working = []
for m in CANDIDATES:
    if m not in all_models:
        print(f"{m:<34} {'absent':<10} {'-':>6}  not in key's list")
        continue
    ok = False
    detail = ""
    ms = 0
    for attempt in range(3):
        t0 = time.time()
        try:
            rr = requests.post(
                f"{BASE}/models/{m}:generateContent",
                headers=H,
                json={"contents": [{"parts": [{"text": "Reply with exactly: OK"}]}],
                      "generationConfig": {"temperature": 0}},
                timeout=60,
            )
            ms = int((time.time() - t0) * 1000)
            if rr.ok:
                cand = rr.json().get("candidates", [])
                if cand:
                    parts = cand[0].get("content", {}).get("parts", [])
                    text = "".join(p.get("text", "") for p in parts).strip()
                    detail = 'replied "' + text[:30] + '"'
                    ok = True
                    break
                detail = "200 but no candidates/parts"
                break
            code = rr.status_code
            msg = ""
            try:
                msg = rr.json().get("error", {}).get("message", "")[:60]
            except ValueError:
                pass
            detail = f"HTTP {code}: {msg}"
            if code == 404:
                break
        except requests.RequestException as e:
            detail = type(e).__name__
        time.sleep(5)
    verdict = "WORKS" if ok else "fail"
    if ok:
        working.append((m, ms))
    print(f"{m:<34} {verdict:<10} {ms:>6}  {detail}")

print("\n=== recommended ===")
if working:
    working.sort(key=lambda x: x[1])
    for m, ms in working:
        print(f"   {m}  ({ms} ms)")
else:
    print("   none responded in 3 tries - quota or region issue")
