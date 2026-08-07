"""Гейт прибора: проверяем судью ДО того, как мерить им объект.

Положительный контроль — судья обязан узнавать человека там, где узнать можно
(текст с именем и приметами): порог >= 8 из 10.
Отрицательный контроль — судья не должен «узнавать» в тексте, где примет нет:
порог <= 2 из 10 (уровень случайного выбора из пяти).

Провал любого из двух = замер объекта не проводится. Прибор тоже подозреваемый.
"""
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from obshchee import sudya_uznavaniya  # noqa: E402

KORPUS = pathlib.Path(__file__).parents[1] / "korpus" / "korpus.json"
ZAMER = pathlib.Path(__file__).parents[1] / "zamer"


def progon(model, kandidaty, teksty, pole):
    itogi = []
    for t in teksty:
        nomer, syroj = sudya_uznavaniya(model, kandidaty, t[pole])
        itogi.append({
            "id": t["id"],
            "ozhidalsya": t["kandidat"],
            "otvet": nomer,
            "verno": nomer == t["kandidat"],
            "syroj_otvet": syroj,
        })
        print(f"  {t['id']}: ответ {nomer}, ждали {t['kandidat']}"
              f" — {'узнал' if nomer == t['kandidat'] else 'нет'}", flush=True)
    return itogi


def main():
    model = sys.argv[1] if len(sys.argv) > 1 else "qwen2.5:7b"
    dannye = json.loads(KORPUS.read_text(encoding="utf-8"))
    kandidaty, teksty = dannye["kandidaty"], dannye["teksty"]

    print(f"=== ГЕЙТ ПРИБОРА, судья {model} ===")
    print("Положительный контроль (оригиналы, порог >= 8/10):")
    plus = progon(model, kandidaty, teksty, "text")
    n_plus = sum(1 for r in plus if r["verno"])

    print("Отрицательный контроль (выхолощенные, порог <= 2/10):")
    minus = progon(model, kandidaty, teksty, "vyholoshchennyj")
    n_minus = sum(1 for r in minus if r["verno"])

    proshel = n_plus >= 8 and n_minus <= 2
    itog = {
        "model": model,
        "polozhitelnyj": n_plus,
        "otricatelnyj": n_minus,
        "gejt_projden": proshel,
        "detali_plus": plus,
        "detali_minus": minus,
    }
    ZAMER.mkdir(exist_ok=True)
    (ZAMER / f"gejt_{model.replace(':', '_')}.json").write_text(
        json.dumps(itog, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\nИТОГ ГЕЙТА: положительный {n_plus}/10 (нужно >=8), "
          f"отрицательный {n_minus}/10 (нужно <=2)")
    print("ГЕЙТ ПРОЙДЕН" if proshel else "ГЕЙТ ПРОВАЛЕН — замер объекта не проводится")
    return 0 if proshel else 1


if __name__ == "__main__":
    sys.exit(main())
