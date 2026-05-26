# static-site-graph architecture

`static-site-graph` turns a static or semi-static website into a structured graph and mirror index.

## Object model

```text
Site
  NavNode
  Section
    ListPage
      ListItem
        DetailPage | AttachmentMetadata | ExternalLink
  Edge
  Manifest
  AuditReport
```

## Page families

- `homepage`
- `nav_landing_page`
- `section_list_page`
- `pagination_page`
- `detail_article_page`
- `static_info_page`
- `workflow_page`
- `policy_page`
- `resource_list_page`
- `direct_attachment_item`
- `external_system_link`
- `external_policy_link`
- `cross_domain_article`
- `footer_link`
- `image_asset`
- `unknown_or_error_page`
- `low_content_detail_page`

## Canonical pipeline

1. Fetch homepage.
2. Extract navigation and homepage modules.
3. Build section registry.
4. Crawl list pages and pagination chains.
5. Classify list targets.
6. Extract detail pages and attachment metadata.
7. Emit edges.
8. Validate schema and quality.
9. Export for downstream consumers.
