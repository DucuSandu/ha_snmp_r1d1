"""The snmp_r1d1 integration."""

import logging
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntryType, async_get as async_get_dr
from .const import *
from .coordinator import SnmpDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS = ["sensor", "switch", "binary_sensor", "text"]

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up snmp_r1d1 from a config entry."""
    _LOGGER.info("Starting integration setup for entry_id: %s", entry.entry_id)
    hass.data.setdefault(DOMAIN, {})
    coordinator = SnmpDataUpdateCoordinator(hass, entry)
    hass.data[DOMAIN][entry.entry_id] = coordinator

    # Register device in Device Registry
    device_registry = async_get_dr(hass)
    device_info = entry.data.get(CONF_DEVICE_INFO, {})
    device_entry_data = {
        "config_entry_id": entry.entry_id,
        "identifiers": {(DOMAIN, entry.data[CONF_DEVICE_IP])},
        "manufacturer": device_info.get("manufacturer", "Unknown"),
        "model": device_info.get("model", "Unknown"),
        "name": entry.data[CONF_DEVICE_NAME],
        "sw_version": device_info.get("firmware", "Unknown"),
        "serial_number": device_info.get("serial", "Unknown"),
        "connections": {("ip", entry.data[CONF_DEVICE_IP])},
    }
    _LOGGER.debug("Registering device with data: %s", device_entry_data)
    device = device_registry.async_get_or_create(**device_entry_data)
    _LOGGER.info("Registered device with id: %s, linked to config_entry_id: %s", device.id, entry.entry_id)
    _LOGGER.debug("Device config_entries: %s", list(device.config_entries))

    _LOGGER.info("Forwarding setup to platforms: %s", PLATFORMS)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    
    _LOGGER.info("Performing first data refresh for coordinator")
    await coordinator.async_config_entry_first_refresh()

    _LOGGER.info("Integration setup complete for entry_id: %s", entry.entry_id)
    return True

async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    _LOGGER.info("Unloading entry_id: %s", entry.entry_id)
    coordinator: SnmpDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]
    coordinator._aborted = True  # 🔹 stop polling immediately
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)
        _LOGGER.info("Successfully unloaded entry_id: %s", entry.entry_id)
    else:
        _LOGGER.error("Failed to unload platforms for entry_id: %s", entry.entry_id)
    return unload_ok