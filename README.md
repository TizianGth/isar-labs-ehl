# isar-labs · Atira EHL Challenge „From RFQ to bid“

Unser Agent liest eine öffentliche Ausschreibung auf vier Websites (Vergabeportal, Lieferantenportal, ERP, Webmail),
berechnet das Angebot nach den Regeln des Briefs und gibt es signiert im Vergabeportal ab. Ein Sprachmodell (Qwen)
navigiert und entscheidet; Python rechnet deterministisch.

**Stand:** Gewertete Submission `sub_6bdf6c7dfe36193f` (Commit `011dadc`): **Quality 100, eligible, 7:19 min, $0,095 —
Platz 1 im Public-Leaderboard** (`run_6e07bf4619c1ff9c`). Robustheits-Features für fremde Datensätze (Hedging, Köder-Mails,
Task-Lifecycle, lange Dokumente) stehen in `docs/11-mvp-status.md`.

## Struktur

| Pfad | Inhalt |
| --- | --- |
| `agent/` | **Das Paket für Atira** (wird hochgeladen): `agent.json`, `main.py`, `rfq/` (Browser, Explorer, Regeln, Abgabe), `tests/` |
| `dev/` | Nie hochgeladen: Oracle (Public-Lösung), HTML-Fixtures, Scorer, Leak-Check, Scout-Tools, Run-Log |
| `docs/` | Doku: Challenge, Architektur, Site-Recon, Rechenregeln, Testing, Status |
| `Makefile` | Alle Befehle aus dem Root (`make help`) |
| `AGENTS.md` | Verbindliche Regeln für Menschen und Coding-Agents |

## Befehle

```bash
make setup      # einmalig: uv sync + Chromium
make test-run   # Tests + Leak-Check + Manifest, dann Sandbox-Test-Run von agent/ (Live-Logs)
make check      # nur die Prüfungen (Tests, Leak-Check, Manifest)
make status     # letzte Test-Runs/Submissions
```

Lokal gegen die Scouting-Sites (zählt nie, keine Abgabe):

```bash
atira models proxy          # Terminal 1, offen lassen
make run-local              # Terminal 2: voller Lauf (~7–12 min), schreibt dev/runs/local.{log,json}
make score                  # Punkte je Feld gegen das Oracle
make replay                 # nur Regeln + Abgabe mit der Evidenz von dev/runs/local.json
```

Voraussetzungen: [`uv`](https://docs.astral.sh/uv/), die Atira-CLI (`atira login`) und Python 3.12.

## Wichtig

- **Paket ist immer `agent/`.** `atira test-run .` im Root ist absichtlich nicht möglich (kein `agent.json` im Root):
  es würde `dev/` mit der Public-Lösung und `docs/` hochladen — bei einer Submission ein Fair-Play-Verstoß.
  Alternativ direkt: `cd agent && atira test-run .`
- **Keine Werte des Public-Szenarios in `agent/`** (`make leaks` prüft das).
- **`atira submit ./agent` nur nach Absprache im Team** — öffentlich, nicht umkehrbar, die letzte Submission geht ins Finale.
- Details: `AGENTS.md`, `docs/11-mvp-status.md` (Stand, Backlog), `docs/04-business-rules.md` (Rechenregeln).
