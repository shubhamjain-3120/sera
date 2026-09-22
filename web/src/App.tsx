import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, Route, Routes, useNavigate, useParams } from "react-router-dom";
import { AlertTriangle, Archive, Check, ChevronLeft, FileImage, FileSpreadsheet, FileText, ListChecks, LoaderCircle, Lock, Plus, Save, Search, ShieldCheck, Upload, X } from "lucide-react";
import { API, api, type GridCell } from "./api";
import type { Agency, Artifact, Draft, EvidenceFact, EvidenceLocation, EvidenceSource, FieldType, FillPlanTarget, Location, MappingCandidate, ReviewAction, ReviewDecision, TemplateField, VerificationFinding, VerificationReport } from "./types";

function App() {
  return <Routes><Route path="/" element={<Library />} /><Route path="/templates/:artifactId/:draftId" element={<Inspector />} /><Route path="/evidence" element={<EvidenceLibrary />} /><Route path="/evidence/process/:artifactId" element={<EvidenceRun />} /><Route path="/evidence/:artifactId/:snapshotId" element={<EvidenceInspector />} /><Route path="/fill-plans" element={<FillPlanLibrary />} /><Route path="/fill-plans/:fillPlanId" element={<FillPlanInspector />} /></Routes>;
}

function Brand() {
  return <Link className="brand" to="/"><span className="brand-mark"><span /></span><span>formwork<small>Document intelligence</small></span></Link>;
}

function WorkspaceNav() {
  return <nav className="workspace-nav"><Link to="/">Templates</Link><Link to="/evidence">Evidence</Link><Link to="/fill-plans">Fill Plans</Link></nav>;
}

function Library() {
  const navigate = useNavigate();
  const inputRef = useRef<HTMLInputElement>(null);
  const client = useQueryClient();
  const { data = [], isLoading } = useQuery({ queryKey: ["artifacts"], queryFn: api.listArtifacts });
  const upload = useMutation({
    mutationFn: api.upload,
    onSuccess: (artifact) => { client.invalidateQueries({ queryKey: ["artifacts"] }); navigate(`/templates/${artifact.id}/${artifact.draft_id}?run=${artifact.run_id}`); },
  });
  return <div className="app-shell">
    <header><Brand /><WorkspaceNav /><div className="phase-badge"><span /> Phase 5 · Human review</div></header>
    <main className="library">
      <section className="hero">
        <p className="eyebrow">Your document workspace</p>
        <h1>Make every field<br /><em>accountable.</em></h1>
        <p>Inspect the structure beneath PDFs and workbooks. Correct it, version it, and keep every original untouched.</p>
        <button className="primary" onClick={() => inputRef.current?.click()} disabled={upload.isPending}><Upload size={17} /> {upload.isPending ? "Uploading…" : "Inspect a template"}</button>
        <input ref={inputRef} hidden type="file" accept=".pdf,.xlsx" onChange={(event) => event.target.files?.[0] && upload.mutate(event.target.files[0])} />
        {upload.error && <div className="error-banner">{upload.error.message}</div>}
      </section>
      <section className="recent">
        <div className="section-heading"><div><span className="eyebrow">Recent work</span><h2>Templates</h2></div><span>{data.length} total</span></div>
        {isLoading ? <div className="empty"><LoaderCircle className="spin" /> Loading templates</div> : data.length === 0 ?
          <button className="dropzone" onClick={() => inputRef.current?.click()}><Upload /><strong>Drop in a PDF or workbook</strong><span>Originals are content-hashed and stored unchanged.</span></button> :
          <div className="artifact-grid">{data.map((item) => <ArtifactCard key={item.id} item={item} />)}</div>}
      </section>
    </main>
  </div>;
}

function EvidenceLibrary() {
  const navigate = useNavigate();
  const inputRef = useRef<HTMLInputElement>(null);
  // A case is derived from the upload batch: the first source starts one and
  // the rest of the batch joins it, so different clients stay isolated.
  const [caseKey, setCaseKey] = useState<string | null>(null);
  const client = useQueryClient();
  const { data = [], isLoading } = useQuery({ queryKey: ["evidence-sources"], queryFn: api.listEvidenceSources });
  const upload = useMutation({
    mutationFn: (file: File) => api.uploadEvidenceSource(file, caseKey ?? undefined),
    onSuccess: (source) => { setCaseKey(source.case_key ?? null); client.invalidateQueries({ queryKey: ["evidence-sources"] }); client.invalidateQueries({ queryKey: ["cases"] }); navigate(`/evidence/process/${source.id}?run=${source.run_id}`); },
  });
  return <div className="app-shell">
    <header><Brand /><WorkspaceNav /><div className="phase-badge"><span /> Phase 5 · Human review</div></header>
    <main className="library evidence-library">
      <section className="hero evidence-hero"><p className="eyebrow">Source evidence</p><h1>Trace every fact<br /><em>to its source.</em></h1><p>Parse PDFs, images, workbooks, and text into immutable evidence snapshots. Originals stay private and unchanged.</p><div className="upload-row"><span className="case-chip">{caseKey ? <><Lock size={12} /> Adding to {caseKey}</> : "Next upload starts a new case"}</span><button className="primary" onClick={() => inputRef.current?.click()} disabled={upload.isPending}><Upload size={17} /> {upload.isPending ? "Ingesting…" : caseKey ? "Add to this case" : "Add source"}</button>{caseKey && <button className="ghost" onClick={() => setCaseKey(null)}><Plus size={15} /> Start new case</button>}</div><input ref={inputRef} hidden type="file" accept=".pdf,.xlsx,.png,.jpg,.jpeg,.txt" onChange={(event) => event.target.files?.[0] && upload.mutate(event.target.files[0])} />{upload.error && <div className="error-banner">{upload.error.message}</div>}</section>
      <section className="recent"><div className="section-heading"><div><span className="eyebrow">Immutable snapshots</span><h2>Evidence sources</h2></div><span>{data.length} total</span></div>{isLoading ? <div className="empty"><LoaderCircle className="spin" /> Loading evidence</div> : data.length === 0 ? <button className="dropzone" onClick={() => inputRef.current?.click()}><Upload /><strong>Add a source document</strong><span>PDF, image, XLSX, or raw text. Filled reference outputs are rejected.</span></button> : <div className="artifact-grid">{data.map((source) => <EvidenceSourceCard key={source.id} source={source} />)}</div>}</section>
    </main>
  </div>;
}

