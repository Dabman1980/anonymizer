#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Тесты HTTP-режима (`api.py`).

Запуск (fastapi и httpx в ядровом venv не нужны и туда не ставятся):
    <venv-с-fastapi>/bin/python -m unittest discover tests -v

⚠️ Без fastapi весь файл уходит в skip, а прогон рапортует OK — это ровно тот
случай, на котором проект уже обжигался 03.08.2026. Поэтому: увидев skip,
считать API непроверенным, а не работающим.

Что здесь проверяется, кроме «эндпоинт отвечает»:
- утечка через сообщение об ошибке (движок кладёт в текст исключения фрагмент
  разбираемых данных) — наружу и в журнал не должен уйти ни один символ текста;
- fail-closed: сбой = отказ, а не «вот ваш текст, вроде обработали»;
- отсутствие протечки состояния между запросами (нумерация ключей с 1);
- вердикт `/health`: молчащий словарный слой обязан краснеть машинно.

Все данные в фикстурах синтетические (репозиторий публичный).
"""

import asyncio
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

TOKEN = "test-token-0123456789abcdef"
# Токен обязан быть в окружении ДО импорта api: без него модуль не собирается.
os.environ["ANONYMIZER_TOKEN"] = TOKEN

try:
    from fastapi.testclient import TestClient
    import api
    HTTP_DOSTUPEN = True
except ImportError:  # нет fastapi/httpx — транспорт не проверяется
    HTTP_DOSTUPEN = False

PRICHINA = ("нужен fastapi + httpx; ядровый venv их не содержит намеренно — "
            "гонять API-тесты отдельным venv")

AUTH = {"Authorization": f"Bearer {TOKEN}"}

# Маркер, по которому ищем утечку пользовательского текста в ответах и логах.
MARKER = "СЕКРЕТНАЯ-СТРОКА-ПОЛЬЗОВАТЕЛЯ-777"


class FakeNer:
    """Подставной NER-слой: находит заранее названные слова как ФИО.

    Настоящая natasha в тестах транспорта не нужна и вредна: модель грузится
    секунды, а проверяем мы обёртку, а не качество распознавания.
    """

    def __init__(self, slova=()):
        self.slova = list(slova)

    def spans(self, text):
        out = []
        for slovo in self.slova:
            i = text.find(slovo)
            if i >= 0:
                out.append((i, i + len(slovo), "fio", "ФИО", slovo))
        return sorted(out)


class FakeSurnames:
    """Подставной словарный слой: ничего не добирает и ничего не отсеивает."""

    def is_personal_name(self, word):
        return False

    def is_verb_form(self, word):
        return False


def sloi_gotovy(**kw):
    """Слои в состоянии «щит поднят целиком»."""
    if not HTTP_DOSTUPEN:
        return None
    l = api.Layers(
        ner=kw.get("ner", FakeNer()),
        ner_status=kw.get("ner_status", "включён (Natasha)"),
        surnames=kw.get("surnames", FakeSurnames()),
        surnames_status=kw.get("surnames_status", "включён (pymorphy3)"),
    )
    l.dict_rules = kw.get("dict_rules", [])
    l.dict_status = kw.get("dict_status", "пусто")
    return l


def klient(layers=None):
    app = api.create_app(layers if layers is not None else sloi_gotovy())
    return TestClient(app, raise_server_exceptions=False)


@unittest.skipUnless(HTTP_DOSTUPEN, PRICHINA)
class HealthTests(unittest.TestCase):
    def test_gotov_otdaet_200_i_ready_true(self):
        with klient() as c:
            r = c.get("/health")
        self.assertEqual(r.status_code, 200)
        telo = r.json()
        self.assertTrue(telo["ready"])
        self.assertEqual(telo["status"], "ok")
        self.assertEqual(telo["version"], api.API_VERSION)
        self.assertIn("core_fingerprint", telo)

    def test_molchashchij_slovarnyj_sloj_krasnit_health(self):
        """⭐ Главная проверка вердикта.

        Без pymorphy3 словарный слой отключается БЕЗ ошибки: ядро работает,
        ответы приходят, а утечка (фамилии, которые NER пропускает точечно)
        возвращается незаметно. Значит вердикт обязан быть машинным.
        """
        sloi = sloi_gotovy(surnames=None,
                           surnames_status="недоступен — pip3 install pymorphy3")
        with klient(sloi) as c:
            r = c.get("/health")
        self.assertEqual(r.status_code, 503)
        self.assertFalse(r.json()["ready"])
        self.assertEqual(r.json()["status"], "degraded")
        self.assertIn("pymorphy3", r.json()["surnames_status"])

    def test_otsutstvie_ner_krasnit_health(self):
        sloi = sloi_gotovy(ner=None, ner_status="недоступен — pip3 install natasha")
        with klient(sloi) as c:
            r = c.get("/health")
        self.assertEqual(r.status_code, 503)
        self.assertFalse(r.json()["ready"])

    def test_health_ne_trebuet_tokena(self):
        """Точка мониторинга: сборщик метрик не носит секретов."""
        with klient() as c:
            r = c.get("/health")
        self.assertIn(r.status_code, (200, 503))


@unittest.skipUnless(HTTP_DOSTUPEN, PRICHINA)
class AuthTests(unittest.TestCase):
    def test_bez_zagolovka_401(self):
        with klient() as c:
            r = c.post("/anonymize", json={"text": "привет"})
        self.assertEqual(r.status_code, 401)

    def test_chuzhoj_token_401(self):
        with klient() as c:
            r = c.post("/anonymize", json={"text": "привет"},
                       headers={"Authorization": "Bearer ne-tot-token-0000000"})
        self.assertEqual(r.status_code, 401)

    def test_restore_tozhe_pod_tokenom(self):
        with klient() as c:
            r = c.post("/restore", json={"text": "привет", "keys": {}})
        self.assertEqual(r.status_code, 401)

    def test_svoj_token_puskaet(self):
        with klient() as c:
            r = c.post("/anonymize", json={"text": "привет"}, headers=AUTH)
        self.assertEqual(r.status_code, 200)

    def test_bez_peremennoj_okruzheniya_sluzhba_ne_startuet(self):
        """Fail-closed на старте: открытая служба принимает чужие ПДн от всех."""
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ANONYMIZER_TOKEN", None)
            with self.assertRaises(RuntimeError):
                api.create_app(sloi_gotovy())

    def test_pustoj_token_ne_schitaetsya_zadannym(self):
        with mock.patch.dict(os.environ, {"ANONYMIZER_TOKEN": "   "}):
            with self.assertRaises(RuntimeError):
                api.create_app(sloi_gotovy())


@unittest.skipUnless(HTTP_DOSTUPEN, PRICHINA)
class AnonymizeTests(unittest.TestCase):
    """Смоук на НАСТОЯЩЕМ ядре: regex-слой natasha не требует."""

    def test_inn_maskiruetsya_i_popadaet_v_klyuchi(self):
        with klient() as c:
            r = c.post("/anonymize",
                       json={"text": "ИНН 7799001122 в договоре", "use_ner": False},
                       headers=AUTH)
        self.assertEqual(r.status_code, 200)
        telo = r.json()
        self.assertNotIn("7799001122", telo["text"])
        self.assertIn("[ИНН_ЮЛ_1]", telo["text"])
        self.assertEqual(telo["keys"]["[ИНН_ЮЛ_1]"]["original"], "7799001122")
        self.assertEqual(telo["stats"]["total"], 1)
        self.assertEqual(telo["stats"]["by_type"], {"inn_ul": 1})

    def test_krugovoj_reys_anonymize_restore(self):
        ishodnyj = "Телефон +7 (846) 555-12-34, почта ivanov@example.ru"
        with klient() as c:
            a = c.post("/anonymize", json={"text": ishodnyj, "use_ner": False},
                       headers=AUTH).json()
            self.assertNotIn("555-12-34", a["text"])
            b = c.post("/restore", json={"text": a["text"], "keys": a["keys"]},
                       headers=AUTH)
        self.assertEqual(b.status_code, 200)
        self.assertEqual(b.json()["text"], ishodnyj)

    def test_ner_sloj_primenyaetsya_kogda_zaproshen(self):
        sloi = sloi_gotovy(ner=FakeNer(["Кузнецов Андрей"]))
        with klient(sloi) as c:
            r = c.post("/anonymize",
                       json={"text": "Договор подписал Кузнецов Андрей лично"},
                       headers=AUTH).json()
        self.assertIn("[ФИО_1]", r["text"])
        self.assertNotIn("Кузнецов", r["text"])

    def test_use_ner_false_otklyuchaet_sloj(self):
        sloi = sloi_gotovy(ner=FakeNer(["Кузнецов Андрей"]))
        with klient(sloi) as c:
            r = c.post("/anonymize",
                       json={"text": "Договор подписал Кузнецов Андрей лично",
                             "use_ner": False},
                       headers=AUTH).json()
        self.assertIn("Кузнецов", r["text"])

    def test_use_dict_upravlyaet_lichnym_slovaryom(self):
        pravila, _ = api.core.load_custom_dict(
            self._vremennyj_slovar("Ромашка Трейд|КОМПАНИЯ"))
        sloi = sloi_gotovy(dict_rules=pravila, dict_status="1 запись")
        with klient(sloi) as c:
            vklyuchen = c.post("/anonymize",
                               json={"text": "Счёт от Ромашка Трейд получен",
                                     "use_ner": False, "use_dict": True},
                               headers=AUTH).json()
            vyklyuchen = c.post("/anonymize",
                                json={"text": "Счёт от Ромашка Трейд получен",
                                      "use_ner": False, "use_dict": False},
                                headers=AUTH).json()
        self.assertIn("[КОМПАНИЯ_1]", vklyuchen["text"])
        self.assertIn("Ромашка Трейд", vyklyuchen["text"])

    def _vremennyj_slovar(self, stroka):
        d = tempfile.mkdtemp()
        p = Path(d) / "slovar.txt"
        p.write_text(stroka + "\n", encoding="utf-8")
        self.addCleanup(lambda: (p.unlink(), Path(d).rmdir()))
        return p

    def test_sostoyanie_ne_techet_mezhdu_zaprosami(self):
        """Нумерация каждого запроса начинается с 1, ключи не смешиваются.

        Тяжёлые слои переиспользуются, а `Anonymizer` обязан быть свежим:
        иначе второй клиент получит `[ИНН_2]` и ключи первого.
        """
        with klient() as c:
            pervyj = c.post("/anonymize",
                            json={"text": "ИНН 7799001122", "use_ner": False},
                            headers=AUTH).json()
            vtoroj = c.post("/anonymize",
                            json={"text": "ИНН 7799334455", "use_ner": False},
                            headers=AUTH).json()
        self.assertIn("[ИНН_ЮЛ_1]", pervyj["text"])
        self.assertIn("[ИНН_ЮЛ_1]", vtoroj["text"])
        self.assertEqual(list(vtoroj["keys"]), ["[ИНН_ЮЛ_1]"])
        self.assertEqual(vtoroj["keys"]["[ИНН_ЮЛ_1]"]["original"], "7799334455")
        self.assertNotIn("7799001122", json.dumps(vtoroj, ensure_ascii=False))
        self.assertEqual(vtoroj["stats"]["total"], 1)


@unittest.skipUnless(HTTP_DOSTUPEN, PRICHINA)
class FailClosedTests(unittest.TestCase):
    def test_sboy_yadra_daet_otkaz_a_ne_ishodnyj_tekst(self):
        with klient() as c:
            with mock.patch.object(api.core.Anonymizer, "replace",
                                   side_effect=RuntimeError("сбой на " + MARKER)):
                r = c.post("/anonymize", json={"text": MARKER + " ИНН 7799001122"},
                           headers=AUTH)
        self.assertGreaterEqual(r.status_code, 500)
        self.assertNotIn(MARKER, r.text)
        self.assertNotIn("7799001122", r.text)

    def test_zaproshennyj_no_nedostupnyj_sloj_daet_otkaz(self):
        """Молча отработать без слоя = выдать текст, который лишь выглядит
        обезличенным. Поэтому отказ."""
        sloi = sloi_gotovy(surnames=None, surnames_status="недоступен")
        with klient(sloi) as c:
            r = c.post("/anonymize", json={"text": MARKER}, headers=AUTH)
        self.assertEqual(r.status_code, 503)
        self.assertNotIn("text", r.json())
        self.assertNotIn(MARKER, r.text)

    def test_bitye_klyuchi_restore_dayut_otkaz_a_ne_500(self):
        with klient() as c:
            r = c.post("/restore",
                       json={"text": "[ФИО_1] звонил", "keys": {"[ФИО_1]": "строка"}},
                       headers=AUTH)
        self.assertEqual(r.status_code, 422)


@unittest.skipUnless(HTTP_DOSTUPEN, PRICHINA)
class PrivacyTests(unittest.TestCase):
    """Ни текст, ни ключи не должны утекать в ответы об ошибке, логи и на диск."""

    def test_otkaz_validacii_ne_citiruet_tekst(self):
        """Обработчик FastAPI по умолчанию вернул бы поле `input` целиком.

        ⚠️ Ошибка нарочно посажена на поле `text`, а не на соседнее: первая
        редакция проверки ломала `use_ner`, и цитата `input` содержала строку
        «да-нет», а не текст. Мутация «цитировать input» её пережила.
        """
        with klient() as c:
            r = c.post("/anonymize", json={"text": {"не строка": MARKER}},
                       headers=AUTH)
        self.assertEqual(r.status_code, 422)
        self.assertNotIn(MARKER, r.text)

    def test_otkaz_po_lishnemu_polyu_ne_citiruet_znachenie(self):
        with klient() as c:
            r = c.post("/anonymize",
                       json={"text": "привет", "use_ner": False, "лишнее": MARKER},
                       headers=AUTH)
        self.assertEqual(r.status_code, 422)
        self.assertNotIn(MARKER, r.text)

    def test_bityj_json_ne_citiruetsya(self):
        with klient() as c:
            r = c.post("/anonymize",
                       content=('{"text": "' + MARKER + '"').encode("utf-8"),
                       headers={**AUTH, "Content-Type": "application/json"})
        self.assertEqual(r.status_code, 400)
        self.assertNotIn(MARKER, r.text)

    def test_v_logi_ne_popadaet_tekst(self):
        with klient() as c:
            with self.assertLogs("anonymizer.api", level="INFO") as zhurnal:
                c.post("/anonymize",
                       json={"text": MARKER + " ИНН 7799001122", "use_ner": False},
                       headers=AUTH)
        vyvod = "\n".join(zhurnal.output)
        self.assertNotIn(MARKER, vyvod)
        self.assertNotIn("7799001122", vyvod)
        self.assertIn("масок=", vyvod)

    def test_v_logi_ne_popadaet_tekst_iz_isklyucheniya(self):
        """Сообщение об ошибке — тоже ПДн: движок кладёт туда фрагмент данных."""
        with klient() as c:
            with mock.patch.object(api.core.Anonymizer, "replace",
                                   side_effect=RuntimeError("сбой на " + MARKER)):
                with self.assertLogs("anonymizer.api", level="INFO") as zhurnal:
                    c.post("/anonymize", json={"text": MARKER}, headers=AUTH)
        self.assertNotIn(MARKER, "\n".join(zhurnal.output))

    def test_zhurnal_schetchikov_voobshche_vedetsya(self):
        """Журнала не было вовсе: uvicorn настраивает только свои логгеры.

        Найдено живым прогоном, а не тестом — счётчики (единственный
        разрешённый след) молча исчезали. Отсутствие журнала «выглядит»
        как безупречная приватность, поэтому проверка отдельная.
        """
        import logging
        api.create_app(sloi_gotovy())
        self.assertTrue(api.log.isEnabledFor(logging.INFO))

    def test_klyuchi_ne_pishutsya_na_disk(self):
        """Инвариант проекта: ключи живут только в памяти запроса."""
        rabochij = tempfile.mkdtemp()
        bylo = os.getcwd()
        os.chdir(rabochij)
        try:
            with klient() as c:
                r = c.post("/anonymize",
                           json={"text": "ИНН 7799001122", "use_ner": False},
                           headers=AUTH)
            self.assertEqual(r.status_code, 200)
            self.assertEqual(os.listdir(rabochij), [])
        finally:
            os.chdir(bylo)
            os.rmdir(rabochij)


@unittest.skipUnless(HTTP_DOSTUPEN, PRICHINA)
class BodyLimitTests(unittest.TestCase):
    def test_slishkom_bolshoe_telo_otklonyaetsya_po_content_length(self):
        with mock.patch.dict(os.environ, {"ANONYMIZER_MAX_BODY": "500"}):
            with klient() as c:
                r = c.post("/anonymize", json={"text": "я" * 2000}, headers=AUTH)
        self.assertEqual(r.status_code, 413)

    def test_slishkom_bolshoe_telo_otklonyaetsya_bez_content_length(self):
        """Chunked-передача: заголовка длины нет вовсе, считаем на потоке."""
        def potok():
            yield b'{"text": "'
            for _ in range(50):
                yield b"a" * 100
            yield b'"}'

        with mock.patch.dict(os.environ, {"ANONYMIZER_MAX_BODY": "500"}):
            with klient() as c:
                r = c.post("/anonymize", content=potok(),
                           headers={**AUTH, "Content-Type": "application/json"})
        self.assertEqual(r.status_code, 413)

    def test_zayavlennaya_dlina_otsekaetsya_do_chteniya_tela(self):
        """Проверка Content-Length ценна тем, что отказ идёт ДО чтения тела.

        ⚠️ Через TestClient это не отличить: счётчик на потоке ловит тот же
        случай, и мутация «убрать проверку Content-Length» переживала прогон.
        Поэтому проверка адресная: поток обязан не читаться вовсе.
        """
        class ZaprosBezChteniya:
            def __init__(self, limit, zayavleno):
                self.app = SimpleNamespace(state=SimpleNamespace(max_body=limit))
                self.headers = {"content-length": str(zayavleno)}

            def stream(self):
                raise AssertionError("тело не должно читаться при отказе по длине")

        with self.assertRaises(api.HTTPException) as ctx:
            asyncio.run(api.prochitat_telo(ZaprosBezChteniya(500, 100000)))
        self.assertEqual(ctx.exception.status_code, 413)

    def test_telo_v_predelakh_limita_prohodit(self):
        with mock.patch.dict(os.environ, {"ANONYMIZER_MAX_BODY": "5000"}):
            with klient() as c:
                r = c.post("/anonymize", json={"text": "я" * 100, "use_ner": False},
                           headers=AUTH)
        self.assertEqual(r.status_code, 200)


if __name__ == "__main__":
    unittest.main()
