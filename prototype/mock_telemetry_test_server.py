"""Loopback-only length-prefixed JSON test server for end-to-end tests."""

from __future__ import annotations

import json
import socket
import struct
import threading
from typing import Any

from mock_guest_session import TestHostResponder
from mock_telemetry_agent import Envelope


def _read_exact(connection: socket.socket, size: int) -> bytes | None:
    result = bytearray()
    while len(result) < size:
        chunk = connection.recv(size - len(result))
        if not chunk:
            return None
        result.extend(chunk)
    return bytes(result)


class LoopbackTestServer:
    def __init__(self, port: int = 0) -> None:
        self.messages: list[dict[str, Any]] = []
        self._listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._listener.bind(("127.0.0.1", port))
        self._listener.listen(1)
        self._listener.settimeout(0.2)
        self.port = self._listener.getsockname()[1]
        self._stop = threading.Event()
        self._responder = TestHostResponder()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self) -> "LoopbackTestServer":
        self._thread.start()
        return self

    def _run(self) -> None:
        connection: socket.socket | None = None
        try:
            while not self._stop.is_set() and connection is None:
                try:
                    connection, _ = self._listener.accept()
                except TimeoutError:
                    continue
            if connection is None:
                return
            connection.settimeout(0.2)
            with connection:
                while not self._stop.is_set():
                    try:
                        header = _read_exact(connection, 4)
                    except TimeoutError:
                        continue
                    except OSError:
                        return
                    if header is None:
                        return
                    size = struct.unpack("!I", header)[0]
                    if size > 4 * 1024 * 1024:
                        return
                    try:
                        body = _read_exact(connection, size)
                    except OSError:
                        return
                    if body is None:
                        return
                    value = json.loads(body)
                    if isinstance(value, dict):
                        self.messages.append(value)
                        request = Envelope(
                            int(value["int_msgid"]),
                            int(value["source_module"]),
                            int(value["destination_module"]),
                            str(value["emitted_at"]),
                            dict(value["payload"]),
                        )
                        responses = self._responder.handle(request)
                        if responses:
                            response_body = json.dumps(
                                [item.as_dict() for item in responses],
                                ensure_ascii=True,
                                separators=(",", ":"),
                            ).encode()
                            connection.sendall(struct.pack("!I", len(response_body)) + response_body)
        finally:
            self._listener.close()

    def stop(self) -> None:
        self._stop.set()
        try:
            with socket.create_connection(("127.0.0.1", self.port), timeout=0.1):
                pass
        except OSError:
            pass
        self._thread.join(timeout=2)

    def __enter__(self) -> "LoopbackTestServer":
        return self.start()

    def __exit__(self, *_: object) -> None:
        self.stop()
