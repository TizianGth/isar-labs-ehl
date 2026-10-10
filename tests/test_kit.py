"""The kit's plumbing against fakes: reading the run, submitting the answer, the local scouting mode, the browsers'
CDP URLs, one call to each hosted model, and an entry point that exits cleanly without an answer."""
import io
import json
import os
import sys
import threading
import unittest
from contextlib import redirect_stdout
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import agent  # noqa: E402
import browsers  # noqa: E402
import models  # noqa: E402
import run as contract  # noqa: E402

RUN = {
    "run_id": "run_test", "brief": "Prepare the bid.", "answer_schema": {"type": "object"},
    "access": [{"siteId": "tender-portal", "url": "https://tender.example/",
                "credentials": {"username": "u", "password": "s3cret"}}],
    "submission": {"kind": "api", "url": "http://placeholder/v1/runs/current/answer"},
}


class FakeServer:
    """One HTTP server for the API and the models: answers each request with the next scripted reply per path."""

    def __init__(self, replies: dict[str, list[tuple[int, dict]]]):
        self.replies, self.requests = replies, []
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def handle_one(self):
                length = int(self.headers.get("content-length") or 0)
                body = json.loads(self.rfile.read(length)) if length else None
                # self.headers matches header names in any case: urllib sends X-api-key
                fake.requests.append({"method": self.command, "path": self.path, "headers": self.headers,
                                      "body": body})
                status, reply = fake.replies[self.path].pop(0)
                data = json.dumps(reply).encode()
                self.send_response(status)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            do_GET = do_POST = handle_one

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


class Case(unittest.IsolatedAsyncioTestCase):
    def serve(self, replies):
        server = FakeServer(replies)
        self.addCleanup(server.close)
        return server


class ReadingTheRun(Case):
    def test_a_run_reads_its_task_with_the_run_token(self):
        api = self.serve({"/v1/runs/current": [(200, RUN)]})
        run, token = contract.load_run({"CHALLENGE_API_URL": api.url + "/", "CHALLENGE_RUN_TOKEN": "rt_secret"})
        self.assertEqual((run, token), (RUN, "rt_secret"))
        [request] = api.requests
        self.assertEqual(request["method"], "GET")
        self.assertEqual(request["headers"]["x-api-key"], "rt_secret")
        # the API's edge refuses Python's default urllib agent
        self.assertEqual(request["headers"]["User-Agent"], contract.USER_AGENT)

    def test_a_restarting_api_is_asked_again(self):
        api = self.serve({"/v1/runs/current": [(503, {"error": "restarting"}), (200, RUN)]})
        with patch("time.sleep"):
            run, _ = contract.load_run({"CHALLENGE_API_URL": api.url, "CHALLENGE_RUN_TOKEN": "rt_secret"})
        self.assertEqual(run, RUN)
        self.assertEqual(len(api.requests), 2)

    def test_a_refused_token_ends_the_agent_at_once(self):
        api = self.serve({"/v1/runs/current": [(401, {"error": "unknown token"})]})
        with self.assertRaises(SystemExit) as stop:
            contract.load_run({"CHALLENGE_API_URL": api.url, "CHALLENGE_RUN_TOKEN": "rt_wrong"})
        self.assertIn("HTTP 401", str(stop.exception))
        self.assertEqual(len(api.requests), 1)

    def test_locally_it_reads_the_scouting_run_that_atira_scout_env_exports(self):
        run, token = contract.load_run({"CHALLENGE_SCOUTING": json.dumps(RUN)})
        self.assertEqual((run, token), (RUN, None))

    def test_without_a_run_or_the_scouting_logins_it_says_how_to_load_them(self):
        with self.assertRaises(SystemExit) as stop:
            contract.load_run({})
        self.assertIn("atira scout --env", str(stop.exception))
        with self.assertRaises(SystemExit) as stop:
            contract.load_run({"CHALLENGE_SCOUTING": "not json"})
        self.assertIn("atira scout --env", str(stop.exception))


    def test_seconds_left_counts_down_to_the_runs_expiry(self):
        run = {**RUN, "expires_at": 1_700_000_600_000}
        self.assertEqual(contract.seconds_left(run, now=1_700_000_000), 600)
        self.assertEqual(contract.seconds_left({**RUN, "expires_at": None}), float("inf"))

