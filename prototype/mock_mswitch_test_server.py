"""Loopback-only Fake Mswitch server for binary framing tests."""

from __future__ import annotations

import socket
import threading

from mock_guest_session import TestHostResponder
from mock_telemetry_agent import Envelope
from mswitch_frame_transport import MswitchFrameEncoder
from mswitch_protocol import SerialFrameDecoder


class LoopbackMswitchTestServer:
    def __init__(self, test_uuid: str, port: int = 0) -> None:
        self.messages: list[Envelope] = []
        self._listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._listener.bind(("127.0.0.1", port))
        self._listener.listen(1)
        self._listener.settimeout(0.2)
        self.port = self._listener.getsockname()[1]
        self._stop = threading.Event()
        self._responder = TestHostResponder()
        self._encoder = MswitchFrameEncoder(test_uuid, test_mode=True)
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self) -> "LoopbackMswitchTestServer":
        self._thread.start()
        return self

    def _run(self) -> None:
        connection: socket.socket | None = None
        decoder = SerialFrameDecoder()
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
                        chunk = connection.recv(65536)
                    except TimeoutError:
                        continue
                    if not chunk:
                        return
                    for frame in decoder.feed(chunk):
                        request = self._encoder.decode(frame)
                        self.messages.append(request)
                        for response in self._responder.handle(request):
                            connection.sendall(self._encoder.encode(response))
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

    def __enter__(self) -> "LoopbackMswitchTestServer":
        return self.start()

    def __exit__(self, *_: object) -> None:
        self.stop()
