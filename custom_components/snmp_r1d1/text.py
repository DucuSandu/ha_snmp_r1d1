"""Text platform for snmp_r1d1 integration."""

import logging
from homeassistant.components.text import TextEntity
from homeassistant.helpers.device_registry import DeviceEntryType
from homeassistant.helpers.entity import DeviceInfo
from .const import *
from .coordinator import SnmpDataUpdateCoordinator
from .helpers import make_entity_name, make_port_entity_name, make_entity_id

_LOGGER = logging.getLogger(__name__)

async def async_setup_entry(hass, config_entry, async_add_entities):
    """Set up Text Entities from a config entry."""
    _LOGGER.info("Starting text entities setup for entry_id: %s", config_entry.entry_id)
    coordinator: SnmpDataUpdateCoordinator = hass.data[DOMAIN][config_entry.entry_id]
    device_info_data = config_entry.data.get(CONF_DEVICE_INFO, {})

    device_info = {
        "identifiers": {(DOMAIN, config_entry.data[CONF_DEVICE_IP])},
        "name": config_entry.data[CONF_DEVICE_NAME],
        "manufacturer": device_info_data.get("manufacturer", "Unknown"),
        "model": device_info_data.get("model", "Unknown"),
    }

    prefix = config_entry.data[CONF_ENTITY_PREFIX]
    entities = []

    # Device-level text entities
    for key, entry in coordinator.validated_oids.get("device", {}).items():
        if entry.get("type") == "text":
            entities.append(SnmpDeviceText(coordinator, key, device_info, prefix, entry))
            _LOGGER.info(f"Added device text entity: {key}")

    # Port-level text entities with zero-padded keys
    port_count = int(device_info_data.get("port_count", 1))
    ports_oids = coordinator.validated_oids.get("ports", {})
    _LOGGER.info("Processing %d ports for text entities", port_count)
    for port_key in sorted(ports_oids.keys(), key=lambda x: int(x[1:])):
        if int(port_key[1:]) > port_count:
            _LOGGER.warning(f"Skipping port {port_key}: exceeds port_count {port_count}")
            continue
        port_attrs = ports_oids[port_key]
        for key, entry in port_attrs.items():
            if entry.get("type") == "text":
                entities.append(SnmpPortText(coordinator, port_key, key, device_info, prefix, entry))
                _LOGGER.info(f"Added port text entity: {port_key}_{key}")

    if not entities:
        _LOGGER.info("No text entities added for this device")
    else:
        _LOGGER.info("Text setup completed with %d entities", len(entities))
    async_add_entities(entities)

class SnmpDeviceText(TextEntity):
    """Representation of a device-level text entity (e.g., sysName)."""

    def __init__(self, coordinator: SnmpDataUpdateCoordinator, text_type: str, device_info: DeviceInfo, prefix: str, entry: dict):
        """Initialize the device-level text entity."""
        super().__init__()
        device_name = coordinator.config_entry.data[CONF_DEVICE_NAME]
        self.coordinator = coordinator
        self.text_type = text_type
        self._attr_device_info = device_info
        self._attr_should_poll = False
        self._attr_unique_id = make_entity_id(coordinator.config_entry.entry_id, text_type, suffix="text")
        self._attr_name = make_entity_name(text_type)
        self._attr_device_class = entry.get("device_class")
        self._entry = entry
        self._attr_mode = "text"
        self._attr_max = 64

    async def async_added_to_hass(self):
        """Register listener when entity is added."""
        _LOGGER.debug("Adding listener for entity %s", self._attr_unique_id)
        self.async_on_remove(self.coordinator.async_add_listener(self.async_write_ha_state))

    @property
    def native_value(self):
        """Return the current value of the text entity."""
        if not self.coordinator.data or "device" not in self.coordinator.data:
            _LOGGER.debug(f"No device data for {self.text_type}")
            return None
        value = self.coordinator.data["device"].get(self.text_type)
        return value if value is not None else ""

    async def async_set_value(self, value: str):
        """Set the value of the text entity."""
        result = await self.coordinator.async_set_text_value(self.text_type, value)
        if result:
            self.async_write_ha_state()
        else:
            _LOGGER.error(f"Failed to set {self._attr_name} to {value}")

    @property
    def extra_state_attributes(self):
        if not self.coordinator.data or "device" not in self.coordinator.data:
            return {}
        return {"firmware": self.coordinator.data["device"].get("firmware", "Unknown")}

class SnmpPortText(TextEntity):
    """Representation of a port-level text entity (e.g., ifAlias)."""

    def __init__(self, coordinator: SnmpDataUpdateCoordinator, padded_port_key: str, text_type: str, device_info: DeviceInfo, prefix: str, entry: dict):
        """Initialize the port-level text entity."""
        super().__init__()
        device_name = coordinator.config_entry.data[CONF_DEVICE_NAME]
        self.coordinator = coordinator
        self.padded_port_key = padded_port_key  # e.g., "p01"
        self.text_type = text_type
        self._attr_device_info = device_info
        self._attr_should_poll = False
        self._attr_unique_id = make_entity_id(coordinator.config_entry.entry_id, text_type, suffix="text", port=padded_port_key)
        self._attr_name = make_port_entity_name(padded_port_key, text_type)
        self._attr_device_class = entry.get("device_class")
        self._entry = entry
        self._attr_mode = "text"
        self._attr_max = 64

    async def async_added_to_hass(self):
        """Register listener when entity is added."""
        _LOGGER.debug("Adding listener for entity %s", self._attr_unique_id)
        self.async_on_remove(self.coordinator.async_add_listener(self.async_write_ha_state))

    @property
    def native_value(self):
        """Return the current value of the text entity."""
        if not self.coordinator.data or "ports" not in self.coordinator.data:
            _LOGGER.debug(f"No port data for {self.padded_port_key}_{self.text_type}")
            return None
        port_data = self.coordinator.data["ports"].get(self.padded_port_key, {})
        value = port_data.get(self.text_type)
        return value if value is not None else ""

    async def async_set_value(self, value: str):
        """Set the value of the text entity."""
        result = await self.coordinator.async_set_text_value(self.text_type, value, port=self.padded_port_key)
        if result:
            self.async_write_ha_state()
        else:
            _LOGGER.error(f"Failed to set {self._attr_name} to {value}")

    @property
    def extra_state_attributes(self):
        if not self.coordinator.data or "ports" not in self.coordinator.data:
            return {}
        port_data = self.coordinator.data["ports"].get(self.padded_port_key, {})
        port_name = port_data.get("port_name", "Unknown")
        return {"port_name": port_name}