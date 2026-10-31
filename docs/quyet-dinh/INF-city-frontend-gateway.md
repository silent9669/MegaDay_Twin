# INF: isolated city frontend behind the current Worker

The user requested an immersive city, server-side demo login, merge and Cloudflare deployment while preserving the teammate backend. The live domain is already owned by this repository's `hackathon` Worker and D1 database. Reassigning that domain to a separate UI Worker would displace the team's deployment.

Keep the domain and database here; connect the independently maintained city Worker through a service binding. Forward only city-owned paths, validate a signed access session before serving existing application routes, and retain the original `/healthz` D1 probe. The Vite operations overview remains at `/?workspace=operations`. No Python, DuckDB schema, API types, economics, governance or mock-data generation changes are part of this integration.

The hosted password gate is an additional access boundary. It does not grant any backend role or approval permission. The city continues to use its existing sandbox engine until the planned FastAPI endpoints provide reviewed data. See [deployment boundary](../city-demo/deployment-boundary.md).
