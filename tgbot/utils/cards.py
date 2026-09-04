from __future__ import annotations

from tgbot.services.ai import Evaluation
from tgbot.services.avito import Listing
from tgbot.utils.emoji import em
from tgbot.utils.text import format_price, h, rating_bar, time_ago, truncate

CAPTION_LIMIT = 1000
MESSAGE_LIMIT = 4000


def rating_marker(rating: float) -> str:
    if rating >= 7.5:
        return em("green")
    if rating >= 5:
        return em("blue")
    return em("red")


def verdict_line(evaluation: Evaluation) -> str:
    icon = {"buy": em("check"), "consider": em("eye"), "skip": em("cross")}.get(evaluation.verdict, em("eye"))
    return f"{icon} <b>{evaluation.verdict_label}</b>"


def listing_card(listing: Listing, evaluation: Evaluation, index: int | None = None) -> str:
    number = f"{index}. " if index else ""
    market = f" · рынок ≈ {format_price(evaluation.market_price)}" if evaluation.market_price else ""
    lines = [
        f"{em('tag')} <b>{number}{h(truncate(listing.title, 90))}</b>",
        f"{em('money')} <b>{format_price(listing.price)}</b>{market}",
        f"{em('pin')} {h(listing.location or 'локация не указана')} · {time_ago(listing.published_at)}",
        "",
        f"{rating_marker(evaluation.rating)} Оценка: <b>{evaluation.rating:g}/10</b> <code>{rating_bar(evaluation.rating)}</code>",
        verdict_line(evaluation),
        f"{em('brush')} Состояние: {h(evaluation.condition)}",
    ]
    if evaluation.summary:
        lines.append(f"{em('info')} {h(truncate(evaluation.summary, 260))}")
    if evaluation.red_flags:
        lines.append(f"{em('red')} Риски: {h(truncate('; '.join(evaluation.red_flags), 160))}")
    if evaluation.recommended_offer:
        lines.append(f"{em('send_money')} Торг: предложить {format_price(evaluation.recommended_offer)}")
    if not evaluation.ai_used:
        lines.append(f"{em('bot')} <i>Оценка без ИИ (ключ 1min.ai не настроен или недоступен)</i>")
    text = "\n".join(lines)
    return text if len(text) <= CAPTION_LIMIT else text[:CAPTION_LIMIT - 1] + "…"


def listing_details(listing: Listing, evaluation: Evaluation) -> str:
    lines = [
        f"{em('tag')} <b>{h(listing.title)}</b>",
        f"{em('money')} <b>{format_price(listing.price)}</b>",
        f"{em('pin')} {h(listing.location or 'локация не указана')} · {time_ago(listing.published_at)}",
    ]
    if listing.seller_name or listing.seller_type:
        lines.append(f"{em('profile')} Продавец: {h(listing.seller_name or '—')} ({h(listing.seller_type or 'тип неизвестен')})")
    lines.append("")
    lines.append(f"{rating_marker(evaluation.rating)} <b>Оценка {evaluation.rating:g}/10</b> · {verdict_line(evaluation)}")
    if evaluation.condition_score is not None:
        lines.append(f"{em('brush')} Состояние: {h(evaluation.condition)} ({evaluation.condition_score:g}/10)")
    else:
        lines.append(f"{em('brush')} Состояние: {h(evaluation.condition)}")
    if evaluation.market_price:
        lines.append(f"{em('growth')} Рыночная цена: ≈ {format_price(evaluation.market_price)}")
    if evaluation.recommended_offer:
        lines.append(f"{em('send_money')} Рекомендуемый торг: {format_price(evaluation.recommended_offer)}")
    if evaluation.profit_potential:
        lines.append(f"{em('wallet')} Выгода: {h(evaluation.profit_potential)}")
    if evaluation.summary:
        lines.append(f"\n{em('info')} {h(evaluation.summary)}")
    if evaluation.pros:
        lines.append(f"\n{em('check')} <b>Плюсы</b>\n" + "\n".join(f"• {h(item)}" for item in evaluation.pros))
    if evaluation.cons:
        lines.append(f"\n{em('cross')} <b>Минусы</b>\n" + "\n".join(f"• {h(item)}" for item in evaluation.cons))
    if evaluation.red_flags:
        lines.append(f"\n{em('red')} <b>Красные флаги</b>\n" + "\n".join(f"• {h(item)}" for item in evaluation.red_flags))
    if evaluation.questions_to_seller:
        lines.append(f"\n{em('write')} <b>Спросить у продавца</b>\n" + "\n".join(f"• {h(item)}" for item in evaluation.questions_to_seller))
    if listing.params:
        params = "\n".join(f"• {h(key)}: {h(value)}" for key, value in list(listing.params.items())[:12])
        lines.append(f"\n{em('box')} <b>Характеристики</b>\n{params}")
    if listing.description:
        lines.append(f"\n{em('font')} <b>Описание</b>\n{h(truncate(listing.description, 900))}")
    text = "\n".join(lines)
    return text if len(text) <= MESSAGE_LIMIT else text[:MESSAGE_LIMIT - 1] + "…"
