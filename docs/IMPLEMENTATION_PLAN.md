# Formwork implementation

The current product is a local, single-reviewer PDF/XLSX filling workflow.

- Template inspection and evidence extraction retain their existing behavior and source provenance.
- A new form fill pins a published template and case evidence snapshots. `gpt-6-sol` at high reasoning in fast mode maps the fields with one structured call. It may make common-sense intake assumptions, including the current period asked by the form and counts from listed records. Unsupported values remain blank.
- The review overlay shows all fields. Mapped hover text comes from stored evidence blocks or fact provenance, never model-authored quotes. Reviewer edits may link a fact; otherwise they are labeled as reviewer-entered.
- The intake inventory groups facts into business, people, vehicles, coverage, and other. Citation usage updates after each edit. Unused facts are visible and do not block approval.
- Whole-form approval generates a PDF or XLSX from the original template. Writer failures identify the field and prevent download. A subsequent edit removes approval.
- The migration drops legacy Fill Plans, decisions, and verification reports while retaining templates and evidence.

Acceptance cases: the Rivington supplemental PDF and a workbook target. Filled reference outputs may be used only to evaluate generated outputs, never as evidence inputs.
