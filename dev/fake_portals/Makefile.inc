# Developer-only synthetic portals. Never included in the submission package.
FAKE_DIR := dev/fake_portals
FAKE_PYTHON ?= $(abspath agent/.venv/bin/python)
FAKE_PROFILE ?= workbench
FAKE_CASE ?= baseline
FAKE_PORT ?= 18540
FAKE_OUT ?= $(abspath dev/runs/fake-portals)
FAKE_PROXY ?= http://127.0.0.1:8765/qwen/v1

.PHONY: fake-test fake-browser fake-serve fake-agent fake-matrix

fake-test: ## Synthetische Portal-/Scorer-Tests ohne Modelle (Browser optional)
	cd $(FAKE_DIR) && PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -v

fake-browser: ## Alle lokalen Chromium-Tests, ohne Modell oder externe Websites
	cd $(FAKE_DIR) && FAKE_PORTALS_BROWSER_TESTS=1 PYTHONDONTWRITEBYTECODE=1 "$(FAKE_PYTHON)" -m unittest discover -v
	cd $(FAKE_DIR) && PYTHONDONTWRITEBYTECODE=1 "$(FAKE_PYTHON)" verify_browser.py
	cd $(FAKE_DIR) && PYTHONDONTWRITEBYTECODE=1 "$(FAKE_PYTHON)" verify_submission.py

fake-serve: ## Vier Fake-Portale starten (FAKE_PROFILE, FAKE_CASE, FAKE_PORT)
	python3 $(FAKE_DIR)/variant_server.py --profile "$(FAKE_PROFILE)" --case "$(FAKE_CASE)" --port "$(FAKE_PORT)"

fake-agent: ## Ein echter Agentenlauf lokal; benötigt laufenden Atira-Modellproxy
	"$(FAKE_PYTHON)" $(FAKE_DIR)/run_agent.py --agent $(AGENT) --python "$(FAKE_PYTHON)" --profile "$(FAKE_PROFILE)" --case "$(FAKE_CASE)" --model-base "$(FAKE_PROXY)" --out "$(FAKE_OUT)" --parallel 2

fake-matrix: ## Alle sechs UIs / fünf Datenfälle sequenziell, ein eingefrorener Agent
	"$(FAKE_PYTHON)" $(FAKE_DIR)/run_matrix.py --agent $(AGENT) --python "$(FAKE_PYTHON)" --model-base "$(FAKE_PROXY)" --out "$(FAKE_OUT)"
