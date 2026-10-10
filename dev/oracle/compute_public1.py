"""Reference answer for public-1, worked out by hand from the scouting sites on 2026-10-10.

DEV ONLY — never copy anything from this file into agent/. It exists so we can score local runs field by field
(dev/eval/score.py) without waiting for a sandbox test run. Every input below is quoted from the sites; fields
marked UNVERIFIED must be confirmed by a test run's per-field points before we trust them.
"""
import json
from decimal import ROUND_FLOOR, ROUND_HALF_UP, Decimal as D
from math import ceil
from pathlib import Path

C = D("0.01")
r2 = lambda x: x.quantize(C, ROUND_HALF_UP)

# --- tender + Q&A -------------------------------------------------------------------------------------------
# LV: 01.01 24 Stk A-1 | 01.02 29 Stk A-2 | 01.03 16 Stk A-2 | 01.04 21 Stk A-4 | 01.05 22 Satz A-5
# Q&A quantity changes: 01.01 -> 26 (8148, 12.01) then 25 (3916, 16.01) => 25
#                       01.05 -> 26 Satz (8151, 24.01 11:00Z), 32 Satz (8152, 13:00Z) withdrawn by 8153 => 26 Satz
# Spec R-6156 (status "gültig"): A-5 1 Satz = 5 Stück => 130
qty = {"01.01": 25, "01.02": 29, "01.03": 16, "01.04": 21, "01.05": 26 * 5}

# Material selection (spec R-6156 + amendments + Technikfreigabe):
#   A-1 80/200/210/Schwenkauge/EN 1493 -> HZ-1609 (HZ-8405 is status 03 gesperrt)
#   A-2 63/usable 489 -> amendments 485 (22.01) then 482 (23.01) -> +2*9 (ER-4001) = 500 /250/Flansch/EN 1493 -> HZ-2170
#   A-4 50/usable 576 + 2*12 (ER-9526) = 600 /210/Schwenkauge/none -> HZ-3916 (HZ-4889 is Flansch)
#   A-5 125/300/250/Flansch/none -> HZ-6932 (HZ-3567 has stroke 325)
mat = {"01.01": "HZ-1609", "01.02": "HZ-2170", "01.03": "HZ-2170", "01.04": "HZ-3916", "01.05": "HZ-6932"}

# ERP (VK13 valid 2027, scales: 1 / 10 / 25; MMBE free stock; MM03 lead times; search row data)
erp = {
    "HZ-1609": dict(cur="EUR", unit=1, scales={1: D("3117.71"), 10: D("2930.65"), 25: D("2743.58")},
                    carton=11, alloc_g=3100, mass_g=2125, stock=31, lead_stock=2, lead_prod=7),
    "HZ-2170": dict(cur="USD", unit=100, scales={1: D("73697"), 10: D("69275"), 25: D("64853")},
                    carton=3, alloc_g=875, mass_g=1875, stock=29, lead_stock=1, lead_prod=11),
    "HZ-3916": dict(cur="USD", unit=100, scales={1: D("206169"), 10: D("193799"), 25: D("181429")},
                    carton=11, alloc_g=2125, mass_g=625, stock=14, lead_stock=3, lead_prod=8),
    "HZ-6932": dict(cur="EUR", unit=100, scales={1: D("212063"), 10: D("199339"), 25: D("186615")},
                    carton=7, alloc_g=2125, mass_g=1375, stock=51, lead_stock=1, lead_prod=12),
}

# Mails (exact reference, authorised sender, dated <= 2027-01-25T09:00Z)
FX = {"USD": D("1.0873")}            # Devisenfixing FX-6282 (24.01); the 26.01 one (1.2500) is future-dated
MET = D("3.15")                      # Preisindizes MET-4607 window 25.01.2027-28.02.2027
DISCOUNT = D("5")                    # Rabattfreigabe 24.01 17:00 conditional: >= 1.00 EUR undiscounted net
PACK_TARIFF = D("13.25")             # Verpackungstarife VP-1134 (all positions)
POOL_FEE, POOL = D("517.25"), ["01.03", "01.04", "01.05"]          # Rüstpools RP-5730
BONUS, BONUS_BANDS = ["01.03", "01.04", "01.05"], [(D("0"), D("5000"), D("0")),
                                                     (D("5000"), D("15000"), D("0.02")),
                                                     (D("15000"), None, D("0.05"))]   # Projektbonus PB-1474
