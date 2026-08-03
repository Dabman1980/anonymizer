#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Регресс-тесты обезличивателя.
Запуск из корня репозитория: python3 -m unittest discover tests -v

Кейсы от 06.07.2026 (smoke-тест из проекта Аси):
- захват адреса перепрыгивал границу предложения и глотал уже
  вставленный плейсхолдер [ФИО_1] внутрь ключа АДРЕС;
- restore был однопроходным и не разворачивал вложенный плейсхолдер.

Кейс от 07.07.2026 (живая приёмка щита ПДн Аси):
- маркер списка Outlook «·» в начале строки слепил NER —
  «·        Литвинов Павел, Начальник отдела» уходил в LLM как есть.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from anonymizer import Anonymizer, restore_text, _ner_view

# NER-слой один на все тесты: модель Natasha грузится несколько секунд
try:
    from anonymizer import NerLayer
    _NER = NerLayer()
except ImportError:
    _NER = None

# Тесты гоняем без NER и словаря: только regex-слой, детерминированно
def make_anon():
    return Anonymizer(use_ner=False, use_dict=False)


def make_ner_anon():
    """Anonymizer с общим NER-слоем (модель не перегружается на каждый тест)."""
    anon = Anonymizer(use_ner=False, use_dict=False)
    anon.ner = _NER
    return anon


class TestAddressSentenceBoundary(unittest.TestCase):
    def test_address_stops_at_sentence_end(self):
        # Регресс: адрес без метки глотал текст за точкой конца предложения
        text = ("Наш офис: г. Самара, ул. Мира, д. 7. "
                "Контакт: Кузнецов Андрей Викторович.")
        anon = make_anon()
        result = anon.replace(text)

        addresses = [v["original"] for v in anon.keys.values()
                     if v["type"] == "АДРЕС"]
        self.assertEqual(addresses, ["г. Самара, ул. Мира, д. 7"])
        self.assertIn("Контакт: [ФИО_1]", result)

    def test_no_placeholder_inside_keys(self):
        # Ни один original в ключах не должен содержать чужой плейсхолдер
        text = ("Наш офис: г. Самара, ул. Мира, д. 7. "
                "Контакт: Кузнецов Андрей Викторович.")
        anon = make_anon()
        anon.replace(text)
        for ph, val in anon.keys.items():
            self.assertNotIn("[", val["original"],
                             f"вложенный плейсхолдер в ключе {ph}")

    def test_labeled_address_stops_at_sentence_end(self):
        text = ("Адрес регистрации: г. Москва, ул. Тверская, д. 1, кв. 5. "
                "Телефон уточняется.")
        anon = make_anon()
        anon.replace(text)
        addresses = [v["original"] for v in anon.keys.values()
                     if v["type"] == "АДРЕС"]
        self.assertEqual(addresses, ["г. Москва, ул. Тверская, д. 1, кв. 5"])

    def test_abbreviations_do_not_split_address(self):
        # Точки сокращений (ул., д., инициал «Б.») — не граница предложения
        text = ("Адрес регистрации: г. Москва, ул. Б. Полянка, д. 1, кв. 5. "
                "Паспортные данные ниже.")
        anon = make_anon()
        anon.replace(text)
        addresses = [v["original"] for v in anon.keys.values()
                     if v["type"] == "АДРЕС"]
        self.assertEqual(addresses, ["г. Москва, ул. Б. Полянка, д. 1, кв. 5"])


class TestRestoreMultipass(unittest.TestCase):
    def test_nested_placeholder_restored(self):
        # Регресс: [АДРЕС_1] разворачивается в текст с [ФИО_1] —
        # однопроходный restore оставлял буквальный "[ФИО_1]"
        keys = {
            "[ФИО_1]": {"original": "Кузнецов Андрей Викторович",
                        "type": "ФИО"},
            "[АДРЕС_1]": {"original": "г. Самара, ул. Мира, д. 7. "
                                      "Контакт: [ФИО_1]",
                          "type": "АДРЕС"},
        }
        restored = restore_text("Офис: [АДРЕС_1]", keys)
        self.assertNotIn("[ФИО_1]", restored)
        self.assertIn("Кузнецов Андрей Викторович", restored)

    def test_self_referential_original_terminates(self):
        # original содержит собственный плейсхолдер — лимит проходов
        # гарантирует завершение
        keys = {"[ФИО_1]": {"original": "дубль [ФИО_1]", "type": "ФИО"}}
        restored = restore_text("x [ФИО_1]", keys)
        self.assertIsInstance(restored, str)

    def test_roundtrip(self):
        # Полный цикл: обезличили → расшифровали → исходный текст
        text = ("Наш офис: г. Самара, ул. Мира, д. 7. "
                "Контакт: Кузнецов Андрей Викторович, тел. +7 (846) 555-12-34.")
        anon = make_anon()
        masked = anon.replace(text)
        self.assertNotIn("Кузнецов", masked)
        self.assertNotIn("Самара", masked)
        self.assertEqual(anon.restore(masked), text)


