# SNMP Integration (`snmp_r1d1`)

A **custom Home Assistant integration** that provides monitoring and control of SNMP-enabled network devices such as managed switches, routers, and other infrastructure.  
This integration is fully GUI-driven (no YAML configuration required) and supports **SNMP v1, v2c, and v3** with vendor-specific OID profiles.

---

## ✨ Features

- **GUI configuration only** – no YAML editing required.
- **SNMP protocol support**:
  - v1 / v2c (community strings)
  - v3 (user-based, with authentication and privacy protocols)
- **Polling & update cycle**:
  - Configurable polling interval for sensors
  - Independent update cycle for MAC address tables
  - Slow-cycle updates for static attributes (firmware, model, serial, etc.)
- **Entity types supported**:
  - Sensors (numeric, text, data rate, counters with `diff` calculation)
  - Binary Sensors (on/off states with custom value maps)
  - Switches (device-wide and per-port control, including PoE control)
  - Text entities (readable and writable values via SNMP SET)
- **Per-port monitoring**:
  - Port admin/oper status
  - Traffic in/out (with diff calculation)
  - Port errors (input/output counters)
  - Port speed and name/alias
- **PoE support**:
  - Device PoE budget and usage
  - Per-port PoE enable/disable switches
  - Per-port PoE power and status
- **MAC address tracking**:
  - Global MAC collection switch
  - Per-port MAC collection switches
  - Normalized MAC table entities
- **Vendor device profiles included**:
  - **Generic MIB-II** (basic SNMP support)
  - **Zyxel GS1920 series**
  - **Ubiquiti EdgeRouter**
  - Easy to extend with new devices by adding Python files in `devices/`

---

## 📂 Project Structure

```
custom_components/snmp_r1d1/
├── __init__.py              # Integration setup/unload
├── manifest.json            # Integration metadata
├── const.py                 # Core constants
├── coordinator.py           # Data update coordinator
├── snmp.py                  # Async SNMP client using pysnmp.hlapi.asyncio
├── config_flow.py           # GUI setup flow (user, settings, credentials, test, discover, validate)
├── device_loader.py         # Loader for vendor device definition files
├── helpers.py               # Utility helpers (entity names, value mapping)
├── sensor.py                # Sensors (device-level & per-port)
├── binary_sensor.py         # Binary sensors (device-level & per-port)
├── switch.py                # Switches (device-level, per-port, MAC collection)
├── text.py                  # Text entities (device-level & per-port)
├── mac_table.py             # MAC table entity/switch logic
├── translations/
│   └── en.json              # English translations for config flow & options
└── devices/
    ├── generic.py           # Generic SNMP device definition (MIB-II)
    ├── zyxel_gs1920.py      # Zyxel GS1920 device profile
    └── ubnt_edgerouter.py   # Ubiquiti EdgeRouter profile
```

---

## ⚙️ Installation

1. Copy the `snmp_r1d1` folder into your Home Assistant `custom_components` directory:
   ```
   custom_components/snmp_r1d1/
   ```
   so that the path is:
   ```
   <config>/custom_components/snmp_r1d1/manifest.json
   ```

2. Restart Home Assistant.

3. Go to **Settings → Devices & Services → Add Integration**.

4. Search for **SNMP Integration (snmp_r1d1)** and follow the wizard.

---

## 🧭 Configuration Flow

The integration is set up entirely via the **Home Assistant GUI**:

1. **Basic Info**  
   - Device IP  
   - Device name (friendly name)  
   - Device type (select from vendor profiles) : files in /devices
   - Entity prefix (used for unique entity IDs)

2. **Settings**  
   - SNMP version (v1, v2c, v3)  
   - Polling interval (seconds)  
   - MAC update cycle (multiplier of polling interval)  
   - Enable controls (if disabled, switch/text entities fall back to read-only)  
   - Custom OIDs (optional `name:oid` pairs, comma-separated)

3. **Credentials**  
   - v1/v2c: Read community, optional write community  
   - v3: Username, authentication protocol/key, privacy protocol/key  

4. **Connection Test**  
   - Validates SNMP connectivity with the device using `access_test_oid`.  
   - If controls are enabled, performs a read/write/reset test.

