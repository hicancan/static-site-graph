# Architecture rules

- Framework code lives under `src/sitegraph/`.
- Schemas live under `schemas/` and define the stable external contract.
- Example site configs live under `examples/sites/<site_id>/config/`.
- Generated data must not be required for tests unless stored as a small fixture.
- Site-specific overrides belong in instance configs or examples, not in framework code.

## Layering

1. Fetch layer: HTTP and browser snapshot acquisition.
2. Classification layer: URL/page type and target type.
3. Extraction layer: nav/list/detail/attachment/external extraction.
4. Graph layer: nodes and edges.
5. Validation layer: schema and quality checks.
6. Export layer: stable JSON/JSONL artifacts for downstream systems.
