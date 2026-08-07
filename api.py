#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""HTTP-режим обезличивателя: тонкая обёртка FastAPI над ядром `anonymizer.py`.

Зачем. Пока потребитель один (Ася импортирует модуль с диска), фикс доезжает
копированием файла. Потребителей будет больше — и тогда каждый, кто носит ядро
у себя, отстаёт от исправлений. HTTP-режим оставляет ядро в одном месте.

⛔ Служба предназначена для контура РФ (`ru-vps-1`): она обрабатывает чужие ПДн,
это 🔴-зона по 152-ФЗ. Наружу контура её выставлять нельзя.

Инварианты, которые здесь держатся (нарушение любого = утечка):

1. **Ключи соответствия живут только в памяти запроса.** Ни файлового кеша, ни
   записи на диск, ни глобального словаря ключей. Ответ отдаётся вызывающему —
   дальше ключи существуют только у него.
2. **В логи не попадает ни текст, ни ключи, ни фрагменты значений** — только
   счётчики (сколько масок, сколько символов, сколько миллисекунд). Причина не
   теоретическая: сообщение об ошибке — тоже ПДн, движок охотно кладёт в него
   кусок разбираемых данных. Поэтому и наружу, и в журнал уходит текст ошибки,
   написанный здесь, а не полученный от исключения.
3. **Fail-closed.** Любой сбой — отказ (4xx/5xx), а не «вот ваш текст, вроде
   обработали». Если запрошен слой, которого нет, — отказ, а не тихая работа
   без слоя: ровно так утечка возвращалась незаметно (см. HANDOFF, про
   `surnames_status` и отсутствующий pymorphy3).
4. **Состояние не течёт между запросами.** Тяжёлые слои (NER, словарь фамилий)
   поднимаются один раз при старте и переиспользуются, но `Anonymizer` —
   свой на каждый запрос: счётчики, ключи и дедупликация обязаны начинаться
   с нуля, иначе нумерация одного клиента продолжится у другого.
5. **Без токена служба не стартует.** Открытый обезличиватель — это открытый
   приём чужих ПДн.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import sys
import threading
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from starlette.concurrency import run_in_threadpool

# Ядро лежит рядом с этим файлом; запуск uvicorn возможен из любого каталога.
sys.path.insert(0, str(Path(__file__).resolve().parent))

import anonymizer as core  # noqa: E402

API_VERSION = "1.0.0"

# Предел тела запроса по умолчанию. Не «сколько не жалко», а верхняя граница
# осмысленного: обезличивание идёт по регулярным выражениям и нейросети, на
# мегабайтах это минуты работы и заблокированная очередь.
DEFAULT_MAX_BODY = 256 * 1024

log = logging.getLogger("anonymizer.api")


def nastroit_zhurnal() -> None:
    """Свой обработчик журнала — иначе счётчиков не будет видно вовсе.

    Найдено живым прогоном: uvicorn настраивает только собственные логгеры,
    у корневого обработчика нет, и все `log.info` этого модуля молча
    исчезали. Журнал, которого нет, — это не «нет утечки», это отсутствие
    единственного разрешённого следа (счётчиков).
    """
    uroven = os.environ.get("ANONYMIZER_LOG_LEVEL", "INFO").upper()
    log.setLevel(getattr(logging, uroven, logging.INFO))
    if log.handlers or logging.getLogger().handlers:
        return  # окружение (systemd, тесты, чужое приложение) уже всё настроило
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    log.addHandler(handler)


# ══════════════════════════════════════════════════════════════
#  Тяжёлые слои: один раз на процесс
# ══════════════════════════════════════════════════════════════

@dataclass
class Layers:
    """Разделяемые между запросами слои ядра.

    Все три объекта не хранят состояния разбора: `NerLayer.spans` создаёт
    свой `Doc` на вызов, `SurnameDictLayer` только спрашивает морфологию,
    правила словаря — скомпилированные шаблоны. Состояние (ключи, счётчики)
    живёт в `Anonymizer`, а он на каждый запрос свой.
    """

    ner: object | None = None
    ner_status: str = "выключен"
    surnames: object | None = None
    surnames_status: str = "выключен"
    dict_rules: list = field(default_factory=list)
    dict_status: str = "выключен"
    # Замок вокруг обращений к модели. Ни natasha, ни pymorphy3 не обещают
    # потокобезопасности, а цена ошибки здесь — спаны одного запроса,
    # применённые к тексту другого. Сериализация дешевле такой ошибки.
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    @property
    def ready(self) -> bool:
        """Оба слоя щита подняты.

        ⚠️ Именно `surnames` делает вердикт нетривиальным: без pymorphy3 слой
        отключается БЕЗ ошибки, ядро работает, тесты зелёные — и утечка
        (фамилии, которые NER пропускает точечно) возвращается незаметно.
        Поэтому вердикт машинный, а не «посмотреть глазами в статусы».
        """
        return self.ner is not None and self.surnames is not None


