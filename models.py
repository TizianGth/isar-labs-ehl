"""The free hosted models in short: one call each. Docs: $API/start#models and $API/docs/run-contract.md#llm-calls

Qwen (Qwen3.8-Flash-Next): an OpenAI-compatible chat model with tool calls, images and a 256k context. Every run gets
QWEN_BASE_URL and QWEN_API_KEY; on your machine `atira scout --env` points them at `atira models proxy`, which you
keep running in another terminal.

Clef (Cloudflare Clef): a decision model. POST $CLEF_BASE_URL/v1/decide with a `state`, up to 4 `images` and your
`questions`: "choice" picks one of your options, "noul" gives the probability of yes, "score" rates on your scale.
CLEF_BASE_URL and CLEF_API_KEY are set only where the platform hosts Clef. A run waits only for the models listed in
agent.json "models" to warm up: list ["qwen", "clef"] if your agent relies on Clef.

Any other provider works with your own key, through the run's metering proxy: see the docs above.
"""
import os

import httpx  # comes with the OpenAI SDK
from openai import AsyncOpenAI

QWEN_MODEL = "qwen3.8-flash-next"


def qwen() -> AsyncOpenAI:
    """A client for the hosted Qwen; a cold model takes a while to answer its first call, hence the long timeout."""
    return AsyncOpenAI(base_url=os.environ["QWEN_BASE_URL"], api_key=os.environ["QWEN_API_KEY"], timeout=360)


async def ask_qwen(prompt: str) -> str:
    """One chat call: the reply's text."""
    reply = await qwen().chat.completions.create(model=QWEN_MODEL, messages=[{"role": "user", "content": prompt}])
    return reply.choices[0].message.content or ""


async def ask_clef(state, question: str) -> float:
    """One decision: how likely Clef finds the answer to a yes/no question about `state` (text or JSON) is yes."""
    body = {"state": state, "questions": {"q": {"type": "noul", "instructions": question}}}
    async with httpx.AsyncClient(timeout=120) as client:
        response = await client.post(os.environ["CLEF_BASE_URL"].rstrip("/") + "/v1/decide", json=body,
                                     headers={"Authorization": f"Bearer {os.environ['CLEF_API_KEY']}"})
    response.raise_for_status()
    return response.json()["answers"]["q"]["noul"]
