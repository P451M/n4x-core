import json
import os
import urllib.error
import urllib.request

origin = os.environ.get("N4X_HOST_CONTROL_ORIGIN", "http://127.0.0.1:7744").rstrip("/")
archive = "/opt/n4x/cache/official-system.zip"


def post(path, payload, timeout):
    request = urllib.request.Request(
        origin + path,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = json.loads(response.read().decode())
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode()
        raise SystemExit(path + " failed (" + str(exc.code) + "): " + raw) from None
    if not isinstance(body, dict):
        raise SystemExit(path + " returned a non-object")
    if body.get("error"):
        raise SystemExit(path + " failed: " + json.dumps(body))
    return body


imported = post("/n4x-host/import", {"archive": archive}, 120)
revision_id = imported.get("imported")
if not revision_id:
    raise SystemExit("import did not return a revision id: " + json.dumps(imported))
print(json.dumps(post("/n4x-host/enable", {"revision_id": revision_id}, 300)))
