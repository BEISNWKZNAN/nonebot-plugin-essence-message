from pydantic import BaseModel

import nonebot_plugin_localstore as store


class config(BaseModel):
    essence_random_limit: int = 5
    essence_enable_groups: list = ["all"]

    def db():
        PATH_DATA = store.get_data_file("essence_message", "essence_message.db")
        return PATH_DATA
