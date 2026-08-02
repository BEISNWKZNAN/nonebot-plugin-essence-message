from pathlib import Path
from typing import Optional, Union

from nonebot import require
from pydantic import BaseModel, Field

require("nonebot_plugin_localstore")
from nonebot_plugin_localstore import get_data_file, get_data_dir, get_cache_dir


class Config(BaseModel):
    essence_random_limit: int = 5
    essence_random_cooldown: int = 5
    essence_enable_groups: list[
        Union[int, str, list[Union[int, str]]]
    ] = Field(
        default_factory=lambda: ["all"]
    )
    good_essence_enable_groups: list[Union[int, str]] = Field(default_factory=list)
    whale_essnece_enable_groups: list[Union[int, str]] = Field(default_factory=list)
    good_bound: int = 3

    def essence_pool(self, group_id: int) -> Optional[tuple[int, ...]]:
        configured = self.essence_enable_groups
        if not configured:
            return None

        standalone_all = False
        for configured_pool in configured:
            if not isinstance(configured_pool, list):
                if str(configured_pool) == "all":
                    standalone_all = True
                elif str(configured_pool) == str(group_id):
                    return (group_id,)
                continue
            if "all" in configured_pool:
                return ()
            pool = tuple(
                int(item)
                for item in configured_pool
                if str(item).lstrip("-").isdigit()
            )
            if group_id in pool:
                return pool
        return (group_id,) if standalone_all else None

    def pool_feature_enabled(
        self, group_id: int, enabled_groups: list[Union[int, str]]
    ) -> bool:
        if "all" in enabled_groups:
            return True
        enabled = {str(item) for item in enabled_groups}
        pool = self.essence_pool(group_id)
        if pool is None:
            return False
        if not pool:
            return bool(enabled_groups)
        return any(str(member) in enabled for member in pool)

    def db(self) -> Path:
        return get_data_file("essence_message", "essence_message.db")

    def img(self) -> Path:
        return get_data_dir("essence_message") / "img"

    def cache(self) -> Path:
        return get_cache_dir("essence_message")
