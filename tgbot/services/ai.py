from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
from dataclasses import asdict, dataclass, field
from typing import Any

import aiohttp

from tgbot.services.avito import Listing
from tgbot.utils.text import format_price, format_price_range, time_ago

logger = logging.getLogger(__name__)

ONE_MIN_BASE = "https://api.1min.ai"
JSON_BLOCK_RE = re.compile(r"\{.*\}", re.DOTALL)
MAX_DESCRIPTION_CHARS = 2500


class AiError(Exception):
    pass


@dataclass(slots=True)
class SearchRequest:
    query: str
    location_name: str
    price_min: int | None = None
    price_max: int | None = None
    wishes: str | None = None

    @property
    def fingerprint(self) -> str:
        raw = f"{self.query.strip().lower()}|{self.price_min}|{self.price_max}|{(self.wishes or '').strip().lower()}"
        return hashlib.md5(raw.encode("utf-8")).hexdigest()[:32]


@dataclass(slots=True)
class Evaluation:
    rating: float
    verdict: str
    matches_request: bool
    condition: str
    condition_score: float | None
    summary: str
    pros: list[str] = field(default_factory=list)
    cons: list[str] = field(default_factory=list)
    red_flags: list[str] = field(default_factory=list)
    market_price: int | None = None
    recommended_offer: int | None = None
    profit_potential: str = ""
    questions_to_seller: list[str] = field(default_factory=list)
    ai_used: bool = True

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False)

    @classmethod
    def from_json(cls, raw: str) -> "Evaluation":
        data = json.loads(raw)
        return cls(**{key: data.get(key) for key in cls.__dataclass_fields__ if key in data})

    @property
    def verdict_label(self) -> str:
        return {"buy": "Брать", "consider": "Присмотреться", "skip": "Пропустить"}.get(self.verdict, "Присмотреться")


def _first_text(value: Any, depth: int = 0) -> str | None:
    if depth > 6:
        return None
    if isinstance(value, str):
        return value if value.strip() else None
    if isinstance(value, list):
        parts = [text for text in (_first_text(v, depth + 1) for v in value) if text]
        return "\n".join(parts) if parts else None
    if isinstance(value, dict):
        for key in ("resultObject", "content", "text", "result", "message", "answer"):
            if key in value:
                found = _first_text(value[key], depth + 1)
                if found:
                    return found
        for nested in value.values():
            if isinstance(nested, (dict, list)):
                found = _first_text(nested, depth + 1)
                if found:
                    return found
    return None


def extract_answer(payload: Any) -> str:
    if isinstance(payload, dict):
        record = payload.get("aiRecord")
        if isinstance(record, dict):
            detail = record.get("aiRecordDetail")
            if isinstance(detail, dict):
                found = _first_text(detail.get("resultObject"))
                if found:
                    return found
    found = _first_text(payload)
    if not found:
        raise AiError("empty answer from 1min.ai")
    return found


def _extract_asset_key(payload: Any) -> str | None:
    if not isinstance(payload, dict):
        return None
    for path in (("fileContent", "path"), ("fileContent", "key"), ("asset", "key"), ("asset", "path"), ("path",), ("key",), ("id",)):
        node: Any = payload
        for part in path:
            node = node.get(part) if isinstance(node, dict) else None
        if isinstance(node, str) and node:
            return node
    return None


