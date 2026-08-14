"""ElectrumX JSON-RPC client (local node only, read-only)."""

from __future__ import annotations

import hashlib
import json
import logging
import socket
import ssl
from dataclasses import dataclass

from openclaw.skills.bitcoin_balance_monitor.models import ElectrumXSettings

logger = logging.getLogger(__name__)

ALLOWED_METHODS = frozenset(
    {
        "server.version",
        "blockchain.scripthash.get_balance",
    },
)


class ElectrumXError(Exception):
    """ElectrumX protocol or connectivity error."""


@dataclass(frozen=True, slots=True)
class ScriptBalance:
    """Balance for a single scriptPubKey."""

    confirmed_sats: int
    unconfirmed_sats: int

    @property
    def total_sats(self) -> int:
        return self.confirmed_sats + self.unconfirmed_sats


def scripthash_from_script(script_pubkey: bytes) -> str:
    """Compute Electrum scripthash: reverse(SHA256(SHA256(script)))."""
    digest = hashlib.sha256(hashlib.sha256(script_pubkey).digest()).digest()
    return digest[::-1].hex()


class ElectrumXClient:
    """Minimal synchronous Electrum protocol client over TCP."""

    def __init__(self, settings: ElectrumXSettings) -> None:
        self._settings = settings
        self._request_id = 0
        self._sock: socket.socket | None = None

    def __enter__(self) -> ElectrumXClient:
        self.connect()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:  # noqa: ANN001
        self.close()

    @property
    def endpoint(self) -> str:
        return f"{self._settings.host}:{self._settings.port}"

    def connect(self) -> None:
        """Open TCP connection to the allowlisted ElectrumX node."""
        host = self._settings.host.strip()
        normalized = host.lower()
        if normalized not in self._settings.allowed_hosts:
            msg = (
                f"ElectrumX host {host!r} is not in allowed_hosts "
                f"{sorted(self._settings.allowed_hosts)}"
            )
            raise ElectrumXError(msg)

        timeout = self._settings.timeout_seconds
        try:
            if self._settings.use_ssl:
                context = ssl.create_default_context()
                raw_sock = socket.create_connection((host, self._settings.port), timeout=timeout)
                self._sock = context.wrap_socket(raw_sock, server_hostname=host)
            else:
                self._sock = socket.create_connection((host, self._settings.port), timeout=timeout)
        except OSError as exc:
            raise ElectrumXError(
                f"ElectrumX unreachable at {self.endpoint}: {exc}",
            ) from exc

        self._request_id = 0
        logger.info("Connected to ElectrumX at %s", self.endpoint)

    def close(self) -> None:
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None

    def _call(self, method: str, params: list) -> object:
        if method not in ALLOWED_METHODS:
            msg = f"RPC method not allowed: {method}"
            raise ElectrumXError(msg)

        if self._sock is None:
            raise ElectrumXError("Not connected to ElectrumX")

        self._request_id += 1
        payload = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params,
            "id": self._request_id,
        }
        message = json.dumps(payload) + "\n"
        try:
            self._sock.sendall(message.encode("utf-8"))
            response_data = self._read_line()
        except OSError as exc:
            raise ElectrumXError(
                f"ElectrumX I/O error at {self.endpoint}: {exc}",
            ) from exc

        try:
            response = json.loads(response_data)
        except json.JSONDecodeError as exc:
            raise ElectrumXError(f"Invalid JSON from ElectrumX: {response_data[:200]!r}") from exc

        if not isinstance(response, dict):
            raise ElectrumXError(f"Unexpected ElectrumX response type: {type(response)!r}")

        if response.get("error"):
            error = response["error"]
            raise ElectrumXError(f"ElectrumX RPC error for {method}: {error}")

        return response.get("result")

    def _read_line(self) -> str:
        assert self._sock is not None
        chunks: list[bytes] = []
        while True:
            chunk = self._sock.recv(4096)
            if not chunk:
                raise ElectrumXError("ElectrumX connection closed unexpectedly")
            chunks.append(chunk)
            if b"\n" in chunk:
                break
        data = b"".join(chunks)
        line, _, _rest = data.partition(b"\n")
        return line.decode("utf-8")

    def ping(self) -> None:
        """Verify connectivity via server.version."""
        result = self._call("server.version", ["bitcoin_balance_monitor", "1.4"])
        if result is None:
            raise ElectrumXError("ElectrumX server.version returned no result")
        logger.debug("ElectrumX server.version: %s", result)

    def get_balance(self, script_pubkey: bytes) -> ScriptBalance:
        """Return confirmed and unconfirmed balance for a scriptPubKey."""
        sh = scripthash_from_script(script_pubkey)
        result = self._call("blockchain.scripthash.get_balance", [sh])
        if not isinstance(result, dict):
            raise ElectrumXError(f"Unexpected get_balance result: {result!r}")

        confirmed = int(result.get("confirmed", 0))
        unconfirmed = int(result.get("unconfirmed", 0))
        return ScriptBalance(confirmed_sats=confirmed, unconfirmed_sats=unconfirmed)
