from __future__ import annotations

import asyncio
import ipaddress
import json
import socket
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any


class BaseTransport(ABC):
    async def open(self) -> None:
        pass

    async def close(self) -> None:
        pass

    @abstractmethod
    async def send(self, message: dict[str, Any]) -> None:
        raise NotImplementedError


class MemoryTransport(BaseTransport):
    def __init__(self) -> None:
        self.messages: list[dict[str, Any]] = []

    async def send(self, message: dict[str, Any]) -> None:
        self.messages.append(json.loads(json.dumps(message)))


class FileDumpTransport(BaseTransport):
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._fp = None
        self._lock = asyncio.Lock()

    async def open(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fp = self.path.open("a", encoding="utf-8", buffering=1)

    async def send(self, message: dict[str, Any]) -> None:
        if self._fp is None:
            raise RuntimeError("transport not opened")
        line = json.dumps(message, ensure_ascii=False, separators=(",", ":"))
        async with self._lock:
            self._fp.write(line + "\n")

    async def close(self) -> None:
        if self._fp:
            self._fp.close()
            self._fp = None


class NetworkPolicy:
    """Default-safe policy for generic lab sockets.

    Public destinations are blocked unless explicitly enabled. Loopback,
    private, and link-local addresses are accepted for isolated/LAN tests.
    """

    def __init__(self, allow_public: bool = False) -> None:
        self.allow_public = allow_public

    async def validate(self, host: str, port: int, socktype: int) -> None:
        if not 1 <= port <= 65535:
            raise ValueError(f"invalid port: {port}")
        loop = asyncio.get_running_loop()
        infos = await loop.run_in_executor(
            None,
            lambda: socket.getaddrinfo(host, port, type=socktype),
        )
        if self.allow_public:
            return
        for info in infos:
            ip = ipaddress.ip_address(info[4][0])
            if not (ip.is_loopback or ip.is_private or ip.is_link_local):
                raise ValueError(f"public address blocked by policy: {ip}")


class TcpTransport(BaseTransport):
    def __init__(self, host: str, port: int, timeout: float = 5.0, allow_public: bool = False) -> None:
        self.host = host
        self.port = port
        self.timeout = timeout
        self.policy = NetworkPolicy(allow_public=allow_public)
        self.reader = None
        self.writer = None

    async def open(self) -> None:
        await self.policy.validate(self.host, self.port, socket.SOCK_STREAM)
        self.reader, self.writer = await asyncio.wait_for(
            asyncio.open_connection(self.host, self.port),
            timeout=self.timeout,
        )

    async def send(self, message: dict[str, Any]) -> None:
        if self.writer is None:
            raise RuntimeError("transport not opened")
        payload = json.dumps(message, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.writer.write(payload + b"\n")
        await self.writer.drain()

    async def close(self) -> None:
        if self.writer is not None:
            self.writer.close()
            await self.writer.wait_closed()
            self.writer = None
            self.reader = None


class UdpTransport(BaseTransport):
    def __init__(self, host: str, port: int, allow_public: bool = False) -> None:
        self.host = host
        self.port = port
        self.policy = NetworkPolicy(allow_public=allow_public)
        self.sock: socket.socket | None = None

    async def open(self) -> None:
        await self.policy.validate(self.host, self.port, socket.SOCK_DGRAM)
        infos = socket.getaddrinfo(self.host, self.port, type=socket.SOCK_DGRAM)
        family, socktype, proto, _, sockaddr = infos[0]
        self._sockaddr = sockaddr
        self.sock = socket.socket(family, socktype, proto)
        self.sock.setblocking(False)

    async def send(self, message: dict[str, Any]) -> None:
        if self.sock is None:
            raise RuntimeError("transport not opened")
        payload = json.dumps(message, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        loop = asyncio.get_running_loop()
        await loop.sock_sendto(self.sock, payload, self._sockaddr)

    async def close(self) -> None:
        if self.sock:
            self.sock.close()
            self.sock = None
