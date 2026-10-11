# Hosted eval authentication failure

The first development agent run `run_4ecb248a3470364d` cannot provide a valid
quality/performance comparison because none of the four sites authenticated.
No replacement agent runs were started by this diagnostic task.

A fresh platform preview `pv_af1f788ed294d0f8` targets the exact current development
version `evv_d920a5c072b9084d80fe` of eval `ev_25cdea6d26f33fa1`. Its API response
contains nonempty `name`, `url`, `username`, and `password` strings for `tender`,
`specification`, `erp`, and `mail`, matching the four manifest names. Usernames
have the documented preview `p_` prefix. The probe submits the exact returned
username/password strings through the actual rendered form using URL encoding;
there is no trimming, regeneration, or credential substitution.

All four login pages return HTTP 200. All four login POSTs return HTTP 200 with
an invalid-login page. The adapter's upstream `/v1/eval-worlds/verify` response is
HTTP **401**, for both Python urllib and an equivalent request using the official
Node `fetch` transport. Explicit User-Agent and both accepted bearer/header
forms do not change the failure. The hosted error message's retained vocabulary
contains **login** and **site**, and excludes token/bearer/world/version/expiry
terms. The full message was intentionally not exposed by the deployed diagnostic
instrumentation, so its exact wording is unavailable here. We cannot honestly
name an exact server-side cause from those two words.

A separate read-only request with a personal token confirms the endpoint's
invalid-world-token error is `authentication_error: invalid eval world token`,
which differs from the hosted login/site rejection. This supports a valid world
bearer reaching the login check, rather than a transport or User-Agent failure.
The official OpenAPI contract requires `{site, username, password}` and limits
successful verification to credentials on the world token's current version;
the preview and current version IDs in this reproduction match.

To reproduce using an existing preview response saved in an ignored private
file, without printing credentials:

```sh
python3 dev/evals/diagnostics/probe_hosted_auth.py PRIVATE_PREVIEW_RESPONSE.json
```

The resulting sanitized report is `hosted-auth-result.json`. A platform operator
can also run `probe_node_verify.mjs PRIVATE_LOGIN_JSON` from the eval world's
existing environment, where `ATIRA_API_URL` and `ATIRA_EVAL_TOKEN` are present.
The input object's fields are `site`, `username`, and `password`. That probe
prints the upstream error type and text after redacting every supplied secret;
it makes no answer request and does not export the world's bearer. The probe makes
only login GET/POST requests. It does not submit a bid, read expected answers,
mint new previews, launch an agent run, or cancel any backend run.

Required next step: inspect the platform's verification rejection for this eval,
version, preview and site names, including how each emitted login is attached to
the current version/site. If the platform expects a different site identifier,
that identifier must be returned/documented in the preview/run contract. If the
preview logins are associated with the wrong credential store/version, the
platform must correct that association. A hosted `/verify` 200 with its expected
preview ID on all four sites is required before another agent comparison.

The Node fallback in the currently hosted development version was temporary
diagnostic instrumentation, invoked only after a read-only verify 401. It never
retried the answer endpoint. It has now been removed from the permanent adapter
source; no further deployment was performed, so the hosted diagnostic version
and permanent source intentionally differ until the infrastructure is corrected. Six holdout
versions and the locale version remain untouched after the original deployment.
All eight worlds remain private and preserved. There is no further redeployment
planned until a concrete protocol correction is identified.
