# Tender portal login smoke test

This package is for `atira test-run` connectivity checks only. It signs in to the tender portal using the current
run's credentials and verifies that the authenticated tender-search page is available. It does not create, save,
sign, or submit a bid, and it must not be used with `atira submit`.

Because the challenge run expects a signed bid, Atira will mark this smoke-only run as having no answer. The useful
result is in the run logs: the portal login and authenticated page check should pass before the agent exits.
