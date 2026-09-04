from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tgbot.database.models import ListingEvaluation
from tgbot.services.ai import Evaluation, ListingEvaluator, SearchRequest, heuristic_evaluation
from tgbot.services.avito import AvitoClient, Listing, Location
from tgbot.services.settings import SettingsService

logger = logging.getLogger(__name__)

ProgressCallback = Callable[[str], Awaitable[None]]
EVALUATION_TTL = timedelta(days=3)


@dataclass(slots=True)
class RatedListing:
    listing: Listing
    evaluation: Evaluation

    @property
    def rating(self) -> float:
        return self.evaluation.rating


class SearchService:
    def __init__(
        self,
        avito: AvitoClient,
        evaluator: ListingEvaluator,
        session_factory: async_sessionmaker[AsyncSession],
        settings: SettingsService,
    ) -> None:
        self.avito = avito
        self.evaluator = evaluator
        self._session_factory = session_factory
        self.settings = settings

    async def find_listings(self, request: SearchRequest, location: Location, pages: int = 1, limit: int | None = None) -> list[Listing]:
        self.avito.configure(self.settings.values.avito_proxy, self.settings.values.avito_request_delay)
        limit = limit or self.settings.values.listings_per_search
        listings = await self.avito.search(request.query, location, request.price_min, request.price_max, pages=pages, limit=min(limit, 50))
        listings.sort(key=lambda item: item.published_at or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
        return listings[:limit]

    def pick_candidates(self, request: SearchRequest, listings: list[Listing], limit: int) -> list[Listing]:
        def score(item: Listing) -> float:
            quick = heuristic_evaluation(request, item).rating
            recency = 0.0
            if item.published_at:
                age_hours = (datetime.now(timezone.utc) - item.published_at).total_seconds() / 3600
                recency = max(0.0, 1.5 - age_hours / 48)
            return quick + recency

        return sorted(listings, key=score, reverse=True)[:limit]

    async def _cached_evaluation(self, session: AsyncSession, listing_id: int, request_hash: str) -> Evaluation | None:
        query = select(ListingEvaluation).where(ListingEvaluation.listing_id == listing_id, ListingEvaluation.request_hash == request_hash)
        row = (await session.execute(query)).scalar_one_or_none()
        if row is None or row.evaluated_at < datetime.utcnow() - EVALUATION_TTL:
            return None
        try:
            evaluation = Evaluation.from_json(row.payload)
        except (ValueError, TypeError):
            return None
        return evaluation if evaluation.ai_used else None

    async def _store_evaluation(self, session: AsyncSession, listing: Listing, request_hash: str, evaluation: Evaluation) -> None:
        query = select(ListingEvaluation).where(ListingEvaluation.listing_id == listing.id, ListingEvaluation.request_hash == request_hash)
        row = (await session.execute(query)).scalar_one_or_none()
        if row is None:
            session.add(ListingEvaluation(
                listing_id=listing.id, request_hash=request_hash, rating=evaluation.rating,
                payload=evaluation.to_json(), listing_payload=listing.to_json(),
            ))
        else:
            row.rating = evaluation.rating
            row.payload = evaluation.to_json()
            row.listing_payload = listing.to_json()
            row.evaluated_at = datetime.utcnow()
        await session.commit()

    async def get_stored(self, listing_id: int, request_hash: str) -> RatedListing | None:
        async with self._session_factory() as session:
            query = select(ListingEvaluation).where(ListingEvaluation.listing_id == listing_id, ListingEvaluation.request_hash == request_hash)
            row = (await session.execute(query)).scalar_one_or_none()
        if row is None or not row.listing_payload:
            return None
        try:
            return RatedListing(Listing.from_json(row.listing_payload), Evaluation.from_json(row.payload))
        except (ValueError, TypeError, KeyError):
            return None

    async def evaluate(self, request: SearchRequest, listing: Listing, fetch_details: bool = True) -> RatedListing:
        values = self.settings.values
        async with self._session_factory() as session:
            cached = await self._cached_evaluation(session, listing.id, request.fingerprint)
        if cached:
            async with self._session_factory() as session:
                await self._store_evaluation(session, listing, request.fingerprint, cached)
            return RatedListing(listing, cached)
        if fetch_details and not listing.description:
            try:
                listing = await self.avito.fetch_details(listing)
            except Exception as exc:
                logger.info("details unavailable for %s: %s", listing.id, exc)
        evaluation = await self.evaluator.evaluate(request, listing, values.ai_model, values.ai_analyze_images, values.ai_max_images)
        async with self._session_factory() as session:
            await self._store_evaluation(session, listing, request.fingerprint, evaluation)
        return RatedListing(listing, evaluation)

    async def search_and_rate(
        self,
        request: SearchRequest,
        location: Location,
        on_progress: ProgressCallback | None = None,
    ) -> tuple[list[RatedListing], int]:
        values = self.settings.values
        listings = await self.find_listings(request, location, pages=1)
        if not listings:
            return [], 0
        candidates = self.pick_candidates(request, listings, values.ai_candidates_per_search)
        rated: list[RatedListing] = []
        for index, listing in enumerate(candidates, start=1):
            if on_progress:
                await on_progress(f"Найдено {len(listings)} объявлений. Оцениваю {index} из {len(candidates)}…")
            rated.append(await self.evaluate(request, listing))
        rated.sort(key=lambda item: item.rating, reverse=True)
        return rated, len(listings)