class OneMinClient:
    def __init__(self, api_key: str, timeout: int = 90) -> None:
        self.api_key = api_key
        self.timeout = aiohttp.ClientTimeout(total=timeout)
        self._session: aiohttp.ClientSession | None = None

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)

    def _headers(self) -> dict[str, str]:
        return {"API-KEY": self.api_key, "Authorization": f"Bearer {self.api_key}"}

    def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=self.timeout)
        return self._session

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()

    async def _post_json(self, path: str, body: dict[str, Any], params: dict[str, str] | None = None) -> Any:
        session = self._get_session()
        last_error = "unknown"
        for attempt in range(3):
            try:
                async with session.post(f"{ONE_MIN_BASE}{path}", json=body, params=params, headers=self._headers()) as response:
                    text = await response.text()
                    if response.status == 429 or response.status >= 500:
                        last_error = f"HTTP {response.status}: {text[:200]}"
                        await asyncio.sleep(2.0 * (attempt + 1))
                        continue
                    if response.status >= 400:
                        raise AiError(f"HTTP {response.status}: {text[:300]}")
                    try:
                        return json.loads(text)
                    except json.JSONDecodeError:
                        return {"content": text}
            except aiohttp.ClientError as exc:
                last_error = str(exc)
                await asyncio.sleep(2.0 * (attempt + 1))
        raise AiError(last_error)

    async def upload_image(self, image_bytes: bytes, filename: str = "photo.jpg") -> str | None:
        session = self._get_session()
        form = aiohttp.FormData()
        form.add_field("asset", image_bytes, filename=filename, content_type="image/jpeg")
        try:
            async with session.post(f"{ONE_MIN_BASE}/api/assets", data=form, headers=self._headers()) as response:
                if response.status >= 400:
                    logger.info("1min asset upload failed: HTTP %s", response.status)
                    return None
                payload = await response.json(content_type=None)
        except (aiohttp.ClientError, json.JSONDecodeError) as exc:
            logger.info("1min asset upload error: %s", exc)
            return None
        return _extract_asset_key(payload)

    async def chat(self, prompt: str, model: str, image_keys: list[str] | None = None) -> str:
        image_keys = image_keys or []
        unified_prompt: dict[str, Any] = {
            "prompt": prompt,
            "settings": {
                "webSearchSettings": {"webSearch": False},
                "historySettings": {"isMixed": False},
                "withMemories": False,
            },
        }
        if image_keys:
            unified_prompt["attachments"] = {"images": image_keys}
        attempts: list[tuple[str, dict[str, Any]]] = [
            ("/api/chat-with-ai", {"type": "UNIFY_CHAT_WITH_AI", "model": model, "promptObject": unified_prompt}),
        ]
        if image_keys:
            attempts.append(("/api/features", {
                "type": "CHAT_WITH_IMAGE",
                "model": model,
                "promptObject": {"prompt": prompt, "isMixed": False, "imageList": image_keys},
            }))
        attempts.append(("/api/features", {
            "type": "CHAT_WITH_AI",
            "model": model,
            "promptObject": {"prompt": prompt, "isMixed": False, "webSearch": False},
        }))
        errors: list[str] = []
        for path, body in attempts:
            try:
                payload = await self._post_json(path, body, params={"isStreaming": "false"})
                return extract_answer(payload)
            except AiError as exc:
                errors.append(f"{path}: {exc}")
                logger.info("1min request via %s failed: %s", path, exc)
        raise AiError("; ".join(errors))


