# Private synthetic eval worlds

This directory copies the existing invented portal fixtures into an isolated,
development-only bundle builder. The originals under the external fake-portals
workspace are untouched. Nothing here belongs in the submitted `agent/` package.

`build.py` creates eight reproducible bundles under ignored `bundles/` and records
their data and runtime SHA-256 hashes in `suite.json`. The two development worlds
are available for tuning. Freeze the six holdouts before development backend
runs; after reading their run results, treat them as development data. A repaired
agent requires fresh holdout seeds/version names for another unseen-site claim.

| Bundle | Structure and business rule | Deterministic fault |
| --- | --- | --- |
| dev-classic | Original classic navigation, counterfactual prices/quantities/approval | None |
| dev-locale | Workbench UI, locale boundary | None |
| holdout-withdrawal | Legacy navigation, future withdrawal, commercial evidence relocated to ERP | None |
| holdout-stock | Busy office UI, pooled physical stock | ERP session expires once on first catalogue access |
| holdout-scanned | Scanned specification UI | Nested viewer replaces control identities |
| holdout-blocked | Modern UI, blocked material and justified no-bid | None |
| holdout-documents | Room/directory discovery, tender commercial archive, three positions | Delayed alternate viewer; readable page with stuck decorative subresource |
| holdout-search | Search-first discovery, pooled stock, three positions | Foreign-reference signing message |

References, prices, approval percentages, effective quantities, dates, commercial
keys, material IDs, ordering and production lead times vary by seed. An
unauthorized, recent approval is included in every world. Relocated records are
removed from the mailbox, including direct message URLs: the alternative archive
is the only available source. Its sender, date and exact source body remain
visible. These worlds retain parts of the same portal kernel; passing them is
useful evidence, but does not establish unrestricted generalization. The new
structures alter discovery and evidence placement, rather than providing only
new CSS. Delay routes have visible alternatives and need not be visited; a
backend result does not demonstrate delay recovery unless its trace visited one.

## Build and offline verification

From the repository root:

```sh
python3 dev/evals/build.py
agent/.venv/bin/python -m pytest dev/evals/tests -q
```

The independent Decimal oracle imports no agent resolver. It first reproduces
all five previously hand-derived goldens, then derives counterfactual answers.
It implements the deliberately narrower synthetic grammar: dated quantity
changes/withdrawals, sender/reference/cutoff filtering, exact technical matches,
price tiers, currencies, mass surcharge windows, two discount scopes, setup,
carton rounding and shared stock. It is not a replacement for the full challenge
business-rule evaluator.

Tests use a local API stub and real HTTP requests, with two simultaneous run
identities. They check cookie tampering and role boundaries, logout/revocation,
isolated drafts/mail/TANs, foreign TAN rejection, exact forwarding of deliberately
incorrect UI values, single forwarding for runs, repeated preview submissions,
schema-error recovery, six skins, both new discovery structures, fault endpoints,
private-file route denial, and deterministic bundle generation. They launch no
model calls and consume no backend runs.

## Private deployment

Use a verified task-local Atira CLI which exposes `eval`, not the older globally
installed executable. For example, after the root task verifies its checksum:

```sh
node dev/runs/optimization-7de9cda/atira.mjs eval deploy dev/evals/bundles/dev-classic --slug dev-classic --no-preview
```

Keep the default private visibility. Do not pass `--public`. Record the returned
eval ID/version along with both suite hashes; deployment metadata is stored in
each bundle's ignored `.atira/` folder. Deployment and backend execution are
performed by the root task according to its shared 12-run budget. The offline tests do not confirm hosting. `deployments.json` records the eight
private eval IDs, package/version hashes and platform status checks: every intended
current version is ready and private. An authenticated hosted-site smoke test and
agent runs remain separate validation steps. Hosted authentication failed on all
four development sites with platform verification HTTP 401; see
`diagnostics/HOSTED-AUTH-FAILURE.md`. No private agent result is valid until that
infrastructure failure is resolved.

The manifest starts `python3 world.py`, listens on the four supplied `PORT_*`
values at `0.0.0.0`, and uses `ATIRA_SITE_URL_*` for cross-site links. The world
calls only `ATIRA_API_URL/v1/eval-worlds/{verify,answer}` with the ephemeral bearer
from `ATIRA_EVAL_TOKEN`. Every business page requires a signed, role-bound cookie;
verification is refreshed after ten seconds. State is keyed by run/preview
identity and expired state is removed in amortized cleanup. Passwords remain only
in memory for refresh and are never written to logs or bundles. Slow verification
on one identity does not hold the global registry lock.

Final signing uses the kernel's draft and the run's actual TAN. The exact
normalized UI bid is forwarded, without importing or substituting expected
answers. A successful response returns a visible acceptance receipt immediately;
there is no redirect through credentials that the platform has just invalidated.
A schema rejection preserves the draft for correction. A transport failure is
reported as a submission infrastructure failure; an ambiguous response must be
investigated in platform logs, since an end-to-end response-loss guarantee is not
established by the stub tests.
