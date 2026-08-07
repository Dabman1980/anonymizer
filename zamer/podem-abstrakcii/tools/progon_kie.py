"""Редакция 2 замера (07.08.2026): генератор и судья — фронтир-модели через Kie.

Зачем понадобилась. В редакции 1 генератором были локальные 4–7B, и они операцию
не исполняли: `qwen2.5:7b` копировала пример из промпта, а без примера выдавала один
шаблонный абзац на все десять заметок. Приём на тесте не был предъявлен — значит и
вердикт по нему не выносился.

⭐ Генератор и судья — из РАЗНЫХ лабораторий намеренно: GPT-5.6 Sol пишет, Gemini 3.1
Pro судит. Судья, оценивающий собственную выдачу, — не судья.

Критерий приёмки НЕ менялся: он записан в `kriterii.md` до первого прогона и проверяется
здесь как есть. Менялись только приборы, и каждый новый прибор проходит свой гейт.

Материал синтетический (🟢 зелёная зона 152-ФЗ) — иначе внешний шлюз был бы закрыт.
"""
import json
import pathlib
import re
import sys
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, str(pathlib.Path(__file__).parents[3]))
sys.path.insert(0, str(pathlib.Path.home() / ".claude" / "skills" / "external-panel"))

from anonymizer import Anonymizer  # noqa: E402

try:
    import kie  # noqa: E402
except ImportError:  # pragma: no cover
    sys.exit("нужен клиент Kie из скилла external-panel")

from obshchee import (PROMPT_ABSTRAKCIYA, PROMPT_SMYSL, PROMPT_SUDYA,  # noqa: E402
                      dolya_kirillicy)

KORPUS = pathlib.Path(__file__).parents[1] / "korpus" / "korpus.json"
ZAMER = pathlib.Path(__file__).parents[1] / "zamer"

GENERATOR = "gpt-5-6-sol"
SUDYA = "gemini-3.1-pro"


def sprosit_gpt(prompt, effort="medium"):
    st, raw = kie.ask_gpt(GENERATOR, prompt, effort=effort,
                          instructions="Ты редактор русских текстов. Отвечай только по-русски.")
    if st != 200:
        raise RuntimeError(f"Kie GPT вернул {st}")
    return kie.extract_gpt(raw).strip()


def sprosit_gemini(prompt, effort="low"):
    st, raw = kie.ask_gemini(SUDYA, SUDYA, prompt, effort=effort)
    if st != 200:
        raise RuntimeError(f"Kie Gemini вернул {st}")
    return kie.extract_gemini(raw).strip()


def sudya_uznavaniya(kandidaty, text):
    spisok = "\n".join(f"{k['id']}. {k['imya']} — {k['dose']}" for k in kandidaty)
    otvet = sprosit_gemini(PROMPT_SUDYA.format(kandidaty=spisok, text=text))
    m = re.search(r"\d+", otvet)
    if not m:
        return 0
    n = int(m.group(0))
    return n if 0 <= n <= len(kandidaty) else 0


def sudya_smysla(tezis, text):
    otvet = sprosit_gemini(PROMPT_SMYSL.format(tezis=tezis, text=text))
    return otvet.upper().lstrip("*_ ").startswith("ДА")


def parallelno(zadachi, potokov=6):
    with ThreadPoolExecutor(max_workers=potokov) as pool:
        return list(pool.map(lambda f: f(), zadachi))


def gejt_priborov(kandidaty, teksty):
    """Оба судьи проходят гейт ДО замера объекта. Пороги — из kriterii.md."""
    plus = parallelno([lambda t=t: sudya_uznavaniya(kandidaty, t["text"]) for t in teksty])
    minus = parallelno([lambda t=t: sudya_uznavaniya(kandidaty, t["vyholoshchennyj"])
                        for t in teksty])
    n_plus = sum(1 for r, t in zip(plus, teksty) if r == t["kandidat"])
    n_minus = sum(1 for r, t in zip(minus, teksty) if r == t["kandidat"])

    svoj = parallelno([lambda t=t: sudya_smysla(t["glavnyj_tezis"], t["text"]) for t in teksty])
    chuzhoj = parallelno([lambda i=i, t=t: sudya_smysla(
        teksty[(i + 1) % len(teksty)]["glavnyj_tezis"], t["text"])
        for i, t in enumerate(teksty)])

    itog = {
        "uznavanie_polozhitelnyj": n_plus, "uznavanie_otricatelnyj": n_minus,
        "smysl_svoj_tezis": sum(svoj), "smysl_chuzhoj_tezis": sum(chuzhoj),
        "gejt_uznavaniya": n_plus >= 8 and n_minus <= 2,
        "gejt_smysla": sum(svoj) >= 9 and sum(chuzhoj) <= 2,
    }
    itog["gejt_projden"] = itog["gejt_uznavaniya"] and itog["gejt_smysla"]
    return itog


