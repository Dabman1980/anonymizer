"""Основной прогон: маскирование (боевой anonymizer) против подъёма абстракции.

Меряем два числа на каждой ветке:
  • узнавание — сколько текстов из 10 судья-«знакомый» связал с верным человеком
    (ниже — лучше);
  • сохранность смысла — сколько текстов сохранили главный тезис (выше — лучше).

Критерий приёмки записан в kriterii.md ДО прогона и здесь только проверяется.
"""
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))
sys.path.insert(0, str(pathlib.Path(__file__).parents[3]))  # корень репозитория с ядром
from obshchee import podnyat_abstrakciyu, sudya_smysla, sudya_uznavaniya  # noqa: E402
from anonymizer import Anonymizer  # noqa: E402

KORPUS = pathlib.Path(__file__).parents[1] / "korpus" / "korpus.json"
ZAMER = pathlib.Path(__file__).parents[1] / "zamer"


def main():
    model = sys.argv[1] if len(sys.argv) > 1 else "qwen2.5:7b"          # генератор абстракции
    sudya = sys.argv[2] if len(sys.argv) > 2 else "qwen2.5:7b"          # судья (прошёл гейт)
    dannye = json.loads(KORPUS.read_text(encoding="utf-8"))
    kandidaty, teksty = dannye["kandidaty"], dannye["teksty"]

    proba = Anonymizer(use_ner=True, use_dict=False)
    print(f"Слой NER: {proba.ner_status} · словарный добор: {proba.surnames_status}")
    if "включён" not in proba.ner_status or "включён" not in proba.surnames_status:
        print("⛔ Боевые слои не поднялись — замер недействителен")
        return 2

    itogi = []
    for t in teksty:
        # Свой экземпляр на текст: нумерация плейсхолдеров не должна течь между текстами
        anon = Anonymizer(use_ner=True, use_dict=False)
        maska = anon.replace(t["text"])
        abstr, kir = podnyat_abstrakciyu(model, t["text"])

        stroka = {"id": t["id"], "kandidat": t["kandidat"],
                  "maskirovanie": maska, "abstrakciya": abstr,
                  "dolya_kirillicy": round(kir, 3), "yazyk_ok": kir >= 0.9}
        for vetka in ("maskirovanie", "abstrakciya"):
            nomer, syroj = sudya_uznavaniya(sudya, kandidaty, stroka[vetka])
            smysl, syroj_s = sudya_smysla(sudya, t["glavnyj_tezis"], stroka[vetka])
            stroka[f"uznan_{vetka}"] = (nomer == t["kandidat"])
            stroka[f"otvet_{vetka}"] = nomer
            stroka[f"smysl_{vetka}"] = smysl
            stroka[f"syroj_{vetka}"] = [syroj, syroj_s]
        itogi.append(stroka)
        print(f"  {t['id']}: маска — узнан {stroka['uznan_maskirovanie']}, "
              f"смысл {stroka['smysl_maskirovanie']} · "
              f"абстракция — узнан {stroka['uznan_abstrakciya']}, "
              f"смысл {stroka['smysl_abstrakciya']}", flush=True)

    brak = [r["id"] for r in itogi if not r["yazyk_ok"]]
    u_m = sum(1 for r in itogi if r["uznan_maskirovanie"])
    u_a = sum(1 for r in itogi if r["uznan_abstrakciya"])
    s_m = sum(1 for r in itogi if r["smysl_maskirovanie"])
    s_a = sum(1 for r in itogi if r["smysl_abstrakciya"])

    # Критерий из kriterii.md, п. 5: узнавание ниже минимум на 2 текста И смысл >= 8
    prinyat = (u_a <= u_m - 2) and (s_a >= 8)

    svodka = {
        "model_generator": model,
        "model_sudya": sudya,
        "uznavanie_maskirovanie": u_m,
        "uznavanie_abstrakciya": u_a,
        "smysl_maskirovanie": s_m,
        "smysl_abstrakciya": s_a,
        "kriterij_1_uznavanie_nizhe_na_2": u_a <= u_m - 2,
        "kriterij_2_smysl_ne_menee_8": s_a >= 8,
        "brak_po_yazyku": brak,
        "verdikt": "ПРИНЯТО" if prinyat else "ОТВЕРГНУТО",
        "detali": itogi,
    }
    ZAMER.mkdir(exist_ok=True)
    (ZAMER / f"progon_{model.replace(':', '_')}_sud_{sudya.replace(':', '_')}.json").write_text(
        json.dumps(svodka, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\n=== ИТОГ ({model}) ===")
    print(f"Узнавание: маскирование {u_m}/10 · абстракция {u_a}/10 (ниже — лучше)")
    print(f"Сохранность смысла: маскирование {s_m}/10 · абстракция {s_a}/10")
    print(f"Брак по языку выдачи: {brak or 'нет'}")
    print(f"ВЕРДИКТ: {svodka['verdikt']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