def build_layers() -> Layers:
    """Поднимает слои. Отсутствие слоя — не исключение, а статус: решение о
    том, работать ли без него, принимается выше (и по умолчанию — не работать).
    """
    layers = Layers()

    try:
        layers.ner = core.NerLayer()
        layers.ner_status = "включён (Natasha)"
    except ImportError:
        layers.ner_status = "недоступен — pip3 install natasha"
    except Exception as exc:  # пакет есть, но модель не поднялась
        layers.ner_status = f"ошибка загрузки: {type(exc).__name__}"

    try:
        layers.surnames = core.SurnameDictLayer()
        layers.surnames_status = "включён (pymorphy3)"
    except ImportError:
        layers.surnames_status = "недоступен — pip3 install pymorphy3"
    except Exception as exc:
        layers.surnames_status = f"ошибка загрузки: {type(exc).__name__}"

    rules, dict_file = core.load_custom_dict()
    layers.dict_rules = rules
    # В статусе только имя файла и число записей: сами записи — коммерческая
    # тайна, а `/health` открыт без токена.
    layers.dict_status = (f"{len(rules)} записей ({dict_file.name})" if rules
                          else f"пусто — {dict_file.name}")
    return layers


def make_anonymizer(layers: Layers, use_ner: bool, use_dict: bool) -> core.Anonymizer:
    """Свежий `Anonymizer` на запрос, с переиспользованием тяжёлых слоёв.

    Конструктор ядра поднимал бы natasha и pymorphy3 заново на каждый вызов
    (секунды), поэтому создаём его пустым и подставляем готовые слои — тот же
    приём, что в тестах ядра (`make_ner_anon`).

    ⚠️ Флаг `use_ner` в ядре управляет ДВУМЯ слоями сразу — нейросетевым и
    словарным добором фамилий. Здесь семантика сохранена намеренно: иначе
    у HTTP-режима и у прямого импорта один и тот же флаг значил бы разное.
    """
    anon = core.Anonymizer(use_ner=False, use_dict=False)
    if use_ner:
        anon.ner = layers.ner
        anon.ner_status = layers.ner_status
        anon.surnames = layers.surnames
        anon.surnames_status = layers.surnames_status
    if use_dict:
        anon.custom_rules = layers.dict_rules
        anon.dict_status = layers.dict_status
    return anon


def core_fingerprint() -> str:
    """Короткий отпечаток файла ядра — чтобы отличить «выкачено» от «кажется».

    Версии у ядра нет, а знать, то ли оно, нужно машинно: отпечаток сравнивается
    с локальным без чтения содержимого по ssh.
    """
    try:
        data = Path(core.__file__).resolve().read_bytes()
    except OSError:
        return "неизвестен"
    return hashlib.sha256(data).hexdigest()[:12]


# ══════════════════════════════════════════════════════════════
#  Схемы запросов
# ══════════════════════════════════════════════════════════════

class AnonymizeIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str
    use_ner: bool = True
    use_dict: bool = True


class KeyEntry(BaseModel):
    # `extra="ignore"`: ключи возвращаются нашим же ответом, но потребитель
    # мог добавить в них своё поле — это не повод отказать в расшифровке.
    model_config = ConfigDict(extra="ignore")

    original: str
    type: str = ""


class RestoreIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str
    keys: dict[str, KeyEntry] = Field(default_factory=dict)