5. **Discovery**  
   - Queries device OIDs for model, serial, firmware, port count, PoE capabilities, etc.

6. **Config Parsing**  
   - Builds entity definitions for attributes, device sensors, and per-port sensors.

7. **OID Validation**  
   - Confirms which OIDs are valid on the device.  
   - Skips unsupported OIDs automatically.

8. **Entity Presentation**  
   - Shows discovered entities grouped by type before final confirmation.

9. **Finish**  
   - Integration is installed and entities are created.  

Reconfigure or delete is available from the integration’s options menu.

---

## 🛠️ Adding Vendor Profiles

To add support for a new device:

1. Create a new file in `custom_components/snmp_r1d1/devices/` named after the device (e.g. `cisco_ios.py`).  
2. Define the required dictionaries: `config`, `attributes`, `device`, and optionally `ports`.  
3. Restart Home Assistant and the new device type will appear in the configuration wizard.

Options for managing SNMP Data
	# OPTIONALS: Calculation, Unit, and Value Mapping Options for OIDs.
	# These options customize how raw SNMP values are processed and displayed for sensors, binary sensors, and switches.
	# Structure:
	#
	# - "calc": (Optional) Defines the calculation applied to the raw value.
	#   - Types:
	#     - "direct": No calculation, returns raw value (default, optional).
	#     - "diff": Rate of change (current - previous) / elapsed time, used for counters.
	#   - Note: To invert a diff (e.g., for upload/download inversion), use "math": "-x" together with calc="diff".
	#
	# - "math": (Optional) Apply a math formula string with variable x to the final value (after calc if present, or directly to the raw SNMP value if no calc is used)
	#   - Examples:
	#       "x/100"       → divide by 100
	#       "100*x"       → multiply by 100
	#       "(x/2)*10"    → divide by 2 then multiply by 10
	#       "-x"          → invert the value
	#   - Any valid Python math expression is allowed, using `x` as the variable.
	#   - Supports standard math functions: sin, cos, log, sqrt, etc.
	#
	# - "unit": (Optional) Specifies the unit of measurement (e.g., "W", "%", "Mbit/s").
	#   - Overrides native_unit_of_measurement.
	#   - If omitted, falls back to a unit derived from device_class (e.g., "W" for power, "°C" for temperature).
	#
	# - "vmap": (Optional) Maps raw SNMP values to states or labels (value mapping).
	#   - For sensor: {"<raw_value>": "<state>"} or {"<operator><value>": "<state>"}.
	#       Operators: "<", ">" for numeric comparisons.
	#       Example: {"0": "off", ">0": "delivering"}
	#   - For binary_sensor: {"on": "<value|list>", "off": "<value|list>"}.
	#       Lists can contain multiple values or comparisons, e.g., {"on": [">0"], "off": ["0"]}.
	#   - For switch: {"on": "<value>", "off": "<value>"}.
	#       Only exact values allowed, since switches require precise states.
	#
	# - Validation: calc, math, unit, and vmap are validated in config_flow during parse_config.
	#
	# Examples:
	#   "poe_usage": {"oid": "...", "type": "sensor", "calc": "diff", "math": "x/1000", "unit": "W"}  # diff in mW → W
	#   "cpu_usage": {"oid": "...", "type": "sensor", "unit": "%"}                                   # percentage
	#   "port_speed": {"oid": "...", "type": "sensor", "device_class": "data_rate", "math": "x/1000000", "unit": "Mbit/s"}  # bps → Mbps
	#   "poe_status": {"oid": "...", "type": "sensor", "vmap": {"0": "off", "1": "waiting", ">1": "delivering"}}            # multi-state
	#   "port_status": {"oid": "...", "type": "binary_sensor", "vmap": {"on": [">0"], "off": ["0"]}}                        # binary with comparison
	#   "port_admin": {"oid": "...", "type": "switch", "vmap": {"on": "1", "off": "2"}}                                     # binary switch
	#
	# INTEGER {disabled(1), searching(2), deliveringPower(3), fault(4), test(5), otherFault(6)}