"""Гейт второго прибора — судьи смысла. Именно он решает критерий 2, а он был
провален на один текст: значит цифра обязана быть доказана до того, как на неё опираются.

Положительный контроль: оригинальный текст против СВОЕГО тезиса — обязан быть ДА (>= 9/10).
Отрицательный контроль: оригинальный текст против ЧУЖОГО тезиса (сдвиг на одну строку) —
обязан быть НЕТ (<= 2/10). Судья, говорящий ДА на чужой тезис, штампует согласие.
"""
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from obshchee import sudya_smysla  # noqa: E402

KORPUS = pathlib.Path(__file__).parents[1] / "korpus" / "korpus.json"
ZAMER = pathlib.Path(__file__).parents[1] / "zamer"


def main():
    model = sys.argv[1] if len(sys.argv) > 1 else "qwen2.5:7b"
    teksty = json.loads(KORPUS.read_text(encoding="utf-8"))["teksty"]

    plus = [sudya_smysla(model, t["glavnyj_tezis"], t["text"])[0] for t in teksty]
    minus = [sudya_smysla(model, teksty[(i + 1) % len(teksty)]["glavnyj_tezis"], t["text"])[0]
             for i, t in enumerate(teksty)]

    n_plus, n_minus = sum(plus), sum(minus)
    proshel = n_plus >= 9 and n_minus <= 2
    ZAMER.mkdir(exist_ok=True)
    (ZAMER / f"gejt_smysla_{model.replace(':', '_')}.json").write_text(
        json.dumps({"model": model, "svoj_tezis_DA": n_plus, "chuzhoj_tezis_DA": n_minus,
                    "gejt_projden": proshel}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Судья смысла {model}: свой тезис ДА {n_plus}/10 (нужно >=9), "
          f"чужой тезис ДА {n_minus}/10 (нужно <=2)")
    print("ГЕЙТ ПРОЙДЕН" if proshel else "ГЕЙТ ПРОВАЛЕН — на цифру сохранности опираться нельзя")
    return 0 if proshel else 1


if __name__ == "__main__":
    sys.exit(main())
