"""The run contract: read the run (brief, websites, logins, where the answer goes) and keep the clock.

In a test or scored run the platform sets CHALLENGE_API_URL and CHALLENGE_RUN_TOKEN. Locally,
`eval "$(atira scout --env)"` exports the public scouting run as CHALLENGE_SCOUTING instead (nothing counts there).
"""
import json
import os
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

USER_AGENT = "isar-labs-rfq-agent/0.1"  # the API's edge refuses Python's default urllib agent
LOCAL_RUN_SECONDS = 20 * 60  # locally we pretend to have the same budget as a scored run


def _get(url: str, token: str) -> tuple[int, str]:
    request = Request(url, headers={"Accept": "application/json", "User-Agent": USER_AGENT, "x-api-key": token})
    try:
        with urlopen(request, timeout=30) as response:
            return response.status, response.read().decode("utf-8", errors="replace")
    except HTTPError as error:
        return error.code, error.read().decode("utf-8", errors="replace")


def load_run(env=os.environ) -> tuple[dict, str | None]:
    """The run and its token; locally (no token) the scouting run from CHALLENGE_SCOUTING."""
    token = env.get("CHALLENGE_RUN_TOKEN")
    if not token:
        if not env.get("CHALLENGE_SCOUTING"):
            raise SystemExit('No run token and no scouting data. Locally run: eval "$(atira scout --env)"')
        run = json.loads(env["CHALLENGE_SCOUTING"])
        run.setdefault("expires_at", (time.time() + LOCAL_RUN_SECONDS) * 1000)
        if run["expires_at"] > (time.time() + 24 * 3600) * 1000:  # scouting data says "until revoked"
            run["expires_at"] = (time.time() + LOCAL_RUN_SECONDS) * 1000
        return run, None
    url = env["CHALLENGE_API_URL"].rstrip("/") + "/v1/runs/current"
    status, body = 0, ""
    for attempt in range(6):
        try:
            status, body = _get(url, token)
        except (URLError, OSError) as error:
            status, body = 0, str(error)
        if status == 200:
            return json.loads(body), token
        if status in (401, 403, 404):
            break
        time.sleep(1.5 * (attempt + 1))
    raise SystemExit(f"Cannot read the run: HTTP {status} {body[:300]}")


def seconds_left(run: dict) -> float:
    expires_at = run.get("expires_at")
    return float("inf") if not expires_at else expires_at / 1000 - time.time()
