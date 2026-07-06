#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Регресс-тесты обезличивателя.
Запуск из корня репозитория: python3 -m unittest discover tests -v

Кейсы от 06.07.2026 (smoke-тест из проекта Аси):
- захват адреса перепрыгивал границу предложения и глотал уже
  вставленный плейсхолдер [ФИО_1] внутрь ключа АДРЕС;
- restore был однопроходным и не разворачивал вложенный плейсхолдер.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from anonymizer import Anonymizer, restore_text

# Тесты гоняем без NER и словаря: только regex-слой, детерминированно
def make_anon():
    return Anonymizer(use_ner=False, use_dict=False)


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


if __name__ == "__main__":
    unittest.main()
