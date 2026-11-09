import asyncio
import base64
import hashlib
import json
import struct

ACCEPT_GUID = b"258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
TEXT, CLOSE = 1, 8


def frame(opcode: int, payload: bytes) -> bytes:
    return bytes([0x80 | opcode, len(payload)]) + payload


async def read_frame(reader: asyncio.StreamReader) -> tuple[int, bytes]:
    first, second = await reader.readexactly(2)
    length = second & 0x7F
    if length == 126:
        length = struct.unpack("!H", await reader.readexactly(2))[0]
    elif length == 127:
        length = struct.unpack("!Q", await reader.readexactly(8))[0]
    mask = await reader.readexactly(4) if second & 0x80 else bytes(4)
    data = await reader.readexactly(length)
    return first & 0x0F, bytes(byte ^ mask[index % 4] for index, byte in enumerate(data))


class ClosingServer:
    def __init__(self, close_code: int = 1001) -> None:
        self.close_code = close_code
        self.authenticated = asyncio.Event()
        self.close = asyncio.Event()
        self.closing = asyncio.Event()
        self.drop = asyncio.Event()
        self.port = 0
        self._server: asyncio.Server | None = None

    async def __aenter__(self) -> "ClosingServer":
        self._server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        self.port = self._server.sockets[0].getsockname()[1]
        return self

    async def __aexit__(self, *exc: object) -> None:
        self.drop.set()
        self._server.close()

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        request = await reader.readuntil(b"\r\n\r\n")
        key = next(
            line.split(b":", 1)[1].strip()
            for line in request.split(b"\r\n")
            if line.lower().startswith(b"sec-websocket-key")
        )
        accept = base64.b64encode(hashlib.sha1(key + ACCEPT_GUID).digest())
        writer.write(
            b"HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
            b"Sec-WebSocket-Accept: " + accept + b"\r\n\r\n"
        )
        writer.write(frame(TEXT, json.dumps({"type": "auth_required"}).encode()))
        await read_frame(reader)
        writer.write(frame(TEXT, json.dumps({"type": "auth_ok"}).encode()))
        self.authenticated.set()
        await self.close.wait()
        writer.write(frame(CLOSE, struct.pack("!H", self.close_code)))
        await writer.drain()
        while (await read_frame(reader))[0] != CLOSE:
            pass
        self.closing.set()
        await self.drop.wait()
        writer.close()
