"""Общие инструменты замера: вызов локальной модели, судья узнавания, судья смысла.

Всё локально (Ollama на 127.0.0.1) — материал замера синтетический, но сам приём
предназначен для текстов, которые в облако отправлять нельзя, поэтому и прибор
локальный: мерить надо тот объект, который потом пойдёт в бой.
"""
import json
import re
import urllib.request

OLLAMA = "http://127.0.0.1:11434/api/generate"


def sprosit(model, prompt, temperature=0.0, num_predict=400):
    """Один вызов локальной модели. temperature=0 — замер должен воспроизводиться."""
    body = json.dumps({
        "model": model,
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": temperature, "num_predict": num_predict, "seed": 42},
    }).encode("utf-8")
    req = urllib.request.Request(OLLAMA, data=body,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=600) as resp:
        return json.load(resp)["response"].strip()


PROMPT_SUDYA = """Ты — общий знакомый этих людей. Вот кого ты знаешь:

{kandidaty}

Прочитай заметку и определи, о ком из списка в ней идёт речь.

Заметка:
\"\"\"{text}\"\"\"

Ответь ОДНОЙ цифрой — номером человека из списка.
Если по заметке определить человека невозможно, ответь 0.
Не пиши ничего, кроме цифры."""


def sudya_uznavaniya(model, kandidaty, text):
    """Прокси теста «даст ли текст знакомому опознать участника».

    Возвращает номер кандидата (int) или 0. Ответ вне диапазона считается 0:
    судья, который не смог назвать номер, не опознал.
    """
    spisok = "\n".join(f"{k['id']}. {k['imya']} — {k['dose']}" for k in kandidaty)
    otvet = sprosit(model, PROMPT_SUDYA.format(kandidaty=spisok, text=text), num_predict=12)
    m = re.search(r"\d+", otvet)
    if not m:
        return 0, otvet
    n = int(m.group(0))
    return (n if 0 <= n <= len(kandidaty) else 0), otvet


PROMPT_SMYSL = """Вот главная мысль исходной заметки:
"{tezis}"

Вот переработанный текст:
\"\"\"{text}\"\"\"

Сохранена ли в переработанном тексте эта главная мысль?
Ответь одним словом: ДА или НЕТ. Ничего больше не пиши."""


def sudya_smysla(model, tezis, text):
    """Сохранность содержания. Без неё выигрыш в приватности ничего не стоит."""
    otvet = sprosit(model, PROMPT_SMYSL.format(tezis=tezis, text=text), num_predict=12)
    return otvet.strip().upper().startswith("ДА"), otvet


PROMPT_ABSTRAKCIYA = """Перепиши заметку так, чтобы в ней не осталось ничего, по чему можно
опознать конкретного человека: убери имена, города, профессии, места работы, семейные
обстоятельства и любые другие приметы.

При этом сохрани суть: что именно произошло между людьми и какой вывод сделал автор.
Пиши о механике происходящего, а не о конкретике: не «кто и где», а «что повторяется
и почему».

Заметка:
\"\"\"{text}\"\"\"

Отвечай ТОЛЬКО по-русски. Выдай ТОЛЬКО переписанный текст, без пояснений и заголовков."""

# ⛔ Страховка от бага 07.08.2026: при правке промпта из него выпал блок с самой
# заметкой, а `.format(text=...)` без плейсхолдера не падает — обе модели отвечали,
# не видя текста, и их «шаблонный ответ» я чуть не записал в свойство моделей.
assert "{text}" in PROMPT_ABSTRAKCIYA, "в промпте потеряна подстановка заметки"


def dolya_kirillicy(text):
    """Ревизия 1 от 07.08.2026: qwen2.5:7b на одном тексте ушёл в китайский, а судья
    смысла засчитал это как сохранённый смысл. Значит выдачу надо проверять машинно
    до того, как её судят: русская заметка обязана быть русской."""
    bukvy = [c for c in text if c.isalpha()]
    if not bukvy:
        return 0.0
    return sum(1 for c in bukvy if "\u0400" <= c <= "\u04FF") / len(bukvy)


def podnyat_abstrakciyu(model, text):
    otvet = sprosit(model, PROMPT_ABSTRAKCIYA.format(text=text), num_predict=350)
    return otvet, dolya_kirillicy(otvet)