def _oshibki_bez_znachenij(exc: ValidationError) -> str:
    """Текст отказа по ValidationError — БЕЗ значений полей.

    Штатное сообщение pydantic (и обработчик FastAPI по умолчанию) кладёт в
    ответ поле `input`, то есть тот самый текст, который прислали обезличить.
    Отдаём только имя поля и код ошибки — этого хватает, чтобы починить вызов.
    """
    chasti = []
    for err in exc.errors():
        pole = ".".join(str(x) for x in err.get("loc", ())) or "тело"
        chasti.append(f"{pole}: {err.get('type', 'invalid')}")
    return "поля не прошли проверку — " + "; ".join(chasti[:10])


# ══════════════════════════════════════════════════════════════
#  Транспорт: токен, размер тела, разбор
# ══════════════════════════════════════════════════════════════

def proverit_token(request: Request) -> None:
    """`Authorization: Bearer <token>`. Сравнение постоянного времени."""
    expected: bytes = request.app.state.token_bytes
    header = request.headers.get("authorization", "")
    scheme, _, value = header.partition(" ")
    if scheme.lower() != "bearer" or not hmac.compare_digest(
            value.strip().encode("utf-8"), expected):
        raise HTTPException(
            status_code=401,
            detail="нужен заголовок Authorization: Bearer <token>",
            headers={"WWW-Authenticate": "Bearer"},
        )


async def prochitat_telo(request: Request) -> bytes:
    """Тело запроса с жёстким пределом размера.

    Content-Length проверяется до чтения (отказ без затрат), но одного его мало:
    при chunked-передаче заголовка нет вовсе, поэтому байты считаются и на потоке.
    """
    limit: int = request.app.state.max_body
    zayavleno = request.headers.get("content-length")
    if zayavleno is not None:
        try:
            if int(zayavleno) > limit:
                raise HTTPException(413, f"тело запроса больше {limit} байт")
        except ValueError:
            raise HTTPException(400, "некорректный Content-Length")

    vsego = 0
    kuski = []
    async for kusok in request.stream():
        vsego += len(kusok)
        if vsego > limit:
            raise HTTPException(413, f"тело запроса больше {limit} байт")
        kuski.append(kusok)
    return b"".join(kuski)


def razobrat(raw: bytes, model: type[BaseModel]) -> BaseModel:
    """JSON → модель. Ни одно сообщение об отказе не цитирует присланное."""
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise HTTPException(400, "тело запроса не разобрано как JSON в UTF-8")
    if not isinstance(data, dict):
        raise HTTPException(422, "ожидается объект JSON")
    try:
        return model.model_validate(data)
    except ValidationError as exc:
        raise HTTPException(422, _oshibki_bez_znachenij(exc))


# ══════════════════════════════════════════════════════════════
#  Приложение
# ══════════════════════════════════════════════════════════════

