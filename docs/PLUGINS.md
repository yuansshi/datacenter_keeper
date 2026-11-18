# Extend Labkeeper

Keep organization-specific integrations in separate packages. The public core
provides entry points for status providers and floor-plan sources, plus a shared
tool registry for API/MCP automation.

Plugins run as application code. Install only plugins you trust, and keep their
credentials and actual lab data outside the source repository.

## Status providers

A provider answers how a device is doing for devices explicitly routed to it.
Generic TCP reachability is built in. Optional vendor adapters can provide richer
inventory, ports, networks, or stack membership.

A provider receives Settings in its constructor. Its poll method returns
StatusUpdate objects keyed by DeviceRef.key. Devices omitted from a poll keep
their previous observation; omission does not imply they are healthy.

This small example checks one TCP port. A richer provider should supply evidence
appropriate to the service it monitors.

~~~python
import asyncio
from datetime import datetime, timezone

from labkeeper.providers.base import DeviceRef, StatusProvider, StatusUpdate


class ExampleTcpProvider(StatusProvider):
    name = "example_tcp"
    label = "Example service reachability"

    @property
    def poll_interval(self):
        return 60

    async def poll(self, devices: list[DeviceRef]):
        updates = {}
        for device in devices:
            if not device.mgmt_ip:
                continue
            reachable = False
            try:
                _, writer = await asyncio.wait_for(
                    asyncio.open_connection(device.mgmt_ip, 443), timeout=2
                )
                writer.close()
                await writer.wait_closed()
                reachable = True
            except (OSError, TimeoutError):
                pass
            updates[device.key] = StatusUpdate(
                status="online" if reachable else "offline",
                detail="TCP 443 answered" if reachable else "TCP 443 did not answer",
                live={
                    "inventory": {
                        "checked_at": datetime.now(timezone.utc).isoformat()
                    },
                    "example_tcp": {"port": 443},
                },
            )
        return updates
~~~

Register the provider in your plugin package:

~~~toml
[project.entry-points."labkeeper.providers"]
example_tcp = "my_package.provider:ExampleTcpProvider"
~~~

Install it in the same environment as Labkeeper. For local development from
backend, after syncing the core environment:

~~~bash
uv pip install --python .venv/bin/python --no-deps -e /path/to/your-plugin
~~~

Route a device to the provider:

~~~yaml
devices:
  SWITCH_01:
    mgmt_ip: 192.0.2.10
    custom:
      labkeeper:
        provider: example_tcp
~~~

Use documentation-only addresses in fixtures and real endpoints only in private
deployment inventory.

### Metadata and network grouping

StatusUpdate.live is merged into the device's live JSON. Use the provider-neutral
inventory object for common metadata such as firmware, lan_ip, mac, network_name,
external_url, region, product_type, and checked_at. Keep additional fields in a
namespace named for your provider.

StatusUpdate.network can hold a NetworkInfo object to associate a device with a
network. StatusUpdate.stack and stack_checked can report current stack membership.
For simple file-defined groups, set custom.labkeeper.network in the inventory.

### On-demand port details

An optional device_live method returns a source name, error, and ports array.
Port fields can include id, name, enabled, status, is_uplink, speed, duplex,
errors, warnings, clients, poe, power_wh, type, vlan, allowed_vlans, neighbor,
neighbor_port, and traffic_kbps. Return the fields you actually know.

A TCP answer is not a functional test. Preserve uncertainty, read-only behavior,
and meaningful freshness when adding a new provider.

## Floor-plan sources

A source can provide live layout synchronization, browser-export import, or both.

- Live sources implement can_sync, list_sites, and fetch.
- Browser-export sources implement browser_export, matches_export, and
  parse_export. Their declared origins control the narrow cross-origin export
  endpoint.
- Raise FloorSourceError with a user-safe message for a failed fetch or import.

This fictional fixed layout demonstrates the source interface; replace fetch
with your authorized facilities/DCIM reader.

~~~python
from labkeeper.floorsources import FloorPlanSource, SourcePlan, SourceSite
from labkeeper.schemas import FloorCell, FloorPlanIn


class ExampleFloorSource(FloorPlanSource):
    name = "example_floor"
    label = "Example floor source"

    @property
    def can_sync(self):
        return True

    async def list_sites(self):
        return [SourceSite(id="example", name="Example lab", group="Example building")]

    async def fetch(self, site_id):
        return SourcePlan(
            site_id=site_id,
            plan=FloorPlanIn(
                name="Example lab",
                rows=["A", "B"],
                columns=["01", "02"],
                cells=[FloorCell(row="A", col="01", type="rack")],
            ),
        )
~~~

Register it in your plugin package:

~~~toml
[project.entry-points."labkeeper.floor_sources"]
example_floor = "my_package.source:ExampleFloorSource"
~~~

Synced cells belong to the source. Names, site labels, and rack-prefix overrides
can remain user-controlled; stopping sync detaches the plan. Browser-export code
must declare its source origins and use the supplied Labkeeper URL/user
placeholders rather than embedding a deployment address.

## Assistant and MCP tools

Tools are registered in the backend's shared tool registry with a Pydantic input
model. Use the existing inventory and booking tools as patterns. Mark mutating
tools accurately, validate input, preserve audit attribution, and enforce the
same service rules as the UI and REST API.

MCP clients can use the tool registry without an external AI account. The in-app
assistant is an optional client of those same tools.
