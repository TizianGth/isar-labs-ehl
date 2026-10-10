# Synthetic portal test environment

This directory is developer tooling, never part of the `agent/` upload.
Use invented data only. Do not copy scenario values or navigation paths into
the runtime. Bind HTTP servers only to loopback. Do not serve goldens or scores.
Keep UI tests, model-driven agent tests and official Atira scores distinct.
Run model-driven tests sequentially through the existing local Atira proxy.
Browser tests need existing Playwright/Chromium; do not silently install them.
Run artifacts belong in `dev/runs/fake-portals/`, not committed source files.
Do not modify the shared agent checkout while another teammate is developing.
Changes here must preserve forms, TAN flow, actual receipt and independent scoring.