def build_evaluation_prompt(request: SearchRequest, listing: Listing, photos_attached: int) -> str:
    params_text = "; ".join(f"{key}: {value}" for key, value in list(listing.params.items())[:25]) or "не указаны"
    description = listing.description.strip()[:MAX_DESCRIPTION_CHARS] or "описание отсутствует"
    wishes = (request.wishes or "").strip() or "не указаны"
    photos_line = f"приложено {photos_attached} шт., оцени состояние по ним" if photos_attached else "фото не приложены, опирайся на текст"
    return f"""Ты — эксперт-оценщик объявлений с Авито с многолетним опытом перекупа техники и товаров. Работаешь одновременно для перекупщика (ищет маржу) и обычного покупателя (ищет честную выгодную покупку). Оцени объявление строго, честно и конкретно, без воды.

ЗАПРОС ПОЛЬЗОВАТЕЛЯ
- Ищет: {request.query}
- Бюджет: {format_price_range(request.price_min, request.price_max)}
- Регион: {request.location_name}
- Пожелания: {wishes}

ОБЪЯВЛЕНИЕ
- Заголовок: {listing.title}
- Цена: {format_price(listing.price)}
- Локация: {listing.location or 'не указана'}
- Опубликовано: {time_ago(listing.published_at)}
- Продавец: {listing.seller_name or 'не указан'} ({listing.seller_type or 'тип неизвестен'})
- Категория: {listing.category or 'не указана'}
- Характеристики: {params_text}
- Фото: {photos_line}
- Описание:
{description}

ЧТО ОЦЕНИТЬ
1. Соответствие запросу: та ли модель, комплектация, поколение, память, версия, что нужны пользователю. Если это аксессуар, запчасть, копия, «под заказ» или другой товар — matches_request=false и низкая оценка.
2. Состояние: по описанию и фото (царапины, сколы, ремонт, замена деталей, износ АКБ, комплект, коробка, чеки, гарантия).
3. Цена: сравни с реальной рыночной ценой на вторичке для такого состояния (оцени сам по своим знаниям рынка РФ). Укажи market_price.
4. Риски и красные флаги: цена сильно ниже рынка, шаблонный/скопированный текст, «срочно», предоплата, отправка только через посредников, нет фото или стоковые фото, восстановленный/реф под видом оригинала, серые схемы.
5. Выгода: насколько это хорошая сделка для покупателя и есть ли маржа для перепродажи. Предложи recommended_offer — с какой суммы разумно торговаться.

ШКАЛА rating (0-10)
- 9-10: идеально подходит, цена заметно ниже рынка, состояние отличное, рисков нет — брать сразу.
- 7-8: хорошее предложение, цена ниже или на уровне рынка, мелкие недостатки.
- 5-6: обычное рыночное предложение либо есть заметные вопросы.
- 3-4: слабое: дорого, состояние плохое, частичное несоответствие.
- 0-2: не подходит, мошенничество или явный обман.
verdict: "buy" при rating >= 7.5, "consider" при 5-7.4, "skip" при < 5.

Ответь ТОЛЬКО одним валидным JSON-объектом без markdown и пояснений, все тексты на русском:
{{
  "rating": число от 0 до 10 с одним знаком после запятой,
  "verdict": "buy" | "consider" | "skip",
  "matches_request": true | false,
  "condition": "состояние одной фразой",
  "condition_score": число от 0 до 10,
  "summary": "2-3 предложения, почему такая оценка",
  "pros": ["плюс", "..."],
  "cons": ["минус", "..."],
  "red_flags": ["риск", "..."],
  "market_price": целое число рублей или null,
  "recommended_offer": целое число рублей или null,
  "profit_potential": "выгода для перепродажи одной фразой",
  "questions_to_seller": ["вопрос продавцу", "..."]
}}"""


def _clean_list(value: Any, limit: int = 5) -> list[str]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()][:limit]


def _clean_int(value: Any) -> int | None:
    if value in (None, "", "null"):
        return None
    try:
        return int(float(str(value).replace(" ", "")))
    except ValueError:
        return None