function EvidenceSourceCard({ source }: { source: EvidenceSource }) {
  const Icon = source.kind === "pdf" || source.kind === "text" ? FileText : source.kind === "image" ? FileImage : FileSpreadsheet;
  const destination = source.snapshot_id ? `/evidence/${source.id}/${source.snapshot_id}` : `/evidence/process/${source.id}?run=${source.run_id}`;
  return <Link className="artifact-card" to={destination}><div className={`file-icon ${source.kind}`}><Icon /></div><div><h3>{source.filename}</h3><p>{source.case_key ?? "Unassigned"} · {source.kind.toUpperCase()} · {(source.size_bytes / 1024).toFixed(0)} KB</p></div><span className="hash">{source.sha256.slice(0, 8)}</span></Link>;
}

function EvidenceRun() {
  const { artifactId = "" } = useParams();
  const runId = new URLSearchParams(location.search).get("run");
  const run = useQuery({ queryKey: ["run", runId], queryFn: () => api.run(runId!), enabled: Boolean(runId), refetchInterval: (q) => ["succeeded", "failed"].includes(q.state.data?.status ?? "") ? false : 700 });
  const snapshotId = run.data?.result?.snapshot_id;
  if (run.data?.status === "succeeded" && typeof snapshotId === "string") return <EvidenceViewLoader artifactId={artifactId} snapshotId={snapshotId} />;
  return <Processing run={run.data} error={run.error} />;
}

function EvidenceInspector() {
  const { artifactId = "", snapshotId = "" } = useParams();
  return <EvidenceViewLoader artifactId={artifactId} snapshotId={snapshotId} />;
}

function EvidenceViewLoader({ artifactId, snapshotId }: { artifactId: string; snapshotId: string }) {
  const snapshot = useQuery({ queryKey: ["evidence-snapshot", snapshotId], queryFn: () => api.evidenceSnapshot(snapshotId) });
  if (!snapshot.data) return <div className="center"><LoaderCircle className="spin" /> Loading evidence snapshot…</div>;
  return <EvidenceView artifactId={artifactId} data={snapshot.data.snapshot} snapshotHash={snapshot.data.snapshot_sha256} />;
}

function EvidenceView({ artifactId, data, snapshotHash }: { artifactId: string; data: Awaited<ReturnType<typeof api.evidenceSnapshot>>["snapshot"]; snapshotHash: string }) {
  const [selectedId, setSelectedId] = useState(data.facts[0]?.id ?? "");
  const [filter, setFilter] = useState("");
  const selected = data.facts.find((fact) => fact.id === selectedId);
  const visible = data.facts.filter((fact) => `${fact.label} ${String(fact.value ?? "explicitly absent")} ${fact.entity_role}`.toLowerCase().includes(filter.toLowerCase()));
  useEffect(() => {
    document.querySelector(`[data-fact-id="${selectedId}"]`)?.scrollIntoView({ block: "nearest" });
  }, [selectedId]);
  return <div className="inspector evidence-inspector"><header><Brand /><div className="crumb"><ChevronLeft size={16} /><Link to="/evidence">Evidence</Link><span>/</span><strong>{data.source_sha256.slice(0, 12)}</strong></div><div className="snapshot-badge"><Lock size={13} /> Immutable · {snapshotHash.slice(0, 10)}</div></header><div className="workbench evidence-workbench"><aside className="field-list"><div className="panel-title"><div><p className="eyebrow">Extracted evidence</p><h2>{data.facts.length} facts</h2></div></div><label className="search"><Search size={15} /><input placeholder="Find a fact or entity" value={filter} onChange={(event) => setFilter(event.target.value)} /></label><div className="evidence-summary"><span>{data.facts.filter((fact) => fact.accepted).length} accepted</span><span>{data.unreadable_regions.length} unreadable</span></div><div className="field-scroll">{visible.map((fact, index) => <button key={fact.id} data-fact-id={fact.id} className={`field-row fact-row ${fact.id === selectedId ? "active" : ""}`} onClick={() => setSelectedId(fact.id)}><span className="field-number">{String(index + 1).padStart(2, "0")}</span><span><strong>{fact.label}</strong><small>{fact.value === null ? "Explicitly absent" : String(fact.value)} · {fact.entity_role}</small></span><i className={fact.accepted ? "fact-accepted" : "fact-review"} /></button>)}</div></aside><main className="preview-panel"><div className="preview-toolbar"><div><strong>Original source</strong><span> · read-only</span></div><div className="legend"><i className="selected" /> Exact provenance</div></div>{data.warnings.length ? <div className="warning"><AlertTriangle size={15} /> {data.warnings[0]}</div> : null}<EvidencePreview artifactId={artifactId} fact={selected} facts={data.facts} onSelect={setSelectedId} blocks={data.parse_blocks} /></main><aside className="properties">{selected ? <FactProperties fact={selected} parser={`${data.parser_provider} · ${data.parser_version}`} extractor={data.extractor_version} /> : <div className="empty-property"><Archive /><p>No facts were accepted. Review unreadable regions and parser warnings.</p></div>}</aside></div></div>;
}

function FactProperties({ fact, parser, extractor }: { fact: EvidenceFact; parser: string; extractor: string }) {
  const location = fact.provenance[0];
  return <><div className="panel-title"><div><p className="eyebrow">Evidence fact</p><h2>{fact.accepted ? "Accepted fact" : "Needs review"}</h2></div></div><div className="fact-properties"><dl><dt>Meaning</dt><dd>{fact.key}</dd><dt>Value</dt><dd>{fact.value === null ? <em>No value — explicitly absent</em> : String(fact.value)}</dd><dt>Entity</dt><dd>{fact.entity_role} · {fact.entity_id}</dd><dt>Confidence</dt><dd>{Math.round(fact.confidence * 100)}%</dd>{fact.unit && <><dt>Unit</dt><dd>{fact.unit}</dd></>}{fact.date_context && <><dt>Date context</dt><dd>{fact.date_context}</dd></>}</dl><div className="location-card"><span>Exact provenance</span><strong>{formatEvidenceLocation(location)}</strong><small>{location.coordinate_system}</small></div>{fact.semantics.map((item) => <span className="semantic-chip" key={item}>{item.replaceAll("_", " ")}</span>)}{fact.uncertainty.map((item) => <div className="uncertainty" key={item}><AlertTriangle size={13} /> {item}</div>)}{fact.duplicate_of && <p className="relation">Duplicate of {fact.duplicate_of}</p>}{fact.contradicts.length > 0 && <p className="relation contradiction">Contradicts {fact.contradicts.join(", ")}</p>}<div className="version-note"><span>Parser</span>{parser}<span>Extractor</span>{extractor}</div></div></>;
}

function formatEvidenceLocation(location?: EvidenceLocation) {
  if (!location) return "No source location";
  if (location.kind === "xlsx_range") return `${location.sheet}!${location.cell_range}`;
  if (location.kind === "text_span") return `Characters ${location.char_start}–${location.char_end}`;
  return `Page ${location.page} · [${location.rect?.join(", ")}]`;
}

