# Architecture

Labkeeper combines lab inventory, checks, and booking in one Python application
with a React frontend. Its default persistence is SQLite; PostgreSQL is optional.
The core is independent of any particular organization's lab or vendor account.

## Components

~~~text
Browser UI                     Scripts / MCP clients
    |                                  |
REST API /api/v1                 MCP tools
    |                                  |
    +------- services / tool registry --+
                    |
     inventory import + booking engine
     console probes + status providers
     racks + floor plans + activity log
                    |
              SQLAlchemy database
~~~

- **Inventory importer:** reads pyATS-style YAML, resolves extends inside configured
  roots, normalizes device identity and membership, and upserts the inventory.
  Serial identity lets several testbeds share one physical device. Missing
  sources are soft-removed so history can survive; manual rack placement is kept.
- **Booking engine:** uses half-open start/end time windows. Overlapping windows
  conflict when their reservations share exclusive devices. Partial bookings hold
  only the selected devices or stack members.
- **Status providers:** poll devices assigned to them and return status, evidence,
  and optional network/stack metadata. Generic TCP reachability and a simulated
  demo provider work without cloud accounts. Meraki is an optional adapter.
- **Console tester:** opens direct or jump-host connections, negotiates Telnet
  where needed, sends a newline, classifies the response, and closes its session.
  It distinguishes device prompts, login/password prompts, busy lines, refused
  connections, and timeouts.
- **Layout tools:** maintain rack placements and typed floor-plan grids. Manual
  editing and grid imports are local features. Optional AI can interpret images
  or PDFs; floor-plan source plugins can supply external layouts.
- **API and MCP:** expose the same service functions to the UI and automation.
  Input validation and audit events belong at these service boundaries.
- **Optional assistant:** uses the tool registry when explicitly enabled and
  configured with an external AI account and model.

## Data model

The main entities are Network, Rack, FloorPlan, Device, Testbed, TestbedDevice,
Booking, Event, and ImportSource. A Device can reference a parent logical stack.
TestbedDevice records whether the membership is exclusive for conflict checks.

The application creates tables on startup and supports limited additive schema
changes. Plan explicit migrations and database backups for more substantial
schema changes in a deployment.

## Extension boundaries

Status providers register through the labkeeper.providers package entry point.
Floor-plan sources use labkeeper.floor_sources. Provider-specific code belongs in
those adapters, not in booking or inventory assumptions. The frontend can display
provider-neutral inventory metadata and optional provider details.

See [PLUGINS.md](PLUGINS.md) for interfaces and examples.

## Identity, evidence, and policy

A user-name header attributes actions; it is not built-in authentication.
Deployments must establish trustworthy identity and protect backend access.

Health state and booking state remain separate. A reservation is not a
certification of readiness, and a failed probe does not prove a device is
physically powered off. Keep evidence source and limits visible when extending
the workflow.

See [SECURITY.md](SECURITY.md) for deployment boundaries and
[CONFIGURATION.md](CONFIGURATION.md) for configurable policies.