SETUP = {"01.03": D("85.00"), "01.05": D("57.50")}
# Konditionsgruppen: 01.02 K-3650 base_only, 01.03 K-2264 none, 01.05 K-9018 base_and_surcharge; others default
SCOPE = {"01.01": "base_only", "01.02": "base_only", "01.03": "none", "01.04": "base_only",
         "01.05": "base_and_surcharge"}
# Terminfreigabe: key per position -> (article, weeks, ceiling)
TZ = {"01.01": ("HZ-1609", 3, 24), "01.02": ("HZ-2170", 3, 45), "01.03": ("HZ-2170", 3, 44),
      "01.04": ("HZ-3916", 3, 21), "01.05": ("HZ-6932", 3, 160)}


def largest_remainder(total: D, weights: dict[str, D]) -> dict[str, D]:
    cents = int((total * 100).to_integral_value())
    wsum = sum(weights.values())
    raw = {p: D(cents) * w / wsum for p, w in weights.items()}
    base = {p: int(v.to_integral_value(ROUND_FLOOR)) for p, v in raw.items()}
    rest = cents - sum(base.values())
    for p in sorted(weights, key=lambda p: (-(raw[p] - base[p]), p))[:rest]:
        base[p] += 1
    return {p: D(c) / 100 for p, c in base.items()}


def main():
    base = {}
    for p, q in qty.items():
        e = erp[mat[p]]
        scale = max(s for s in e["scales"] if s <= q)
        per_piece = e["scales"][scale] / e["unit"]
        base[p] = r2(per_piece * FX.get(e["cur"], D(1)))
    undiscounted = r2(sum(qty[p] * base[p] for p in qty))
    d = DISCOUNT / 100 if undiscounted >= D("1.00") else D(0)

    pool = largest_remainder(POOL_FEE, {p: D(qty[p] * erp[mat[p]]["alloc_g"]) for p in POOL})
    g = {p: qty[p] * base[p] for p in BONUS}
    gt = sum(g.values())
    credit = r2(sum(max(D(0), min(gt, hi if hi is not None else gt) - lo) * rate for lo, hi, rate in BONUS_BANDS))
    bonus = largest_remainder(credit, g)

    pooled = {}
    for p in qty:
        pooled[mat[p]] = pooled.get(mat[p], 0) + qty[p]

    positions, total = [], D(0)
    for p in sorted(qty):
        q, e = qty[p], erp[mat[p]]
        mass = D(e["mass_g"]) / 1000 * MET
        setup = SETUP.get(p, D(0)) + pool.get(p, D(0))
        pack = ceil(q / e["carton"]) * PACK_TARIFF
        b = bonus.get(p, D(0))
        disc_base = base[p] * (1 - d) if SCOPE[p] != "none" else base[p]
        disc_mass = mass * (1 - d) if SCOPE[p] == "base_and_surcharge" else mass
        unit = r2(disc_base + disc_mass + (setup + pack - b) / q)
        art, weeks, ceiling = TZ[p]
        if art == mat[p] and pooled[mat[p]] <= ceiling:
            lead = weeks
        else:
            lead = e["lead_stock"] if pooled[mat[p]] <= e["stock"] else e["lead_prod"]
        total += q * unit
        positions.append(dict(position=p, material_number=mat[p], quantity=q, unit_price=float(unit),
                              lead_time_weeks=lead, base_unit_price=float(base[p]),
                              surcharge_per_unit=float(r2(mass + setup / q)),
                              packaging_per_unit=float(r2(pack / q)), rebate_total=float(b)))
    answer = dict(tender_reference="VG-2027-7093", decision="bid", currency="EUR",
                  discount_percent=float(DISCOUNT if d else 0), positions=positions, total_net=float(r2(total)))
    out = Path(__file__).with_name("public-1.json")
    out.write_text(json.dumps(answer, indent=2, ensure_ascii=False) + "\n")
    print(json.dumps(answer, indent=2, ensure_ascii=False))
    print("undiscounted net order value:", undiscounted, "| bonus credit:", credit, "| pool:", pool)


if __name__ == "__main__":
    main()
