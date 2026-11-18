# Security and deployment

Labkeeper is an alpha application for a trusted lab environment. The local demo
binds to localhost. Add deployment controls before making a server accessible to
other users or networks.

## Identity and access

The application uses a user-name header for attribution. A name entered in the UI
or supplied by a client is not verified authentication.

For a shared deployment, use an authenticated reverse proxy. The proxy must strip
client-supplied identity headers and set the configured auth.user_header from the
verified identity. Restrict direct access to the backend so clients cannot bypass
the proxy. Terminate TLS appropriately for your environment.

Labkeeper does not provide a built-in SSO login, tenant isolation, approval
workflow, or a comprehensive role-based access-control system. Booking rules are
not a substitute for these controls.

## Booking release policy

The example setup supports courtesy releases: anyone with access can release a
booking, with the action logged under their supplied identity.

For owner-only releases, set both values:

~~~yaml
booking:
  release_policy: owner
  allow_force_release: false
~~~

Use trustworthy identity for this policy to have meaning. Review who can use the
API and MCP endpoint; write tools can change bookings and inventory.

## Credentials and inventory

- Keep actual testbed files, API keys, environment files, databases, uploads,
  backups, console logs, and support bundles out of the public repository.
- Treat optional plugins as application code with the same access as the server.
- Give optional provider credentials only the permissions required for their
  reads.
- Configure SSH host-key verification and the known-hosts data for jump hosts.
- Restrict health and console probes to the networks and equipment you own.
- Review the content sent to an optional external AI service before enabling it.

Credentials read from testbed files are used in memory and redacted from
inventory/source responses. This does not encrypt the original files or make
uploaded files and database backups safe to publish. Protect storage and backups
with filesystem and deployment access controls.

## Interpreting checks

A TCP connection, login prompt, or an online cloud status is a particular
observation, not proof that all requested lab work is complete. Console tests
connect and nudge the line; they do not reset devices, clear other sessions, or
prove that power switching works.

Booking a device does not automatically run or enforce a health check. Show the
source and freshness of evidence in any additional acceptance workflow you build.

## Reporting a vulnerability

Do not include secrets, real inventory, exploit output from another organization,
or private hostnames in a public issue. Use the repository's private vulnerability
reporting channel if available. For a non-sensitive issue, include the version,
a minimal synthetic reproduction, and the affected behavior.