class SubmittingTheAnswer(Case):
    def run_on(self, api):
        return {**RUN, "submission": {"kind": "api", "url": api.url + "/v1/runs/current/answer"}}

    def test_the_answer_is_posted_as_json_with_the_run_token(self):
        api = self.serve({"/v1/runs/current/answer": [(400, {"issues": ["total_net is missing"]}),
                                                      (202, {"status": "accepted"})]})
        run = self.run_on(api)
        self.assertEqual(contract.submit_answer(run, "rt_secret", {"bid": True})[0], 400)
        status, body = contract.submit_answer(run, "rt_secret", {"bid": True, "total_net": 1.5})
        self.assertEqual((status, json.loads(body)), (202, {"status": "accepted"}))
        request = api.requests[-1]
        self.assertEqual((request["method"], request["body"]), ("POST", {"bid": True, "total_net": 1.5}))
        self.assertEqual(request["headers"]["x-api-key"], "rt_secret")

    def test_a_site_submission_or_a_local_run_is_not_posted(self):
        with self.assertRaises(ValueError):
            contract.submit_answer({**RUN, "submission": {"kind": "site", "url": "https://tender.example/"}},
                                   "rt_secret", {})
        with self.assertRaises(ValueError):
            contract.submit_answer(RUN, None, {})


class TheBrowsers(unittest.TestCase):
    def test_cdp_urls_lists_every_browser_of_the_run(self):
        both = ["http://127.0.0.1:9222", "http://127.0.0.1:9223"]
        self.assertEqual(browsers.cdp_urls({"CDP_URLS": json.dumps(both), "CDP_URL": both[0]}), both)
        self.assertEqual(browsers.cdp_urls({"CDP_URL": both[0]}), both[:1])
        self.assertEqual(browsers.cdp_urls({"CDP_URLS": "broken", "CDP_URL": both[0]}), both[:1])
        self.assertEqual(browsers.cdp_urls({}), [])


class TheModels(Case):
    async def test_one_call_to_qwen(self):
        reply = {"id": "c1", "object": "chat.completion", "created": 0, "model": models.QWEN_MODEL, "choices": [
            {"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": "Weiter"}}]}
        qwen = self.serve({"/v1/chat/completions": [(200, reply)]})
        with patch.dict(os.environ, {"QWEN_BASE_URL": qwen.url + "/v1", "QWEN_API_KEY": "rt_secret"}):
            self.assertEqual(await models.ask_qwen("Which button submits?"), "Weiter")
        [request] = qwen.requests
        self.assertEqual(request["body"]["model"], models.QWEN_MODEL)
        self.assertEqual(request["headers"]["Authorization"], "Bearer rt_secret")

    async def test_one_decision_from_clef(self):
        clef = self.serve({"/v1/decide": [(200, {"answers": {"q": {"type": "noul", "noul": 0.97}}})]})
        with patch.dict(os.environ, {"CLEF_BASE_URL": clef.url + "/", "CLEF_API_KEY": "local"}):
            self.assertEqual(await models.ask_clef({"page": "Angebot eingegangen"}, "Is the bid in?"), 0.97)
        [request] = clef.requests
        self.assertEqual(request["body"]["questions"], {"q": {"type": "noul", "instructions": "Is the bid in?"}})
        self.assertEqual(request["headers"]["Authorization"], "Bearer local")


class TheEntryPoint(Case):
    """main() with a fake browser: no answer exits cleanly, an answer is submitted."""

    async def start(self, environment, answer=None):
        async def connect(playwright, number=0):
            return "browser", "page"

        async def solve(task, page):
            self.seen = (task, page)
            return answer

        out = io.StringIO()
        with patch.dict(os.environ, environment, clear=True), patch.object(browsers, "connect", connect), \
                patch.object(agent, "solve", solve), redirect_stdout(out):
            code = await agent.main()
        return code, out.getvalue()

    async def test_without_an_answer_it_exits_cleanly(self):
        api = self.serve({"/v1/runs/current": [(200, RUN)]})
        code, out = await self.start({"CHALLENGE_API_URL": api.url, "CHALLENGE_RUN_TOKEN": "rt_secret"})
        self.assertEqual(code, 0)
        self.assertEqual(self.seen, (RUN, "page"))
        self.assertIn("No answer to submit", out)
        self.assertEqual([request["method"] for request in api.requests], ["GET"])
        self.assertNotIn("s3cret", out)

    async def test_an_answer_is_submitted_once(self):
        api = self.serve({"/v1/runs/current/answer": [(202, {"status": "accepted"})]})
        api.replies["/v1/runs/current"] = [(200, {**RUN, "submission": {
            "kind": "api", "url": api.url + "/v1/runs/current/answer"}})]
        code, out = await self.start({"CHALLENGE_API_URL": api.url, "CHALLENGE_RUN_TOKEN": "rt_secret"}, {"bid": 1})
        self.assertEqual(code, 0)
        self.assertIn("HTTP 202", out)
        self.assertEqual(api.requests[-1]["body"], {"bid": 1})

    async def test_locally_the_answer_is_printed_not_submitted(self):
        code, out = await self.start({"CHALLENGE_SCOUTING": json.dumps(RUN)}, {"bid": 1})
        self.assertEqual(code, 0)
        self.assertIn('{"bid": 1}', out)
        self.assertIn("local", out)


if __name__ == "__main__":
    unittest.main()
