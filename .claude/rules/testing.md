# Testing rules

- Unit tests must not depend on live websites.
- Live crawl tests must be opt-in and clearly marked.
- Every extractor must have at least one fixture test before becoming a default extractor.
- Schema validation is required for every exported artifact.
- Manifest quality checks must fail on silent drops and missing stop reasons.
