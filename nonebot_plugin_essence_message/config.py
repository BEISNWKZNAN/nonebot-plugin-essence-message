from pydantic import BaseModel
from nonebot import require

require("nonebot_plugin_localstore")
from nonebot_plugin_localstore import get_data_file,get_data_dir,get_cache_dir


class config(BaseModel):
    essence_random_limit: int = 5
    essence_enable_groups: list = ["all"]
    good_essence_rule: bool = False
    good_bound: int = 3

    def db():
        PATH_DATA = get_data_file("essence_message", "essence_message.db")
        return PATH_DATA
    
    def img():
        PATH_DATA = get_data_dir("essence_message")
        return PATH_DATA / "img"

    def cache():
        PATH_DATA = get_cache_dir("essence_message")
        return PATH_DATA 