def main():
    dannye = json.loads(KORPUS.read_text(encoding="utf-8"))
    kandidaty, teksty = dannye["kandidaty"], dannye["teksty"]
    ZAMER.mkdir(exist_ok=True)

    print(f"Баланс Kie до прогона: {kie.credit()[1] if kie.credit()[0] == 200 else '?'}")

    print(f"=== ГЕЙТ ПРИБОРОВ (судья {SUDYA}) ===")
    gejt = gejt_priborov(kandidaty, teksty)
    print(f"  узнавание: свой {gejt['uznavanie_polozhitelnyj']}/10 (>=8), "
          f"пустышка {gejt['uznavanie_otricatelnyj']}/10 (<=2) → "
          f"{'годен' if gejt['gejt_uznavaniya'] else 'НЕГОДЕН'}")
    print(f"  смысл: свой тезис {gejt['smysl_svoj_tezis']}/10 (>=9), "
          f"чужой {gejt['smysl_chuzhoj_tezis']}/10 (<=2) → "
          f"{'годен' if gejt['gejt_smysla'] else 'НЕГОДЕН'}")
    (ZAMER / "red2_gejt_priborov.json").write_text(
        json.dumps(gejt, ensure_ascii=False, indent=2), encoding="utf-8")
    if not gejt["gejt_projden"]:
        print("⛔ ГЕЙТ ПРОВАЛЕН — замер объекта не проводится")
        return 1

    print(f"=== ПРОГОН (генератор {GENERATOR}) ===")
    abstrakcii = parallelno(
        [lambda t=t: sprosit_gpt(PROMPT_ABSTRAKCIYA.format(text=t["text"])) for t in teksty], 4)

    itogi = []
    for t, abstr in zip(teksty, abstrakcii):
        maska = Anonymizer(use_ner=True, use_dict=False).replace(t["text"])
        (u_m, u_a, s_m, s_a) = parallelno([
            lambda: sudya_uznavaniya(kandidaty, maska),
            lambda: sudya_uznavaniya(kandidaty, abstr),
            lambda: sudya_smysla(t["glavnyj_tezis"], maska),
            lambda: sudya_smysla(t["glavnyj_tezis"], abstr),
        ])
        itogi.append({
            "id": t["id"], "kandidat": t["kandidat"],
            "maskirovanie": maska, "abstrakciya": abstr,
            "dolya_kirillicy": round(dolya_kirillicy(abstr), 3),
            "uznan_maskirovanie": u_m == t["kandidat"],
            "uznan_abstrakciya": u_a == t["kandidat"],
            "smysl_maskirovanie": s_m, "smysl_abstrakciya": s_a,
        })
        print(f"  {t['id']}: маска — узнан {u_m == t['kandidat']}, смысл {s_m} · "
              f"абстракция — узнан {u_a == t['kandidat']}, смысл {s_a}", flush=True)

    u_m = sum(1 for r in itogi if r["uznan_maskirovanie"])
    u_a = sum(1 for r in itogi if r["uznan_abstrakciya"])
    s_m = sum(1 for r in itogi if r["smysl_maskirovanie"])
    s_a = sum(1 for r in itogi if r["smysl_abstrakciya"])
    brak = [r["id"] for r in itogi if r["dolya_kirillicy"] < 0.9]

    # Критерий из kriterii.md п. 5, не менялся: узнавание ниже минимум на 2 И смысл >= 8
    prinyat = (u_a <= u_m - 2) and (s_a >= 8)
    svodka = {
        "redakciya": 2, "generator": GENERATOR, "sudya": SUDYA,
        "gejt_priborov": gejt,
        "uznavanie_maskirovanie": u_m, "uznavanie_abstrakciya": u_a,
        "smysl_maskirovanie": s_m, "smysl_abstrakciya": s_a,
        "brak_po_yazyku": brak,
        "kriterij_1_uznavanie_nizhe_na_2": u_a <= u_m - 2,
        "kriterij_2_smysl_ne_menee_8": s_a >= 8,
        "verdikt": "ПРИНЯТО" if prinyat else "ОТВЕРГНУТО",
        "detali": itogi,
    }
    (ZAMER / "red2_progon_kie.json").write_text(
        json.dumps(svodka, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\n=== ИТОГ РЕДАКЦИИ 2 ===")
    print(f"Узнавание: маскирование {u_m}/10 · абстракция {u_a}/10 (ниже — лучше)")
    print(f"Сохранность смысла: маскирование {s_m}/10 · абстракция {s_a}/10")
    print(f"Брак по языку: {brak or 'нет'}")
    print(f"ВЕРДИКТ: {svodka['verdikt']}")
    print(f"Баланс Kie после: {kie.credit()[1] if kie.credit()[0] == 200 else '?'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
