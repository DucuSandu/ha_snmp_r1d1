"""Switch platform for snmp_r1d1 integration."""

import logging
from homeassistant.components.switch import SwitchEntity
from homeassistant.helpers.entity import DeviceInfo
from .const import *
from . import mac_table
from .coordinator import SnmpDataUpdateCoordinator

from .helpers import apply_bool_vmap, make_entity_name, make_port_entity_name, make_entity_id
_LOGGER = logging.getLogger(__name__)



# ================================================================
# Entry point: setup all switch entities
# ================================================================
async def async_setup_entry(hass, config_entry, async_add_entities):
    """Set up switch entities from a config entry."""
    _LOGGER.info("Starting switch setup")
    coordinator: SnmpDataUpdateCoordinator = hass.data[DOMAIN][config_entry.entry_id]
    device_info_data = config_entry.data.get(CONF_DEVICE_INFO, {})

    # Device metadata for HA registry
    device_info = {
        "identifiers": {(DOMAIN, config_entry.data[CONF_DEVICE_IP])},
        "name": config_entry.data[CONF_DEVICE_NAME],
        "manufacturer": device_info_data.get("manufacturer", "Unknown"),
        "model": device_info_data.get("model", "Unknown"),
    }

    prefix = config_entry.data[CONF_ENTITY_PREFIX]
    entities = []


    # ----------------------------
    # MAC table switches (independent of CONF_ENABLE_CONTROLS, if OIDs exist)
    # ----------------------------
    has_mac_table = any(
        entry.get("type") == "mac_table"
        for entry in coordinator.validated_oids.get("device", {}).values()
    )
    has_mac_port = any(
        entry.get("type") == "mac_port"
        for entry in coordinator.validated_oids.get("device", {}).values()
    )

    if has_mac_table and has_mac_port:
        port_count = int(device_info_data.get("port_count", 1))
        all_ports = [str(p) for p in range(1, port_count + 1)]
        entities.append(mac_table.GlobalMacCollectionSwitch(
            coordinator, prefix, device_info,
            config_entry.options.get("mac_excluded_ports", []),
            config_entry
        ))
        for port in range(1, port_count + 1):
            entities.append(
                mac_table.PortMacCollectionSwitch(
                    coordinator, prefix, port, device_info,
                    config_entry.options.get("mac_excluded_ports", []),
                    config_entry
                )
            )
        _LOGGER.info("MAC table switches created")
    else:
        _LOGGER.info("No MAC table OIDs found, skipping MAC switches")

    # Skip setup SNMP Switches if controls are not enabled for this device (CONF_ENABLE_CONTROLS)
    if not config_entry.data.get(CONF_ENABLE_CONTROLS, False):
        async_add_entities(entities)
        _LOGGER.info("Controls are disabled, skipping switch setup")
        return

    # ----------------------------
    # Device-level SNMP switches
    # ----------------------------
    for key, entry in coordinator.validated_oids.get("device", {}).items():
        if entry.get("type") == "switch":
            entities.append(SnmpDeviceSwitch(coordinator, key, device_info, prefix, entry))
            _LOGGER.info(f"Added device switch: {key}")

    # ----------------------------
    # Port-level SNMP switches
    # ----------------------------
    for port_key, port_attrs in coordinator.validated_oids.get("ports", {}).items():
        for key, entry in port_attrs.items():
            if entry.get("type") == "switch":
                entities.append(SnmpPortSwitch(coordinator, port_key, key, device_info, prefix, entry))
                _LOGGER.info(f"Added port switch: {port_key}_{key}")

    # Register all created switch entities
    async_add_entities(entities)
    _LOGGER.info("Switch setup completed")


# ================================================================
# Entity: Device-level switch
# ================================================================
class SnmpDeviceSwitch(SwitchEntity):
    """Representation of a device-level switch entity."""

    def __init__(self, coordinator, switch_type, device_info, prefix, entry: dict):
        super().__init__()
        self.coordinator = coordinator
        self.switch_type = switch_type
        self._attr_device_info = device_info
        self._attr_should_poll = False
        # Unique ID for HA registry
        self._attr_unique_id = make_entity_id(coordinator.config_entry.entry_id, switch_type, suffix="switch")
        # Human-readable name
        self._attr_name = make_entity_name(switch_type)
        self._entry = entry

    async def async_added_to_hass(self):
        """Register update listener when entity is added."""
        self.async_on_remove(self.coordinator.async_add_listener(self.async_write_ha_state))

    @property
    def is_on(self):
        """Return ON/OFF state of the switch."""
        if not self.coordinator.data or "device" not in self.coordinator.data:
            return None
        raw_value = self.coordinator.data["device"].get(self.switch_type)
        if raw_value is None:
            return None
        return apply_bool_vmap(raw_value, self._entry.get("vmap", {}), self._attr_unique_id)
        
    async def async_turn_on(self, **kwargs):
        """Send SNMP set to turn switch ON."""
        result = await self.coordinator.async_set_switch_state(self.switch_type, True)
        if result:
            self.async_write_ha_state()

    async def async_turn_off(self, **kwargs):
        """Send SNMP set to turn switch OFF."""
        result = await self.coordinator.async_set_switch_state(self.switch_type, False)
        if result:
            self.async_write_ha_state()

    @property
    def extra_state_attributes(self):
        """Optional attributes: firmware version."""
        if not self.coordinator.data or "device" not in self.coordinator.data:
            return {}
        return {"firmware": self.coordinator.data["device"].get("firmware", "Unknown")}


# ================================================================
# Entity: Port-level switch
# ================================================================
class SnmpPortSwitch(SwitchEntity):
    """Representation of a port-level switch entity."""

    def __init__(self, coordinator, padded_port_key, switch_type, device_info, prefix, entry: dict):
        super().__init__()
        self.coordinator = coordinator
        self.padded_port_key = padded_port_key  # e.g., "p01"
        self.switch_type = switch_type
        self._attr_device_info = device_info
        self._attr_should_poll = False
        # Unique ID includes port
        self._attr_unique_id = make_entity_id(coordinator.config_entry.entry_id, switch_type, suffix="switch", port=padded_port_key)
        # Human-readable name: Port-05 Admin State
        self._attr_name = make_port_entity_name(padded_port_key, switch_type)
        self._entry = entry

    async def async_added_to_hass(self):
        """Register update listener when entity is added."""
        self.async_on_remove(self.coordinator.async_add_listener(self.async_write_ha_state))

    @property
    def is_on(self):
        """Return ON/OFF state for the port switch."""
        if not self.coordinator.data or "ports" not in self.coordinator.data:
            return None
        port_data = self.coordinator.data["ports"].get(self.padded_port_key, {})
        raw_value = port_data.get(self.switch_type)
        if raw_value is None:
            return None
        return apply_bool_vmap(raw_value, self._entry.get("vmap", {}), self._attr_unique_id)

    async def async_turn_on(self, **kwargs):
        """Send SNMP set to turn port switch ON."""
        result = await self.coordinator.async_set_switch_state(self.switch_type, True, port=self.padded_port_key)
        if result:
            self.async_write_ha_state()

    async def async_turn_off(self, **kwargs):
        """Send SNMP set to turn port switch OFF."""
        result = await self.coordinator.async_set_switch_state(self.switch_type, False, port=self.padded_port_key)
        if result:
            self.async_write_ha_state()

    @property
    def extra_state_attributes(self):
        """Optional attributes: include port name if available."""
        if not self.coordinator.data or "ports" not in self.coordinator.data:
            return {}
        port_data = self.coordinator.data["ports"].get(self.padded_port_key, {})
        return {"port_name": port_data.get("port_name", "Unknown")}