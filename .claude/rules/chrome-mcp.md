# Chrome / browser audit rules

Chrome is used to audit and model; Python HTTP is used for bulk crawling.

Use Chrome for:

- homepage navigation tree extraction;
- representative list pages;
- representative detail pages;
- low-content pages;
- pages where HTTP extraction disagrees with visible DOM;
- hover menus, dynamic content, login gates, CAPTCHA, or abnormal HTTP statuses.

For each audited page, record:

- URL;
- page type;
- visible title;
- DOM selector evidence;
- screenshot or textual notes;
- comparison with HTTP extractor;
- outcome and remediation.

Do not call a site model complete until Chrome has verified each distinct page family.
