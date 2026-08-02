import asyncio
import base64
import binascii
import hashlib
import json
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse

import httpx
from nonebot.adapters.onebot.v11.bot import Bot
from nonebot.log import logger


FORMAT_VERSION = 3
MAX_NESTING_DEPTH = 256


def _decode_base64_source(source: str) -> tuple[Optional[bytes], int]:
    payload = "".join(source.removeprefix("base64://").split())
    padding = (-len(payload)) % 4
    try:
        return base64.b64decode(payload + "=" * padding, validate=True), padding
    except (ValueError, binascii.Error):
        return None, padding


def _is_complete_media(content: bytes, media_type: str) -> bool:
    if not content:
        return False
    if media_type != "image":
        return True
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        return content.endswith(b"IEND\xaeB`\x82")
    if content.startswith(b"\xff\xd8\xff"):
        return content.endswith(b"\xff\xd9")
    if content.startswith((b"GIF87a", b"GIF89a")):
        return content.endswith(b";")
    if content.startswith(b"RIFF") and content[8:12] == b"WEBP":
        return len(content) >= 12 and int.from_bytes(content[4:8], "little") + 8 <= len(
            content
        )
    return True


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
        node: dict[str, Any] = {"type": self.type}
        if self.data:
            node["data"] = self.data
        if self.children:
            node["children"] = [child.to_dict() for child in self.children]
        return node

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
        segments = self.children if self.type == "group" else [self]
        return json.dumps(
            {
                "version": FORMAT_VERSION,
                "segments": [segment.to_dict() for segment in segments],
            },
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )

    @classmethod
    def deserialize(cls, value: str | bytes) -> "Msg":
        value = _decode_document(value)
        document = json.loads(value)
        if not isinstance(document, dict):
            raise ValueError("Unsupported message document")

        if document.get("version") == FORMAT_VERSION:
            raw_segments = document.get("segments")
            if not isinstance(raw_segments, list):
                raise ValueError("Message document has no segments")
            segments = [cls.from_dict(segment) for segment in raw_segments]
            return segments[0] if len(segments) == 1 else cls("group", children=segments)

        if document.get("version") == 2 and isinstance(
            document.get("message"), dict
        ):
            message = cls.from_dict(document["message"])
            if message.type == "message":
                return (
                    message.children[0]
                    if len(message.children) == 1
                    else cls("group", children=message.children)
                )
            return message

        raise ValueError("Unsupported message document")

    @classmethod
    def from_database(cls, message_type: str, value: str | bytes) -> "Msg":
        try:
            return cls.deserialize(value)
        except (json.JSONDecodeError, TypeError, UnicodeError, ValueError):
            nodes = _legacy_nodes(message_type, _decode_document(value))
            return nodes[0] if len(nodes) == 1 else cls("group", children=nodes)

    @classmethod
    async def from_onebot(
        cls,
        raw_message: dict[str, Any],
        bot: Bot,
        image_dir: Path,
        database_dir: Path,
    ) -> "Msg":
        async with httpx.AsyncClient(
            follow_redirects=True,
            timeout=httpx.Timeout(30.0),
        ) as client:
            children = await cls._from_onebot_segments(
                raw_message["message"],
                bot,
                client,
                image_dir,
                database_dir,
                visited_replies=set(),
                depth=0,
            )
        return children[0] if len(children) == 1 else cls("group", children=children)

    @classmethod
    async def _from_onebot_segments(
        cls,
        raw_segments: Any,
        bot: Bot,
        client: httpx.AsyncClient,
        image_dir: Path,
        database_dir: Path,
        visited_replies: set[str],
        depth: int,
    ) -> list["Msg"]:
        if depth > MAX_NESTING_DEPTH:
            return []
        if isinstance(raw_segments, str):
            return [cls("text", {"text": raw_segments})]
        if isinstance(raw_segments, dict):
            raw_segments = [raw_segments]

        result: list[Msg] = []
        for raw_segment in raw_segments:
            if isinstance(raw_segment, dict):
                segment_type = raw_segment.get("type")
                raw_data = raw_segment.get("data", {})
            else:
                segment_type = raw_segment.type
                raw_data = raw_segment.data
            if not isinstance(segment_type, str):
                continue
            data = dict(raw_data) if isinstance(raw_data, dict) else {"raw": raw_data}

            children: list[Msg] = []
            if segment_type in {"image", "record", "video"}:
                source_kind = (
                    "base64"
                    if isinstance(data.get("file"), str)
                    and data["file"].startswith("base64://")
                    else "url"
                    if isinstance(data.get("url"), str)
                    else "file-id"
                    if data.get("file") is not None
                    else "missing"
                )
                content, source = await _download_media(
                    data,
                    client,
                    segment_type,
                )
                if content is None:
                    logger.warning(
                        "媒体持久化失败, 跳过消息段: type={} source={}",
                        segment_type,
                        source_kind,
                    )
                    continue
                media_dir = (
                    image_dir
                    if segment_type == "image"
                    else database_dir / "media" / segment_type
                )
                relative_path = await asyncio.to_thread(
                    store_media,
                    content,
                    media_dir,
                    database_dir,
                    segment_type,
                    source,
                )
                data["local_path"] = relative_path
                # file IDs and URLs are temporary transport details. They must
                # never enter the database.
                data.pop("file", None)
                data.pop("url", None)
            elif segment_type == "reply":
                reply_id = data.get("id", data.get("seq"))
                reply_key = str(reply_id) if reply_id is not None else ""
                if reply_key and reply_key not in visited_replies:
                    visited_replies.add(reply_key)
                    try:
                        replied = await bot.get_msg(message_id=int(reply_key))
                        children = await cls._from_onebot_segments(
                            replied["message"],
                            bot,
                            client,
                            image_dir,
                            database_dir,
                            visited_replies,
                            depth + 1,
                        )
                        _copy_message_metadata(data, replied)
                    except Exception:
                        children = []
            elif segment_type in {"node", "forward"}:
                nested = data.pop("content", None)
                if nested is None:
                    nested = data.pop("message", None)
                if (
                    nested is None
                    and segment_type == "forward"
                    and data.get("id") is not None
                ):
                    try:
                        forwarded = await bot.get_forward_msg(id=str(data["id"]))
                        nested = forwarded.get(
                            "messages",
                            forwarded.get("message", forwarded.get("content")),
                        )
                    except Exception:
                        nested = None
                children = await cls._from_forward_content(
                    nested,
                    bot,
                    client,
                    image_dir,
                    database_dir,
                    visited_replies,
                    depth + 1,
                )

            result.append(cls(segment_type, data, children))
        return result

    @classmethod
    async def _from_forward_content(
        cls,
        content: Any,
        bot: Bot,
        client: httpx.AsyncClient,
        image_dir: Path,
        database_dir: Path,
        visited_replies: set[str],
        depth: int,
    ) -> list["Msg"]:
        if content is None or depth > MAX_NESTING_DEPTH:
            return []
        if isinstance(content, dict):
            if "type" not in content and isinstance(content.get("messages"), list):
                content = content["messages"]
            else:
                content = [content]
        if isinstance(content, str):
            return [cls("text", {"text": content})]

        result: list[Msg] = []
        for item in content:
            if (
                isinstance(item, dict)
                and "type" not in item
                and ("message" in item or "content" in item)
            ):
                metadata: dict[str, Any] = {}
                _copy_message_metadata(metadata, item)
                sender = item.get("sender")
                if isinstance(sender, dict):
                    metadata["sender"] = sender
                nested_content = item.get("message", item.get("content"))
                nested = await cls._from_onebot_segments(
                    nested_content,
                    bot,
                    client,
                    image_dir,
                    database_dir,
                    visited_replies,
                    depth,
                )
                result.append(cls("node", metadata, nested))
            else:
                result.extend(
                    await cls._from_onebot_segments(
                        [item],
                        bot,
                        client,
                        image_dir,
                        database_dir,
                        visited_replies,
                        depth,
                    )
                )
        return result

    def migrate_images(self, image_dir: Path, database_dir: Path) -> int:
        migrated = 0
        if self.type == "image":
            source = self.data.get("file")
            if isinstance(source, str) and source.startswith("base64://"):
                content, _ = _decode_base64_source(source)
                if content is not None and _is_complete_media(content, "image"):
                    self.data.pop("file", None)
                    self.data["local_path"] = store_media(
                        content, image_dir, database_dir, "image"
                    )
                    migrated += 1
        for child in self.children:
            migrated += child.migrate_images(image_dir, database_dir)
        return migrated

    def discard_stored_media_sources(self) -> int:
        """Remove volatile URLs and file IDs from stored media."""
        changed = 0
        if self.type in {"image", "record", "video"}:
            for key in ("file", "url"):
                if key in self.data:
                    self.data.pop(key)
                    changed += 1
        for child in self.children:
            changed += child.discard_stored_media_sources()
        return changed

    def prune_empty_children(self) -> None:
        for child in self.children:
            child.prune_empty_children()
        self.children = [child for child in self.children if child.has_content()]

    def normalize_root(self) -> "Msg":
        if self.type == "group" and len(self.children) == 1:
            return self.children[0]
        return self

    def has_content(self) -> bool:
        if self.type in {"image", "record", "video"}:
            return isinstance(self.data.get("local_path", self.data.get("path")), str)
        if self.type in {"group", "reply", "node", "forward"}:
            return any(child.has_content() for child in self.children)
        if self.type == "text":
            return isinstance(self.data.get("text"), str) and bool(self.data["text"])
        return bool(self.data) or bool(self.children)

    def text_content(self) -> str:
        text = ""
        if self.type == "text":
            value = self.data.get("text")
            if isinstance(value, str):
                text = value
        return text + "".join(child.text_content() for child in self.children)

    def contains_only_truncated_images(self) -> bool:
        """Return whether this message has no content except truncated images."""
        if self.type == "image":
            source = self.data.get("file")
            if not isinstance(source, str) or not source.startswith("base64://"):
                return False
            content, _ = _decode_base64_source(source)
            return content is None or not _is_complete_media(content, "image")
        if self.type in {"group", "reply", "node", "forward"} and self.children:
            return all(
                child.contains_only_truncated_images() for child in self.children
            )
        return False