@unittest.skipUnless(_NER, "natasha не установлена — NER-тесты пропущены")
class TestNerBulletedLines(unittest.TestCase):
    def test_outlook_bullet_line_masked(self):
        # Регресс 07.07.2026: «·» + отступ в начале строки слепили NER
        text = "·        Литвинов Павел, Начальник отдела"
        anon = make_ner_anon()
        result = anon.replace(text)
        self.assertNotIn("Литвинов", result)
        self.assertIn("[ФИО_1]", result)
        # Маркер и отступ в выводе сохранены — правится только вход NER
        self.assertTrue(result.startswith("·        "))

    def test_all_list_markers_masked(self):
        for marker in ("·", "•", "—", "-", "*"):
            with self.subTest(marker=marker):
                text = f"{marker}        Литвинов Павел, Начальник отдела"
                result = make_ner_anon().replace(text)
                self.assertNotIn("Литвинов", result)

    def test_bullet_without_space_masked(self):
        # «·Литвинов» без пробела после маркера — тоже вёрстка писем
        result = make_ner_anon().replace("·Литвинов Павел, Начальник отдела")
        self.assertNotIn("Литвинов", result)

    def test_numbering_across_regex_and_ner_layers(self):
        # Regex-слой ловит полное ФИО (ФИО_1), NER добирает
        # маркированную строку (ФИО_2) — нумерация сквозная, без дырок
        text = ("Участники встречи:\n"
                "·        Кузнецов Андрей Викторович, аналитик\n"
                "·        Литвинов Павел, Начальник отдела")
        anon = make_ner_anon()
        result = anon.replace(text)
        self.assertNotIn("Кузнецов", result)
        self.assertNotIn("Литвинов", result)
        self.assertIn("[ФИО_1]", result)
        self.assertIn("[ФИО_2]", result)
        self.assertEqual(anon.counters.get("fio"), 2)

    def test_dedup_between_bullet_and_plain_text(self):
        # Одно имя в маркированной строке и в обычном предложении —
        # один плейсхолдер (дедупликация не ломается от маркеров)
        text = ("·        Литвинов Павел, Начальник отдела\n"
                "Литвинов Павел подтвердил участие.")
        anon = make_ner_anon()
        result = anon.replace(text)
        self.assertNotIn("Литвинов", result)
        self.assertEqual(result.count("[ФИО_1]"), 2)
        self.assertEqual(anon.counters.get("fio"), 1)

    def test_roundtrip_with_bullet(self):
        # Восстановление возвращает исходную строку вместе с маркером
        text = "·        Литвинов Павел, Начальник отдела"
        anon = make_ner_anon()
        masked = anon.replace(text)
        self.assertEqual(anon.restore(masked), text)


