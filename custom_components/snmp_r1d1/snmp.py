"""SNMP client for asynchronous operations using pysnmp.hlapi.asyncio."""

import asyncio
import logging
from dataclasses import dataclass
from pysnmp.smi import view
from pysnmp.hlapi.asyncio import (
    get_cmd,          # SNMP GET request
    set_cmd,          # SNMP SET request
    next_cmd,         # SNMP GETNEXT (walk one OID at a time)
    bulk_cmd,         # SNMP GETBULK (multiple OIDs in one request)
    walk_cmd,         # High-level subtree walk using GETNEXT
    bulk_walk_cmd,    # High-level subtree walk using GETBULK
    #bulk_table_cmd,   # Not available in asyncio (sync API only)
    SnmpEngine,       # Core SNMP engine
    CommunityData,    # SNMP v1/v2c auth
    UsmUserData,      # SNMP v3 auth
    ContextData,      # SNMP context (used mainly in v3)
    ObjectType,       # Represents an SNMP OID and its value
    ObjectIdentity,   # Identifies an OID
    Integer,          # SNMP INTEGER type
    OctetString,      # SNMP OCTET STRING type
    UdpTransportTarget,   # Transport target for IPv4
    Udp6TransportTarget,  # Transport target for IPv6
    usmNoAuthProtocol,    # SNMP v3: No authentication
    usmHMACMD5AuthProtocol,   # SNMP v3: MD5 authentication
    usmHMACSHAAuthProtocol,   # SNMP v3: SHA authentication
    usmNoPrivProtocol,        # SNMP v3: No privacy
    usmAesCfb128Protocol,     # SNMP v3: AES-128 privacy
    usm3DESEDEPrivProtocol,   # SNMP v3: 3DES privacy
)

_LOGGER = logging.getLogger(__name__)

_snmp_engine = None  # Cached SNMP engine instance


def _init_snmp_engine() -> SnmpEngine:
    """Blocking initialization of SnmpEngine — must be called from executor thread."""
    engine = SnmpEngine()
    # Create a MIB view controller for resolving OIDs
    mib_view_controller = view.MibViewController(
        engine.message_dispatcher.mib_instrum_controller.get_mib_builder()
    )
    # Cache the controller in the engine for reuse
    engine.cache["mibViewController"] = mib_view_controller
    # Load default MIBs (this triggers the blocking filesystem I/O)
    mib_view_controller.mibBuilder.load_modules()
    _LOGGER.debug("Initialized and cached SNMP engine with preloaded MIBs")
    return engine


async def async_get_snmp_engine() -> SnmpEngine:
    """Return a cached SnmpEngine, initializing it in a thread executor if needed."""
    global _snmp_engine
    if _snmp_engine is None:
        loop = asyncio.get_event_loop()
        _snmp_engine = await loop.run_in_executor(None, _init_snmp_engine)
    return _snmp_engine


@dataclass
class SnmpCredentials:
    """Structure for SNMP version and credentials."""

    version: str
    read_community: str = None   # v1/v2c read-only community string
    write_community: str = None  # v1/v2c read-write community string
    username: str = None         # v3 username
    auth_protocol: str = None    # v3 auth protocol ("SHA", "MD5", or None)
    auth_key: str = None         # v3 auth key
    privacy_protocol: str = None # v3 privacy protocol ("AES", "3DES", or None)
    privacy_key: str = None      # v3 privacy key

    def __post_init__(self):
        """Validate credentials based on version."""
        if self.version in ["v1", "v2c"]:
            # v1/v2c requires a community string
            if not self.read_community:
                raise ValueError("Read community string is required for SNMP v1/v2c")
        elif self.version == "v3":
            # v3 requires at least a username
            if not self.username:
                raise ValueError("Username is required for SNMP v3")
        else:
            raise ValueError(f"Unsupported SNMP version: {self.version}")


