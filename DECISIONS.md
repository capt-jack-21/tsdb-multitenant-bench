# Decisions

| # | Decision | Alternatives rejected | Why |
|---|---|---|---|
| 1 | One shared hypertable with tenant_id | Schema-per-tenant, database-per-tenant | Most common SaaS pattern and the cleanest RLS test; others listed out of scope |
| 2 | Python 3 + psycopg3 COPY generator | Go, Node.js | Fastest to write; COPY is the realistic fast path |
| 3 | Develop in local Docker; official runs on one pinned dedicated-vCPU VM, rented hourly | Shared-vCPU VM, laptop-only | Shared CPUs add noise; hourly rental keeps total cost small |
| 4 | No Kafka, Kubernetes or Terraform in this project | Broker in the ingest path | Not needed to answer the question; may be a later, separately measured comparison |
| 5 | Code under Apache-2.0; articles and data under CC BY 4.0 | MIT, AGPL | Maximum reuse and visibility, patent grant for code |
| 6 | Direct connections, no pooler | PgBouncer | Keeps the experiment to one variable; pooling is a follow-up topic |
| 7 | Publish protocol before running | Publish only results | Prevents post-hoc tuning and invites review |
