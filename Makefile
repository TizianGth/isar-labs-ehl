# Befehle aus dem Repo-Root. Das hochgeladene Paket ist immer agent/ (nie der Root: dev/ enthält die Public-Lösung).
SHELL := /bin/bash
.DEFAULT_GOAL := help

AGENT := agent
RUNS  := dev/runs
OUT   ?= $(RUNS)/local.json

include dev/fake_portals/Makefile.inc

.PHONY: help setup test leaks manifest check test-run run-local score replay status clean

help: ## Diese Übersicht
	@grep -hE '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*## "} {printf "  make %-10s %s\n", $$1, $$2}'

setup: ## Einmalig: Abhängigkeiten und Chromium für lokale Läufe installieren
	cd $(AGENT) && uv sync && uv run --no-sync playwright install chromium

test: ## Unit-Tests des Agenten (nur synthetische Daten)
	cd $(AGENT) && uv run --no-sync pytest -q

leaks: ## Prüft, dass keine Werte des Public-Szenarios im Paket stehen
	python3 dev/check_no_leaks.py

manifest: ## Zeigt, welche Dateien hochgeladen würden (lädt nichts hoch)
	atira test-run ./$(AGENT) --dry-run

check: test leaks manifest ## Alles vor einem Test-Run: Tests, Leak-Check, Paket-Manifest

test-run: check ## Sandbox-Test-Run des Pakets agent/ starten und Logs live verfolgen
	atira test-run ./$(AGENT) --follow

submit: check ## Abgabe des Pakets agent/ starten und Logs live verfolgen
	atira submit ./$(AGENT) --follow

run-local: ## Lokaler Lauf gegen die Scouting-Sites, ohne Abgabe (vorher: atira models proxy)
	cd $(AGENT) && eval "$$(atira scout --env)" && \
	  uv run --no-sync python main.py --dry-run --out ../$(OUT) > ../$(OUT:.json=.log) 2>&1; \
	  echo "Log: $(OUT:.json=.log)  Ergebnis: $(OUT)  ->  make score"

score: ## Feldweise Punkte eines lokalen Laufs gegen das (plattformbestätigte) Oracle
	python3 dev/eval/score.py $(OUT)

replay: ## Nur Regeln + Abgabe (Dry-Run) mit der Evidenz eines früheren lokalen Laufs
	cd $(AGENT) && eval "$$(atira scout --env)" && \
	  uv run --no-sync python main.py --dry-run --evidence ../$(OUT) --out ../$(RUNS)/replay.json > ../$(RUNS)/replay.log 2>&1; \
	  echo "Log: $(RUNS)/replay.log"

status: ## Letzte Test-Runs und Submissions des Teams
	atira status

clean: ## Caches entfernen (Läufe in dev/runs bleiben)
	find $(AGENT) dev -name __pycache__ -type d -prune -exec rm -rf {} +
	rm -rf $(AGENT)/.pytest_cache
