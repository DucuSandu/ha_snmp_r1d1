"""MAC table entity helpers for snmp_r1d1 integration.

This module provides diagnostic and control entities for handling MAC address tables
learned via SNMP. It defines device-level and port-level sensors/switches that expose
MAC counts, last-update times, and toggles for enabling/disabling MAC collection.

All unique IDs are built consistently using the make_entity_id() helper to ensure
stable and predictable entity IDs in Home Assistant.
"""

import json
import logging
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity import EntityCategory
from homeassistant.components.sensor import SensorEntity
from homeassistant.components.switch import SwitchEntity
from .helpers import make_entity_name, make_port_entity_name, make_entity_id
_LOGGER = logging.getLogger(__name__)


# ======================================================================
# Utility: Normalize ports
# ======================================================================

def _normalize_ports(ports: dict) -> dict:
    """Convert raw SNMP integer keys ('1','2',..) into padded pXX keys.

    Example:
        {"1": ["aa:bb:cc"], "2": []} → {"p01": ["aa:bb:cc"], "p02": []}
    """
    normalized = {}
    for key, macs in ports.items():
        try:
            port_int = int(key)
            port_key = f"p{port_int:02d}"
        except ValueError:
            port_key = key
        normalized[port_key] = macs
    return normalized


# ======================================================================
# Device-level MAC Sensors
# ======================================================================

class DeviceMacTableSensor(SensorEntity):
    """Global MAC table sensor (MAC → port mapping with diagnostic attributes)."""

    def __init__(self, coordinator, device_info: dict, prefix: str):
        self.coordinator = coordinator
        self._attr_device_info = device_info
        self._attr_should_poll = False
        self._attr_unique_id = make_entity_id(coordinator.config_entry.entry_id, "sensor", "mac_table", prefix)
        self._attr_name = make_entity_name("mac_table")
        self._attr_entity_category = EntityCategory.DIAGNOSTIC

    async def async_added_to_hass(self):
        self.async_on_remove(
            self.coordinator.async_add_listener(self.async_write_ha_state)
        )

    @property
    def native_value(self):
        if not self.coordinator.data:
            return None
        table = self.coordinator.data.get("mac_table", {})
        ports = table.get("ports", {})
        return sum(len(macs) for macs in ports.values())

    @property
    def extra_state_attributes(self):
        if not self.coordinator.data:
            return None
        table = self.coordinator.data.get("mac_table", {})
        raw_ports = table.get("ports", {})
        norm_ports = _normalize_ports(raw_ports)

        rows = []
        for port in sorted(norm_ports.keys(), key=lambda p: int(p[1:]) if p.startswith("p") else p):
            macs = norm_ports[port]
            rows.append({
                "port": port,
                "macs": macs if macs else []
            })

        return {
            "mac_table": rows,
            "last_updated": table.get("last_updated"),
        }



class DeviceMacCountSensor(SensorEntity):
    """Total MAC count across all ports."""

    def __init__(self, coordinator, device_info: dict, prefix: str):
        self.coordinator = coordinator
        self._attr_device_info = device_info
        self._attr_should_poll = False
        self._attr_unique_id = make_entity_id(coordinator.config_entry.entry_id, "sensor", "mac_count", prefix)
        self._attr_name = make_entity_name("mac_count")

    async def async_added_to_hass(self):
        self.async_on_remove(
            self.coordinator.async_add_listener(self.async_write_ha_state)
        )

    @property
    def native_value(self):
        if not self.coordinator.data:
            return None
        table = self.coordinator.data.get("mac_table", {})
        ports = table.get("ports", {})
        return sum(len(macs) for macs in ports.values())


class DeviceMacTableLastUpdateSensor(SensorEntity):
    """Sensor to expose the last updated timestamp of the MAC table."""

    def __init__(self, coordinator, device_info: dict, prefix: str):
        self.coordinator = coordinator
        self._attr_device_info = device_info
        self._attr_should_poll = False
        self._attr_unique_id = make_entity_id(coordinator.config_entry.entry_id, "sensor", "mac_table_last_update", prefix)
        self._attr_name = make_entity_name("mac_table_last_update")

    async def async_added_to_hass(self):
        self.async_on_remove(
            self.coordinator.async_add_listener(self.async_write_ha_state)
        )

    @property
    def native_value(self):
        if not self.coordinator.data:
            return None
        table = self.coordinator.data.get("mac_table", {})
        return table.get("last_updated")


# ======================================================================
# Port-level MAC Sensors
# ======================================================================

