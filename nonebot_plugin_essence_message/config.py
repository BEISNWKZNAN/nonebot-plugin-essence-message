from pathlib import Path
from pydantic import BaseModel

PATH = Path().absolute()
PATH_DATA = PATH / "data" / "essence_message"


class config(BaseModel):
    essence_random_limit:int = 5

    def db():
        (PATH_DATA).mkdir(parents=True, exist_ok=True)
        return PATH_DATA / "essence_message.db"