def _decode_document(value: str | bytes) -> str:
    if isinstance(value, str):
        return value
    if not isinstance(value, bytes):
        raise TypeError("Message document must be text or bytes")

    for encoding in ("utf-8", "gb18030"):
        try:
            return value.decode(encoding)
        except UnicodeDecodeError:
            pass
    return value.decode("utf-8", errors="replace")


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
    if message_type not in {"text", "image", "at", "face", "reply", "group"}:
        raise ValueError("Unsupported legacy message type")
    if message_type == "group":
        nodes = []
        for raw_node in _legacy_parse(value):
            fields = _legacy_parse(raw_node)
            if fields:
                nested_type = fields[0]
                nested_value = ",".join(fields[1:]) if len(fields) > 1 else ""
                if nested_type == "group" and nested_value:
                    nested_value += ","
                nodes.extend(_legacy_nodes(nested_type, nested_value))
        return nodes
    if message_type == "reply":
        fields = _legacy_parse(value)
        if not fields:
            return [Msg("reply")]
        nested_type = fields[0]
        nested_value = ",".join(fields[1:]) if len(fields) > 1 else ""
        if nested_type == "group" and nested_value:
            nested_value += ","
        children = _legacy_nodes(nested_type, nested_value)
        return [Msg("reply", children=children)]
    data_key = {
        "text": "text",
        "at": "qq",
        "face": "id",
        "image": "file",
    }.get(message_type, "raw")
    return [Msg(message_type, {data_key: value})]