class PortMacTableSensor(SensorEntity):
    """Port-level MAC table sensor.
    native_value = number of MACs learned on this port.
    extra_state_attributes = sorted list of MAC addresses.
    """

    def __init__(self, coordinator, device_info: dict, prefix: str, port):
        self.coordinator = coordinator
        self.raw_port_key = str(port)                 # numeric lookup
        self.padded_port_key = f"p{int(port):02d}"    # for names/unique_id
        self._attr_device_info = device_info
        self._attr_should_poll = False
        self._attr_unique_id = make_entity_id(coordinator.config_entry.entry_id, "sensor", "mac_table", prefix, self.padded_port_key)
        self._attr_name = make_port_entity_name(self.padded_port_key, "mac_table")

    async def async_added_to_hass(self):
        self.async_on_remove(
            self.coordinator.async_add_listener(self.async_write_ha_state)
        )

    @property
    def native_value(self):
        """Return the number of MACs learned on this port."""
        if not self.coordinator.data:
            return None
        table = self.coordinator.data.get("mac_table", {})
        ports = table.get("ports", {})
        return len(ports.get(self.raw_port_key, []))

    @property
    def extra_state_attributes(self):
        """Return the sorted list of MAC addresses as an attribute."""
        if not self.coordinator.data:
            return {}
        table = self.coordinator.data.get("mac_table", {})
        ports = table.get("ports", {})
        macs = sorted(ports.get(self.raw_port_key, []))
        return {"macs": macs}    


# ======================================================================
# MAC Collection Switches
# ======================================================================

class GlobalMacCollectionSwitch(SwitchEntity):
    """Global switch to disable MAC collection on all ports.

    ON  = MAC collection disabled for all ports (all ports in excluded set)
    OFF = MAC collection enabled for all ports (no ports excluded)
    """

    def __init__(self, coordinator, device_info: dict, prefix: str, excluded_ports, config_entry):
        self.coordinator = coordinator
        self.config_entry = config_entry
        self._attr_device_info = device_info
        self._attr_should_poll = False
        self._attr_unique_id = make_entity_id(coordinator.config_entry.entry_id, "switch", "mac_collection", prefix)
        self._attr_name = make_entity_name("disable_mac_collection")
        self._excluded_ports = set(excluded_ports)
        self._total_ports = int(device_info.get("port_count", 1))

    async def async_added_to_hass(self):
        self.async_on_remove(
            self.coordinator.async_add_listener(self.async_write_ha_state)
        )

    @property
    def is_on(self):
        """ON = all ports excluded = MAC collection fully disabled."""
        all_ports = {str(p) for p in range(1, self._total_ports + 1)}
        return all_ports.issubset(self._excluded_ports)

    async def async_turn_on(self, **kwargs):
        """Disable MAC collection: exclude all ports."""
        self._excluded_ports = {str(p) for p in range(1, self._total_ports + 1)}
        self.async_write_ha_state()
        await self._save_options()

    async def async_turn_off(self, **kwargs):
        """Enable MAC collection: clear all exclusions."""
        self._excluded_ports.clear()
        self.async_write_ha_state()
        await self._save_options()

    async def _save_options(self):
        new_options = dict(self.config_entry.options)
        valid_ports = [p for p in self._excluded_ports if str(p).isdigit()]
        new_options["mac_excluded_ports"] = sorted(valid_ports, key=int)
        self.hass.config_entries.async_update_entry(self.config_entry, options=new_options)
        self._excluded_ports = set(str(p) for p in new_options["mac_excluded_ports"])
        _LOGGER.info("Updated global mac_excluded_ports: %s", new_options["mac_excluded_ports"])


class PortMacCollectionSwitch(SwitchEntity):
    """Port-level switch to disable MAC collection on a single port.

    ON  = this port is excluded from MAC collection (disabled)
    OFF = this port is included in MAC collection (enabled)
    """

    def __init__(self, coordinator, device_info: dict, prefix: str, port, excluded_ports, config_entry):
        self.coordinator = coordinator
        self.raw_port_key = str(port)                 # numeric lookup
        self.padded_port_key = f"p{int(port):02d}"    # for names/unique_id
        self.config_entry = config_entry
        self._attr_device_info = device_info
        self._attr_should_poll = False
        self._attr_unique_id = make_entity_id(coordinator.config_entry.entry_id, "switch", "mac_collection", prefix, self.padded_port_key)
        self._attr_name = make_port_entity_name(self.padded_port_key, "disable_mac_collection")
        self._excluded_ports = set(excluded_ports)

    async def async_added_to_hass(self):
        self.async_on_remove(
            self.coordinator.async_add_listener(self.async_write_ha_state)
        )

    @property
    def is_on(self):
        """ON = this port is excluded = MAC collection disabled for this port."""
        return self.raw_port_key in self._excluded_ports

    async def async_turn_on(self, **kwargs):
        """Disable MAC collection for this port."""
        self._excluded_ports.add(self.raw_port_key)
        self.async_write_ha_state()
        await self._save_options()

    async def async_turn_off(self, **kwargs):
        """Enable MAC collection for this port."""
        self._excluded_ports.discard(self.raw_port_key)
        self.async_write_ha_state()
        await self._save_options()

    async def _save_options(self):
        new_options = dict(self.config_entry.options)
        valid_ports = [p for p in self._excluded_ports if str(p).isdigit()]
        new_options["mac_excluded_ports"] = sorted(valid_ports, key=int)
        self.hass.config_entries.async_update_entry(self.config_entry, options=new_options)
        self._excluded_ports = set(str(p) for p in new_options["mac_excluded_ports"])
        _LOGGER.info("Updated mac_excluded_ports: %s", new_options["mac_excluded_ports"])