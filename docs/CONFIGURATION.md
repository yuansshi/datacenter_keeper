# Configure your lab

Start with [labkeeper.example.yaml](../labkeeper.example.yaml). Relative paths in
the configuration are resolved from that file. Environment variables override
configuration values using LABKEEPER_ followed by nested keys separated by __.

For example, LABKEEPER_REACHABILITY__POLL_INTERVAL=30 changes the TCP polling
interval. Keep credentials in a private environment or secret store, not in Git.

## Inventory

Set testbeds.paths to one or more YAML folders. Labkeeper understands pyATS-style
devices, connections, credentials, topology, and extends; installing pyATS is not
required to read the inventory.

A leaf YAML definition becomes a testbed. Referenced base files contribute
devices. Device identity is the serial when provided, which lets several testbeds
share the same hardware. Changing or removing a source is reflected on the next
import; historical bookings remain in the database.

Set testbeds.include to names, file stems, relative paths, or globs to restrict
which testbeds are tracked. An empty list includes all leaf testbeds. Use exclude
for archive or temporary files. Do not put real inventory or credentials in the
public source repository.

### Generic device fields

~~~yaml
testbed:
  name: example_bench

devices:
  SWITCH_01:
    type: switch
    serial: LAB-SWITCH-001
    model: Example-24P
    connections:
      mgmt: {protocol: ssh, ip: 192.0.2.10}
      console: {protocol: telnet, ip: 192.0.2.20, port: 2001}
    custom:
      labkeeper:
        provider: reachability
        network: Example lab
        rack: ROOM_A
        position: 12
        u_height: 1
        tags: [example, switching]
~~~

Replace the documentation addresses with endpoints that you own. A logical stack
can use chassis_type: stack with separate physical members and a shared stack
name. See the included demo definitions for complete examples.

The generic provider is selected explicitly above. Devices with a management
address can use TCP reachability by default; a serial or model name does not
automatically opt a device into a vendor cloud.

## Health and console checks

The TCP provider checks reachability on the configured ports. It does not
authenticate to the service or certify the device's function. Providers can
report online, alerting, offline, dormant, or unknown; unknown is useful when
evidence is missing.

Console probes use the documented protocol, host, and port. A connection can
answer with a device prompt, a login/password prompt, a busy-line message, or
nothing recognizable. Keep those results distinct. The built-in probe does not
log into the device and execute an acceptance test.

An SSH proxy can route a console connection through a jump host declared in the
testbed. Keep its credentials private. Use console.verify_host_keys: true and
maintain the application's SSH known-hosts data for real SSH endpoints.

Device health and reservation availability are separate. A free booking slot is
not proof that a device is healthy.

## Booking policy

Bookings use time windows and exclusive device membership. Overlapping
reservations conflict when they share exclusive hardware, even if they use
different testbed names. Shared support-device types remain visible without
creating that exclusive conflict.

The example configuration uses courtesy releases: another teammate can release a
forgotten booking, and the action is attributed in the log.

For owner-only releases:

~~~yaml
booking:
  release_policy: owner
  allow_force_release: false
~~~

This is a booking rule, not a replacement for authentication. The application must
receive trustworthy identity from your deployment. Maintenance flags can prevent
new bookings while preserving inventory visibility.

## Optional Meraki adapter

No Meraki account is needed for the core or demo. To enable the adapter
deliberately:

~~~yaml
meraki:
  enabled: true
  api_key_env: MERAKI_DASHBOARD_API_KEY
  base_url: https://api.meraki.com/api/v1
  org_ids: []  # Set your own organization IDs to restrict collection.
~~~

Provide your own key in the server environment:

~~~bash
export MERAKI_DASHBOARD_API_KEY=...
~~~

Route only the devices you want the adapter to monitor:

~~~yaml
devices:
  SWITCH_01:
    type: switch
    serial: YOUR-DEVICE-SERIAL
    custom:
      labkeeper:
        provider: meraki
~~~

Use serials returned by your own provider inventory. Regional API hosts and
credentials can be configured through meraki.regions; this repository supplies
no organization IDs, account credentials, or private cloud mappings. The adapter
uses read-only inventory/status requests.

## Optional AI features

The assistant and image/PDF floor-plan interpretation need the AI extra and your
own API account. The core inventory, booking, TCP checks, console probes, manual
floor plans, API, and MCP tools do not.

~~~bash
cd backend
uv sync --extra ai
export ANTHROPIC_API_KEY=...
export LABKEEPER_AGENT__ENABLED=true
export LABKEEPER_AGENT__MODEL=your-supported-model
~~~

Choose a model supported by the service you configure; no model name is assumed.
Selected input and tool results may be sent to that service when AI features run.

## Docker with your own inventory

The default Compose setup is a local demo. To run your own lab, keep the data
volume, mount your inventory read-only, and override the command and settings in a
private Compose override. For example:

~~~yaml
services:
  labkeeper:
    command: [serve]
    environment:
      LABKEEPER_TESTBEDS__PATHS: '["/testbeds"]'
    volumes:
      - ./my-testbeds:/testbeds:ro
~~~

Store this as a local override, create my-testbeds, and add your definitions.
Configure optional credentials privately. Follow [security guidance](SECURITY.md)
before exposing the server beyond localhost.

## Storage and customization

The default database is SQLite under data_dir. Back up the database, inventory,
configuration, and uploaded floor plans together. A database_url can select
PostgreSQL when installed with the postgres extra.

Use a provider plugin for another status source, a floor-plan source plugin for
facilities data, or the REST/MCP APIs for automation. See [PLUGINS.md](PLUGINS.md).
