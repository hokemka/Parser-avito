from __future__ import annotations

from tgbot.database.models import SearchTask, Tariff, User
from tgbot.services.subscriptions import AccessInfo
from tgbot.utils.emoji import em
from tgbot.utils.text import format_dt, format_money, format_price_range, h, pluralize, time_ago


def welcome_text(first_name: str | None) -> str:
    name = h(first_name) if first_name else "друг"
    return (
        f"{em('smile')} <b>Привет, {name}!</b>\n\n"
        f"Я ищу выгодные объявления на Авито и оцениваю каждое с помощью ИИ: состояние, цена относительно рынка, риски и потенциал перепродажи.\n\n"
        f"{em('eye')} <b>Найти выгодное</b> — разовый поиск с оценкой 0–10\n"
        f"{em('bell')} <b>Мониторинг</b> — новые объявления прилетают сразу после публикации\n"
        f"{em('star')} <b>Подписка</b> — мониторинги и безлимитные поиски\n\n"
        f"Выберите раздел в меню ниже."
    )


def menu_text() -> str:
    return f"{em('home')} <b>Главное меню</b>\n\nЧто делаем?"


def help_text(free_searches: int) -> str:
    return (
        f"{em('info')} <b>Как это работает</b>\n\n"
        f"1. Нажмите «Найти выгодное», укажите товар, город и бюджет.\n"
        f"2. Бот собирает свежие объявления с Авито и отправляет лучшие в ИИ-оценщик.\n"
        f"3. Каждое объявление получает оценку 0–10, вердикт, разбор состояния и рисков, рекомендуемый торг.\n"
        f"4. С подпиской можно включить мониторинг: новые подходящие объявления приходят автоматически.\n\n"
        f"{em('gift')} Без подписки доступно {pluralize(free_searches, 'бесплатный поиск', 'бесплатных поиска', 'бесплатных поисков')}.\n"
        f"{em('lock')} Мониторинг и безлимитный поиск — по подписке."
    )


def access_line(access: AccessInfo) -> str:
    if access.has_subscription and access.expires_at:
        return f"{em('star')} Подписка: <b>{h(access.tariff_name)}</b> до {format_dt(access.expires_at)}"
    if access.is_admin:
        return f"{em('settings')} Статус: <b>администратор</b>"
    return f"{em('lock')} Подписка: <b>нет</b> · бесплатных поисков осталось: {access.free_searches_left}"


def profile_text(user: User, access: AccessInfo, active_tasks: int) -> str:
    username = f"@{h(user.username)}" if user.username else "не указан"
    return (
        f"{em('profile')} <b>Профиль</b>\n\n"
        f"{em('tag')} ID: <code>{user.id}</code>\n"
        f"{em('font')} Username: {username}\n"
        f"{em('calendar')} С нами с {format_dt(user.registered_at)}\n"
        f"{em('wallet')} Баланс: <b>{format_money(user.balance)}</b>\n"
        f"{access_line(access)}\n"
        f"{em('bell')} Мониторингов: {active_tasks} из {access.max_tasks}\n"
        f"{em('stats')} Поисков выполнено: {user.searches_count}"
    )


def tariffs_text(access: AccessInfo, tariffs: list[Tariff]) -> str:
    lines = [
        f"{em('star')} <b>Подписка</b>",
        "",
        access_line(access),
        "",
        "Подписка открывает мониторинг новых объявлений и безлимитные поиски с ИИ-оценкой.",
        "",
    ]
    for tariff in tariffs:
        description = f" — {h(tariff.description)}" if tariff.description else ""
        lines.append(f"{em('tag')} <b>{h(tariff.name)}</b> · {tariff.price_rub} ₽ / {pluralize(tariff.days, 'день', 'дня', 'дней')}{description}")
    lines.append("")
    lines.append("Выберите тариф:")
    return "\n".join(lines)


def tariff_text(tariff: Tariff) -> str:
    description = f"\n{h(tariff.description)}\n" if tariff.description else ""
    return (
        f"{em('tag')} <b>Тариф «{h(tariff.name)}»</b>\n{description}\n"
        f"{em('calendar')} Срок: {pluralize(tariff.days, 'день', 'дня', 'дней')}\n"
        f"{em('bell')} Мониторингов: до {tariff.max_tasks}\n"
        f"{em('clock')} Проверка новых объявлений: каждые {tariff.check_interval // 60 or 1} мин\n"
        f"{em('money')} Цена: <b>{tariff.price_rub} ₽</b> · {tariff.price_stars} ⭐\n\n"
        f"Выберите способ оплаты:"
    )


def task_text(task: SearchTask) -> str:
    status = f"{em('green')} активен" if task.is_active else f"{em('red')} на паузе"
    error = f"\n{em('cross')} Последняя ошибка: {h(task.last_error)}" if task.last_error else ""
    wishes = f"\n{em('write')} Пожелания: {h(task.wishes)}" if task.wishes else ""
    return (
        f"{em('bell')} <b>Мониторинг «{h(task.query)}»</b>\n\n"
        f"Статус: {status}\n"
        f"{em('pin')} Город: {h(task.location_name)}\n"
        f"{em('money')} Бюджет: {format_price_range(task.price_min, task.price_max)}{wishes}\n"
        f"{em('stats')} Минимальная оценка: {task.min_rating}/10\n"
        f"{em('clock')} Интервал: каждые {max(1, task.check_interval // 60)} мин\n"
        f"{em('calendar')} Создан: {format_dt(task.created_at)}\n"
        f"{em('elapsed')} Проверка: {time_ago(task.last_checked_at) if task.last_checked_at else 'ещё не было'}\n"
        f"{em('box')} Найдено новых: {task.found_count} · отправлено: {task.notified_count}{error}"
    )


def search_summary_text(query: str, location_name: str, price_min: int | None, price_max: int | None, wishes: str | None) -> str:
    wishes_line = f"\n{em('write')} Пожелания: {h(wishes)}" if wishes else ""
    return (
        f"{em('eye')} <b>Проверьте запрос</b>\n\n"
        f"{em('box')} Товар: <b>{h(query)}</b>\n"
        f"{em('pin')} Город: {h(location_name)}\n"
        f"{em('money')} Бюджет: {format_price_range(price_min, price_max)}{wishes_line}\n\n"
        f"«Найти сейчас» — соберу свежие объявления и оценю лучшие.\n"
        f"«Мониторинг» — буду присылать новые подходящие объявления автоматически."
    )