function EvidencePreview({ artifactId, fact, facts, onSelect, blocks }: { artifactId: string; fact?: EvidenceFact; facts: EvidenceFact[]; onSelect: (factId: string) => void; blocks: Array<{ type: string; text: string; source: EvidenceLocation }> }) {
  const location = fact?.provenance[0];
  if (!location) return <div className="center">Select a fact to inspect its source.</div>;
  if (location.kind === "xlsx_range") {
    const field: TemplateField = { id: fact!.id, label: fact!.label, field_type: "text", writable: false, options: [], location: { kind: "xlsx_range", sheet: location.sheet, cell_range: location.cell_range } };
    const sheets = [...new Set(blocks.map((block) => block.source.sheet).filter(Boolean))].map((name) => ({ name: name!, state: "visible", protected: false }));
    return <WorkbookPreview artifactId={artifactId} fields={[field]} selected={field.id} onSelect={() => undefined} sheets={sheets} />;
  }
  if (location.kind === "text_span") return <div className="text-source">{blocks.filter((block) => block.source.kind === "text_span").map((block, index) => <p className={block.source.char_start === location.char_start ? "active" : ""} key={index}>{block.text}</p>)}</div>;
  const imageUrl = location.kind === "pdf_rect" ? `${API}/api/v1/artifacts/${artifactId}/pages/${location.page ?? 1}.png?scale=1.5` : `${API}/api/v1/artifacts/${artifactId}/content`;
  // Every fact sharing this page is drawn so the source region can be clicked
  // back to its extracted fact, not only the one already selected.
  const onPage = facts.filter((item) => {
    const spot = item.provenance[0];
    return spot && spot.kind === location.kind && (spot.page ?? 1) === (location.page ?? 1) && spot.rect;
  });
  return <div className="pdf-wrap"><div className="page-nav"><span>{location.kind === "image_rect" ? "Original image" : `Page ${location.page}`}</span></div><div className="paper"><img alt="Original evidence source" src={imageUrl} />{onPage.map((item) => {
    const spot = item.provenance[0];
    const overlay = { kind: "pdf_rect" as const, page: spot.page, rect: spot.rect, rotation: spot.rotation, page_width: spot.page_width, page_height: spot.page_height, coordinate_system: spot.coordinate_system };
    return <button key={item.id} aria-label={item.label} title={item.label} className={`pdf-field evidence-region ${item.id === fact!.id ? "active" : ""} ${item.accepted ? "" : "needs-review"}`} style={pdfRectStyle(overlay)} onClick={() => onSelect(item.id)} />;
  })}</div></div>;
}

function FillPlanLibrary() {
  const navigate = useNavigate();
  const client = useQueryClient();
  const [caseKey, setCaseKey] = useState("");
  const [agencyKey, setAgencyKey] = useState("");
  const plans = useQuery({ queryKey: ["fill-plans"], queryFn: api.listFillPlans });
  const templates = useQuery({ queryKey: ["artifacts"], queryFn: api.listArtifacts });
  const cases = useQuery({ queryKey: ["cases"], queryFn: api.listCases });
  const agencies = useQuery({ queryKey: ["agencies"], queryFn: api.listAgencies });
  const activeCase = caseKey || cases.data?.[0]?.case_key || "";
  const activeAgency = agencyKey || agencies.data?.find((item) => item.is_default)?.key || "";
  const create = useMutation({
    mutationFn: async (artifact: Artifact) => {
      if (!activeCase) throw new Error("Ingest evidence for a case before creating a Fill Plan");
      const versions = await api.versions(artifact.draft_id);
      if (!versions.length) throw new Error("Publish this template before creating a Fill Plan");
      return api.createFillPlan(activeCase, versions[0].id, activeAgency);
    },
    onSuccess: (plan) => { client.invalidateQueries({ queryKey: ["fill-plans"] }); navigate(`/fill-plans/${plan.id}`); },
  });
  return <div className="app-shell">
    <header><Brand /><WorkspaceNav /><div className="phase-badge"><span /> Phase 5 · Human review</div></header>
    <main className="library fill-plan-library">
      <section className="hero fill-plan-hero"><p className="eyebrow">Grounded mapping</p><h1>Map evidence.<br /><em>Expose uncertainty.</em></h1><p>Create a revisioned Fill Plan against a published template and a frozen case-evidence bundle. Nothing is written to the original target.</p><div className="upload-row"><label className="picker"><span>Agency</span><select aria-label="Filing agency" value={activeAgency} onChange={(event) => setAgencyKey(event.target.value)}>{agencies.data?.map((item) => <option key={item.key} value={item.key}>{item.name}</option>)}</select></label><label className="picker"><span>Case</span><select aria-label="Mapping case" value={activeCase} onChange={(event) => setCaseKey(event.target.value)}>{cases.data?.length ? cases.data.map((item) => <option key={item.case_key} value={item.case_key}>{item.case_key} · {item.source_count} source{item.source_count === 1 ? "" : "s"}</option>) : <option value="">No ingested cases yet</option>}</select></label></div>{create.error && <div className="error-banner">{create.error.message}</div>}</section>
      {agencies.data && activeAgency && <AgencyDetails agency={agencies.data.find((item) => item.key === activeAgency)!} />}
      <section className="recent"><div className="section-heading"><div><span className="eyebrow">Published targets</span><h2>Start a Fill Plan</h2></div><span>{templates.data?.length ?? 0} templates</span></div><div className="artifact-grid">{templates.data?.map((artifact) => <button className="artifact-card mapping-create" key={artifact.id} onClick={() => create.mutate(artifact)} disabled={create.isPending}><div className={`file-icon ${artifact.kind}`}>{artifact.kind === "pdf" ? <FileText /> : <FileSpreadsheet />}</div><div><h3>{artifact.filename}</h3><p>Use latest published version · {activeCase || "no case"}</p></div><Plus size={16} /></button>)}</div></section>
      <section className="recent"><div className="section-heading"><div><span className="eyebrow">Frozen proposals</span><h2>Fill Plans</h2></div><span>{plans.data?.length ?? 0} total</span></div>{plans.isLoading ? <div className="empty"><LoaderCircle className="spin" /> Loading Fill Plans</div> : plans.data?.length ? <div className="artifact-grid">{plans.data.map((plan) => <Link className="artifact-card" to={`/fill-plans/${plan.id}`} key={plan.id}><div className="file-icon mapping"><ListChecks /></div><div><h3>{plan.template_name}</h3><p>{plan.case_key} · Revision {plan.current_revision} · {plan.issue_count} issues</p></div>{plan.blocker_count ? <span className="blocker-count">{plan.blocker_count} blocked</span> : <span className="hash">ready</span>}</Link>)}</div> : <div className="empty">No Fill Plans yet. Publish a template and choose a case above.</div>}</section>
    </main>
  </div>;
}

