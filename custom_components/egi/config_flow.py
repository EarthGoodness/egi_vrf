import logging
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback

from . import const
from .adapters import get_adapter
from .modbus_client import get_shared_client
from .options_flow import EgiVrfOptionsFlowHandler

_LOGGER = logging.getLogger(__name__)


class EgiVrfConfigFlow(config_entries.ConfigFlow, domain=const.DOMAIN):
    """Handle a config flow for EGI VRF / Solo integration."""

    VERSION = 1

    def __init__(self):
        self._connection_type = None
        self._adapter_type = None

    async def async_step_user(self, user_input=None):
        """Initial config step: choose adapter or monitor mode."""
        if user_input is not None:
            if user_input.get("monitor_only"):
                return self.async_create_entry(
                    title="EGI Monitor Only",
                    data={"adapter_type": "none"}
                )
            self._adapter_type = user_input.get("adapter_type")
            self._connection_type = user_input.get("connection_type")
            if self._connection_type == "serial":
                return await self.async_step_serial()
            if self._connection_type == "tcp":
                return await self.async_step_tcp()

        schema = vol.Schema({
            vol.Optional("monitor_only", default=False): bool,
            vol.Optional("adapter_type", default="light"): vol.In(["solo", "light", "pro"]),
            vol.Optional("connection_type", default="serial"): vol.In(["serial", "tcp"]),
        })
        return self.async_show_form(step_id="user", data_schema=schema)

    async def async_step_serial(self, user_input=None):
        """Handle serial connection setup."""
        errors = {}
        if user_input is not None:
            full_input = {
                "connection_type": "serial",
                "adapter_type": self._adapter_type,
                **user_input,
            }
            error = await self._async_test_connection(full_input)
            if error is None:
                adapter = get_adapter(self._adapter_type)
                port = full_input["port"]
                return self.async_create_entry(
                    title=f"{adapter.name} (Serial {port})",
                    data=full_input,
                )
            errors["base"] = error
            
        schema = vol.Schema({
            vol.Required("port", default=const.DEFAULT_PORT): str,
            vol.Optional("baudrate", default=const.DEFAULT_BAUDRATE): int,
            vol.Optional("parity", default=const.DEFAULT_PARITY): vol.In(["N", "E", "O"]),
            vol.Optional("stopbits", default=const.DEFAULT_STOPBITS): vol.In([1, 2]),
            vol.Optional("bytesize", default=const.DEFAULT_BYTESIZE): vol.In([7, 8]),
            vol.Optional("slave_id", default=const.DEFAULT_SLAVE_ID): int,
        })
        return self.async_show_form(step_id="serial", data_schema=schema, errors=errors)

    async def async_step_tcp(self, user_input=None):
        """Handle TCP connection setup."""
        errors = {}
        if user_input is not None:
            full_input = {
                "connection_type": "tcp",
                "adapter_type": self._adapter_type,
                **user_input,
            }
            error = await self._async_test_connection(full_input)
            if error is None:
                adapter = get_adapter(self._adapter_type)
                host = full_input["host"]
                port = full_input["port"]
                return self.async_create_entry(
                    title=f"{adapter.name} (TCP {host}:{port})",
                    data=full_input,
                )
            errors["base"] = error

        schema = vol.Schema({
            vol.Required("host"): str,
            vol.Required("port", default=502): int,
            vol.Optional("slave_id", default=const.DEFAULT_SLAVE_ID): int,
        })
        return self.async_show_form(step_id="tcp", data_schema=schema, errors=errors)

    async def _async_test_connection(self, config):
        def _try_connect():
            try:
                client = get_shared_client(
                    connection_type=config.get("connection_type", "serial"),
                    slave_id=config.get("slave_id", 1),
                    port=config.get("port"),
                    baudrate=config.get("baudrate"),
                    parity=config.get("parity"),
                    stopbits=config.get("stopbits"),
                    bytesize=config.get("bytesize"),
                    host=config.get("host"),
                )
                if not client.connect():
                    return "cannot_connect"
                # 0-based addressing works for both Solo (IDU power at 0)
                # and VRF (first IDU 0-0 block starts at 0).
                result = client.read_holding_registers(0, 1)
                client.close()
                if result is None:
                    return "no_response"
            except Exception as e:
                _LOGGER.error("Connection test failed: %s", e)
                return "cannot_connect"
            return None

        return await self.hass.async_add_executor_job(_try_connect)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return EgiVrfOptionsFlowHandler(config_entry)
