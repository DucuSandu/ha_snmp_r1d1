"""Binary sensor platform for snmp_r1d1 integration."""

import logging
from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.helpers.device_registry import DeviceEntryType
from homeassistant.helpers.entity import DeviceInfo
from .const import *
from .coordinator import SnmpDataUpdateCoordinator
from .helpers import apply_bool_vmap, make_entity_name, make_entity_id


_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass, config_entry, async_add_entities):
    """Set up binary sensors from a config entry."""
    _LOGGER.info("Starting binary sensor setup for entry_id: %s", config_entry.entry_id)
    coordinator: SnmpDataUpdateCoordinator = hass.data[DOMAIN][config_entry.entry_id]
    device_info_data = config_entry.data.get(CONF_DEVICE_INFO, {})
    _LOGGER.info("device_info_data[port_count]: %s", device_info_data.get("port_count"))

    device_info = {
        "identifiers": {(DOMAIN, config_entry.data[CONF_DEVICE_IP])},
        "name": config_entry.data[CONF_DEVICE_NAME],
        "manufacturer": device_info_data.get("manufacturer", "Unknown"),
        "model": device_info_data.get("model", "Unknown"),
    }

    entities = []

    # Device-level binary sensors for "binary_sensor" type OIDs in "device" section
    device_oids = coordinator.validated_oids.get("device", {})
    for key, entry in device_oids.items():
        if entry.get("type") == "binary_sensor":
            entities.append(SnmpBinarySensor(coordinator, device_info, key, entry))
            _LOGGER.debug(f"Added device binary sensor: {key}")
        else:
            _LOGGER.debug(f"Skipping device OID {key}: type={entry.get('type')}")

    # Port-level binary sensors for "binary_sensor" type OIDs in "ports" section
    port_count = int(device_info_data.get("port_count", 1))
    ports_oids = coordinator.validated_oids.get("ports", {})
    _LOGGER.info("Processing %d ports, found %d port entries in validated_oids", port_count, len(ports_oids))
    for port_key in sorted(ports_oids.keys(), key=lambda x: int(x[1:])):  # Sort numerically
        if int(port_key[1:]) > port_count:
            _LOGGER.warning(f"Skipping port {port_key}: exceeds port_count {port_count}")
            continue
        port_attrs = ports_oids[port_key]
        _LOGGER.debug(f"Processing port {port_key}: attributes={port_attrs}")
        for key, entry in port_attrs.items():
            if entry.get("type") == "binary_sensor":
                entities.append(SnmpPortBinarySensor(coordinator, device_info, key, entry, port_key))
                _LOGGER.debug(f"Added port binary sensor: {port_key}_{key}")
            else:
                _LOGGER.debug(f"Skipping port OID {port_key}_{key}: type={entry.get('type')}")

    if not entities:
        _LOGGER.info("No binary sensors added. Check validated_oids, port_count, and CONF_ENABLE_CONTROLS: %s",
                    config_entry.data.get(CONF_ENABLE_CONTROLS))
    else:
        _LOGGER.info("Binary sensor setup completed with %d entities", len(entities))
    async_add_entities(entities)

class SnmpBinarySensor(BinarySensorEntity):
    """Representation of a device-level binary sensor."""

    def __init__(self, coordinator: SnmpDataUpdateCoordinator, device_info: dict, sensor_type: str, entry: dict):
        super().__init__()
        self.coordinator = coordinator
        self.sensor_type = sensor_type
        self._attr_device_info = device_info
        self._attr_has_entity_name = True
        self._attr_should_poll = False
        self._attr_unique_id = make_entity_id(coordinator.config_entry.entry_id, "binary_sensor", sensor_type)
        self._attr_name = make_entity_name(sensor_type)
        self._attr_device_class = entry.get("device_class")
        self._entry = entry  # Store entry for vmap

    async def async_added_to_hass(self):
        self.async_on_remove(self.coordinator.async_add_listener(self.async_write_ha_state))

    @property
    def is_on(self):
        if not self.coordinator.data or "device" not in self.coordinator.data:
            return None
        raw_value = self.coordinator.data["device"].get(self.sensor_type)
        if raw_value is None:
            return None
        return apply_bool_vmap(raw_value, self._entry.get("vmap", {}), self._attr_unique_id)
        
    @property
    def extra_state_attributes(self):
        if not self.coordinator.data or "device" not in self.coordinator.data:
            return {}
        return {"firmware": self.coordinator.data["device"].get("firmware", "Unknown")}

class SnmpPortBinarySensor(BinarySensorEntity):
    """Representation of a port-level binary sensor."""

    def __init__(self, coordinator: SnmpDataUpdateCoordinator, device_info: dict, sensor_type: str, entry: dict, padded_port_key: str):
        super().__init__()
        self.coordinator = coordinator
        self.padded_port_key = padded_port_key  # e.g., "p01"
        self.sensor_type = sensor_type
        self._attr_device_info = device_info
        self._attr_has_entity_name = True
        self._attr_should_poll = False
        self._attr_unique_id = make_entity_id(coordinator.config_entry.entry_id, "binary_sensor", sensor_type, padded_port_key)
        self._attr_name = make_entity_name(sensor_type, port_key=padded_port_key)
        self._attr_device_class = entry.get("device_class")
        self._entry = entry  # Store entry for vmap

    async def async_added_to_hass(self):
        self.async_on_remove(self.coordinator.async_add_listener(self.async_write_ha_state))

    @property
    def is_on(self):
        if not self.coordinator.data or "ports" not in self.coordinator.data:
            return None
        port_data = self.coordinator.data["ports"].get(self.padded_port_key, {})
        raw_value = port_data.get(self.sensor_type)
        if raw_value is None:
            return None
        return apply_bool_vmap(raw_value, self._entry.get("vmap", {}), self._attr_unique_id)

    @property
    def extra_state_attributes(self):
        if not self.coordinator.data or "ports" not in self.coordinator.data:
            return {}
        port_data = self.coordinator.data["ports"].get(self.padded_port_key, {})
        port_name = port_data.get("port_name", "Unknown")
        return {"port_name": port_name}