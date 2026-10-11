"""Capture offline synthetic request character sizes with the actual prompt builders; never call a model.
Usage: python dev/measure_reuse_requests.py <agent_dir> [output.json]. Characters are not metered tokens."""
import asyncio, json, re, sys, time, os
os.environ["RFQ_CLEF_ROUTE"] = "0"
os.environ["RFQ_PROFILE"] = "0"
from types import SimpleNamespace
sys.path.insert(0, sys.argv[1])
from rfq import llm
from rfq.agent import orchestrator as O
from rfq.agent.fused import FusedExplorer
from rfq.agent.explorer import TOOLS
from rfq.agent.prompts import FUSED_SYSTEM, EXTRACT_SYSTEM

LOGIN = '[e1] textbox "Benutzer"\n[e2] textbox "Passwort" (password)\n[e3] button "Anmelden"'
def table(n, cols, row):
    return '| ' + ' | '.join(cols) + ' |\n' + '\n'.join('| ' + ' | '.join(row(i)) + ' |' for i in range(n))
MAILLIST = ('heading "Posteingang"\n[e1] link "Posteingang (12)" -> /m?f=in\n[e2] link "Archiv (4)" -> /m?f=ar\n'
            '[e3] textbox "Suche"\n[e4] button "Suchen"\n' +
            table(10, ['Von', 'Betreff', 'Datum'], lambda i: [f'user{i}@syn.test', f'[e{10+i}] link "SYN-0 Betreff {i}" -> /m/{i}', f'0{i%9+1}.02.2027 10:0{i%9} (TT/MM)'])
            + '\nSeite 1 von 3 [e30] link "Weiter" -> /m?p=2')
LIST = ('heading "Positionen"\n' + table(12, ['Pos', 'Text', 'Menge', 'Einheit', 'Anf.'], lambda i: [f'{i+1}.0', f'Zylinder Typ {i}', f'{(i+1)*10}', 'Stk', f'A-{i}'])
        + '\n[e1] link "Bieterfragen" -> /qa\n[e2] link "Dokumente" -> /docs\n' + '\n'.join(f'Absatz {i}: Lieferbedingungen gelten gemäß Abschnitt {i}.' for i in range(25)))
DETAIL = ('[e1] link "Zurück" -> /list\nheading "Material MAT-0"\n' + '\n'.join(f'Feld {i}: Wert {i},5 mm (de-DE)' for i in range(30))
          + '\n' + table(4, ['Ab Menge', 'Preis'], lambda i: [str(10**i), f'{i+1}2,50 EUR (de-DE)']) + '\n[e2] link "Staffeln" -> /scales')
PAGES = {'login': LOGIN, 'mail_list': MAILLIST, 'table_page': LIST, 'detail': DETAIL}

def capture():
    run = {'expires_at': (time.time() + 1200) * 1000, 'run_id': 'syn', 'brief': ''}
    orch = O.Orchestrator(run, {})
    orch.brief = {'tender_reference': 'SYN-0', 'scenario_datetime_utc': '2027-01-01T00:00:00Z', 'director_localpart': 'boss',
                  'authorized_records': [{'topic': t, 'subject_keyword': f'Kw{t}', 'sender_localpart': 'x'} for t in
                                         ('clause_rules', 'tech_release', 'packaging', 'delivery', 'fx')]}
    missions = {}
    async def capture(site, mission, attempts=2):
        missions[mission.name] = orch._remembering(site, mission)
    orch.run_mission = capture
    async def no_erp(): return []
    orch.erp_phase = no_erp
    orch.site_for = lambda role: SimpleNamespace(url='https://' + role + '.syn.test/', site_id=role)
    asyncio.run(orch.gather_evidence())
    # ERP mission through the real builder
    reqs = [{'ref': 'A-1', 'bore_mm': '50', 'stroke_mm': '200', 'pressure_bar': '10', 'mounting': 'Flansch', 'catalog_group': 'G1'}]
    try:
        missions['erp:G1'] = orch._remembering(SimpleNamespace(url='https://erp.syn.test/'), orch.erp_mission('G1', reqs))
    except Exception as e:
        print('erp mission builder failed', e)
    out = []
    for mname, mission in missions.items():
        for pname, page in PAGES.items():
            captured = {}
            async def ask(system, user, **kw):
                captured['system'], captured['user'] = system, user
                return {'actions': [{'a': 'finish', 'missing': ['x']}]}
            llm.ask_json = ask
            async def observe(**kw): return page
            site = SimpleNamespace(site_id=mname, url='https://s.syn.test/', page=SimpleNamespace(url='https://s.syn.test/p'), observe=observe)
            mission.deadline = time.time() + 600
            ex = FusedExplorer(site, mission, orch.extract)
            ex.history = ['click -> clicked; page changed', 'extract_urls -> 3 sources extracted: ' + 'x' * 140]
            ex.last_result = 'e1: typed; field now contains ...; then clicked'
            asyncio.run(ex.run())
            if 'user' not in captured:
                continue
            u = captured['user']
            marks = ['MISSION', 'CONTEXT:', 'OPERATIONS:', 'CHECKLIST:', 'FACT SHAPES', 'OTHER FACT KINDS', 'LAST TWO ACTIONS:', 'LAST RESULT:', '<<<UNTRUSTED SNAPSHOT>>>']
            pos = [(u.find(m), m) for m in marks if u.find(m) >= 0] + [(len(u), 'END')]
            pos.sort()
            seg = {m: pos[i + 1][0] - p for i, (p, m) in enumerate(pos[:-1])}
            seg['system'] = len(captured['system'])
            seg['total'] = len(u) + len(captured['system'])
            out.append({'mission': mname, 'page': pname, **seg})
            if pname == 'login' and '--dump' in sys.argv:
                print('=====', mname); print(u)
    return out

if __name__ == '__main__':
    out = capture()
    print(json.dumps({"requests": len(out), "characters": sum(row["total"] for row in out)}))
    if len(sys.argv) > 2:
        with open(sys.argv[2], 'w') as handle:
            json.dump(out, handle, indent=2)
