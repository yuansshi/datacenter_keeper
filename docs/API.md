# Reserve and release through the API

Scripts, CI jobs, and test runs can reserve hardware before a run and release it
afterward. Testbed and device selectors accept names; IDs and serials are also
supported. The running server exposes its OpenAPI reference at /docs.

The examples below target a local demo:

~~~bash
LK=http://127.0.0.1:8000
~~~

## Identity and demo state

Send X-Labkeeper-User for attribution. Automation can also specify owner or by
where the endpoint supports it. These caller-supplied names are not authentication;
see [SECURITY.md](SECURITY.md).

The demo contains seeded bookings, including one that blocks alpha_smoke through
shared hardware. The examples use delta_smoke and a short window before its
seeded future reservation. If an earlier demo session changed that state, choose
an available window or start a fresh demo data directory.

## Check availability

~~~bash
curl -sS "$LK/api/v1/testbeds/delta_smoke/availability?duration_minutes=60"
~~~

Availability checks reservation conflicts and maintenance. It does not certify
device health or run console tests.

## Reserve

~~~bash
curl --fail-with-body -sS -X POST "$LK/api/v1/reserve" \
  -H "X-Labkeeper-User: demo-user" \
  -H "Content-Type: application/json" \
  -d '{"testbed":"delta_smoke","duration_minutes":60,"purpose":"example test run"}'
~~~

A 201 response contains the booking ID. Save that actual ID for later operations.

As an alternative to reserving the whole testbed, reserve only one device:

~~~bash
curl --fail-with-body -sS -X POST "$LK/api/v1/testbeds/delta_smoke/reserve" \
  -H "X-Labkeeper-User: demo-user" \
  -H "Content-Type: application/json" \
  -d '{"devices":["DELTA_01"],"duration_minutes":60,"purpose":"single-device test"}'
~~~

Run these as alternatives, or release the first booking before trying the second.
Selecting a logical stack reserves its physical members.

| Field | Meaning |
|---|---|
| testbed | Name or ID; supplied in the path for the per-testbed route |
| devices | Optional device/stack names, serials, or IDs; omit for the whole testbed |
| duration_minutes | Length of the reservation |
| start / end | Optional ISO 8601 time window instead of an immediate reservation |
| purpose | Description shown on the booking |
| owner | Person the booking is for; defaults to the supplied user identity |

A 409 response means there is a conflict. Its detail.conflicts lists the affected
bookings and time windows. Testbeds with different names still conflict if they
share exclusive hardware.

## Release

Replace 42 with the ID returned by your reservation:

~~~bash
curl --fail-with-body -sS -X POST "$LK/api/v1/release" \
  -H "X-Labkeeper-User: demo-user" \
  -H "Content-Type: application/json" \
  -d '{"booking_id":42}'
~~~

Or release the testbed's current bookings:

~~~bash
curl --fail-with-body -sS -X POST "$LK/api/v1/testbeds/delta_smoke/release" \
  -H "X-Labkeeper-User: demo-user"
~~~

A release response contains released bookings. Future bookings are cancelled
rather than marked as completed. The per-testbed release body can filter by owner,
devices, and include_upcoming.

The example configuration uses courtesy releases, allowing another caller to
free forgotten hardware with attribution. Owner-only behavior requires both
booking.release_policy: owner and booking.allow_force_release: false. These
policies assume trustworthy caller identity; the application is not a complete
authorization service.

## Extend and list

~~~bash
curl --fail-with-body -sS -X POST "$LK/api/v1/extend" \
  -H "X-Labkeeper-User: demo-user" \
  -H "Content-Type: application/json" \
  -d '{"booking_id":42,"minutes":30}'

curl -sS "$LK/api/v1/reservations?testbed=delta_smoke"
curl -sS "$LK/api/v1/reservations?owner=demo-user&state=all"
~~~

Extending is subject to ownership, duration limits, and conflicting reservations.

## Python context manager

The httpx dependency is installed with the backend. This example releases its
reservation after the block, including when the test raises an exception:

~~~python
import contextlib

import httpx


@contextlib.contextmanager
def reserved(testbed, *, minutes=60, devices=None, purpose=""):
    with httpx.Client(
        base_url="http://127.0.0.1:8000/api/v1",
        headers={"X-Labkeeper-User": "example-job"},
        timeout=30,
    ) as api:
        response = api.post(
            "/reserve",
            json={
                "testbed": testbed,
                "duration_minutes": minutes,
                "devices": devices,
                "purpose": purpose,
            },
        )
        response.raise_for_status()
        booking = response.json()
        try:
            yield booking
        finally:
            api.post("/release", json={"booking_id": booking["id"]}).raise_for_status()


with reserved("delta_smoke", devices=["DELTA_01"], purpose="example test"):
    pass  # Run your tests here.
~~~

The MCP tools expose the same booking operations. See the running server's
OpenAPI documentation for inventory, checks, racks, floor plans, and activity
endpoints.
