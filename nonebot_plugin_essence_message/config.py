from pydantic import BaseModel
from nonebot import require

require("nonebot_plugin_localstore")
from nonebot_plugin_localstore import get_data_file


class config(BaseModel):
    essence_random_limit: int = 5
    essence_enable_groups: list = ["all"]

    def db():
        PATH_DATA = get_data_file("essence_message", "essence_message.db")
        return PATH_DATA