def _copy_message_metadata(target: dict[str, Any], message: dict[str, Any]) -> None:
    for key in (
        "message_id",
        "message_seq",
        "real_id",
        "time",
        "user_id",
        "group_id",
        "nickname",
        "source",
        "summary",
        "prompt",
    ):
        value = message.get(key)
        if value is not None:
            target[key] = value


async def _download_media(
    data: dict[str, Any],
    client: httpx.AsyncClient,
    media_type: str,
) -> tuple[Optional[bytes], Optional[str]]:
    source = data.get("file")
    if isinstance(source, str) and source.startswith("base64://"):
        content, _ = _decode_base64_source(source)
        if content is not None and _is_complete_media(content, media_type):
            return content, source
        return None, source

    url = data.get("url")
    if not isinstance(url, str) or not url.startswith(("http://", "https://")):
        return None, source if isinstance(source, str) else None
    try:
        response = await client.get(url)
        response.raise_for_status()
        content = response.content
        content_type = response.headers.get("content-type", "").lower()
        if not content:
            return None, url
        if content_type.startswith("text/") or "json" in content_type:
            return None, url
        if not _is_complete_media(content, media_type):
            return None, url
        return content, str(response.url)
    except httpx.HTTPError:
        return None, url


def _media_suffix(
    content: bytes, media_type: str, source: Optional[str] = None
) -> str:
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if content.startswith((b"GIF87a", b"GIF89a")):
        return ".gif"
    if content.startswith(b"RIFF") and content[8:12] == b"WEBP":
        return ".webp"
    if content.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if content.startswith(b"#!AMR"):
        return ".amr"
    if content.startswith(b"OggS"):
        return ".ogg"
    if content.startswith(b"RIFF") and content[8:12] == b"WAVE":
        return ".wav"
    if len(content) >= 12 and content[4:8] == b"ftyp":
        return ".mp4"
    if isinstance(source, str):
        suffix = Path(urlparse(source).path).suffix.lower()
        if suffix and len(suffix) <= 10 and suffix[1:].isalnum():
            return suffix
    return {"image": ".jpg", "record": ".audio", "video": ".video"}.get(
        media_type, ".bin"
    )


