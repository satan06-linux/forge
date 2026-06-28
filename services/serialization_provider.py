# ForgePrompt Phase 7 — SerializationProvider
from abc import ABC, abstractmethod
from typing import Any
import json
import logging

logger = logging.getLogger(__name__)

class SerializationProvider(ABC):
    @abstractmethod
    def serialize(self, obj: Any) -> bytes:
        pass

    @abstractmethod
    def deserialize(self, data: bytes) -> Any:
        pass

    @abstractmethod
    def content_type(self) -> str:
        pass


class JSONSerializationProvider(SerializationProvider):
    def serialize(self, obj: Any) -> bytes:
        return json.dumps(obj, default=str).encode('utf-8')

    def deserialize(self, data: bytes) -> Any:
        return json.loads(data.decode('utf-8'))

    def content_type(self) -> str:
        return 'application/json'


class MessagePackSerializationProvider(SerializationProvider):
    def __init__(self):
        try:
            import msgpack
            self._msgpack = msgpack
            self._has_msgpack = True
        except ImportError:
            self._has_msgpack = False
            logger.warning("msgpack not installed, falling back to JSON for MessagePackSerializationProvider")
            self._json_fallback = JSONSerializationProvider()

    def serialize(self, obj: Any) -> bytes:
        if self._has_msgpack:
            return self._msgpack.packb(obj, use_bin_type=True)
        return self._json_fallback.serialize(obj)

    def deserialize(self, data: bytes) -> Any:
        if self._has_msgpack:
            return self._msgpack.unpackb(data, raw=False)
        return self._json_fallback.deserialize(data)

    def content_type(self) -> str:
        return 'application/msgpack'


class ProtobufSerializationProvider(SerializationProvider):
    def serialize(self, obj: Any) -> bytes:
        raise NotImplementedError('Protobuf serialization requires .proto schema compilation. Use MessagePack or JSON for Phase 7.')

    def deserialize(self, data: bytes) -> Any:
        raise NotImplementedError('Protobuf serialization requires .proto schema compilation. Use MessagePack or JSON for Phase 7.')

    def content_type(self) -> str:
        return 'application/protobuf'


def get_serializer(format_name: str = 'json') -> SerializationProvider:
    format_name = format_name.lower()
    if format_name == 'msgpack':
        return MessagePackSerializationProvider()
    elif format_name == 'protobuf':
        return ProtobufSerializationProvider()
    return JSONSerializationProvider()
