"""Пересуд локальной выдачи ГОДНЫМ судьёй — чтобы сравнение шло одной линейкой.

Локальный прогон судил сам себя (`qwen2.5:7b`), а этот судья провалил гейт смысла:
на ЧУЖОЙ тезис он отвечает «ДА» в 6 случаях из 10. Значит его «сохранность 10/10»
ничего не значит. Здесь та же локальная выдача пересуживается `gemini-3.1-pro`,
который оба гейта прошёл, — и только после этого две генерации сравнимы.
"""
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from progon_kie import parallelno, sudya_smysla, sudya_uznavaniya  # noqa: E402

KORPUS = pathlib.Path(__file__).parents[1] / "korpus" / "korpus.json"
ZAMER = pathlib.Path(__file__).parents[1] / "zamer"


def main():
    istochnik = ZAMER / "progon_qwen2.5_7b_sud_qwen2.5_7b.json"
    dannye = json.loads(KORPUS.read_text(encoding="utf-8"))
    kandidaty, teksty = dannye["kandidaty"], dannye["teksty"]
    lokalnye = {r["id"]: r["abstrakciya"]
                for r in json.loads(istochnik.read_text(encoding="utf-8"))["detali"]}

    uzn = parallelno([lambda t=t: sudya_uznavaniya(kandidaty, lokalnye[t["id"]])
                      for t in teksty])
    smysl = parallelno([lambda t=t: sudya_smysla(t["glavnyj_tezis"], lokalnye[t["id"]])
                        for t in teksty])

    u = sum(1 for r, t in zip(uzn, teksty) if r == t["kandidat"])
    s = sum(smysl)
    itog = {"generator": "qwen2.5:7b (локально)", "sudya": "gemini-3.1-pro",
            "uznavanie": u, "smysl": s,
            "kriterij_1_uznavanie_nizhe_na_2": u <= 10 - 2, "kriterij_2_smysl_ne_menee_8": s >= 8}
    (ZAMER / "red2_peresud_lokalnoj_vydachi.json").write_text(
        json.dumps(itog, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Локальная выдача глазами годного судьи: узнавание {u}/10, смысл {s}/10")
    return 0


if __name__ == "__main__":
    sys.exit(main())