def create_app(layers: Layers | None = None) -> FastAPI:
    """Собирает приложение. `layers=None` — слои поднимутся на старте (lifespan).

    ⛔ Без `ANONYMIZER_TOKEN` приложение не собирается. Это не удобство
    настройки, а fail-closed: служба, поднятая «пока без токена», принимает
    чужие ПДн от кого угодно.
    """
    nastroit_zhurnal()
    token = os.environ.get("ANONYMIZER_TOKEN", "").strip()
    if not token:
        raise RuntimeError(
            "не задан ANONYMIZER_TOKEN — служба обезличивания не стартует "
            "без токена (fail-closed)")
    if len(token) < 16:
        log.warning("токен короче 16 символов — это перебираемо")

    try:
        max_body = int(os.environ.get("ANONYMIZER_MAX_BODY", DEFAULT_MAX_BODY))
    except ValueError:
        raise RuntimeError("ANONYMIZER_MAX_BODY должен быть числом байт")
    if max_body <= 0:
        raise RuntimeError("ANONYMIZER_MAX_BODY должен быть больше нуля")

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # Модели грузятся секунды — один раз на процесс, а не на запрос.
        if app.state.layers is None:
            app.state.layers = build_layers()
        log.info("слои: ner=%s, surnames=%s, dict=%s, ready=%s",
                 app.state.layers.ner_status, app.state.layers.surnames_status,
                 app.state.layers.dict_status, app.state.layers.ready)
        yield

    # Интерактивная документация по умолчанию закрыта: лишняя открытая точка
    # у службы, работающей с ПДн. Включается переменной для отладки.
    docs = os.environ.get("ANONYMIZER_DOCS") == "1"
    app = FastAPI(
        title="Обезличиватель — HTTP-режим",
        version=API_VERSION,
        lifespan=lifespan,
        docs_url="/docs" if docs else None,
        redoc_url=None,
        openapi_url="/openapi.json" if docs else None,
    )
    app.state.layers = layers
    app.state.token_bytes = token.encode("utf-8")
    app.state.max_body = max_body

    @app.exception_handler(HTTPException)
    async def otkaz(request: Request, exc: HTTPException):
        log.info("отказ %s %s → %s", request.method, request.url.path, exc.status_code)
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": exc.status_code, "detail": exc.detail},
            headers=getattr(exc, "headers", None),
        )

    @app.exception_handler(RequestValidationError)
    async def otkaz_validacii(request: Request, exc: RequestValidationError):
        # Обработчик FastAPI по умолчанию вернул бы присланные значения.
        log.info("отказ валидации %s %s", request.method, request.url.path)
        return JSONResponse(status_code=422,
                            content={"error": 422, "detail": "запрос не прошёл проверку"})

    @app.exception_handler(Exception)
    async def sboy(request: Request, exc: Exception):
        """Fail-closed: наружу уходит ТОЛЬКО класс исключения.

        Текст исключения нельзя ни отдать, ни записать: движок кладёт в него
        фрагмент разбираемых данных. И ни при каком сбое наружу не уходит
        исходный текст как «обработанный» — здесь его просто нет.
        """
        log.error("сбой %s %s: %s", request.method, request.url.path,
                  type(exc).__name__)
        return JSONResponse(
            status_code=500,
            content={"error": 500,
                     "detail": f"обработка не выполнена ({type(exc).__name__})"})

    @app.get("/health")
    async def health(request: Request):
        """Состояние слоёв. Без токена — это точка мониторинга.

        Красное состояние видно двумя способами: полем `ready: false` и кодом
        503. Первое — для сборщика метрик, второе — для проверки живости,
        которая кода не разбирает.
        """
        sloi: Layers = request.app.state.layers or Layers()
        telo = {
            "status": "ok" if sloi.ready else "degraded",
            "ready": sloi.ready,
            "ner_status": sloi.ner_status,
            "surnames_status": sloi.surnames_status,
            "dict_status": sloi.dict_status,
            "version": API_VERSION,
            "core_fingerprint": core_fingerprint(),
        }
        return JSONResponse(status_code=200 if sloi.ready else 503, content=telo)

    @app.post("/anonymize")
    async def anonymize(request: Request):
        proverit_token(request)
        payload: AnonymizeIn = razobrat(await prochitat_telo(request), AnonymizeIn)
        sloi: Layers = request.app.state.layers or Layers()

        # Fail-closed: запрошен слой, которого нет, — отказ. Молча отработать
        # без слоя значит вернуть текст, который выглядит обезличенным.
        if payload.use_ner and not sloi.ready:
            raise HTTPException(
                503,
                "слой щита недоступен: ner={}, surnames={}".format(
                    sloi.ner_status, sloi.surnames_status))

        nachalo = time.monotonic()

        def rabota():
            anon = make_anonymizer(sloi, payload.use_ner, payload.use_dict)
            with sloi.lock:
                text = anon.replace(payload.text)
            vsego, po_tipam = anon.stats()
            return text, anon.keys, vsego, po_tipam

        text, keys, vsego, po_tipam = await run_in_threadpool(rabota)

        # Только счётчики: ни текста, ни ключей, ни фрагментов значений.
        log.info("anonymize: символов=%d, масок=%d, типов=%d, мс=%d",
                 len(payload.text), vsego, len(po_tipam),
                 int((time.monotonic() - nachalo) * 1000))
        return {"text": text, "keys": keys,
                "stats": {"total": vsego, "by_type": po_tipam}}

    @app.post("/restore")
    async def restore(request: Request):
        proverit_token(request)
        payload: RestoreIn = razobrat(await prochitat_telo(request), RestoreIn)
        keys = {ph: {"original": v.original, "type": v.type}
                for ph, v in payload.keys.items()}
        text = await run_in_threadpool(core.restore_text, payload.text, keys)
        log.info("restore: символов=%d, ключей=%d", len(payload.text), len(keys))
        return {"text": text}

    return app


# uvicorn api:app — падает при старте, если токена нет. Так и задумано.
app = create_app()
