"""Modbus client wrapper for EGI Adapters with safe shared connection handling.

from __future__ import annotations

import inspect
import logging
import threading
from typing import Any, Dict

from pymodbus.client import ModbusSerialClient, ModbusTcpClient

_LOGGER = logging.getLogger(__name__)

# Global pools for shared Modbus clients and their locks (indexed by connection key)
_client_pool: Dict[str, Any] = {}
_lock_pool: Dict[str, threading.Lock] = {}


def get_shared_client(connection_type: str, slave_id: int = 1, **kwargs) -> "EgiModbusClient":
    """Create or reuse a shared Modbus client based on unique connection key.

    Args:
        connection_type: "serial" or "tcp"
        slave_id: Modbus unit/slave/device address
        **kwargs: serial or tcp parameters:
          - serial: port, baudrate, parity, stopbits, bytesize, timeout
          - tcp: host, port, timeout
    """
    key = _get_client_key(connection_type, **kwargs)

    if key not in _client_pool:
        if connection_type == "serial":
            port = kwargs.get("port")
            _LOGGER.info("Creating new ModbusSerialClient for port: %s", port)
            client = ModbusSerialClient(
                port=port,
                baudrate=kwargs.get("baudrate", 9600),
                parity=kwargs.get("parity", "E"),
                stopbits=kwargs.get("stopbits", 1),
                bytesize=kwargs.get("bytesize", 8),
                timeout=kwargs.get("timeout", 3),
            )
        else:
            host = kwargs.get("host")
            _LOGGER.info("Creating new ModbusTcpClient for host: %s", host)
            client = ModbusTcpClient(
                host=host,
                port=kwargs.get("port", 502),
                timeout=kwargs.get("timeout", 3),
            )

        connected = False
        try:
            connected = client.connect()
        except Exception as exc:  # pragma: no cover
            _LOGGER.warning("Modbus client connect() raised for %s: %s", key, exc)

        if connected:
            _LOGGER.info("Modbus client connected successfully: %s", key)
        else:
            _LOGGER.warning("Modbus client failed to connect (will retry later): %s", key)

        _client_pool[key] = client
        _lock_pool[key] = threading.Lock()
    else:
        _LOGGER.debug("Reusing existing Modbus client for key: %s", key)

    return EgiModbusClient(_client_pool[key], slave_id=slave_id, lock=_lock_pool[key])


def _get_client_key(connection_type: str, **kwargs) -> str:
    """Generate unique key for each client based on transport + critical params."""
    if connection_type == "serial":
        port = (kwargs.get("port") or "").strip()
        baud = kwargs.get("baudrate", 9600)
        parity = kwargs.get("parity", "E")
        stop = kwargs.get("stopbits", 1)
        byte = kwargs.get("bytesize", 8)
        # Include serial parameters so a change forces a new client
        return f"serial::{port}|{baud}|{parity}|{stop}|{byte}"
    # TCP key
    host = kwargs.get("host", "").strip()
    port = kwargs.get("port", 502)
    return f"tcp::{host}:{port}"


class EgiModbusClient:
    """Wraps a pymodbus client and applies unit/slave/device_id + shared lock.

    NOTE:
    - `unit_id` attribute is exposed for UI titles, etc.
    - `connect()` is safe to call repeatedly; it will attempt to (re)connect.
    """

    def __init__(self, modbus_client: Any, slave_id: int = 1, lock: threading.Lock | None = None) -> None:
        self._client = modbus_client
        self._slave_id = int(slave_id)
        self._lock = lock or threading.Lock()
        # Convenience attribute used in __init__.py to render the entry title
        self.unit_id = self._slave_id

    # ---- lifecycle ---------------------------------------------------------

    def connect(self) -> bool:
        """Ensure the underlying client is connected."""
        if self._client is None:
            _LOGGER.error("Modbus client is not initialized")
            return False
        try:
            ok = self._client.connect()
            _LOGGER.debug("Underlying Modbus client connect() → %s", ok)
            return bool(ok)
        except Exception as exc:  # pragma: no cover
            _LOGGER.error("Modbus connect() exception: %s", exc)
            return False

    def close(self) -> None:
        """Keep shared clients open; connection pooling handles lifecycle."""
        _LOGGER.debug("close() skipped — shared client remains open.")

    # ---- helpers -----------------------------------------------------------

    def _addr_kwargs(self, method) -> Dict[str, int]:
        """Return the correct address kw for the installed PyModbus method."""
        try:
            params = inspect.signature(method).parameters
        except Exception:  # pragma: no cover
            params = {}

        if "slave" in params:
            return {"slave": self._slave_id}
        if "unit" in params:
            return {"unit": self._slave_id}
        if "device_id" in params:
            return {"device_id": self._slave_id}

        _LOGGER.debug("No addressing kwarg in %s signature; calling without device address.", method)
        return {}

    # ---- Modbus ops --------------------------------------------------------

    def read_holding_registers(self, address: int, count: int = 1):
        """Read holding registers; returns list[int] or None on error."""
        if self._client is None:
            _LOGGER.error("Modbus client is not initialized")
            return None

        with self._lock:
            try:
                method = self._client.read_holding_registers
                result = method(address=address, count=count, **self._addr_kwargs(method))
                _LOGGER.debug("Read holding registers addr=%s count=%s → %s", address, count, result)
            except Exception as exc:
                _LOGGER.error("Modbus read_holding_registers exception: %s", exc)
                return None

            if hasattr(result, "isError") and result.isError():
                _LOGGER.warning("Modbus read error at addr=%s count=%s: %s", address, count, result)
                return None

            return getattr(result, "registers", None)

    def write_register(self, address: int, value: int) -> bool:
        """Write a single register."""
        if self._client is None:
            _LOGGER.error("Modbus client is not initialized")
            return False

        with self._lock:
            try:
                method = self._client.write_register
                result = method(address=address, value=value, **self._addr_kwargs(method))
                _LOGGER.debug("Wrote register addr=%s value=%s → %s", address, value, result)
            except Exception as exc:
                _LOGGER.error("Modbus write_register exception: %s", exc)
                return False

            if hasattr(result, "isError") and result.isError():
                _LOGGER.warning("Modbus write error at addr=%s: %s", address, result)
                return False

            return True

    def write_registers(self, address: int, values: list[int]) -> bool:
        """Write multiple consecutive registers."""
        if self._client is None:
            _LOGGER.error("Modbus client is not initialized")
            return False

        with self._lock:
            try:
                method = self._client.write_registers
                result = method(address=address, values=values, **self._addr_kwargs(method))
                _LOGGER.debug("Wrote registers addr=%s values=%s → %s", address, values, result)
            except Exception as exc:
                _LOGGER.error("Modbus write_registers exception: %s", exc)
                return False

            if hasattr(result, "isError") and result.isError():
                _LOGGER.warning("Modbus write multiple error at addr=%s: %s", address, result)
                return False

            return True
