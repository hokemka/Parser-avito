from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from tgbot.database.models import User
from tgbot.keyboards.user import MENU_HELP, help_kb, main_menu_inline, main_menu_reply
from tgbot.services.settings import SettingsService
from tgbot.utils.texts import help_text, menu_text, welcome_text
from config import Config

router = Router(name="start")


@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext, user: User, is_admin: bool) -> None:
    await state.clear()
    await message.answer(welcome_text(user.first_name), reply_markup=main_menu_reply(is_admin))
    await message.answer(menu_text(), reply_markup=main_menu_inline())


@router.message(Command("menu"))
async def cmd_menu(message: Message, state: FSMContext, is_admin: bool) -> None:
    await state.clear()
    await message.answer(menu_text(), reply_markup=main_menu_reply(is_admin))
    await message.answer(menu_text(), reply_markup=main_menu_inline())


@router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext, is_admin: bool) -> None:
    await state.clear()
    await message.answer("Действие отменено.", reply_markup=main_menu_reply(is_admin))


@router.callback_query(F.data == "menu:main")
async def callback_main_menu(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    try:
        await callback.message.edit_text(menu_text(), reply_markup=main_menu_inline())
    except Exception:
        await callback.message.answer(menu_text(), reply_markup=main_menu_inline())
    await callback.answer()


@router.message(F.text == MENU_HELP)
async def show_help_message(message: Message, state: FSMContext, settings: SettingsService, config: Config) -> None:
    await state.clear()
    await message.answer(help_text(settings.values.free_searches), reply_markup=help_kb(config.bot.support_username))


@router.callback_query(F.data == "menu:help")
async def show_help_callback(callback: CallbackQuery, settings: SettingsService, config: Config) -> None:
    await callback.message.edit_text(help_text(settings.values.free_searches), reply_markup=help_kb(config.bot.support_username))
    await callback.answer()