def _clean_score(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return max(0.0, min(10.0, round(number, 1)))


def parse_evaluation(raw_answer: str) -> Evaluation:
    text = raw_answer.strip()
    text = re.sub(r"^```(?:json)?", "", text).strip()
    text = re.sub(r"```$", "", text).strip()
    match = JSON_BLOCK_RE.search(text)
    if not match:
        raise AiError("no json in answer")
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError as exc:
        raise AiError(f"invalid json: {exc}") from exc
    if not isinstance(data, dict):
        raise AiError("json is not an object")
    rating = _clean_score(data.get("rating"))
    if rating is None:
        raise AiError("rating missing")
    verdict = str(data.get("verdict") or "").strip().lower()
    if verdict not in ("buy", "consider", "skip"):
        verdict = "buy" if rating >= 7.5 else "consider" if rating >= 5 else "skip"
    return Evaluation(
        rating=rating,
        verdict=verdict,
        matches_request=bool(data.get("matches_request", True)),
        condition=str(data.get("condition") or "нет данных").strip(),
        condition_score=_clean_score(data.get("condition_score")),
        summary=str(data.get("summary") or "").strip(),
        pros=_clean_list(data.get("pros")),
        cons=_clean_list(data.get("cons")),
        red_flags=_clean_list(data.get("red_flags")),
        market_price=_clean_int(data.get("market_price")),
        recommended_offer=_clean_int(data.get("recommended_offer")),
        profit_potential=str(data.get("profit_potential") or "").strip(),
        questions_to_seller=_clean_list(data.get("questions_to_seller"), limit=3),
    )


def heuristic_evaluation(request: SearchRequest, listing: Listing) -> Evaluation:
    rating = 5.0
    cons: list[str] = []
    pros: list[str] = []
    query_words = [w for w in re.findall(r"\w+", request.query.lower()) if len(w) > 1]
    haystack = f"{listing.title} {listing.description}".lower()
    matched = sum(1 for word in query_words if word in haystack)
    matches = matched >= max(1, len(query_words) // 2)
    if matches:
        rating += 1.0
        pros.append("Заголовок соответствует запросу")
    else:
        rating -= 2.0
        cons.append("Заголовок слабо соответствует запросу")
    if listing.price is not None and request.price_max:
        if listing.price <= request.price_max * 0.85:
            rating += 1.5
            pros.append("Цена заметно ниже бюджета")
        elif listing.price <= request.price_max:
            rating += 0.5
            pros.append("Цена в рамках бюджета")
    if listing.price is not None and request.price_min and listing.price < request.price_min * 0.6:
        rating -= 1.0
        cons.append("Подозрительно низкая цена")
    if not listing.images:
        rating -= 1.0
        cons.append("Нет фотографий")
    if not listing.description:
        rating -= 0.5
        cons.append("Нет описания")
    rating = max(0.0, min(10.0, round(rating, 1)))
    return Evaluation(
        rating=rating,
        verdict="buy" if rating >= 7.5 else "consider" if rating >= 5 else "skip",
        matches_request=matches,
        condition="не оценивалось (ИИ недоступен)",
        condition_score=None,
        summary="Оценка выполнена по базовым правилам без ИИ: соответствие запросу, цена относительно бюджета, наличие фото и описания.",
        pros=pros,
        cons=cons,
        red_flags=[],
        market_price=None,
        recommended_offer=int(listing.price * 0.93) if listing.price else None,
        profit_potential="",
        questions_to_seller=[],
        ai_used=False,
    )


class ListingEvaluator:
    def __init__(self, client: OneMinClient, max_concurrent: int = 3) -> None:
        self.client = client
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._http: aiohttp.ClientSession | None = None

    def _get_http(self) -> aiohttp.ClientSession:
        if self._http is None or self._http.closed:
            self._http = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20))
        return self._http

    async def close(self) -> None:
        if self._http and not self._http.closed:
            await self._http.close()
        await self.client.close()

    async def _download(self, url: str) -> bytes | None:
        try:
            async with self._get_http().get(url, headers={"User-Agent": "Mozilla/5.0", "Referer": "https://www.avito.ru/"}) as response:
                if response.status != 200:
                    return None
                data = await response.read()
                return data if len(data) < 6_000_000 else None
        except aiohttp.ClientError as exc:
            logger.info("image download failed %s: %s", url, exc)
            return None

    async def _upload_photos(self, listing: Listing, max_images: int) -> list[str]:
        keys: list[str] = []
        for url in listing.images[:max_images]:
            image = await self._download(url)
            if not image:
                continue
            key = await self.client.upload_image(image, filename=f"{listing.id}_{len(keys)}.jpg")
            if key:
                keys.append(key)
        return keys

    async def evaluate(self, request: SearchRequest, listing: Listing, model: str, analyze_images: bool, max_images: int) -> Evaluation:
        if not self.client.enabled:
            return heuristic_evaluation(request, listing)
        async with self._semaphore:
            image_keys = await self._upload_photos(listing, max_images) if analyze_images and listing.images else []
            prompt = build_evaluation_prompt(request, listing, len(image_keys))
            try:
                answer = await self.client.chat(prompt, model=model, image_keys=image_keys)
                return parse_evaluation(answer)
            except AiError as exc:
                logger.warning("ai evaluation failed for %s: %s", listing.id, exc)
                if image_keys:
                    try:
                        answer = await self.client.chat(build_evaluation_prompt(request, listing, 0), model=model)
                        return parse_evaluation(answer)
                    except AiError as retry_exc:
                        logger.warning("ai text-only retry failed for %s: %s", listing.id, retry_exc)
        return heuristic_evaluation(request, listing)
