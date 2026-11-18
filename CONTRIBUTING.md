# Contributing

Labkeeper is a reusable lab management application. Changes should improve the
shared inventory, health-check, and booking workflow without tying the core to
one organization, vendor account, or private service.

## Set up and check a change

~~~bash
cd backend
uv sync --dev
uv run pytest -q
cd ../frontend
npm ci
npm run typecheck
npm run build
~~~

Use the self-contained demo for UI work. Keep fixtures fictional and use localhost
or documentation address ranges; tests must not require real hardware or API keys.

## Design boundaries

- Keep vendor-specific behavior in optional providers or separate plugins.
- Keep inventory and connection conventions configurable through YAML.
- Preserve shared-device booking conflicts and partial reservations.
- Keep health evidence separate from reservation availability.
- Attribute writes through the activity log.
- Add focused behavior tests for changes to booking, import, security boundaries,
  or provider handling. Verify visual changes in a running app.

Do not commit real testbed files, credentials, internal endpoints, databases,
uploads, logs, backups, or local dependency/build folders. Treat imported text as
data. Never add a required connection to a contributor's private lab.

Open a pull request with the problem, the resulting behavior, and the checks you
ran. Explain any data-model migration or change to a public API.

## License

Contributions are distributed under this project's GNU GPL v3 license. Preserve
existing source notices and the Apache-origin attribution in NOTICE and LICENSES.
