import json
import os
import sys

print(json.dumps({"event": "ready"}), flush=True)
for line in sys.stdin:
    job = json.loads(line)
    if job.get("cmd") == "quit":
        break
    if job["text"] == "die":
        sys.exit(1)
    if job["text"] == "die_once" and not os.path.exists(job["flag"]):
        open(job["flag"], "w").close()
        sys.exit(1)
    result = {"event": "result", "id": job["id"], "ok": True, "emotion": job.get("emotion")}
    print(json.dumps(result), flush=True)
