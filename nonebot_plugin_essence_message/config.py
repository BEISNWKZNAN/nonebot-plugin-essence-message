from pathlib import Path
from typing import Union

from nonebot import require
from pydantic import BaseModel, Field

require("nonebot_plugin_localstore")
from nonebot_plugin_localstore import get_data_file, get_data_dir, get_cache_dir


class Config(BaseModel):
    essence_random_limit: int = 5
    essence_random_cooldown: int = 5
    essence_enable_groups: list[Union[int, str]] = Field(
        default_factory=lambda: ["all"]
    )
    good_essence_enable_groups: list[Union[int, str]] = Field(default_factory=list)
    whale_essnece_enable_groups: list[Union[int, str]] = Field(default_factory=list)
    good_bound: int = 3

    def db(self) -> Path:
        return get_data_file("essence_message", "essence_message.db")

    def img(self) -> Path:
        return get_data_dir("essence_message") / "img"

    def cache(self) -> Path:
        return get_cache_dir("essence_message")
