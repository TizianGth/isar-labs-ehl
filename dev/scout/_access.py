"""Scouting logins for the dev tools: from CHALLENGE_SCOUTING (eval "$(atira scout --env)") or the public endpoint."""
import json, os, urllib.request

def scouting() -> dict:
    if os.environ.get("CHALLENGE_SCOUTING"):
        return json.loads(os.environ["CHALLENGE_SCOUTING"])
    req = urllib.request.Request("https://ehl-challenge.atira.ai/v1/challenges/rfq/scouting",
                                 headers={"User-Agent": "isar-labs-dev/1"})
    return json.load(urllib.request.urlopen(req, timeout=30))

def access() -> dict:
    return {a["siteId"]: a for a in scouting()["access"]}
