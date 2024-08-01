from pathlib import Path

PATH = Path().absolute()
PATH_DATA = PATH / "data" / "essence_message"

class config():
    def db():
        (PATH_DATA).mkdir(parents=True, exist_ok=True)
        return PATH_DATA / 'essence_message.db'