class SnmpClient:
    """Client for SNMP operations."""

    def __init__(self, host: str, credentials: SnmpCredentials):
        self.host = host
        self.credentials = credentials
        self.engine = None  # Initialized lazily via create()
        self.context = ContextData()  # Context data (mainly for v3)

    @classmethod
    async def create(cls, host: str, credentials: SnmpCredentials) -> "SnmpClient":
        """Async factory: create and fully initialize an SnmpClient."""
        client = cls(host, credentials)
        client.engine = await async_get_snmp_engine()
        return client

    # ----------------------
    # Helper: Authentication
    # ----------------------
    def _get_auth_data(self, operation: str = "read"):
        """Configure authentication data for the specified operation."""
        version = self.credentials.version
        if version in ["v1", "v2c"]:
            # SNMP v1/v2c uses community strings
            community = (
                self.credentials.write_community
                if operation == "write" and self.credentials.write_community
                else self.credentials.read_community
            )
            if not community:
                raise ValueError(
                    f"No {'write' if operation == 'write' else 'read'} community string provided"
                )
            return CommunityData(community, mpModel=0 if version == "v1" else 1)
        elif version == "v3":
            # SNMP v3: select auth and priv protocols
            auth_proto = (
                usmHMACSHAAuthProtocol
                if self.credentials.auth_protocol == "SHA"
                else usmHMACMD5AuthProtocol
                if self.credentials.auth_protocol == "MD5"
                else usmNoAuthProtocol
            )
            priv_proto = (
                usmAesCfb128Protocol
                if self.credentials.privacy_protocol == "AES"
                else usm3DESEDEPrivProtocol
                if self.credentials.privacy_protocol == "3DES"
                else usmNoPrivProtocol
            )
            return UsmUserData(
                self.credentials.username,
                authKey=self.credentials.auth_key,
                privKey=self.credentials.privacy_key,
                authProtocol=auth_proto,
                privProtocol=priv_proto,
            )
        raise ValueError("Unsupported SNMP version")

    # ---------------------
    # Helper: Prepare args
    # ---------------------
    async def _prepare_snmp_args(self, oid, operation="read", value=None, value_type="string"):
        """Prepare common SNMP call arguments."""
        auth_data = self._get_auth_data(operation)
        # Create transport target (UDP/IPv4, port 161, timeout 5s)
        transport = await UdpTransportTarget.create((self.host, 161), 5)
        context = self.context

        # If writing, wrap value in appropriate SNMP type
        if value is not None:
            if value_type == "integer":
                val = Integer(value)
            elif value_type == "string":
                val = OctetString(value)
            else:
                raise ValueError(f"Unsupported value_type: {value_type}")
            obj = ObjectType(ObjectIdentity(oid), val)
        else:
            obj = ObjectType(ObjectIdentity(oid))

        return self.engine, auth_data, transport, context, obj

    # ----------------------------
    # Helper: Parse var_binds
    # ----------------------------
    def _parse_var_binds(self, var_binds, normalized_base_oid, result, source="subtree"):
        """Parse var_binds into result dict (optimized for bulk_walk)."""
        last_oid = None

        for oid_obj, val_obj in var_binds:
            oid_str = str(oid_obj)
            value = val_obj.prettyPrint() if hasattr(val_obj, "prettyPrint") else str(val_obj)
            # Save into result dict
            result[oid_str] = value
            last_oid = oid_str

            _LOGGER.debug("%s decoded oid=%s value=%s (val_type=%s)",
                        source, oid_str, value, type(val_obj))

        return last_oid

    # -----------------
    # SNMP core methods
    # -----------------
    async def async_get(self, oid, retries=1):
        """Retrieve a single OID value."""
        _LOGGER.debug(f"Attempting SNMP Get: OID={oid}")

        for attempt in range(retries + 1):
            try:
                args = await self._prepare_snmp_args(oid, operation="read")
                error_indication, error_status, error_index, var_binds = await get_cmd(
                    *args, lookupMib=False
                )

                if error_indication:
                    raise Exception(error_indication)
                if error_status:
                    raise Exception(error_status.prettyPrint())
                if not var_binds or var_binds[0][1] is None:
                    return None
                return str(var_binds[0][1])  # Return the value as string
            except Exception as e:
                _LOGGER.error(f"SNMP get attempt {attempt + 1} failed for OID {oid}: {e}")
                if attempt == retries:
                    return None
                await asyncio.sleep(5)

    async def async_set(self, oid, value, value_type="string", retries=1):
        """Set an OID value and verify with a follow-up get."""
        for attempt in range(retries + 1):
            try:
                args = await self._prepare_snmp_args(
                    oid, operation="write", value=value, value_type=value_type
                )
                error_indication, error_status, error_index, var_binds = await set_cmd(
                    *args, lookupMib=False
                )

                if error_indication or error_status:
                    raise Exception(error_indication or error_status.prettyPrint())

                # Verify with a get
                verified_value = await self.async_get(oid, retries=1)
                if verified_value is None:
                    return False
                if verified_value != str(value):
                    return False
                return True
            except Exception as e:
                _LOGGER.error(f"SNMP set attempt {attempt + 1} failed for OID {oid}: {e}")
                if attempt == retries:
                    return False
                await asyncio.sleep(5)

    async def async_getnext(self, oid, retries=1):
        """Retrieve the next OID value (walk to the next OID)."""
        for attempt in range(retries + 1):
            try:
                args = await self._prepare_snmp_args(oid)
                error_indication, error_status, error_index, var_binds = await next_cmd(
                    *args, lexicographicMode=False, lookupMib=False
                )

                if error_indication:
                    raise Exception(error_indication)
                if error_status:
                    raise Exception(error_status.prettyPrint())

                if not var_binds or not var_binds[0]:
                    return None

                # Unpack the first ObjectType/tuple
                obj = var_binds[0]
                if isinstance(obj, ObjectType):
                    oid_obj, val_obj = obj
                elif isinstance(obj, tuple) and len(obj) == 2:
                    oid_obj, val_obj = obj
                else:
                    _LOGGER.error("async_getnext unexpected obj=%r (type=%s)", obj, type(obj))
                    return None

                if val_obj is None:
                    return None

                next_oid = str(oid_obj)
                value = val_obj.prettyPrint() if hasattr(val_obj, "prettyPrint") else str(val_obj)

                _LOGGER.debug("async_getnext parsed oid=%s value=%s (val_type=%s)", next_oid, value, type(val_obj))
                return {"oid": next_oid, "value": value}

            except Exception as e:
                _LOGGER.error("SNMP get_next attempt %d failed for OID %s: %s", attempt + 1, oid, e)
                if attempt == retries:
                    return None
                await asyncio.sleep(5)

        return None

    async def async_getbulk(self, oid, non_repeaters=0, max_repetitions=10, retries=1):
        """Retrieve multiple OID values in bulk (one-shot)."""
        result = {}
        normalized_base_oid = oid.lstrip(".")
        _LOGGER.warning(
            "async_getbulk called for OID=%s (non_repeaters=%d, max_repetitions=%d)",
            oid, non_repeaters, max_repetitions
        )

        for attempt in range(retries + 1):
            try:
                engine, auth_data, transport, context, _ = await self._prepare_snmp_args(oid)

                error_indication, error_status, error_index, var_binds_table = await bulk_cmd(
                    engine, auth_data, transport, context,
                    non_repeaters, max_repetitions,
                    ObjectType(ObjectIdentity(oid)),
                    lookupMib=False
                )
                _LOGGER.warning("async_getbulk raw var_binds_table for OID=%s: %r", oid, var_binds_table)

                if error_indication:
                    raise Exception(error_indication)
                if error_status:
                    raise Exception(error_status.prettyPrint())

                for var_binds in var_binds_table or []:
                    self._parse_var_binds(var_binds, normalized_base_oid, result, source="getbulk")

                return result if result else None

            except Exception as e:
                _LOGGER.error("SNMP getbulk attempt %d failed for OID %s: %s", attempt + 1, oid, e)
                if attempt == retries:
                    return result if result else None
                await asyncio.sleep(5)

        return result if result else None
        
    async def async_get_subtree(self, oid, retries=1, max_repetitions=25):
        """Retrieve all values in the OID subtree using pysnmp bulk walk."""
        result = {}
        normalized_base_oid = oid.lstrip(".")

        for attempt in range(retries + 1):
            try:
                engine, auth_data, transport, context, _ = await self._prepare_snmp_args(oid)

                # bulk_walk_cmd is an async generator
                async for error_indication, error_status, error_index, var_binds in bulk_walk_cmd(
                    engine,
                    auth_data,
                    transport,
                    context,
                    0,                        # nonRepeaters
                    max_repetitions,          # maxRepetitions
                    ObjectType(ObjectIdentity(oid)),  # required varBind
                    lexicographicMode=False,
                    lookupMib=False,
                ):
                    if error_indication:
                        raise Exception(error_indication)
                    if error_status:
                        raise Exception(error_status.prettyPrint())

                    self._parse_var_binds(var_binds, normalized_base_oid, result, source="bulk_walk")

                return result if result else None

            except Exception as e:
                _LOGGER.error(
                    "SNMP bulk_walk attempt %d failed for OID %s: %s",
                    attempt + 1, oid, e
                )
                if attempt == retries:
                    return result if result else None
                await asyncio.sleep(5)

        return result if result else None

    async def async_get_subtree_idx_list(self, base_oid, max_ports=50, retries=1):
        """Retrieve a list of port indices in the subtree using get_next.

        Typically used during config flow to auto-discover port indices.
        """
        valid_indices = []
        current_oid = base_oid
        port_count = 0
        normalized_base_oid = base_oid.lstrip(".")

        for attempt in range(retries + 1):
            try:
                while port_count < max_ports:
                    args = await self._prepare_snmp_args(current_oid)
                    error_indication, error_status, error_index, var_binds = await next_cmd(
                        *args, lexicographicMode=False, lookupMib=False
                    )

                    if error_indication or error_status:
                        break
                    if not var_binds or not var_binds[0] or var_binds[0][1] is None:
                        break

                    obj = var_binds[0]
                    next_oid = str(obj[0])

                    # Split OID to check structure
                    oid_components = next_oid.split(".")
                    if len(oid_components) <= len(normalized_base_oid.split(".")):
                        break

                    base_check = ".".join(oid_components[: len(normalized_base_oid.split("."))])
                    if base_check != normalized_base_oid:
                        break

                    try:
                        port_index = int(oid_components[-1])
                        if port_index > max_ports:
                            break
                        if str(port_index) not in valid_indices:
                            valid_indices.append(str(port_index))
                            port_count += 1
                    except ValueError:
                        break

                    current_oid = next_oid

                return sorted(valid_indices)
            except Exception as e:
                _LOGGER.error(f"SNMP get_subtree_idx_list attempt {attempt + 1} failed for OID {current_oid}: {e}")
                if attempt == retries:
                    return sorted(valid_indices)
                await asyncio.sleep(5)

        return sorted(valid_indices)
