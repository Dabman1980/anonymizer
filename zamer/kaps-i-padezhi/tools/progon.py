#!/usr/bin/env python3
"""Прогон двух корпусов через обезличиватель из worktree.

Метрика НЕ переписана: используются функции из ocenka.py (копия PEd06 без правок).
Оценка печатается и кладётся в zamer/<metka>.json — оригинал PEd06 не трогаем.

Запуск: progon.py <метка>   (например: do / posle)
"""
import json
import sys
from pathlib import Path

KOREN = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(KOREN / "tools"))
import ocenka  # noqa: E402  — метрика берётся как есть

ANON = Path("/Users/dmitry/ClaudeProjects/real/anonymizer/.claude/worktrees/"
            "agent-a6e0ae2b0745b9cb0")
sys.path.insert(0, str(ANON))
from anonymizer import Anonymizer, NerLayer, SurnameDictLayer  # noqa: E402

_NER = NerLayer()
_SURN = SurnameDictLayer()


def anon():
    """Конфигурация, в которой модуль работает у Аси: regex + NER + словарь.

    use_dict=False принципиально: словарь клиентов — коммерческая тайна.
    """
    a = Anonymizer(use_ner=False, use_dict=False)
    a.ner = _NER
    a.surnames = _SURN
    return a


def progon(teksty):
    return [{"id": t["id"], "vyhod": anon().replace(t["text"])} for t in teksty]


def po_gruppam(teksty, vyhody):
    """Свод по группам корпуса. Считает теми же функциями, что и ocenka.svodka,
    но группы у корпуса-цели свои (K/P/O), а не A/B/C."""
    po_id = {r["id"]: r["vyhod"] for r in vyhody}
    stroki = []
    for t in teksty:
        v = po_id[t["id"]]
        stroki.append({
            "id": t["id"],
            "gruppa": t["gruppa"],
            "propuski": [s for s in t["dolzhno_byt_zamaskirovano"]
                         if not ocenka.zamaskirovan(s, v)],
            "lozhnye": [s for s in t["dolzhno_ostatsya"]
                        if not ocenka.ostalos(s, v)],
            "sohrannost": ocenka.sohrannost(t["text"], v)[0],
            "vyhod": v,
        })
    itog = {}
    for g in sorted({s["gruppa"] for s in stroki}):
        gr = [s for s in stroki if s["gruppa"] == g]
        nado = sum(len(t["dolzhno_byt_zamaskirovano"])
                   for t in teksty if t["gruppa"] == g)
        prop = sum(len(s["propuski"]) for s in gr)
        itog[g] = {
            "spanov": nado,
            "propushcheno": prop,
            "recall": None if nado == 0 else round((nado - prop) / nado, 3),
            "lozhnyh_trevog": sum(len(s["lozhnye"]) for s in gr),
        }
    itog["sohrannost"] = f"{sum(1 for s in stroki if s['sohrannost'])} из {len(stroki)}"
    return stroki, itog


def main():
    metka = sys.argv[1]

    # ── Корпус-хранитель (PEd06, группы A/B/C) — метрика ocenka.py как есть ──
    teksty = json.loads((KOREN / "korpus" / "korpus.json").read_text("utf-8"))["teksty"]
    rez = progon(teksty)
    stroki = ocenka.ocenit(teksty, rez)
    svod = ocenka.svodka(stroki)
    print("=== PEd06 (сторож регрессии) ===")
    print(json.dumps(svod, ensure_ascii=False, indent=2))
    for s in stroki:
        if s["propuski"] or s["lozhnye_trevogi"] or not s["sohrannost"]:
            print(f"  {s['id']}: пропуски={s['propuski']} ложные={s['lozhnye_trevogi']} "
                  f"сохранность={'ок' if s['sohrannost'] else 'СЛОМАНА'}")

    # ── Корпус-цель (K/P/O) ──
    cel = json.loads((KOREN / "korpus" / "korpus_kaps_padezhi.json").read_text("utf-8"))["teksty"]
    rez2 = progon(cel)
    stroki2, svod2 = po_gruppam(cel, rez2)
    print("\n=== Корпус-цель: капс, падежи, Общество ===")
    print(json.dumps(svod2, ensure_ascii=False, indent=2))
    for s in stroki2:
        if s["propuski"] or s["lozhnye"] or not s["sohrannost"]:
            print(f"  {s['id']}: пропуски={s['propuski']} ложные={s['lozhnye']}")
            print(f"      выход: {s['vyhod']!r}")

    (KOREN / "zamer" / f"{metka}.json").write_text(json.dumps(
        {"ped06": {"stroki": stroki, "svodka": svod},
         "cel": {"stroki": stroki2, "svodka": svod2}},
        ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nЗаписано: {KOREN / 'zamer' / (metka + '.json')}")


if __name__ == "__main__":
    main()
