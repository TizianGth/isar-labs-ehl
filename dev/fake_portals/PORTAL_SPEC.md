# Mini RFQ portals: implementation contract

Implement server.py and test_server.py here, no dependencies outside Python's stdlib.
Read cases.json as the sole source of fixture facts. Do not change that file.
This is a functional browser test environment, not a mock of an LLM or a production application.

## Public Python interface

`PortalCluster(case_name='baseline', profile='classic', base_port=0)`:
- load this directory's cases.json; select named case, validate classic/modern/legacy profile.
- `.start()` starts four ThreadingHTTPServer instances on 127.0.0.1, one per role; returns self.
- `.origins` is dict role->root URL for `tender`, `specification`, `erp`, `mail`.
- `.snapshot()` returns JSON-serializable copy of state, with `submitted` (normalized answer or null),
  `receipt` (string/null), `tan_requests` count, `events` (request/mutation events). No expected answer.
- `.stop()` shuts down, closes, and joins all servers. Also support context-manager use.
- expose scenario facts as `.case`, profile as `.profile`.
- CLI `python server.py --profile modern --case baseline --port 0` prints origins JSON and runs until Ctrl-C.
- independent per-cluster state and lock; reject starting twice, support concurrent clusters in tests.
- return useful 404 for unknown paths; no arbitrary file serving or debug/control/answer API endpoints.

## Profiles

classic: simple German navigation, links/tables, conventional multi-page UI.
modern: clearly English labels, different paths from classic, sidebar/tabs and JS-driven views or modal panels.
legacy: root login page, then a genuine same-origin iframe for at least the specification and ERP content;
  plain old table layout, different paths; links remain available to a DOM browser.
All profiles must expose exactly the same selected case facts, preserving explicit numeric locale tags.
Use three truly different paths/navigation presentations, not just CSS color changes. Avoid hidden-only facts.
Keep styles neat but spend effort on testable behavior, not decoration. No external CSS/scripts/fonts/assets.

## Authentication and common UI

Each origin asks for demo username `demo` and password `synthetic-only` on a real form; after POST redirects
to the app and uses a role-specific session cookie (do not accidentally share auth across origins).
Give links contextual visible labels, input labels, real selected options, date `<time datetime=...>` attributes.
Use readable full business prose rather than hidden JSON. Authenticated root should offer home/navigation.
Modern may use a modal; legacy app content must live in an iframe but authentication stays at top level.
Provide concise status legends (R=active/released, B=blocked); content must be the selected case's facts only.

## Tender origin

Home links to tender overview containing exact reference, buyer/title, links to:
- Positions table (position, original qty/unit, requirement reference).
- Paginated Q&A, 2 entries/page, visible dates and exact answer number/text. Real next-page link.
- Submit proposal workflow (clear link or button); never autostart submission.

Proposal form collects bidder company/VAT, offer type main/no_bid, global discount percent/net total,
two position fieldsets with labelled material number, quantity, net unit price, base price,
surcharge per unit, packaging per unit, project rebate total, delivery weeks. Material select has
actual fixture material numbers and readable labels. Permit omission via explicit checkbox per position.
Required declarations: accept tender documents checked, own terms unchecked. Offer no_bid may omit rows.
First POST saves draft and shows a review page with the values the form actually recorded, reference,
type, discount, total, each selected row, declarations, and back/edit link. Do not invent or compute bid values.
Support decimal comma AND decimal point input, using Decimal for parsing; keep numeric JSON output normal.

Review page offers a button to request signing code. POST creates a new TAN mail on the mail origin,
with exact tender reference, sequential request number, TAN value, and readable date; returns signing form.
Then the browser must enter TAN and click final submit, which shows a native confirm dialog before POST.
Server rejects wrong/missing TAN, missing required declarations, own terms, or malformed numeric values.
On valid final POST: store normalized answer as snapshot.submitted, create receipt and render confirmation.
Do not compare numeric values with correct answers or calculate score in the portals; that's an external scorer.
No duplicated final submissions: show existing receipt after repeated final POST.
Submitted JSON matches the agent answer shape:
{tender_reference, decision:'bid'|'no_bid',currency:'EUR',discount_percent,positions:[
 {position,material_number,quantity,unit_price,base_unit_price,surcharge_per_unit,packaging_per_unit,
  rebate_total,lead_time_weeks}],total_net}. Report actual material selected and actual numbers entered.

## Specification origin

Register of technical docs -> revision history with REV-A marked superseded and REV-C marked current.
Current revision pages show each requirement from the fixture, all properties incl. set conversion,
certification and catalog group; at least 2 pages or tabs. A visible link to revision history always exists.
Old revision is a recognizable decoy with incorrect stroke; don't silently label it current.
Do not hide requirements exclusively in JSON or rely on agent knowing the URL.

## ERP origin

Catalog group search form, submits GET query and returns only group's candidates. Display candidate links
to detail page containing dimensions, mounting/certification, status/legend, freely available stock,
inspection/blocked stock separately, stock/production delivery time, packaging units, surcharge mass/index,
allocation weight, and price conditions with date windows, currency, price unit, locale, quantity scales.
Explicitly show certification 'none' versus a named standard. Never autochoose a material.
In at least one profile use tab controls; one profile may have an actual popup opened by a button, but
this must not be required for every candidate or artificially waste navigation time.

## Mail origin

Inbox + archive links/tabs, logged-in mailbox address shown, search combines whitespace-separated
terms with AND over subject/body; retain folder context. Pagination 3 msgs/page, messages with visible
sender/date/subject links. Opened message shows full subject, full sender address, time and ORIGINAL body.
Support query and empty query; never auto-filter away future or wrong-reference decoys.
TAN generated by tender POST appears as new message in inbox; refreshing exposes it. A mailbox search
for old reference words should still be able to find it. No drafts generated by baseline fixture.

## Tests you own

Use unittest/urllib/cookiejar (no extra deps). Exercise login, wrong password, profile-specific paths,
paginated Q&A/mail, ERP group search, render current/old revision, draft-save/review-readback,
TAN creation and wrong-TAN rejection, valid final POST/state/receipt, declarations and duplicate final POST,
and cluster shutdown/isolation. Never post a precomputed answer to a live site. To test HTTP submission,
invent arbitrary values: server must record those values even if they are financially incorrect.
No expected score/answer must be exposed in any served page. Escaping/safe form parsing required.

## Deliverable

Implement fully, run your tests, report changed files/test results and interface/route notes. Keep all work
inside this directory. Codex will independently inspect and run browser tests against the current agent snapshot.
