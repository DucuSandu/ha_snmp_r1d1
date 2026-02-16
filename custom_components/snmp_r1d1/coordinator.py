"""Data coordinator for snmp_r1d1 integration."""

import asyncio
import copy
from datetime import timedelta
from homeassistant.util import dt as dt_util
import logging
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import async_get as async_get_dr
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from .const import *
from .snmp import SnmpClient, SnmpCredentials
from .helpers import to_snmp_bool

# Setup logger for this module
_LOGGER = logging.getLogger(__name__)


class SnmpDataUpdateCoordinator(DataUpdateCoordinator):
    """Manages SNMP polling, caching and write operations for this integration."""

    def __init__(self, hass: HomeAssistant, config_entry):
        """Initialize the coordinator."""
        _LOGGER.info("Initializing coordinator")

        # Save config entry for later use (stores device IP, SNMP credentials, OIDs, etc.)
        self.config_entry = config_entry

        # Initialize SNMP client with target device IP and prepared credentials
        self.client = SnmpClient(
            config_entry.data[CONF_DEVICE_IP],
            self._create_client_credentials()
        )

        # Pre-validated OIDs passed from config flow (structure: device, ports, attributes, etc.)
        self.validated_oids = config_entry.data[CONF_VALIDATED_OIDS]

        # Coordinator's internal data cache
        # Always a dictionary → avoids NoneType errors when entities access coordinator.data
        self.data = {"previous": {}, "last_updated": {}}

        # Cache for firmware polling to avoid frequent repeated SNMP queries
        self._firmware_cache = "Unknown"

        # Track timestamps for last "slow update" and last MAC table refresh
        self._last_slow_update = 0
        self._last_mac_update = 0

        # Control flags and lock for concurrency
        self._aborted = False               # Used to abort polling if entry is being removed
        self._lock = asyncio.Lock()         # Prevents concurrent SNMP calls overlapping

        # Polling interval defined in config
        poll_interval = config_entry.data.get(CONF_POLLING_INTERVAL)
        _LOGGER.debug(f"POLL INTERVAL = {poll_interval} ({type(poll_interval)})")

        # Call parent DataUpdateCoordinator constructor with interval and logging
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=poll_interval),
        )

    # ------------------------------------------------------------------
    # Credentials factory
    # ------------------------------------------------------------------
    def _create_client_credentials(self):
        """Create SNMP credentials object from config entry data."""
        data = self.config_entry.data
        return SnmpCredentials(
            version=data[CONF_SNMP_VERSION],
            read_community=data[CONF_READ_COMMUNITY_STRING],
            write_community=data.get(CONF_WRITE_COMMUNITY_STRING),
            username=data.get(CONF_USERNAME),
            auth_protocol=data.get(CONF_AUTH_PROTOCOL),
            auth_key=data.get(CONF_AUTH_KEY),
            privacy_protocol=data.get(CONF_PRIVACY_PROTOCOL),
            privacy_key=data.get(CONF_PRIVACY_KEY)
        )

    # ------------------------------------------------------------------
    # Write operation: set switch state
    # ------------------------------------------------------------------
    async def async_set_switch_state(self, key: str, state: bool, port: str = None):
        """Set switch state (device-level or port-level)."""

        # Guard: only allow if device was configured with "enable_controls"
        if not self.config_entry.data.get(CONF_ENABLE_CONTROLS, False):
            _LOGGER.warning("Controls are not enabled for this device")
            return False

        # Timestamp for last update tracking
        current_time = dt_util.utcnow().timestamp() 

        # Determine whether setting is at device-level or port-level
        level = "port" if port is not None else "device"
        
        # Look up the corresponding OID and metadata
        if level == "device":
            entry = self.validated_oids.get("device", {}).get(key, {})
            oid = entry.get("oid")
            section = "device"
            identifier = key
        else:
            entry = self.validated_oids.get("ports", {}).get(port, {}).get(key, {})
            oid = entry.get("oid")
            section = "ports"
            identifier = f"port_{port}_{key}"
        
        # If no OID, cannot perform write
        if not oid:
            _LOGGER.error("OID not defined for %s %s", level, identifier)
            return False
        
        # Resolve SNMP integer value using helper (maps "on"/"off" to SNMP values)
        vmap = entry.get("vmap", {})
        value = to_snmp_bool(state, vmap, identifier, _LOGGER)
        if value is None:
            return False
        
        # Perform SNMP SET operation
        _LOGGER.debug("Setting %s %s to %s with OID %s", level, identifier, 'on' if state else 'off', oid)
        try:
            result = await self.client.async_set(oid, value, value_type="integer")
        except Exception as e:
            _LOGGER.error("SNMP set failed for %s %s (OID=%s, value=%s): %s",
                          level, identifier, oid, value, e)
            return False

        # Update local cache if successful
        if result:
            _LOGGER.info("Successfully set %s %s to %s", level, identifier, 'on' if state else 'off')

            # Ensure section exists
            if section not in self.data:
                self.data[section] = {}

            # Ensure port dict exists if writing port-level
            if level == "port" and port not in self.data[section]:
                self.data[section][port] = {}

            # Store the value in cache
            if level == "device":
                self.data["device"][key] = value
            else:
                self.data["ports"].setdefault(port, {})
                self.data["ports"][port][key] = value

            # Mark last updated timestamp
            self.data.setdefault("last_updated", {})
            self.data["last_updated"][identifier] = current_time
        else:
            _LOGGER.error("Failed to set %s %s state", level, identifier)

        return result

    # ------------------------------------------------------------------
    # Write operation: set text value
    # ------------------------------------------------------------------
    async def async_set_text_value(self, key: str, value: str, port: str = None):
        """Set text OID (device-level or port-level)."""

        _LOGGER.debug("Setting text value for key=%s, port=%s", key, port)

        # Guard: disallow if controls not enabled
        if not self.config_entry.data.get(CONF_ENABLE_CONTROLS, False):
            _LOGGER.error("Controls are not enabled for this device")
            return False

        current_time = dt_util.utcnow().timestamp() 

        # Determine scope (device vs port)
        level = "port" if port is not None else "device"
        
        # Resolve metadata
        if level == "device":
            entry = self.validated_oids.get("device", {}).get(key, {})
            oid = entry.get("oid")
            section = "device"
            identifier = key
        else:
            entry = self.validated_oids.get("ports", {}).get(port, {}).get(key, {})
            oid = entry.get("oid")
            section = "ports"
            identifier = f"port_{port}_{key}"
        
        # Guard: no OID means invalid config
        if not oid:
            _LOGGER.error("OID not defined for %s %s", level, identifier)
            return False
        
        _LOGGER.debug("Setting %s %s to %s with OID %s", level, identifier, value, oid)

        # Perform SNMP SET (string type)
        try:
            result = await self.client.async_set(oid, value, value_type="string")
        except Exception as e:
            _LOGGER.error("SNMP set failed for %s %s (OID=%s, value=%s): %s",
                          level, identifier, oid, value, e)
            return False

        # Update local cache if successful
        if result:
            _LOGGER.info("Successfully set %s %s to %s", level, identifier, value)
            if section not in self.data:
                self.data[section] = {}
            if level == "port" and port not in self.data[section]:
                self.data[section][port] = {}
            if level == "device":
                self.data["device"][key] = value
            else:
                self.data["ports"].setdefault(port, {})
                self.data["ports"][port][key] = value
            self.data.setdefault("last_updated", {})
            self.data["last_updated"][identifier] = current_time
        else:
            _LOGGER.error("Failed to set %s %s to %s", level, identifier, value)

        return result

    # ------------------------------------------------------------------
    # Polling loop: main update
    # ------------------------------------------------------------------
    async def _async_update_data(self):
        """Fetch fresh data from SNMP device."""
        if self._aborted:
            _LOGGER.debug("Polling aborted for %s (delete/reconfigure in progress)", self.config_entry.entry_id)
            return {}

        current_time = dt_util.utcnow().timestamp()

        # Ensure self.data is always a dict (fixes NoneType errors on startup)
        if not isinstance(self.data, dict):
            self.data = {"previous": {}, "last_updated": {}}

        # Keep snapshot of previous cycle (excluding "previous" key itself)
        prev_copy = copy.deepcopy(self.data)
        prev_copy.pop("previous", None)

        # Initialize new data container for this cycle
        new_data = {
            "attributes": {},
            "device": {},
            "ports": {},
            "last_updated": {},
            "previous": prev_copy,
        }

        # Retrieve device info (used for PoE ports, manufacturer, etc.)
        poe_ports = self.config_entry.data.get(CONF_DEVICE_INFO, {}).get("poe_ports", [])

        # Use lock to avoid race conditions with concurrent polls
        async with self._lock:
            _LOGGER.debug("Acquired lock for polling")
            try:
                # ------------------------
                # DEVICE-LEVEL POLLING
                # ------------------------
                for key, entry in self.validated_oids.get("device", {}).items():
                    if key == "firmware":
                        continue  # handled separately
                    oid = entry.get("oid")
                    if not oid:
                        continue
                    try:
                        value = await self.client.async_get(oid)
                        if value and value != "No Such Object currently exists at this OID":
                            new_data["device"][key] = value
                        else:
                            new_data["device"][key] = "missing"
                            _LOGGER.debug(f"Set fallback for device {key}: missing, value={value}")
                    except Exception as e:
                        _LOGGER.error(f"Failed to fetch OID {oid} for {key}: {e}")
                        new_data["device"][key] = "error"
                    finally:
                        new_data["last_updated"][f"device_{key}"] = current_time

                # ------------------------
                # FIRMWARE POLLING (slow cycle)
                # ------------------------
                slow_interval = SLOW_UPDATE_CYCLE * self.config_entry.data[CONF_POLLING_INTERVAL]
                if current_time - self._last_slow_update >= slow_interval:
                    firmware_entry = self.validated_oids.get("attributes", {}).get("firmware") or \
                                     self.validated_oids.get("device", {}).get("firmware")
                    if firmware_entry:
                        firmware_oid = firmware_entry.get("oid")
                        if firmware_oid:
                            _LOGGER.debug(f"Polling firmware OID: {firmware_oid}")
                            value = await self.client.async_get(firmware_oid)
                            if value and value != "No Such Object currently exists at this OID":
                                if value != self._firmware_cache:
                                    # Update cache and HA device registry
                                    self._firmware_cache = value
                                    new_device_info = dict(self.config_entry.data.get(CONF_DEVICE_INFO, {}))
                                    new_device_info["firmware"] = value
                                    self.hass.config_entries.async_update_entry(
                                        self.config_entry,
                                        data={**self.config_entry.data, CONF_DEVICE_INFO: new_device_info}
                                    )
                                    device_registry = async_get_dr(self.hass)
                                    device_entry_data = {
                                        "identifiers": {(DOMAIN, self.config_entry.data[CONF_DEVICE_IP])},
                                        "manufacturer": new_device_info.get("manufacturer", "Unknown"),
                                        "model": new_device_info.get("model", "Unknown"),
                                        "name": self.config_entry.data[CONF_DEVICE_NAME],
                                        "sw_version": value,
                                        "serial_number": new_device_info.get("serial", None),
                                        "connections": {("ip", self.config_entry.data[CONF_DEVICE_IP])},
                                    }
                                    device_registry.async_get_or_create(
                                        config_entry_id=self.config_entry.entry_id,
                                        **device_entry_data
                                    )
                                new_data["last_updated"]["device_firmware"] = current_time
                            else:
                                self._firmware_cache = "Unknown"
                    self._last_slow_update = current_time
                new_data["device"]["firmware"] = self._firmware_cache

                # ------------------------
                # PORT-LEVEL POLLING
                # ------------------------
                for port_key, port_attrs in self.validated_oids.get("ports", {}).items():
                    new_data["ports"][port_key] = {}
                    for key, entry in port_attrs.items():
                        oid = entry.get("oid")
                        if not oid:
                            continue
                        _LOGGER.debug(f"Polling port {port_key} OID {key}: {oid}")
                        try:
                            value = await self.client.async_get(oid)
                            if not (isinstance(value, str) and value.startswith("No Such")):
                                new_data["ports"][port_key][key] = value
                            else:
                                _LOGGER.warning(f"Skipping port {port_key} {key} due to invalid response: {value}")
                        except Exception as e:
                            _LOGGER.error(f"Failed to fetch OID {oid} for port {port_key} {key}: {e}")
                            new_data["ports"][port_key][key] = "error"
                        finally:
                            new_data["last_updated"][f"port_{port_key}_{key}"] = current_time
                    new_data["last_updated"][f"port_{port_key}"] = current_time

                # ------------------------
                # MAC TABLE POLLING
                # ------------------------
                mac_interval = self.config_entry.data[CONF_MAC_UPDATE_CYCLE] * self.config_entry.data[CONF_POLLING_INTERVAL]
                if current_time - self._last_mac_update >= mac_interval:
                    mac_table_entry = None
                    mac_port_entry = None
                    # Locate "mac_table" and "mac_port" OIDs from device section
                    for key, entry in self.validated_oids.get("device", {}).items():
                        if entry.get("type") == "mac_table":
                            mac_table_entry = entry
                        elif entry.get("type") == "mac_port":
                            mac_port_entry = entry
                    enabled_ports = set(self.config_entry.options.get("mac_collection_ports", []))

                    if mac_table_entry and mac_port_entry:
                        mac_table_oid = mac_table_entry.get("oid")
                        mac_port_oid = mac_port_entry.get("oid")
                        if mac_table_oid and mac_port_oid:
                            # Fetch raw SNMP data
                            macs = await self.client.async_get_subtree(mac_table_oid)
                            ports = await self.client.async_get_subtree(mac_port_oid)
                            if macs and ports:
                                mac_base = mac_table_oid.lstrip(".")
                                port_base = mac_port_oid.lstrip(".")

                                mac_suffix_map = {
                                    oid[len(mac_base)+1:]: val
                                    for oid, val in macs.items()
                                    if oid.startswith(mac_base + ".")
                                }
                                port_suffix_map = {
                                    oid[len(port_base)+1:]: val
                                    for oid, val in ports.items()
                                    if oid.startswith(port_base + ".")
                                }

                                grouped_ports = {}

                                for suffix, mac_val in mac_suffix_map.items():
                                    octets = suffix.split(".")
                                    try:
                                        mac = ":".join(f"{int(o):02x}" for o in octets)
                                    except ValueError:
                                        _LOGGER.warning("Invalid MAC suffix %s, skipping", suffix)
                                        continue
                                    port = port_suffix_map.get(suffix)
                                    if not port:
                                        continue
                                    port_str = str(port)  # 🔹 keep raw numeric, not padded
                                    if enabled_ports and port_str not in enabled_ports:
                                        continue
                                    grouped_ports.setdefault(port_str, []).append(mac)

                                new_data["mac_table"] = {
                                    "last_updated": dt_util.utcnow().isoformat(),
                                    "ports": grouped_ports,  # 🔹 raw numeric ports
                                    "raw": {                  # 🔹 include untouched SNMP subtree
                                        "mac_results": macs,
                                        "port_results": ports,
                                    },
                                }
                                new_data["last_updated"]["mac_table"] = current_time
                                _LOGGER.debug("MAC table built: %s", new_data["mac_table"])
                        self._last_mac_update = current_time


                _LOGGER.info("Data update completed successfully")

            except Exception as e:
                _LOGGER.error("Error updating data: %s", e)
                raise
            finally:
                _LOGGER.debug("Released lock for polling")

        # Merge new data into coordinator state (keeps previous + last updated info)
        self.data.update(new_data)
        return self.data
