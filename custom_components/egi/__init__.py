"""EGI Adapter integration init"""
import logging
import time
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers.device_registry import async_get as async_get_device_registry

from . import const
from .coordinator import EgiAdapterCoordinator
from .modbus_client import get_shared_client  # ← use the factory for both serial and TCP
from .adapters import get_adapter

_LOGGER = logging.getLogger(__name__)

PLATFORMS = ["climate", "button", "sensor", "select"]


async def _async_config_entry_updated(
    hass: HomeAssistant,
    entry: ConfigEntry
) -> None:
    """Reload the config entry when options are updated."""
    _LOGGER.debug("Config entry %s updated, reloading", entry.entry_id)
    await hass.config_entries.async_reload(entry.entry_id)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry
) -> bool:
    """Set up a config entry."""
    hass.data.setdefault(const.DOMAIN, {})

    # Monitor-only: just sensor & select
    if entry.data.get("adapter_type") == "none":
        _LOGGER.info("Monitor-only mode for %s", entry.entry_id)
        device_registry = async_get_device_registry(hass)
        gateway_id = f"gateway_{entry.entry_id}"
        device_registry.async_get_or_create(
            config_entry_id=entry.entry_id,
            identifiers={(const.DOMAIN, gateway_id)},
            name="EGI Monitoring",
            manufacturer="EGI",
            model="Logging Dashboard Only",
        )
        await hass.config_entries.async_forward_entry_setups(entry, ["sensor", "select"])
        entry.async_on_unload(entry.add_update_listener(_async_config_entry_updated))
        return True

    # Normal mode
    start = time.perf_counter()
    adapter_type = entry.data.get("adapter_type", "light")
    adapter = get_adapter(adapter_type)
    _LOGGER.info("Initializing EGI adapter %s", adapter_type)

    conn = entry.data.get("connection_type", "serial")
    sid = entry.data.get("slave_id", const.DEFAULT_SLAVE_ID)

    if conn == "serial":
        client = get_shared_client(
            connection_type="serial",
            slave_id=sid,
            port=entry.data.get("port"),
            baudrate=entry.data.get("baudrate", const.DEFAULT_BAUDRATE),
            parity=entry.data.get("parity", const.DEFAULT_PARITY),
            stopbits=entry.data.get("stopbits", const.DEFAULT_STOPBITS),
            bytesize=entry.data.get("bytesize", const.DEFAULT_BYTESIZE),
        )
    else:
        # ✅ FIX: Use the same shared-client factory for TCP as well
        client = get_shared_client(
            connection_type="tcp",
            slave_id=sid,
            host=entry.data.get("host"),
            port=entry.data.get("port", 502),
        )

    if not await hass.async_add_executor_job(client.connect):
        raise ConfigEntryNotReady("Cannot connect to Modbus")

    units = await hass.async_add_executor_job(adapter.scan_devices, client)
    if not units:
        _LOGGER.error("No devices found on adapter %s", entry.entry_id)
        return False

    interval = timedelta(seconds=entry.options.get("poll_interval", 2))
    coord = EgiAdapterCoordinator(hass, client, adapter, units, interval)
    try:
        await coord.async_config_entry_first_refresh()
    except Exception as e:
        _LOGGER.error("First refresh failed: %s", e)
        raise ConfigEntryNotReady from e

    hass.data[const.DOMAIN][entry.entry_id] = {
        "client": client,
        "coordinator": coord,
        "adapter": adapter,
    }

    # Register services
    async def _call(method, eid, *args):
        data = hass.data[const.DOMAIN].get(eid, {})
        obj, cli = data.get("adapter"), data.get("client")
        if obj and cli and hasattr(obj, method):
            await hass.async_add_executor_job(getattr(obj, method), cli, *args)
            _LOGGER.info("Called %s on %s", method, eid)
        else:
            _LOGGER.warning("%s/%s not found", method, eid)

    hass.services.async_register(
        const.DOMAIN, "set_system_time",
        lambda call: _call("write_system_time", call.data.get("entry_id"))
    )
    hass.services.async_register(
        const.DOMAIN, "set_brand_code",
        lambda call: _call("write_brand_code", call.data.get("entry_id"), call.data.get("brand_code"))
    )
    if not hass.services.has_service(const.DOMAIN, "scan_idus"):
        hass.services.async_register(const.DOMAIN, "scan_idus", lambda call: coord.async_request_refresh())
    hass.services.async_register(
        const.DOMAIN, "set_log_level",
        lambda call: _call("set_log_level", call.data.get("entry_id"), call.data.get("level"))
    )

    # Register device
    registry = async_get_device_registry(hass)
    gid = f"gateway_{entry.entry_id}"
    dev = registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(const.DOMAIN, gid)},
        name=adapter.name,
        manufacturer="EGI",
        model=f"{adapter.display_type} - {adapter.get_brand_name(coord.gateway_brand_code)}",
    )
    if coord.gateway_brand_code:
        registry.async_update_device(dev.id, sw_version="1.0", name_by_user=adapter.name)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_config_entry_updated))

    # Update title
    try:
        brand = adapter.get_brand_name(coord.gateway_brand_code)
    except Exception:
        brand = "Unknown"
    unit = getattr(client, "unit_id", sid)
    port_str = (
        entry.data.get("port")
        if conn == "serial"
        else f"{entry.data.get('host')}:{entry.data.get('port', 502)}"
    )
    title = f"{adapter.name} - {brand} (ID {unit} / {port_str})"
    hass.config_entries.async_update_entry(entry, title=title)

    coord.setup_duration = time.perf_counter() - start
    _LOGGER.debug("Setup completed in %.2f s", coord.setup_duration)
    return True


async def async_unload_entry(
    hass: HomeAssistant,
    entry: ConfigEntry
) -> bool:
    """Unload a config entry and clean up all resources."""
    plats = ["sensor", "select"] if entry.data.get("adapter_type") == "none" else PLATFORMS
    ok = await hass.config_entries.async_unload_platforms(entry, plats)
    if not ok:
        return False
    hass.data[const.DOMAIN].pop(entry.entry_id, None)
    # remove device
    try:
        registry = async_get_device_registry(hass)
        gid = f"gateway_{entry.entry_id}"
        dev = registry.async_get_device(identifiers={(const.DOMAIN, gid)})
        if dev:
            registry.async_remove_device(dev.id)
    except Exception:  # pragma: no cover
        pass
    # remove services if none remain
    if not hass.data[const.DOMAIN]:
        for s in ("set_system_time", "set_brand_code", "scan_idus", "set_log_level"):
            if hass.services.has_service(const.DOMAIN, s):
                hass.services.async_remove(const.DOMAIN, s)
    _LOGGER.debug("Unloaded entry %s", entry.entry_id)
    return True
