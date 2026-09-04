from __future__ import annotations

import datetime as dt
from typing import Any

from aiogram import Bot
from aiogram.client.session.base import BaseSession
from aiogram.methods import TelegramMethod
from aiogram.methods.base import TelegramType
from aiogram.types import CallbackQuery, Chat, Message, Update, User


class FakeSession(BaseSession):
    def __init__(self) -> None:
        super().__init__()
        self.calls: list[TelegramMethod[Any]] = []
        self._message_id = 1000

    async def close(self) -> None:
        return None

    async def make_request(self, bot: Bot, method: TelegramMethod[TelegramType], timeout: int | None = None) -> TelegramType:
        self.calls.append(method)
        name = type(method).__name__
        if name == "GetMe":
            return User(id=999, is_bot=True, first_name="Bot", username="dealbot")
        if name in ("SendMessage", "SendPhoto", "SendDocument", "CopyMessage", "SendInvoice", "EditMessageText"):
            self._message_id += 1
            chat_id = getattr(method, "chat_id", 1)
            text = getattr(method, "text", None) or getattr(method, "caption", None)
            if name == "CopyMessage":
                from aiogram.types import MessageId
                return MessageId(message_id=self._message_id)
            return Message(
                message_id=self._message_id,
                date=dt.datetime.now(),
                chat=Chat(id=int(chat_id), type="private"),
                from_user=User(id=999, is_bot=True, first_name="Bot"),
                text=text,
            ).as_(bot)
        return True

    async def stream_content(self, *args, **kwargs):
        raise NotImplementedError


OUTGOING = {"SendMessage", "SendPhoto", "SendDocument", "EditMessageText", "EditMessageCaption", "SendInvoice"}


def text_of(method: TelegramMethod[Any]) -> str:
    return getattr(method, "text", None) or getattr(method, "caption", None) or ""


def outgoing(calls: list[TelegramMethod[Any]]) -> list[TelegramMethod[Any]]:
    return [call for call in calls if type(call).__name__ in OUTGOING]


def last_markup(calls: list[TelegramMethod[Any]]):
    for call in reversed(calls):
        markup = getattr(call, "reply_markup", None)
        if markup is not None:
            return markup
    return None


_update_id = 0


def make_message(user_id: int, text: str, username: str = "tester") -> Update:
    global _update_id
    _update_id += 1
    return Update(update_id=_update_id, message=Message(
        message_id=_update_id,
        date=dt.datetime.now(),
        chat=Chat(id=user_id, type="private"),
        from_user=User(id=user_id, is_bot=False, first_name="Tester", username=username),
        text=text,
    ))


def make_callback(user_id: int, data: str, username: str = "tester") -> Update:
    global _update_id
    _update_id += 1
    message = Message(
        message_id=_update_id,
        date=dt.datetime.now(),
        chat=Chat(id=user_id, type="private"),
        from_user=User(id=999, is_bot=True, first_name="Bot"),
        text="menu",
    )
    return Update(update_id=_update_id, callback_query=CallbackQuery(
        id=str(_update_id), from_user=User(id=user_id, is_bot=False, first_name="Tester", username=username),
        chat_instance="x", data=data, message=message,
    ))
