# Labkeeper backend

FastAPI, SQLAlchemy, inventory import, lab checks, and shared-hardware booking.
The application works without a vendor cloud account or AI API key.

From a source checkout, build the UI before starting the backend:

~~~bash
cd frontend
npm ci
npm run build
cd ../backend
uv sync
uv run labkeeper demo
~~~

See the [main README](../README.md) for setup and the
[configuration guide](../docs/CONFIGURATION.md) for your own lab.
