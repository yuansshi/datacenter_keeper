# Source control and releases

The public source is [github.com/yuansshi/labkeeper](https://github.com/yuansshi/labkeeper).

This repository contains the reusable application, synthetic demo inventory,
tests, configuration examples, and extension interfaces. It does not contain a
deployment's actual devices, accounts, secrets, databases, private integrations,
or operational history.

## Build and run from source

~~~bash
cd frontend
npm ci
npm run build
cd ../backend
uv sync
uv run labkeeper demo
~~~

The backend serves the built frontend. A Python-only package installation is not
a substitute for building the browser assets when installing from source. The
Dockerfile builds and includes the frontend.

## Release checks

Use a clean environment so installed private plugins cannot affect verification.

- Run the backend tests with synthetic fixtures.
- Build and type-check the frontend.
- Start a fresh demo and verify inventory, checks, booking conflicts, release, and
  the browser UI.
- Review the staged tree for real inventory, credentials, internal endpoints,
  local files, and generated data.
- Preserve license notices when redistributing the code.

A source merge and a deployed application are separate events. Your deployment
should record the source revision, back up its private data/configuration, and
provide a rollback procedure appropriate to its database and hosting environment.

## Private extensions

Develop organization-specific providers or floor-plan sources in separate
packages. Configure them in the deployment environment. Do not add private
endpoints, credentials, data exports, or dependencies to the public core.
