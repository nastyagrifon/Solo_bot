from aiogram.fsm.state import State, StatesGroup


class PanelUserState(StatesGroup):
    waiting_value = State()
