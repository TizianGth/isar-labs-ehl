"""The run contract: read the run (the brief, the websites and their logins, where the answer goes), submit the answer.

In a test run or a scored run the platform sets CHALLENGE_API_URL and CHALLENGE_RUN_TOKEN. On your own machine there
is no run token: `eval "$(atira scout --env)"` exports the public scouting run as CHALLENGE_SCOUTING instead, and
nothing done with those logins counts. Docs: $API/docs/run-contract.md
"""
import json
import os
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

USER_AGENT = "atira-starter-kit/3"  # the API's edge refuses Python's default urllib agent
LOAD_SCOUTING = 'eval "$(atira scout --env)"; in PowerShell: atira scout --env | Out-String | Invoke-Expression'


def http(url: str, token: str | None = None, payload: dict | None = None, timeout: float = 30) -> tuple[int, str]:
    """GET (or POST `payload` as JSON) with the run token in x-api-key: the status and the body."""
    headers = {"Accept": "application/json", "User-Agent": USER_AGENT}
    if token:
        headers["x-api-key"] = token
    data = None
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode()
        headers["Content-Type"] = "application/json"
    try:
        with urlopen(Request(url, data=data, headers=headers), timeout=timeout) as response:
            return response.status, response.read().decode("utf-8", errors="replace")
    except HTTPError as error:
        return error.code, error.read().decode("utf-8", errors="replace")


def load_run(env=os.environ) -> tuple[dict, str | None]:
    """The run and its token; locally, without a token, the scouting run that `atira scout --env` exported.

    The run has `brief` (the task and its rules, markdown), `access` (per website: `siteId`, `url`, `credentials`),
    `submission` (`kind` "api" or "site", and its `url`), `answer_schema`, `started_at` and `expires_at`.
    """
    token = env.get("CHALLENGE_RUN_TOKEN")
    if not token:
        if not env.get("CHALLENGE_SCOUTING"):
            raise SystemExit(f"No run token and no scouting logins. To develop locally: {LOAD_SCOUTING}")
        try:
            return json.loads(env["CHALLENGE_SCOUTING"]), None
        except ValueError:
            raise SystemExit(f"CHALLENGE_SCOUTING is not the JSON atira exports. Load it again: {LOAD_SCOUTING}")
    url = env["CHALLENGE_API_URL"].rstrip("/") + "/v1/runs/current"
    status, body = 0, ""
    for attempt in range(5):  # rides out a restart of the API
        try:
            status, body = http(url, token)
        except (URLError, OSError) as error:
            status, body = 0, str(error)
        if status == 200:
            return json.loads(body), token
        if status in (401, 403, 404):  # the run is over, or the token is wrong: no point in asking again
            break
        time.sleep(2 * (attempt + 1))
    raise SystemExit(f"Cannot read the run: HTTP {status} {body[:300]}")


def seconds_left(run: dict, now: float | None = None) -> float:
    """Seconds until the run's `expires_at` (epoch milliseconds); after that the answer is refused and the run scores
    0. Every part of the answer scores on its own, so submit your best answer about a minute before time runs out
    rather than keep searching."""
    expires_at = run.get("expires_at")
    if not expires_at:
        return float("inf")
    return expires_at / 1000 - (time.time() if now is None else now)


def submit_answer(run: dict, token: str | None, answer: dict) -> tuple[int, str]:
    """POST the answer to the run's "api" submission target. 202: accepted and final, the run ends. 400: the body
    lists the issues; nothing is recorded, fix the answer and send it again. A "site" submission is entered on that
    website instead, and the website confirms it with a receipt."""
    submission = run["submission"]
    if submission.get("kind") != "api":
        raise ValueError(f"This run takes its answer on the website {submission.get('url')}, not through the API")
    if not token:
        raise ValueError("No run to answer: locally there is no run token. Test it in a run: atira test-run .")
    return http(run.get("answer_url") or submission["url"], token, answer, timeout=60)
