import asyncio
import base64
import binascii
import hashlib
import json
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

import httpx

if TYPE_CHECKING:
    from nonebot.adapters.onebot.v11.bot import Bot


FORMAT_VERSION = 2


class Msg:
    """One recursive data structure for messages, segments, and replies."""

    def __init__(
        self,
        type: str,
        data: Optional[dict[str, Any]] = None,
        children: Optional[list["Msg"]] = None,
    ) -> None:
        self.type = type
        self.data = data or {}
        self.children = children or []

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "data": self.data,
            "children": [child.to_dict() for child in self.children],
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "Msg":
        if not isinstance(value.get("type"), str):
            raise ValueError("Message node has no valid type")
        data = value.get("data", {})
        children = value.get("children", [])
        if not isinstance(data, dict) or not isinstance(children, list):
            raise ValueError("Message node has invalid data")
        return cls(
            value["type"],
            data,
            [cls.from_dict(child) for child in children],
        )

    def serialize(self) -> str:
        return json.dumps(
            {"version": FORMAT_VERSION, "message": self.to_dict()},
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )

    @classmethod
    def deserialize(cls, value: str) -> "Msg":
        document = json.loads(value)
        if (
            not isinstance(document, dict)
            or document.get("version") != FORMAT_VERSION
            or not isinstance(document.get("message"), dict)
        ):
            raise ValueError("Unsupported message document")
        return cls.from_dict(document["message"])

    @classmethod
    def from_database(cls, message_type: str, value: str) -> "Msg":
        try:
            message = cls.deserialize(value)
            if message.type == "message":
                return (
                    message.children[0]
                    if len(message.children) == 1
                    else cls("group", children=message.children)
                )
            return message
        except (json.JSONDecodeError, TypeError, ValueError):
            nodes = _legacy_nodes(message_type, value)
            return nodes[0] if len(nodes) == 1 else cls("group", children=nodes)

    @classmethod
    async def from_onebot(
        cls,
        raw_message: dict[str, Any],
        bot: "Bot",
        image_dir: Path,
        database_dir: Path,
    ) -> "Msg":
        async with httpx.AsyncClient() as client:
            children = await cls._from_onebot_segments(
                raw_message["message"], bot, client, image_dir, database_dir
            )
        return children[0] if len(children) == 1 else cls("group", children=children)

    @classmethod
    async def _from_onebot_segments(
        cls,
        raw_segments: Any,
        bot: "Bot",
        client: httpx.AsyncClient,
        image_dir: Path,
        database_dir: Path,
    ) -> list["Msg"]:
        result = []
        for raw_segment in raw_segments:
            if isinstance(raw_segment, dict):
                segment_type = raw_segment["type"]
                data = dict(raw_segment.get("data", {}))
            else:
                segment_type = raw_segment.type
                data = dict(raw_segment.data)

            children = []
            if segment_type == "image":
                content: Optional[bytes] = None
                source = data.get("file")
                if isinstance(source, str) and source.startswith("base64://"):
                    content = base64.b64decode(source[9:])
                elif isinstance(data.get("url"), str):
                    response = await client.get(data["url"])
                    response.raise_for_status()
                    content = response.content
                if content is not None:
                    relative_path = await asyncio.to_thread(
                        store_image, content, image_dir, database_dir
                    )
                    data = {"path": relative_path}
            elif segment_type == "reply":
                try:
                    replied = await bot.get_msg(message_id=int(data["id"]))
                    children = await cls._from_onebot_segments(
                        replied["message"], bot, client, image_dir, database_dir
                    )
                except Exception:
                    children = []
                data = {}

            result.append(cls(segment_type, data, children))
        return result

    def migrate_images(self, image_dir: Path, database_dir: Path) -> int:
        migrated = 0
        if self.type == "image":
            source = self.data.get("file")
            if isinstance(source, str) and source.startswith("base64://"):
                try:
                    content = base64.b64decode(source[9:], validate=True)
                except (ValueError, binascii.Error):
                    content = None
                if content is not None:
                    self.data = {
                        "path": store_image(content, image_dir, database_dir)
                    }
                    migrated += 1
        for child in self.children:
            migrated += child.migrate_images(image_dir, database_dir)
        return migrated

    def text_content(self) -> str:
        text = ""
        if self.type == "text":
            value = self.data.get("text")
            if isinstance(value, str):
                text = value
        return text + "".join(child.text_content() for child in self.children)


def _legacy_parse(value: str) -> list[str]:
    while value.startswith("[") and value.endswith("]"):
        value = value[1:-1]
    value = f"[{value.strip().strip(',').strip()}]"
    result: list[str] = []
    stack: list[str] = []
    current = ""
    for char in value:
        if char == "[":
            if stack:
                current += char
            stack.append(char)
        elif char == "," and len(stack) == 1:
            result.append(current.strip().strip(",").strip())
            current = ""
        elif char == "]":
            if len(stack) == 1:
                result.append(current.strip().strip(",").strip())
                current = ""
            else:
                current += char
            if stack:
                stack.pop()
        else:
            current += char
    return [item for item in result if item]


def _legacy_nodes(message_type: str, value: str) -> list[Msg]:
    if message_type == "group":
        nodes = []
        for raw_node in _legacy_parse(value):
            fields = _legacy_parse(raw_node)
            if fields:
                nodes.extend(
                    _legacy_nodes(
                        fields[0], ",".join(fields[1:]) if len(fields) > 1 else ""
                    )
                )
        return nodes
    if message_type == "reply":
        fields = _legacy_parse(value)
        children = (
            _legacy_nodes(
                fields[0], ",".join(fields[1:]) if len(fields) > 1 else ""
            )
            if fields
            else []
        )
        return [Msg("reply", children=children)]
    data_key = {
        "text": "text",
        "at": "qq",
        "face": "id",
        "image": "file",
    }.get(message_type, "raw")
    return [Msg(message_type, {data_key: value})]


def _image_suffix(content: bytes) -> str:
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if content.startswith((b"GIF87a", b"GIF89a")):
        return ".gif"
    if content.startswith(b"RIFF") and content[8:12] == b"WEBP":
        return ".webp"
    return ".jpg"


def store_image(content: bytes, image_dir: Path, database_dir: Path) -> str:
    digest = hashlib.sha256(content).hexdigest()
    path = image_dir / f"{digest}{_image_suffix(content)}"
    image_dir.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_bytes(content)
    return path.relative_to(database_dir).as_posix()


def resolve_image_path(data: dict[str, Any], database_dir: Path) -> Optional[str]:
    relative_path = data.get("path")
    if isinstance(relative_path, str):
        return str(database_dir / relative_path)
    source = data.get("file")
    return source if isinstance(source, str) else None
