# Newspaper navigation and RSS inspection

Newspaper Sections is the reader's landing page. Daily World in Brief and
United States in Brief lead when present, followed by The World This Week,
Leaders, Letters, By Invitation, Briefing, regional coverage, and the remaining
print sections. “Briefing” is the publication's section name.

The fixed baseline follows the US-edition contents, rather than alphabetical
order. The print order was checked against the
[July 11–17, 2026 contents reproduction](https://docshare.wps.com/document/the-economist-july-1117-2026-issue-contents/86626/).
Regional order and optional special reports vary between editions; this is
not a claim to reproduce the current issue's exact contents or page numbers.
Unavailable sections are omitted. Unrecognized section names are retained after
the known newspaper sections; Online is always last.

Online is an additional collection of items explicitly labelled `online_only`.
Those stories remain in their newspaper sections, with a visible Online Only
badge on the collapsed card. These labels use existing feed metadata; the
unresolved issue-discovery limitation documented in the September review still
applies. Display order is independent of print-membership classification.

The mobile section bar scrolls horizontally and remains sticky while reading.
Controls have at least 44px touch targets; Previous/Next walks through sections.
Arrow keys, Home, and End work in the tab bar. Selection is stored in the
`section` query parameter so reload, browser history, and copied links work.
US in Brief is recognized by its actual briefing title, not merely by any
article having the generic In Brief category.

## Why “Don't fear the inheritocracy” appeared in In Brief

The cached feed contained two entries with the same publisher GUID
`796fbbcf-0336-42c7-be85-b35c15ebc317`, but different article URLs:

- `/in-brief/2026/09/16/dont-fear-the-inheritocracy`
- `/by-invitation/2026/09/16/dont-fear-the-inheritocracy`

The database keys articles by canonicalized URL, allowing both rows. Category
inference reads the URL path. The original reader treated each XML entry as a
separate article and grouped it under its first category, producing a misleading
In Brief card in addition to the By Invitation card. The Economist's own
[publication account](https://tr.linkedin.com/company/the-economist/) also
identifies this essay as By Invitation. The cache alone does not establish why
the upstream alias appeared.

The reader now collapses matching publisher GUIDs and prefers a specific
section over an In Brief-only copy, regardless of entry order. It does not
merge unrelated articles just because their titles match, guess a new URL, or
delete stored history. If only a generic alias is available, its classification
is left intact rather than guessed. This fixes the observed article when both
copies are present, as they are in the current preview feed.

## RSS tab

Raw RSS opens a readable document inspector with channel information, expandable
entries, section/edition information, and original RSS fields. The actual XML
link opens `/api/feed` in a separate tab. The XML is untouched: all entries,
including cached aliases, remain inspectable. This distinguishes RSS entry
counts from the unique-article counts in the newspaper and recent-article views.
