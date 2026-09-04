from aiogram.fsm.state import State, StatesGroup


class CreateSearch(StatesGroup):
    query = State()
    location = State()
    location_manual = State()
    price = State()
    wishes = State()
    confirm = State()


class EditTask(StatesGroup):
    min_rating = State()


class TopUpBalance(StatesGroup):
    amount = State()


class AdminBroadcast(StatesGroup):
    waiting_message = State()
    buttons_menu = State()
    button_title = State()
    button_url = State()
    confirm = State()


class AdminUsers(StatesGroup):
    search = State()
    change_balance = State()


class AdminTariff(StatesGroup):
    name = State()
    description = State()
    days = State()
    price_rub = State()
    price_stars = State()
    max_tasks = State()
    check_interval = State()
    edit_value = State()


class AdminSettings(StatesGroup):
    edit_value = State()