class TestNerHomoglyphs(unittest.TestCase):
    """Кейс от 16.07.2026 (боевой прогон визиток): OCR прочитал логотип
    латиницей — A U+0041, T U+0054, O U+004F, H U+0048 вместо кириллических.
    На экране не отличить, NER компанию не увидел, и она ушла бы в LLM
    необезличенной. Тот же класс, что маркер списка Outlook: слово выглядит
    русским, а NER слеп. Фикстура — «МТС»: публичная компания, все её буквы
    имеют латинских двойников, синтетическое имя NER бы не узнал."""

    # Ссылка из замера: на ней провалился Tesseract, ломать её нельзя
    ZOOM = "us06web.zoom.us/j/84213?pwd=a3vnxd3ahtyieXyLzX5icAQY1iWoEd.1"

    def test_view_returns_cyrillic_to_ner(self):
        self.assertEqual(_ner_view("MTC"), "МТС")

    def test_view_repairs_mixed_alphabet_word(self):
        # OCR может смешать алфавиты внутри слова: А(кир) T(лат) О(кир) H(лат)
        self.assertEqual(_ner_view("МTС"), "МТС")

    def test_view_never_changes_length(self):
        # Инвариант механизма: спаны NER валидны в оригинале только пока
        # длина совпадает. Разъедется — щит замаскирует не тот кусок.
        for text in ("MTC", self.ZOOM, "·  Литвинов Павел", "Power Query, MAX", ""):
            self.assertEqual(len(_ner_view(text)), len(text), text)

    def test_view_keeps_links_and_codes_latin(self):
        # Внутри ссылки латиница настоящая; лишний спан увёл бы её в плейсхолдер
        self.assertEqual(_ner_view(f"Подключение: {self.ZOOM}"), f"Подключение: {self.ZOOM}")
        self.assertEqual(_ner_view("Пишите на ivan.k@example.ru"), "Пишите на ivan.k@example.ru")
        self.assertEqual(_ner_view("Код AB12CO"), "Код AB12CO")

    def test_view_keeps_real_latin_words(self):
        # У «w», «Q», «G» двойников нет — значит слова латинские по-настоящему
        self.assertEqual(_ner_view("Power Query, ChatGPT"), "Power Query, ChatGPT")

    def test_view_keeps_single_letters(self):
        self.assertEqual(_ner_view("Вариант A или B"), "Вариант A или B")

    # Форма реальной визитки: латинский логотип среди русского текста
    CARD = "г. Самара, ул. Мира, д. 7\nMTC"

    def test_homoglyph_company_masked(self):
        anon = make_ner_anon()
        masked = anon.replace(self.CARD)
        self.assertNotIn("MTC", masked)
        self.assertTrue(anon.keys, "компания-гомоглиф ушла бы в LLM необезличенной")

    def test_text_without_cyrillic_skips_ner_by_design(self):
        # Гвардия replace(): нет кириллицы — NER не зовём вовсе. Значит голое
        # «MTC» латиницей не маскируется, и это НЕ дыра: русский текст без
        # единой кириллической буквы не существует, а на визитке рядом есть
        # адрес и должность. Тест фиксирует границу, чтобы её не «чинили».
        anon = make_ner_anon()
        self.assertEqual(anon.replace("MTC"), "MTC")

    def test_output_keeps_original_spelling(self):
        # Нормализация — только для глаз NER: наружу уходит исходное написание,
        # поэтому настоящее латинское слово не станет кириллическим
        anon = make_ner_anon()
        self.assertEqual(anon.replace("Встреча про MAX в 15:00"), "Встреча про MAX в 15:00")

    def test_link_survives_the_shield(self):
        anon = make_ner_anon()
        self.assertIn(self.ZOOM, anon.replace(f"Созвон завтра: {self.ZOOM}"))

    def test_roundtrip_returns_latin_original(self):
        # Восстановление возвращает то, что было на визитке, а не кириллицу
        anon = make_ner_anon()
        masked = anon.replace(self.CARD)
        self.assertEqual(anon.restore(masked), self.CARD)


class TestBareDomain(unittest.TestCase):
    """Узкий вариант закрытия пробела «домен раскрывает компанию».

    Происхождение — HANDOFF от 16.07.2026 (пробел записан) и замер 03.08.2026
    (ДЗ PEd06, подтверждён на синтетическом корпусе). Решение Дмитрия 03.08:
    маскировать ТОЛЬКО упоминание сайта, ссылку на ресурс оставлять целой.

    ⚠️ Различитель — наличие ПУТИ, а не схемы: эталонная ссылка проекта записана
    без «https://», и критерий «нет схемы» её бы не спас.
    """

    LINK = "us06web.zoom.us/j/84213?pwd=a3vnxd3ahtyieXyLzX5icAQY1iWoEd.1"

    def masked(self, text):
        anon = make_anon()
        return anon.replace(text)

    def test_bare_domain_masked(self):
        self.assertNotIn("severdrev", self.masked("Подробности на сайте www.severdrev.ru, там же прайс."))
        self.assertNotIn("oootrader", self.masked("Пишите на сайт oootrader.com — там форма"))

    def test_domain_at_sentence_end_masked(self):
        # Точка конца предложения не должна мешать захвату
        self.assertNotIn("severdrev", self.masked("Наш сайт severdrev.ru."))

    def test_cyrillic_domain_masked(self):
        self.assertNotIn("сайт.рф", self.masked("Открыт сайт.рф с ценами"))

    # Ссылка без схемы, но в зоне ИЗ СПИСКА — единственная форма, которую держит
    # именно страж пути. ⚠️ Эталонная ссылка проекта (`…zoom.us/j/…`) для этого
    # не годится: зона `us` в список не входит, и тест на ней проходил бы даже
    # при полностью сломанном страже — мутация «широкое правило» это и показала.
    LINK_V_SPISKE = "disk.yandex.ru/d/abc123"

    def test_link_with_path_survives(self):
        # Главный инвариант узкого варианта: ссылка проходит щит целой,
        # хотя схемы у неё нет. Ссылки — главная ценность события у Аси.
        self.assertIn(self.LINK_V_SPISKE, self.masked(f"Файл тут: {self.LINK_V_SPISKE}"))
        self.assertIn(self.LINK, self.masked(f"Созвон завтра: {self.LINK}"))

    def test_link_with_scheme_survives(self):
        for link in ("https://finmodel-pro.ru/prices", "http://example.com/page",
                     "ftp://files.severdrev.ru", "www.severdrev.ru/"):
            self.assertIn(link.rstrip("/") if link.endswith("/") else link,
                          self.masked(f"Открой {link}"), link)

    def test_email_not_touched_by_domain_rule(self):
        # Почту берёт слой EMAIL раньше; правило сайта не должно дробить её
        anon = make_anon()
        anon.replace("Пишите на a.kuznetsov@example.ru.")
        tipy = sorted({v["type"] for v in anon.keys.values()})
        self.assertEqual(tipy, ["EMAIL"])

    def test_ordinary_text_not_a_domain(self):
        # Зоны перечислены закрытым списком именно ради этих случаев
        for text in ("Приезжайте в г.Москва, ул. Мира",
                     "Отчёт лежит в файле svod.pdf",
                     "см. п.2 договора",
                     "Готово.Русский текст дальше",
                     "Версия 5.1.2.3 собрана",
                     "Срок кредита 36 месяцев, ставка 18,5% годовых."):
            anon = make_anon()
            anon.replace(text)
            sajty = [v for v in anon.keys.values() if v["type"] == "САЙТ"]
            self.assertEqual(sajty, [], text)

    def test_roundtrip_returns_domain(self):
        anon = make_anon()
        ishodnik = "Подробности на сайте www.severdrev.ru, там же прайс."
        self.assertEqual(anon.restore(anon.replace(ishodnik)), ishodnik)


