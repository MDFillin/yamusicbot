from aiogram.fsm.state import State, StatesGroup


class NewPlaylist(StatesGroup):
    # В данных состояния: for_upload=True, если после создания надо загрузить туда файлы из очереди.
    name = State()


class EditTrack(StatesGroup):
    # В данных состояния: pid — какой файл из очереди правим, field — какое поле (для text).
    text = State()
    cover = State()


class AdminStates(StatesGroup):
    broadcast = State()  # ждём текст рассылки; в данных — text после предпросмотра


class MergeStates(StatesGroup):
    # В данных состояния: target (kind), target_title, sources — список Source.json(), card — id карточки.
    name = State()  # ждём название нового плейлиста
    sources = State()  # ждём ссылки на плейлисты и альбомы (и нажатия кнопок)
