from aiogram.fsm.state import State, StatesGroup


class NewPlaylist(StatesGroup):
    # В данных состояния: for_upload=True, если после создания надо загрузить туда файлы из очереди.
    name = State()