class TestAddressWithoutLabel(unittest.TestCase):
    """Формы адреса без метки-с-двоеточием.

    Происхождение — замер 03.08.2026 (ДЗ PEd06, синтетический корпус из 12
    текстов с эталоном). Меточное правило требует двоеточия после ключевого
    слова, а живые формулировки его не содержат: шапка заявления и визитка.
    Все три формы уходили в LLM с открытым адресом.
    """

    def masked_addresses(self, text):
        anon = make_anon()
        anon.replace(text)
        return [v["original"] for v in anon.keys.values() if v["type"] == "АДРЕС"]

    def test_prospekt_in_application_header(self):
        # «пр-т» не было в перечне типов улиц, а «по адресу» идёт без двоеточия
        self.assertEqual(
            self.masked_addresses("проживающей по адресу г. Тверь, пр-т Победы, д. 3, кв. 41"),
            ["г. Тверь, пр-т Победы, д. 3, кв. 41"])

    def test_house_number_without_abbreviation(self):
        # Форма визитки: номер дома пишут просто цифрой, без «д.»
        self.assertEqual(
            self.masked_addresses("Главный бухгалтер   г. Казань, ул. Лесная, 14"),
            ["г. Казань, ул. Лесная, 14"])

    def test_two_word_city_captured_whole(self):
        # Захват обрывался на «Нижний», оставляя снаружи половину города,
        # улицу и дом. Счётчик пропусков этого не видел — нашлось глазами.
        self.assertEqual(
            self.masked_addresses("Приезжайте в г. Нижний Новгород, ул. Большая Покровская, д. 18, кв. 5."),
            ["г. Нижний Новгород, ул. Большая Покровская, д. 18, кв. 5"])

    def test_word_gorod_spelled_out(self):
        self.assertEqual(
            self.masked_addresses("Склад: город Пермь, пр-т Парковый, 62а"),
            ["город Пермь, пр-т Парковый, 62а"])

    def test_bare_number_does_not_swallow_prose(self):
        # Голая цифра допущена только после распознанной улицы. Обычное
        # перечисление после адреса захватываться не должно.
        self.assertEqual(
            self.masked_addresses("г. Уфа, б-р Ибрагимова, дом 41, где мы были вчера"),
            ["г. Уфа, б-р Ибрагимова, дом 41"])

    def test_ordinary_text_with_capital_word_untouched(self):
        # Анти-тест: «г» и запятые без адресной формы — не адрес
        for text in ("Годовой отчёт за 2025 год закрыт, выручка 1,5 млрд руб.",
                     "Господин директор, прошу рассмотреть заявку в срок до 14 числа.",
                     "Мир вокруг компании меняется быстрее, чем Учётная политика."):
            self.assertEqual(self.masked_addresses(text), [], text)


if __name__ == "__main__":
    unittest.main()