const AGENCY_DETAIL_FIELDS: Array<[string, string]> = [
  ["legal_name", "Legal name"], ["contact_name", "Contact"], ["address_line1", "Address"], ["address_line2", "Address line 2"],
  ["city", "City"], ["state", "State"], ["postal_code", "ZIP code"], ["phone", "Phone"], ["fax", "Fax"],
  ["email", "Email"], ["website", "Website"], ["producer_number", "Producer number"], ["license_number", "License number"], ["naic_code", "NAIC code"],
];

function AgencyDetails({ agency }: { agency: Agency }) {
  const client = useQueryClient();
  const [draft, setDraft] = useState<Record<string, string>>(agency.details);
  const [open, setOpen] = useState(false);
  useEffect(() => setDraft(agency.details), [agency.key, agency.details]);
  const save = useMutation({
    mutationFn: () => api.updateAgency(agency.key, draft),
    onSuccess: () => client.invalidateQueries({ queryKey: ["agencies"] }),
  });
  const dirty = AGENCY_DETAIL_FIELDS.some(([key]) => (draft[key] ?? "") !== (agency.details[key] ?? ""));
  const filled = AGENCY_DETAIL_FIELDS.filter(([key]) => (agency.details[key] ?? "").trim()).length;
  return <section className="recent agency-panel">
    <div className="section-heading"><div><span className="eyebrow">Trusted source</span><h2>{agency.name} details</h2></div><button className="ghost" onClick={() => setOpen(!open)}>{filled} of {AGENCY_DETAIL_FIELDS.length} set · {open ? "Hide" : "Edit"}</button></div>
    {open && <div className="agency-grid">{AGENCY_DETAIL_FIELDS.map(([key, label]) => <label key={key}><span>{label}</span><input value={draft[key] ?? ""} placeholder="—" onChange={(event) => setDraft({ ...draft, [key]: event.target.value })} /></label>)}</div>}
    {open && <div className="agency-actions"><p>These values fill agency-owned fields on every form and are never taken from an applicant's documents.</p><button className="primary" disabled={!dirty || save.isPending} onClick={() => save.mutate()}><Save size={15} /> {save.isPending ? "Saving…" : "Save details"}</button></div>}
    {save.error && <div className="error-banner">{save.error.message}</div>}
  </section>;
}

function FillPlanInspector() {
  const { fillPlanId = "" } = useParams();
  const plan = useQuery({ queryKey: ["fill-plan", fillPlanId], queryFn: () => api.fillPlan(fillPlanId) });
  if (!plan.data) return <div className="center"><LoaderCircle className="spin" /> Loading Fill Plan…</div>;
  return <FillPlanView plan={plan.data} />;
}

function FillPlanView({ plan }: { plan: Awaited<ReturnType<typeof api.fillPlan>> }) {
  const client = useQueryClient();
  const [selectedId, setSelectedId] = useState(plan.payload.targets[0]?.field.id ?? "");
  const [filter, setFilter] = useState("");
  const target = plan.payload.targets.find((item) => item.field.id === selectedId);
  const fields = plan.payload.targets.map((item) => ({ ...item.field, current_value: mappingRawValue(item) }));
  const visible = plan.payload.targets.filter((item) => `${item.field.label} ${item.field.semantic_type ?? ""}`.toLowerCase().includes(filter.toLowerCase()));
  const inspection = plan.payload.template_schema.inspection as { format?: "pdf" | "xlsx"; page_count?: number; sheets?: Array<{ name: string; state: string; protected: boolean }> };
  const reports = useQuery({ queryKey: ["verifications", plan.id], queryFn: () => api.listVerifications(plan.id) });
  const verify = useMutation({ mutationFn: () => api.verifyFillPlan(plan.id, plan.current_revision), onSuccess: () => client.invalidateQueries({ queryKey: ["verifications", plan.id] }) });
  const report = reports.data?.find((item) => item.fill_plan_revision === plan.current_revision);
  const decisions = useQuery({ queryKey: ["review-decisions", plan.id], queryFn: () => api.listReviewDecisions(plan.id) });
  const review = useMutation({
    mutationFn: (input: { action: ReviewAction; reason?: string; candidate_id?: string; value?: unknown }) => api.reviewFillPlan(plan.id, { expected_revision: plan.current_revision, target_field_id: selectedId, actor: "Local reviewer", ...input }),
    onSuccess: () => { client.invalidateQueries({ queryKey: ["fill-plan", plan.id] }); client.invalidateQueries({ queryKey: ["review-decisions", plan.id] }); },
  });
  return <div className="inspector mapping-inspector">
    <header><Brand /><div className="crumb"><ChevronLeft size={16} /><Link to="/fill-plans">Fill Plans</Link><span>/</span><strong>{plan.template_name}</strong></div><div className="header-actions"><div className="snapshot-badge"><Lock size={13} /> Evidence frozen · {plan.evidence_bundle_sha256.slice(0, 10)}</div><button className="primary small" onClick={() => verify.mutate()} disabled={verify.isPending}>{verify.isPending ? <LoaderCircle className="spin" size={15} /> : <ShieldCheck size={15} />} {report ? "Verified revision" : "Run verification"}</button></div></header>
    <div className="workbench mapping-workbench">
      <aside className="field-list"><div className="panel-title"><div><p className="eyebrow">Review queue</p><h2>{plan.payload.summary.reviewed_count ?? 0} reviewed</h2></div></div><label className="search"><Search size={15} /><input placeholder="Find a target field" value={filter} onChange={(event) => setFilter(event.target.value)} /></label><div className="evidence-summary"><span>{plan.payload.summary.review_pending_count ?? plan.payload.summary.unresolved_count} pending</span><span>{plan.blocker_count} blockers</span></div><div className="field-scroll">{visible.map((item, index) => <button key={item.field.id} className={`field-row mapping-row ${item.field.id === selectedId ? "active" : ""}`} onClick={() => setSelectedId(item.field.id)}><span className="field-number">{String(index + 1).padStart(2, "0")}</span><span><strong>{item.field.label}</strong><small>{mappingValue(item)}</small></span><i className={`mapping-state ${item.state}`} /></button>)}</div></aside>
      <main className="preview-panel"><div className="preview-toolbar"><div><strong>Reviewed preview</strong><span> · overlay only, no output generated</span></div><div className="legend"><i className="selected" /> Selected target</div></div>{plan.payload.issues.some((issue) => issue.severity === "blocker") && <div className="warning"><AlertTriangle size={15} /> This plan contains blockers and cannot proceed to later finalization.</div>}{plan.payload.preview_calculation?.status === "stale" && <div className="warning"><AlertTriangle size={15} /> {plan.payload.preview_calculation.message}</div>}{inspection.format === "pdf" ? <PdfPreview artifactId={plan.target_artifact_id} fields={fields} selected={selectedId} onSelect={setSelectedId} pages={inspection.page_count ?? 1} /> : <WorkbookPreview artifactId={plan.target_artifact_id} fields={fields} selected={selectedId} onSelect={setSelectedId} sheets={inspection.sheets ?? []} />}</main>
      <aside className="properties">{report && <VerificationSummary report={report} targetId={selectedId} />}{verify.error && <div className="error-banner">{verify.error.message}</div>}{review.error && <div className="error-banner">{review.error.message}</div>}{target ? <MappingProperties target={target} onReview={(input) => review.mutate(input)} pending={review.isPending} decisions={(decisions.data ?? []).filter((item) => item.target_field_id === target.field.id)} /> : <div className="empty-property"><Archive /><p>Select a target to inspect its proposals.</p></div>}</aside>
    </div>
  </div>;
}

function VerificationSummary({ report, targetId }: { report: VerificationReport; targetId: string }) {
  const deterministic = report.report.deterministic.findings.filter((item) => !item.target_id || item.target_id === targetId);
  const independent = report.report.independent.findings.filter((item) => !item.target_id || item.target_id === targetId);
  const Finding = ({ item }: { item: VerificationFinding }) => <div className={`verification-finding ${item.severity}`}><AlertTriangle size={13} /><span><strong>{item.code.replaceAll("_", " ")}</strong>{item.message}</span></div>;
  return <section className={`verification-summary ${report.status}`}><div className="verification-heading"><span><ShieldCheck size={15} /> Verification · revision {report.fill_plan_revision}</span><strong>{report.status.replaceAll("_", " ")}</strong></div><div className="verification-stages"><span>Deterministic: {report.report.deterministic.status}</span><span>Independent: {report.report.independent.status}</span></div>{deterministic.map((item) => <Finding key={item.id} item={item} />)}{independent.map((item) => <Finding key={item.id} item={item} />)}{!deterministic.length && !independent.length && <small>No findings for this target.</small>}<small>Selections unchanged · {report.report.selection_hash_after.slice(0, 10)}</small></section>;
}

function mappingValue(target: FillPlanTarget) {
  const selected = target.candidates.find((candidate) => candidate.id === target.selected_candidate_id);
  if (selected) return selected.value === null ? "Explicitly absent" : String(selected.value);
  return target.state === "not_applicable" ? "Not writable" : "Unresolved";
}

function mappingRawValue(target: FillPlanTarget) {
  return target.candidates.find((candidate) => candidate.id === target.selected_candidate_id)?.value ?? null;
}

function MappingProperties({ target, onReview, pending, decisions }: { target: FillPlanTarget; onReview: (input: { action: ReviewAction; reason?: string; candidate_id?: string; value?: unknown }) => void; pending: boolean; decisions: ReviewDecision[] }) {
  const selected = target.candidates.find((candidate) => candidate.id === target.selected_candidate_id);
  return <><div className="panel-title"><div><p className="eyebrow">Human review</p><h2>{target.review ? "Human decision" : selected ? "Review proposal" : target.state === "not_applicable" ? "Excluded target" : "Resolve exception"}</h2></div></div><div className="mapping-properties"><div className="target-identity"><span>Target</span><strong>{target.field.label}</strong><small>{target.field.semantic_type ?? "No semantic type"} · {target.field.field_type}</small></div>{target.review && <div className={`review-authority ${target.review.status}`}><strong>{target.review.status.replaceAll("_", " ")}</strong><span>{target.review.action.replaceAll("_", " ")} by {target.review.actor}</span>{target.review.prefilled_disposition && <small>Prefilled value: {target.review.prefilled_disposition}</small>}{target.review.reason && <small>{target.review.reason}</small>}</div>}{selected && <CandidateCard candidate={selected} selected />}{target.issues.map((issue) => <div className={`mapping-issue ${issue.severity}`} key={issue.id}><AlertTriangle size={13} /><span><strong>{issue.code.replaceAll("_", " ")}</strong>{issue.message}</span></div>)}<ReviewControls target={target} selected={selected} pending={pending} onReview={onReview} />{target.candidates.filter((candidate) => candidate.origin !== "human").length > 0 && <><p className="eyebrow alternatives-title">Grounded candidates</p>{target.candidates.filter((candidate) => candidate.origin !== "human").map((candidate) => <div key={candidate.id}><CandidateCard candidate={candidate} selected={candidate.id === target.selected_candidate_id} /><button className="secondary candidate-select" disabled={pending} onClick={() => onReview({ action: "select_candidate", candidate_id: candidate.id })}>Select and approve</button></div>)}</>} {!target.candidates.length && target.state !== "not_applicable" && <p className="empty-candidates">No source fact passed semantic, type, and entity filtering. A reviewer may enter an explicit value or exception.</p>}{decisions.length > 0 && <div className="decision-history"><p className="eyebrow">Decision history</p>{decisions.map((decision) => <div key={decision.id}><strong>{decision.action.replaceAll("_", " ")}</strong><span>Revision {decision.source_revision} → {decision.resulting_revision}</span><small>{decision.actor}{decision.reason ? ` · ${decision.reason}` : ""}</small></div>)}</div>}</div></>;
}

function ReviewControls({ target, selected, pending, onReview }: { target: FillPlanTarget; selected?: MappingCandidate; pending: boolean; onReview: (input: { action: ReviewAction; reason?: string; candidate_id?: string; value?: unknown }) => void }) {
  const [value, setValue] = useState("");
  const [reason, setReason] = useState("");
  const submitEdit = () => {
    const typed: unknown = target.field.field_type === "number" ? Number(value) : target.field.field_type === "boolean" ? value === "true" : value;
    onReview({ action: "edit", value: typed, reason: reason || undefined });
  };
  const exception = (action: ReviewAction) => onReview({ action, reason: reason || undefined });
  if (!target.field.writable || ["signature", "action"].includes(target.field.field_type)) return <p className="empty-candidates">This control is excluded by technical integrity rules.</p>;
  if (target.review?.dependency_notice && !target.review.dependency_notice.acknowledged) return <div className="review-controls"><label>Required acknowledgement reason<textarea value={reason} onChange={(event) => setReason(event.target.value)} /></label><button className="primary small" disabled={pending} onClick={() => exception("acknowledge_dependency")}>Acknowledge dependency change</button></div>;
  return <div className="review-controls">{selected && selected.origin !== "human" && <button className="primary small" disabled={pending} onClick={() => onReview({ action: "approve" })}><Check size={14} /> Approve proposal</button>}{target.field.current_value !== null && target.field.current_value !== undefined && target.field.current_value !== "" && <button className="secondary" disabled={pending} onClick={() => onReview({ action: "retain_prefilled", reason: reason || undefined })}>Retain prefilled value</button>}<label>Reviewer value{target.field.field_type === "boolean" ? <select value={value} onChange={(event) => setValue(event.target.value)}><option value="">Choose…</option><option value="true">Yes</option><option value="false">No</option></select> : target.field.field_type === "choice" ? <select value={value} onChange={(event) => setValue(event.target.value)}><option value="">Choose…</option>{target.field.options.map((option) => <option key={option}>{option}</option>)}</select> : <input type={target.field.field_type === "number" ? "number" : target.field.field_type === "date" ? "date" : "text"} value={value} onChange={(event) => setValue(event.target.value)} />}</label><label>Reason {target.field.required ? "(required for exceptions)" : "(optional)"}<textarea rows={2} value={reason} onChange={(event) => setReason(event.target.value)} /></label><button className="secondary" disabled={pending || value === ""} onClick={submitEdit}>Save reviewer edit</button><div className="exception-actions"><button disabled={pending} onClick={() => exception("clear")}>Clear</button><button disabled={pending} onClick={() => exception("not_applicable")}>N/A</button><button disabled={pending} onClick={() => exception("intentional_blank")}>Intentional blank</button></div></div>;
}

function CandidateCard({ candidate, selected }: { candidate: MappingCandidate; selected: boolean }) {
  const location = candidate.provenance[0];
  return <div className={`candidate-card ${selected ? "selected" : ""}`}><div className="candidate-heading"><strong>{candidate.value === null ? "Explicitly absent" : String(candidate.value)}</strong><span>{selected ? "selected" : "alternative"}</span></div><p>{candidate.fact_key ?? candidate.derivation?.operation} · {candidate.entity_role ?? "derived"}</p><div className="candidate-metrics"><span>Evidence {Math.round(candidate.evidence_confidence * 100)}%</span><span>Match {Math.round(candidate.match_score * 100)}%</span><span>{candidate.resolution}</span>{candidate.unit && <span>{candidate.unit}</span>}{candidate.date_context && <span>{candidate.date_context}</span>}</div>{location && <div className="location-card"><span>Exact evidence</span><strong>{formatEvidenceLocation(location)}</strong><small>{location.coordinate_system}</small></div>}{candidate.snapshot_id && candidate.source_artifact_id && <Link className="source-link" to={`/evidence/${candidate.source_artifact_id}/${candidate.snapshot_id}`}>Open immutable source evidence →</Link>}{candidate.derivation && <div className="derivation-card"><span>Derivation depth {candidate.derivation.depth}</span><strong>{candidate.derivation.operation}</strong><small>Inputs: {candidate.derivation.input_ids.join(", ")}</small></div>}{candidate.uncertainty.map((item) => <div className="uncertainty" key={item}><AlertTriangle size={13} />{item}</div>)}</div>;
}

function ArtifactCard({ item }: { item: Artifact }) {
  const Icon = item.kind === "pdf" ? FileText : FileSpreadsheet;
  return <Link className="artifact-card" to={`/templates/${item.id}/${item.draft_id}?run=${item.run_id}`}>
    <div className={`file-icon ${item.kind}`}><Icon /></div>
    <div><h3>{item.filename}</h3><p>{item.kind.toUpperCase()} · {(item.size_bytes / 1024).toFixed(0)} KB</p></div>
    <span className="hash">{item.sha256.slice(0, 8)}</span>
  </Link>;
}

function Inspector() {
  const { artifactId = "", draftId = "" } = useParams();
  const query = new URLSearchParams(location.search);
  const runId = query.get("run");
  const run = useQuery({ queryKey: ["run", runId], queryFn: () => api.run(runId!), enabled: Boolean(runId), refetchInterval: (q) => q.state.data?.status === "succeeded" || q.state.data?.status === "failed" ? false : 700 });
  const draftQuery = useQuery({ queryKey: ["draft", draftId], queryFn: () => api.draft(draftId), enabled: !runId || run.data?.status === "succeeded" });
  if (runId && run.data?.status !== "succeeded") return <Processing run={run.data} error={run.error} />;
  if (!draftQuery.data) return <div className="center"><LoaderCircle className="spin" /> Loading inspector…</div>;
  return <Editor initial={draftQuery.data} artifactId={artifactId} />;
}

function Processing({ run, error }: { run?: { status: string; progress: number; error?: string }; error: Error | null }) {
  return <div className="processing"><Brand /><div className="processing-card"><div className="radar"><span /></div><p className="eyebrow">Native inspection</p><h1>{run?.status === "failed" ? "Inspection stopped" : "Reading the document structure"}</h1><p>{run?.error || error?.message || "Reconciling fields, widgets, validations, names, and package parts."}</p><div className="progress"><i style={{ width: `${run?.progress ?? 4}%` }} /></div><span>{run?.progress ?? 4}%</span></div></div>;
}

function Editor({ initial, artifactId }: { initial: Draft; artifactId: string }) {
  const [draft, setDraft] = useState(initial);
  const [selectedId, setSelectedId] = useState(initial.schema.fields[0]?.id ?? "");
  const [filter, setFilter] = useState("");
  const [dirty, setDirty] = useState(false);
  const [toast, setToast] = useState("");
  const save = useMutation({ mutationFn: () => api.updateDraft(draft.id, draft.revision, draft.name, draft.schema), onSuccess: (next) => { setDraft(next); setDirty(false); setToast("Draft saved"); }, onError: (e) => setToast(e.message) });
  const publish = useMutation({ mutationFn: () => api.publish(draft.id, draft.revision), onSuccess: (version) => setToast(`Version ${version.version} published`), onError: (e) => setToast(e.message) });
  useEffect(() => { if (toast) { const timer = setTimeout(() => setToast(""), 3000); return () => clearTimeout(timer); } }, [toast]);
  const selected = draft.schema.fields.find((field) => field.id === selectedId);
  const update = (field: TemplateField) => { setDraft({ ...draft, schema: { ...draft.schema, fields: draft.schema.fields.map((item) => item.id === field.id ? field : item) } }); setDirty(true); };
  const remove = (id: string) => { const fields = draft.schema.fields.filter((field) => field.id !== id); setDraft({ ...draft, schema: { ...draft.schema, fields } }); setSelectedId(fields[0]?.id ?? ""); setDirty(true); };
  const add = () => { const page = Number((draft.schema.inspection as { format?: string }).format === "pdf" ? 1 : undefined); const firstSheet = ((draft.schema.inspection as { sheets?: Array<{ name: string }> }).sheets ?? [])[0]?.name; const field: TemplateField = { id: crypto.randomUUID(), label: "New field", field_type: "text", required: null, writable: true, options: [], location: firstSheet ? { kind: "xlsx_range", sheet: firstSheet, cell_range: "A1" } : { kind: "pdf_rect", page, rect: [36, 36, 180, 58], rotation: 0 } }; setDraft({ ...draft, schema: { ...draft.schema, fields: [...draft.schema.fields, field] } }); setSelectedId(field.id); setDirty(true); };
  const visible = draft.schema.fields.filter((field) => `${field.label} ${field.semantic_type ?? ""}`.toLowerCase().includes(filter.toLowerCase()));
  const inspection = draft.schema.inspection as { format?: "pdf" | "xlsx"; warnings?: string[]; page_count?: number; sheets?: Array<{ name: string; state: string; protected: boolean }> };
  return <div className="inspector">
    <header><Brand /><div className="crumb"><ChevronLeft size={16} /><Link to="/">Templates</Link><span>/</span><strong>{draft.name}</strong></div><div className="header-actions"><span className={dirty ? "unsaved" : "saved"}>{dirty ? "Unsaved changes" : <><Check size={14} /> Saved</>}</span><button className="secondary" onClick={() => save.mutate()} disabled={!dirty || save.isPending}><Save size={15} /> Save draft</button><button className="primary small" onClick={() => publish.mutate()} disabled={dirty || publish.isPending}><ShieldCheck size={16} /> Publish version</button></div></header>
    <div className="workbench">
      <aside className="field-list"><div className="panel-title"><div><p className="eyebrow">Detected structure</p><h2>{draft.schema.fields.length} fields</h2></div><button className="icon-button" aria-label="Add field" onClick={add}><Plus /></button></div><label className="search"><Search size={15} /><input placeholder="Find a field" value={filter} onChange={(e) => setFilter(e.target.value)} /></label><div className="field-scroll">{visible.map((field, index) => <button key={field.id} className={`field-row ${field.id === selectedId ? "active" : ""}`} onClick={() => setSelectedId(field.id)}><span className="field-number">{String(index + 1).padStart(2, "0")}</span><span><strong>{field.label}</strong><small>{field.location.kind === "pdf_rect" ? `Page ${field.location.page}` : `${field.location.sheet} · ${field.location.cell_range}`}</small></span>{!field.writable && <Lock size={13} />}</button>)}</div></aside>
      <main className="preview-panel"><div className="preview-toolbar"><div><strong>{inspection.format === "pdf" ? "Document preview" : "Workbook grid"}</strong><span> · original, read-only</span></div><div className="legend"><i className="detected" /> Detected <i className="selected" /> Selected</div></div>{inspection.warnings?.length ? <div className="warning"><AlertTriangle size={15} /> {inspection.warnings[0]}</div> : null}{inspection.format === "pdf" ? <PdfPreview artifactId={artifactId} fields={draft.schema.fields} selected={selectedId} onSelect={setSelectedId} pages={inspection.page_count ?? 1} /> : <WorkbookPreview artifactId={artifactId} fields={draft.schema.fields} selected={selectedId} onSelect={setSelectedId} sheets={inspection.sheets ?? []} />}</main>
      <aside className="properties">{selected ? <FieldProperties field={selected} update={update} remove={remove} /> : <div className="empty-property"><Archive /><p>Select a field to edit its definition.</p></div>}</aside>
    </div>{toast && <div className="toast">{toast}</div>}
  </div>;
}

function FieldProperties({ field, update, remove }: { field: TemplateField; update: (field: TemplateField) => void; remove: (id: string) => void }) {
  const set = <K extends keyof TemplateField>(key: K, value: TemplateField[K]) => update({ ...field, [key]: value });
  const setConstraint = (key: string, value: string) => update({ ...field, constraints: { ...(field.constraints ?? {}), [key]: value || undefined } });
  const setReviewedLabel = (value: string) => update({ ...field, label: value, label_origin: "human", review_state: "confirmed" });
  const setSemanticType = (value: string) => update({ ...field, semantic_type: value || null, semantic_type_origin: value ? "human" : "unknown", semantic_type_confidence: value ? 1 : null });
  const confidence = field.label_confidence == null ? null : Math.round(field.label_confidence * 100);
  return <><div className="panel-title"><div><p className="eyebrow">Field definition</p><h2>Edit field</h2></div><button className="icon-button danger" aria-label="Delete field" onClick={() => remove(field.id)}><X /></button></div><div className="form-stack"><div className={`review-chip ${field.review_state === "confirmed" ? "confirmed" : ""}`}>{field.review_state === "confirmed" ? "Confirmed" : `${field.label_origin === "layout" ? "Proposed from layout" : "Needs review"}${confidence == null ? "" : ` · ${confidence}%`}`}</div><label>Label<input value={field.label} onChange={(e) => setReviewedLabel(e.target.value)} /></label>{field.native_name && field.native_name !== field.label && <div className="native-name"><span>Native field</span><code>{field.native_name}</code></div>}{field.label_evidence?.length ? <div className="evidence-card"><span>Why this label?</span>{field.label_evidence.map((evidence, index) => <button key={`${evidence.relation}-${index}`} onClick={() => document.querySelector<HTMLButtonElement>(`[aria-label="${CSS.escape(field.label)}"]`)?.focus()}><strong>{evidence.relation.replaceAll("_", " ")}</strong><q>{evidence.text}</q><small>Page {evidence.page} · {evidence.source}</small></button>)}</div> : null}{field.widget_options?.some((option) => option.label || option.export_value) ? <div className="choice-map"><span>Visible choice → PDF value</span>{field.widget_options.filter((option) => option.label || option.export_value).map((option, index) => <div key={`${option.export_value}-${index}`}><strong>{option.label ?? "Unlabeled"}</strong><code>{option.export_value ?? "unknown"}</code></div>)}</div> : null}<label>Type<select value={field.field_type} onChange={(e) => set("field_type", e.target.value as FieldType)}>{["text", "number", "date", "boolean", "choice", "signature", "action", "unknown"].map((type) => <option key={type}>{type}</option>)}</select></label><label>Semantic type<input placeholder="e.g. applicant.legal_name" value={field.semantic_type ?? ""} onChange={(e) => setSemanticType(e.target.value)} /></label><label>Reporting period<input placeholder="e.g. 2026 or 2026-Q3" value={String(field.constraints?.reporting_period ?? "")} onChange={(e) => setConstraint("reporting_period", e.target.value)} /></label><label>Requiredness<select value={field.required === null || field.required === undefined ? "unknown" : String(field.required)} onChange={(e) => set("required", e.target.value === "unknown" ? null : e.target.value === "true")}><option value="unknown">Unknown</option><option value="true">Required</option><option value="false">Optional</option></select></label><label className="toggle-row"><span><strong>Writable</strong><small>Approved fill destination</small></span><input type="checkbox" checked={field.writable} disabled={field.field_type === "signature" || field.field_type === "action"} onChange={(e) => set("writable", e.target.checked)} /></label><div className="location-card"><span>Source location{(field.widget_count ?? 1) > 1 ? ` · ${field.widget_count} widgets` : ""}</span><strong>{field.location.kind === "pdf_rect" ? `Page ${field.location.page} · [${field.location.rect?.join(", ")}]` : `${field.location.sheet}!${field.location.cell_range}`}</strong><small>Original coordinate system retained</small></div><label>Notes<textarea rows={3} value={field.notes ?? ""} onChange={(e) => set("notes", e.target.value)} /></label></div></>;
}

function PdfPreview({ artifactId, fields, selected, onSelect, pages }: { artifactId: string; fields: TemplateField[]; selected: string; onSelect: (id: string) => void; pages: number }) {
  const [page, setPage] = useState(fields.find((f) => f.id === selected)?.location.page ?? 1);
  useEffect(() => { const next = fields.find((f) => f.id === selected)?.location.page; if (next) setPage(next); }, [selected, fields]);
  const pageFields = fields.flatMap((field) => (field.widgets?.length ? field.widgets : [field.location]).filter((location) => location.kind === "pdf_rect" && location.page === page).map((location, index) => ({ field, location, index })));
  return <div className="pdf-wrap"><div className="page-nav"><button disabled={page <= 1} onClick={() => setPage(page - 1)}>←</button><span>Page {page} of {pages}</span><button disabled={page >= pages} onClick={() => setPage(page + 1)}>→</button></div><div className="paper"><img alt={`PDF page ${page}`} src={`${API}/api/v1/artifacts/${artifactId}/pages/${page}.png?scale=1.5`} />{pageFields.map(({ field, location, index }) => <button title={`${field.label}${field.current_value == null ? "" : `: ${String(field.current_value)}`}`} aria-label={field.label} key={`${field.id}-${index}`} onClick={() => onSelect(field.id)} className={`pdf-field ${selected === field.id ? "active" : ""}`} style={pdfRectStyle(location)}>{field.current_value !== null && field.current_value !== undefined && <span>{String(field.current_value)}</span>}</button>)}</div></div>;
}

function pdfRectStyle(location: Location) {
  const [x1, y1, x2, y2] = location.rect ?? [0, 0, 0, 0];
  if (location.coordinate_system === "reducto-normalized-top-left") {
    return { left: `${x1 * 100}%`, top: `${y1 * 100}%`, width: `${(x2 - x1) * 100}%`, height: `${(y2 - y1) * 100}%` };
  }
  const width = location.page_width ?? 612;
  const height = location.page_height ?? 792;
  if (location.coordinate_system === "pixels-top-left") {
    const percent = (value: number, total: number) => `${Math.max(0, value / total * 100)}%`;
    return { left: percent(x1, width), top: percent(y1, height), width: percent(x2 - x1, width), height: percent(y2 - y1, height) };
  }
  const rotation = ((location.rotation ?? 0) % 360 + 360) % 360;
  const percent = (value: number, total: number) => `${Math.max(0, value / total * 100)}%`;
  if (rotation === 90) return { left: percent(y1, height), top: percent(x1, width), width: percent(y2 - y1, height), height: percent(x2 - x1, width) };
  if (rotation === 180) return { left: percent(width - x2, width), top: percent(y1, height), width: percent(x2 - x1, width), height: percent(y2 - y1, height) };
  if (rotation === 270) return { left: percent(height - y2, height), top: percent(width - x2, width), width: percent(y2 - y1, height), height: percent(x2 - x1, width) };
  return { left: percent(x1, width), top: percent(height - y2, height), width: percent(x2 - x1, width), height: percent(y2 - y1, height) };
}

function WorkbookPreview({ artifactId, fields, selected, onSelect, sheets }: { artifactId: string; fields: TemplateField[]; selected: string; onSelect: (id: string) => void; sheets: Array<{ name: string; state: string; protected: boolean }> }) {
  const target = fields.find((f) => f.id === selected);
  const [sheet, setSheet] = useState(target?.location.sheet ?? sheets[0]?.name ?? "");
  useEffect(() => { if (target?.location.sheet) setSheet(target.location.sheet); }, [target?.location.sheet]);
  const anchor = cellAnchor(target?.location.cell_range);
  const grid = useQuery({ queryKey: ["grid", artifactId, sheet, anchor.row, anchor.column], queryFn: () => api.grid(artifactId, sheet, anchor.row, anchor.column), enabled: Boolean(sheet) });
  const byCell = new Map(fields.filter((f) => f.location.sheet === sheet).map((f) => [cellAnchor(f.location.cell_range).coordinate, f]));
  return <div className="workbook"><div className="sheet-tabs">{sheets.map((item) => <button className={sheet === item.name ? "active" : ""} key={item.name} onClick={() => setSheet(item.name)}>{item.state !== "visible" && <span>◌</span>}{item.name}{item.protected && <Lock size={11} />}</button>)}</div><div className="grid-scroll">{grid.isLoading ? <div className="center"><LoaderCircle className="spin" /></div> : <table><tbody>{grid.data?.rows.map((row, rowIndex) => <tr key={rowIndex}>{row.map((cell: GridCell) => { const field = byCell.get(cell.coordinate); const preview = field?.current_value; return <td key={cell.coordinate} title={`${cell.coordinate}${cell.formula ? " · formula" : ""}`} className={`${field ? "detected-cell" : ""} ${field?.id === selected ? "selected-cell" : ""} ${cell.formula ? "formula" : ""}`} onClick={() => field && onSelect(field.id)}><small>{cell.coordinate}</small><span>{preview !== null && preview !== undefined ? String(preview) : cell.value == null ? "" : String(cell.value)}</span></td>; })}</tr>)}</tbody></table>}</div></div>;
}

function cellAnchor(cellRange?: string) {
  const match = (cellRange ?? "A1").match(/\$?([A-Z]+)\$?(\d+)/i);
  const letters = (match?.[1] ?? "A").toUpperCase();
  let column = 0;
  for (const letter of letters) column = column * 26 + letter.charCodeAt(0) - 64;
  const row = Number(match?.[2] ?? 1);
  return { row, column, coordinate: `${letters}${row}` };
}

export default App;
