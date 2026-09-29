# UI styles

`static/ui.css` is the only stylesheet referenced by the page. It imports these
files in cascade order:

- `style.css`: base tokens and shared layout primitives
- `workspace.css`: records and review workspace structure
- `ui_refinements.css`, `ui_round2.css`: shared interaction refinements
- `archive_theme.css`: archive visual theme
- `report_refinements.css`: quality report components
- `review_feedback.css`, `review_archive.css`: review evidence and image feedback
- `settings.css`: recognition plan settings
- `records_archive.css`: record list and filters
- `workbench_tasks.css`: daily workbench and queue
- `process_history.css`: process timeline dialog

The order is intentional during the migration: later feature layers can refine
shared primitives without adding another stylesheet link or relying on a
cache-busting filename in the template.
