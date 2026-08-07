#!/usr/bin/env python3
"""Оценка выдачи по эталону корпуса PEd06.

Определения метрик записаны здесь один раз и применяются ко всем участникам
одинаково — и к детерминированному слою, и к моделям.

Запуск:  ocenka.py zamer/vyhod_baseline.json [ещё файлы...]
"""
import json
import re
import sys
from pathlib import Path

KOREN = Path(__file__).resolve().parent.parent
KORPUS = KOREN / "korpus" / "korpus.json"

PLEJSHOLDER = re.compile(r"\[[A-ZА-ЯЁ][A-ZА-ЯЁ_]*_?\d*\]")
# Токен считается значимым (то есть способным выдать субъекта), если это слово
# от 4 букв или число от 3 цифр. Короткое отбрасывается намеренно: "г", "д", "оф",
# "14" сами по себе никого не идентифицируют, и требовать их маскировки — значит
# мерить не утечку, а аккуратность оформления.
TOKEN = re.compile(r"[А-Яа-яЁёA-Za-z]{4,}|\d{3,}")
# Служебные куски адресов: их наличие в выдаче не означает, что данные утекли.
NEZNACHIMYE = {"www", "http", "https", "mail", "email"}


def probely(s):
    return re.sub(r"\s+", " ", s).strip()


def znachimye_tokeny(span):
    return [t for t in TOKEN.findall(span) if t.lower() not in NEZNACHIMYE]


def zamaskirovan(span, vyhod):
    """Спан замаскирован, если НИ ОДИН его значимый токен не остался в выдаче.

    Проверка по токенам, а не по строке целиком: если модель заменит только фамилию
    и оставит имя с отчеством, строка целиком исчезнет — и наивная проверка засчитает
    маскировку там, где половина данных ушла наружу.
    """
    nizhnij = vyhod.lower()
    return all(t.lower() not in nizhnij for t in znachimye_tokeny(span))


def ostalos(podstroka, vyhod):
    return probely(podstroka).lower() in probely(vyhod).lower()


def sohrannost(original, vyhod):
    """Выдача получена из оригинала ТОЛЬКО заменой кусков на плейсхолдеры.

    Куски между плейсхолдерами должны находиться в оригинале, по порядку и без
    пересечений. Если модель что-то переписала, перевела, сократила или добавила
    от себя — очередной кусок не найдётся.
    """
    o = probely(original).lower()
    poziciya = 0
    for kusok in PLEJSHOLDER.split(vyhod):
        k = probely(kusok).lower()
        if not k:
            continue
        i = o.find(k, poziciya)
        if i < 0:
            return False, k[:40]
        poziciya = i + len(k)
    return True, ""


def ocenit(teksty, rezultaty):
    """Сводит выдачу одного участника в таблицу по текстам.

    Если у участника несколько повторов, для качества берётся ПЕРВЫЙ прогон,
    остальные идут только в проверку воспроизводимости.
    """
    po_id = {}
    for r in rezultaty:
        po_id.setdefault(r["id"], []).append(r)

    stroki = []
    for t in teksty:
        progony = po_id.get(t["id"], [])
        if not progony:
            continue
        pervyj = progony[0]["vyhod"]
        nuzhno = t["dolzhno_byt_zamaskirovano"]
        propuski = [s for s in nuzhno if not zamaskirovan(s, pervyj)]
        lozhnye = [s for s in t["dolzhno_ostatsya"] if not ostalos(s, pervyj)]
        cel, gde = sohrannost(t["text"], pervyj)
        odinakovo = len({probely(p["vyhod"]) for p in progony}) == 1
        stroki.append({
            "id": t["id"],
            "gruppa": t["gruppa"],
            "nuzhno_zamaskirovat": len(nuzhno),
            "propuski": propuski,
            "lozhnye_trevogi": lozhnye,
            "sohrannost": cel,
            "gde_slomalos": gde,
            "povtorov": len(progony),
            "vosproizvodimo": odinakovo if len(progony) > 1 else None,
            "sekundy": progony[0].get("sekundy"),
            "tokenov_v_sekundu": progony[0].get("tokenov_v_sekundu"),
        })
    return stroki


def svodka(stroki):
    itog = {}
    for gruppa in ("A", "B", "C"):
        g = [s for s in stroki if s["gruppa"] == gruppa]
        nado = sum(s["nuzhno_zamaskirovat"] for s in g)
        propushcheno = sum(len(s["propuski"]) for s in g)
        itog[gruppa] = {
            "spanov": nado,
            "propushcheno": propushcheno,
            "recall": None if nado == 0 else round((nado - propushcheno) / nado, 3),
            "lozhnyh_trevog": sum(len(s["lozhnye_trevogi"]) for s in g),
        }
    itog["sohrannost"] = f"{sum(1 for s in stroki if s['sohrannost'])} из {len(stroki)}"
    vosp = [s for s in stroki if s["vosproizvodimo"] is not None]
    itog["vosproizvodimost"] = (
        f"{sum(1 for s in vosp if s['vosproizvodimo'])} из {len(vosp)}" if vosp else "не мерилась"
    )
    goryachie = [s["sekundy"] for s in stroki[1:] if s["sekundy"]]
    itog["sekund_na_tekst_bez_pervogo"] = round(sum(goryachie) / len(goryachie), 2) if goryachie else 0
    return itog


def main():
    teksty = json.loads(KORPUS.read_text(encoding="utf-8"))["teksty"]
    vse = {}
    for put in sys.argv[1:]:
        d = json.loads(Path(put).read_text(encoding="utf-8"))
        stroki = ocenit(teksty, d["rezultaty"])
        vse[d["uchastnik"]] = {"po_tekstam": stroki, "svodka": svodka(stroki)}
        print(f"\n=== {d['uchastnik']} ===")
        print(json.dumps(vse[d["uchastnik"]]["svodka"], ensure_ascii=False, indent=2))
        for s in stroki:
            if s["propuski"] or s["lozhnye_trevogi"] or not s["sohrannost"]:
                print(f"  {s['id']}: пропуски={s['propuski']} "
                      f"ложные={s['lozhnye_trevogi']} "
                      f"сохранность={'ок' if s['sohrannost'] else 'СЛОМАНА: ' + s['gde_slomalos']}")
    (KOREN / "zamer" / "ocenka.json").write_text(
        json.dumps(vse, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nЗаписано: {KOREN / 'zamer' / 'ocenka.json'}")


if __name__ == "__main__":
    main()
