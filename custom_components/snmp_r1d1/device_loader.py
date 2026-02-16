"""
Device loader for snmp_r1d1 integration.

This module is responsible for dynamically loading device definition files
from the ./devices directory. Each device definition file (e.g. zyxel_gs1920.py,
ubnt_edgerouter.py, generic.py) must export dictionaries named:

    - config     : required, must include at least "access_test_oid"
    - attributes : required, contains SNMP OIDs for device attributes
    - device     : required, contains SNMP OIDs for device-wide sensors/switches
    - ports      : optional, contains per-port SNMP OIDs

The loader validates each file, ensures required sections exist, and applies
normalization (e.g. ensuring OIDs always start with "."). The resulting device
definitions are returned in a dictionary keyed by filename (without extension).
"""

import importlib.util
import pkgutil
import pathlib
import logging
from typing import Dict, Any

_LOGGER = logging.getLogger(__name__)


def load_devices() -> Dict[str, Dict[str, Any]]:
    """
    Load device definitions from the ./devices directory (relative to this file).

    Returns:
        Dict[str, Dict[str, Any]]: A mapping of device filename → device definition,
                                   where each definition is itself a dict containing
                                   "config", "attributes", "device", and optionally "ports".
    """
    devices: Dict[str, Dict[str, Any]] = {}

    # Compute the path to the ./devices directory relative to this file
    devices_path = pathlib.Path(__file__).parent / "devices"

    # Iterate over all Python modules in ./devices
    for m in pkgutil.iter_modules([str(devices_path)]):
        # Skip hidden or private modules (names starting with "_")
        if m.name.startswith("_"):
            continue

        try:
            # Dynamically load the device module
            spec = importlib.util.spec_from_file_location(
                m.name, devices_path / f"{m.name}.py"
            )
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)  # type: ignore
        except Exception as e:
            _LOGGER.error("Failed to import device module '%s': %s", m.name, e)
            continue

        # Collect the sections defined in the module
        sections: Dict[str, Dict[str, Any]] = {}
        for section in ["config", "attributes", "device", "ports"]:
            if hasattr(mod, section):
                sec = getattr(mod, section)
                if not isinstance(sec, dict):
                    _LOGGER.error(
                        "Device '%s' section '%s' must be a dict, skipping file",
                        m.name,
                        section,
                    )
                    break

                # 🔹 Normalize OIDs inside this section:
                # - OID must always be a string that either starts with "." or is "na"
                # - If it is a non-empty string and not "na", we ensure it starts with "."
                for key, entry in sec.items():
                    if isinstance(entry, dict) and "oid" in entry:
                        oid = entry["oid"]
                        if isinstance(oid, str) and oid not in ("na", ""):
                            if not oid.startswith("."):
                                entry["oid"] = f".{oid}"
                                _LOGGER.debug(
                                    "Normalized OID for device '%s', section '%s', key '%s': %s → %s",
                                    m.name,
                                    section,
                                    key,
                                    oid,
                                    entry["oid"],
                                )

                sections[section] = sec

        # Ensure required sections exist
        for required in ["config", "attributes", "device"]:
            if required not in sections:
                _LOGGER.error(
                    "Device '%s' missing required section '%s', skipping file",
                    m.name,
                    required,
                )
                break
        else:
            # Validate that the config section includes a valid access_test_oid
            config = sections["config"]
            if "access_test_oid" not in config or not isinstance(
                config["access_test_oid"], str
            ):
                _LOGGER.error(
                    "Device '%s' config missing mandatory key 'access_test_oid', skipping file",
                    m.name,
                )
                continue

            # Prevent duplicate device names
            if m.name in devices:
                _LOGGER.error(
                    "Duplicate device filename '%s', skipping file",
                    m.name,
                )
                continue

            # Save the validated and normalized device definition
            devices[m.name] = sections
            _LOGGER.info("Loaded device definition: %s", m.name)

    return devices