def store_media(
    content: bytes,
    media_dir: Path,
    database_dir: Path,
    media_type: str,
    source: Optional[str] = None,
) -> str:
    digest = hashlib.sha256(content).hexdigest()
    path = media_dir / f"{digest}{_media_suffix(content, media_type, source)}"
    media_dir.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_bytes(content)
    return path.relative_to(database_dir).as_posix()


def resolve_media_source(
    data: dict[str, Any], database_dir: Path
) -> Optional[str]:
    relative_path = data.get("local_path", data.get("path"))
    if isinstance(relative_path, str):
        path = Path(relative_path)
        path = path if path.is_absolute() else database_dir / path
        try:
            if path.is_file():
                content = path.read_bytes()
                encoded = base64.b64encode(content).decode("ascii")
                return "base64://" + encoded
        except OSError:
            pass

    source = data.get("file")
    if isinstance(source, str) and source.startswith("base64://"):
        content, _ = _decode_base64_source(source)
        if content is not None and _is_complete_media(content, "image"):
            encoded = base64.b64encode(content).decode("ascii")
            return "base64://" + encoded
        return None
    return None


# Kept for callers using the old public helpers.
def store_image(content: bytes, image_dir: Path, database_dir: Path) -> str:
    return store_media(content, image_dir, database_dir, "image")


def resolve_image_path(
    data: dict[str, Any], database_dir: Path
) -> Optional[str]:
    return resolve_media_source(data, database_dir)
