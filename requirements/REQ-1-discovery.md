# REQ-1: Discovery & Presence (SSDP / UPnP Advertisement)

| Requirement ID | Title | Status |
|---|---|---|
| REQ-1.1 | SSDP Presence | ✅ Implemented |
| REQ-1.2 | SSDP NOTIFY (Alive/Byebye) | ✅ Implemented |
| REQ-1.3 | M-SEARCH Response | ✅ Implemented |
| REQ-1.4 | Device Description XML | ✅ Implemented |
| REQ-1.5 | Correct LOCATION URL | ✅ Implemented |
| REQ-1.6 | SSDP TTL | ❌ Not applicable |

---

## REQ-1.1: SSDP Presence

**Status: ✅ Implemented**

The server MUST announce itself as a UPnP MediaServer device on the local network so that DLNA clients (specifically Reciva-based radios) can discover it.

### Details
- The server must register a UPnP device of type `urn:schemas-upnp-org:device:MediaServer:1`.
- The device must respond to SSDP M-SEARCH queries and send periodic NOTIFY announcements.
- The server must be discoverable by any standard UPnP control point on the same network segment.

---

## REQ-1.2: SSDP NOTIFY (Alive / Byebye)

**Status: ✅ Implemented** (all entries sent every 5s for instant Reciva discovery)

The server must send periodic SSDP NOTIFY `ssdp:alive` messages so that clients discover it without actively searching.

### Details
- NOTIFY multicast messages must be sent to `239.255.255.250:1900` frequently enough that Reciva radios discover the server promptly (recommended interval: every 5 seconds).
- Messages must include the LOCATION URL pointing to the device description XML.
- All standard NT/USN entries (rootdevice, UUID, device type, ContentDirectory:1, ConnectionManager:1) must be advertised.
- On shutdown, the server must send `ssdp:byebye` messages to allow clients to promptly remove it from their device list.

---

## REQ-1.3: M-SEARCH Response

**Status: ✅ Implemented**

The server must respond to SSDP M-SEARCH queries from clients.

### Details
- Must respond to M-SEARCH with `st: urn:schemas-upnp-org:device:MediaServer:1`.
- Must also respond as root device (M-SEARCH `st: upnp:rootdevice`).
- Response must be unicast to the requesting client's address.
- Response must include the LOCATION URL, USN, and cache-control headers.

---

## REQ-1.4: Device Description XML

**Status: ✅ Implemented**

The server must serve a valid UPnP device description XML (`/device.xml`) that describes the MediaServer device and its services.

### Details
- The device description must list:
  - Device type: `urn:schemas-upnp-org:device:MediaServer:1`
  - Friendly name (user-configurable, default: "Internet Radio Stream")
  - Manufacturer: "reciva-dlna-stream"
  - Model name (includes version)
  - UDN (unique per instance, generated as UUID)
  - URL to the device XML itself
  - Service list: ContentDirectory:1, ConnectionManager:1
  - Each service must include SCPD URL, control URL, and event subscription URL.

---

## REQ-1.5: Correct LOCATION URL

**Status: ✅ Implemented**

The SSDP LOCATION URL must contain the correct IP and port of the HTTP server.

### Details
- The server binds to port 0 (auto-assign) by default. The LOCATION URL must contain the actual port, not 0.
- The IP address in the LOCATION URL must be the server's local network IP (not 127.0.0.1), auto-detected at startup.
- The startup sequence must guarantee that the LOCATION URL is correct before SSDP begins advertising.

---

## REQ-1.6: SSDP TTL

**Status: ❌ Not applicable**

The SSDP multicast TTL was previously set to 4 (UPnP Device Architecture v2.0 requirement) via a monkey-patch. The patch has been removed because:

- On single-subnet deployments (no routers between server and client), TTL 2 works fine.
- The monkey-patch was fragile (patching library internals).
- The upstream library's default TTL of 2 is sufficient for all known Reciva radio use cases.
- If multi-hop SSDP propagation is ever needed, the library default should be configured through a proper upstream setting rather than a monkey-patch.
