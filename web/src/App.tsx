import { lazy, Suspense, useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, Route, Routes, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { AlertTriangle, Archive, Check, ChevronLeft, FileImage, FileSpreadsheet, FileText, LoaderCircle, Lock, Plus, Save, Search, ShieldCheck, Upload, X, Download, CheckCircle2 } from "lucide-react";
import { API, api, type GridCell } from "./api";
import type { Agency, Artifact, Draft, EvidenceFact, EvidenceLocation, EvidenceSource, FieldType, FormFill, FormFillField, FormFieldGeometry, FormFillSnippet, Location, TemplateField } from "./types";
import { ReviewWorkspace, SourceCrop } from "./ReviewWorkspace";

const FilledPdfEditor = lazy(() => import("./FilledPdfEditor").then((module) => ({ default: module.FilledPdfEditor })));

function App() {
  return <Routes><Route path="/" element={<Library />} /><Route path="/templates/:artifactId/:draftId" element={<Inspector />} /><Route path="/evidence" element={<EvidenceLibrary />} /><Route path="/evidence/process/:artifactId" element={<EvidenceRun />} /><Route path="/evidence/:artifactId/:snapshotId" element={<EvidenceInspector />} /><Route path="/form-fills" element={<FormFillLibrary />} /><Route path="/form-fills/:formFillId" element={<FormFillInspector />} /><Route path="/form-fills/:formFillId/edit" element={<Suspense fallback={<div className="center">Loading PDF editor…</div>}><FilledPdfEditor /></Suspense>} /></Routes>;
}

function Brand() {
  return <Link className="brand" to="/"><span className="brand-mark"><span /></span><span>formwork<small>Document intelligence</small></span></Link>;
}

function WorkspaceNav() {
  return <nav className="workspace-nav"><Link to="/">Templates</Link><Link to="/evidence">Evidence</Link><Link to="/form-fills">Form Fills</Link></nav>;
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
  const [searchParams] = useSearchParams();
  return <EvidenceViewLoader artifactId={artifactId} snapshotId={snapshotId} selectedFactId={searchParams.get("fact") ?? undefined} />;
}

function EvidenceViewLoader({ artifactId, snapshotId, selectedFactId }: { artifactId: string; snapshotId: string; selectedFactId?: string }) {
  const snapshot = useQuery({ queryKey: ["evidence-snapshot", snapshotId], queryFn: () => api.evidenceSnapshot(snapshotId) });
  if (!snapshot.data) return <div className="center"><LoaderCircle className="spin" /> Loading evidence snapshot…</div>;
  return <EvidenceView artifactId={artifactId} data={snapshot.data.snapshot} snapshotHash={snapshot.data.snapshot_sha256} selectedFactId={selectedFactId} />;
}

function EvidenceView({ artifactId, data, snapshotHash, selectedFactId }: { artifactId: string; data: Awaited<ReturnType<typeof api.evidenceSnapshot>>["snapshot"]; snapshotHash: string; selectedFactId?: string }) {
  const [selectedId, setSelectedId] = useState(selectedFactId && data.facts.some((fact) => fact.id === selectedFactId) ? selectedFactId : data.facts[0]?.id ?? "");
  const [filter, setFilter] = useState("");
  const selected = data.facts.find((fact) => fact.id === selectedId);
  const visible = data.facts.filter((fact) => `${fact.label} ${String(fact.value ?? "explicitly absent")} ${fact.entity_role}`.toLowerCase().includes(filter.toLowerCase()));
  useEffect(() => {
    if (selectedFactId && data.facts.some((fact) => fact.id === selectedFactId)) setSelectedId(selectedFactId);
  }, [selectedFactId, data.facts]);
  useEffect(() => {
    document.querySelector<HTMLElement>(`[data-fact-id="${selectedId}"]`)?.scrollIntoView?.({ block: "nearest" });
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
  const location = fact?.provenance.find((item) => item.kind === "pdf_rect" || item.kind === "image_rect") ?? fact?.provenance[0];
  const [page, setPage] = useState(location?.page ?? 1);
  useEffect(() => { if (location?.page) setPage(location.page); }, [fact?.id, location?.page]);
  if (!fact || !location) return <div className="center">Select a fact to inspect its source.</div>;
  if (location.kind === "xlsx_range") {
    const field: TemplateField = { id: fact.id, label: fact.label, field_type: "text", writable: false, options: [], location: { kind: "xlsx_range", sheet: location.sheet, cell_range: location.cell_range } };
    const sheets = [...new Set(blocks.map((block) => block.source.sheet).filter(Boolean))].map((name) => ({ name: name!, state: "visible", protected: false }));
    return <WorkbookPreview artifactId={artifactId} fields={[field]} selected={field.id} onSelect={() => undefined} sheets={sheets} />;
  }
  if (location.kind === "text_span") return <div className="text-source">{blocks.filter((block) => block.source.kind === "text_span").map((block, index) => {
    const matchingFact = facts.find((item) => item.provenance.some((source) => source.kind === "text_span" && source.char_start === block.source.char_start && source.char_end === block.source.char_end));
    return <button type="button" className={`text-source-block ${block.source.char_start === location.char_start ? "active" : ""}`} key={index} onClick={() => matchingFact && onSelect(matchingFact.id)}>{block.text}</button>;
  })}</div>;
  const pages = [...new Set(facts.flatMap((item) => item.provenance.filter((spot) => spot.artifact_id === artifactId && spot.kind === location.kind && spot.rect).map((spot) => spot.page ?? 1)))].sort((a, b) => a - b);
  const selectedPage = pages.includes(page) ? page : location.page ?? pages[0] ?? 1;
  const imageUrl = location.kind === "pdf_rect" ? `${API}/api/v1/artifacts/${artifactId}/pages/${selectedPage}.png?scale=1.5` : `${API}/api/v1/artifacts/${artifactId}/content`;
  const regions = facts.flatMap((item) => item.provenance.map((spot, index) => ({ item, spot, index })))
    .filter(({ spot }) => spot.artifact_id === artifactId && spot.kind === location.kind && (spot.page ?? 1) === selectedPage && spot.rect)
    .sort((a, b) => Number(a.item.id === fact.id) - Number(b.item.id === fact.id));
  return <div className="pdf-wrap"><div className="page-nav">{location.kind === "pdf_rect" && <button aria-label="Previous evidence page" disabled={!pages.length || selectedPage <= pages[0]} onClick={() => setPage(pages[pages.indexOf(selectedPage) - 1])}>←</button>}<span>{location.kind === "image_rect" ? "Original image" : `Page ${selectedPage}${pages.length > 1 ? ` of ${pages.length}` : ""}`}</span>{location.kind === "pdf_rect" && <button aria-label="Next evidence page" disabled={!pages.length || selectedPage >= pages[pages.length - 1]} onClick={() => setPage(pages[pages.indexOf(selectedPage) + 1])}>→</button>}</div><div className="paper"><img alt={location.kind === "pdf_rect" ? `Original evidence page ${selectedPage}` : "Original evidence image"} src={imageUrl} />{regions.map(({ item, spot, index }) => {
    const overlay: Location = { kind: "pdf_rect", page: spot.page, rect: spot.rect, rotation: spot.rotation, page_width: spot.page_width, page_height: spot.page_height, coordinate_system: spot.coordinate_system };
    const selected = item.id === fact.id;
    const coarse = isCoarseRegion(spot);
    const fill = selected && !coarse ? item.accepted ? "rgba(216, 243, 122, 0.53)" : "rgba(243, 200, 122, 0.4)" : "transparent";
    return <button key={`${item.id}-${index}`} data-fact-region={item.id} aria-label={`${item.label}, location ${index + 1}`} title={item.label} className={`pdf-field evidence-region ${selected ? "active" : ""} ${coarse ? "coarse" : "precise"} ${item.accepted ? "" : "needs-review"}`} style={{ ...pdfRectStyle(overlay), zIndex: coarse ? 0 : selected ? 2 : 1, backgroundColor: fill }} onClick={() => onSelect(item.id)} />;
  })}</div></div>;
}

function isCoarseRegion(location: EvidenceLocation) {
  if (location.precision && ["coarse", "page", "page-sized", "approximate"].includes(location.precision.toLowerCase())) return true;
  const rect = location.rect;
  if (!rect || rect.length !== 4) return true;
  const [x1, y1, x2, y2] = rect;
  const width = location.page_width ?? 612;
  const height = location.page_height ?? 792;
  const normalized = location.coordinate_system === "reducto-normalized-top-left";
  const area = Math.abs((x2 - x1) * (y2 - y1));
  return normalized ? area >= 0.55 : area / (width * height) >= 0.55;
}

function ArtifactCard({ item }: { item: Artifact }) { const Icon = item.kind === "pdf" ? FileText : FileSpreadsheet; return <Link className="artifact-card" to={`/templates/${item.id}/${item.draft_id}?run=${item.run_id}`}><div className={`file-icon ${item.kind}`}><Icon /></div><div><h3>{item.filename}</h3><p>{item.kind.toUpperCase()} · {(item.size_bytes / 1024).toFixed(0)} KB</p></div><span className="hash">{item.sha256.slice(0, 8)}</span></Link>; }

function ArtifactPlanCard({ artifact, caseKey, creating, onCreate }: { artifact: Artifact; caseKey: string; creating: boolean; onCreate: (versionId: string) => void }) {
  const versions = useQuery({ queryKey: ["versions", artifact.draft_id], queryFn: () => api.versions(artifact.draft_id) });
  const draft = useQuery({ queryKey: ["draft", artifact.draft_id], queryFn: () => api.draft(artifact.draft_id) });
  const latest = [...(versions.data ?? [])].sort((a, b) => b.version - a.version)[0];
  const stale = Boolean(latest && draft.data && draft.data.revision > latest.source_revision);
  const disabled = creating || !caseKey || !latest || stale || versions.isLoading || draft.isLoading;
  return <button className="artifact-card mapping-create template-version-card" onClick={() => latest && onCreate(latest.id)} disabled={disabled}><div className={`file-icon ${artifact.kind}`}>{artifact.kind === "pdf" ? <FileText /> : <FileSpreadsheet />}</div><div><h3>{artifact.filename}</h3><small>{latest ? `Published version ${latest.version}` : "No published version"}</small></div><Plus size={16} /></button>;
}

function FormFillLibrary() {
  const navigate = useNavigate();
  const [caseKey, setCaseKey] = useState("");
  const [agencyKey, setAgencyKey] = useState("");
  const [creationRunId, setCreationRunId] = useState<string | null>(null);
  const client = useQueryClient();
  const fills = useQuery({ queryKey: ["form-fills"], queryFn: api.listFormFills });
  const templates = useQuery({ queryKey: ["artifacts"], queryFn: api.listArtifacts });
  const cases = useQuery({ queryKey: ["cases"], queryFn: api.listCases });
  const agencies = useQuery({ queryKey: ["agencies"], queryFn: api.listAgencies });
  const activeCase = caseKey || cases.data?.[0]?.case_key || "";
  const activeAgency = agencyKey || agencies.data?.find((item) => item.is_default)?.key || "";
  const creationRun = useQuery({ queryKey: ["run", creationRunId], queryFn: () => api.run(creationRunId!), enabled: Boolean(creationRunId), refetchInterval: (q) => ["succeeded", "failed"].includes(q.state.data?.status ?? "") ? false : 700 });
  const create = useMutation({ mutationFn: (versionId: string) => api.createFormFill(activeCase, versionId, activeAgency), onSuccess: (run) => { setCreationRunId(run.id); } });
  useEffect(() => { const id = creationRun.data?.result?.form_fill_id; if (creationRun.data?.status === "succeeded" && typeof id === "string") { client.invalidateQueries({ queryKey: ["form-fills"] }); navigate(`/form-fills/${id}`); } }, [creationRun.data?.status, creationRun.data?.result, navigate, client]);
  return <div className="app-shell"><header><Brand /><WorkspaceNav /><div className="phase-badge"><span /> Form fills</div></header><main className="library form-fill-library"><section className="hero form-fill-hero"><p className="eyebrow">Simple mapping</p><h1>Fill forms.<br /><em>Review once.</em></h1><p>Choose a case and published template. Sol proposes values and you review the form before generating the carrier file.</p><div className="upload-row"><label className="picker"><span>Agency</span><select aria-label="Filing agency" value={activeAgency} onChange={(e) => setAgencyKey(e.target.value)}>{agencies.data?.map((item) => <option key={item.key} value={item.key}>{item.name}</option>)}</select></label><label className="picker"><span>Case</span><select aria-label="Mapping case" value={activeCase} onChange={(e) => setCaseKey(e.target.value)}>{cases.data?.length ? cases.data.map((item) => <option key={item.case_key} value={item.case_key}>{item.case_key} · {item.source_count} source{item.source_count === 1 ? "" : "s"}</option>) : <option value="">No ingested cases yet</option>}</select></label></div>{create.error && <div className="error-banner">{create.error.message}</div>}{creationRun.data && !["succeeded", "failed"].includes(creationRun.data.status) && <div className="run-progress"><LoaderCircle size={16} className="spin" /><span>Mapping with Sol…</span><strong>{creationRun.data.progress}%</strong></div>}{creationRun.data?.status === "failed" && <div className="error-banner">{creationRun.data.error ?? "Form Fill mapping failed"}</div>}</section><section className="recent"><div className="section-heading"><div><span className="eyebrow">Published targets</span><h2>Start a Form Fill</h2></div><span>{templates.data?.length ?? 0} templates</span></div><div className="artifact-grid">{templates.data?.map((artifact) => <ArtifactPlanCard key={artifact.id} artifact={artifact} caseKey={activeCase} creating={create.isPending || Boolean(creationRunId && creationRun.data?.status === "running")} onCreate={(versionId) => { setCreationRunId(null); create.mutate(versionId); }} />)}</div></section><section className="recent"><div className="section-heading"><div><span className="eyebrow">Review queue</span><h2>Form Fills</h2></div><span>{fills.data?.length ?? 0} total</span></div>{fills.isLoading ? <div className="empty"><LoaderCircle className="spin" /> Loading Form Fills</div> : fills.data?.length ? <div className="artifact-grid">{fills.data.map((fill) => <Link className="artifact-card" key={fill.id} to={`/form-fills/${fill.id}`}><div className="file-icon mapping"><FileText /></div><div><h3>{fill.template_name}</h3><p>{fill.case_key} · {fill.status}</p></div><span className="hash">open</span></Link>)}</div> : <div className="empty">No Form Fills yet. Choose a case and template above.</div>}</section></main></div>;
}

function FormFillInspector() {
  const { formFillId = "" } = useParams();
  const fill = useQuery({ queryKey: ["form-fill", formFillId], queryFn: () => api.formFill(formFillId) });
  if (!fill.data) return <div className="center"><LoaderCircle className="spin" /> Loading Form Fill…</div>;
  return <FormFillView fill={fill.data} />;
}

function FormFillView({ fill }: { fill: FormFill }) {
  if (fill.target_kind === "pdf") return <ReviewWorkspace initial={fill} />;
  return <WorkbookFormFillView fill={fill} />;
}

function WorkbookFormFillView({ fill }: { fill: FormFill }) {
  const client = useQueryClient();
  const [selectedId, setSelectedId] = useState(fill.fields[0]?.field.id ?? "");
  const [listMode, setListMode] = useState<"review" | "all">("review");
  const [search, setSearch] = useState("");
  const selected = fill.fields.find((item) => item.field.id === selectedId) ?? fill.fields[0];
  const update = useMutation({ mutationFn: (input: { write_value?: unknown; evidence_fact_ids?: string[]; geometry?: FormFieldGeometry; review_status?: "accepted" | "needs_review" }) => api.updateFormFillField(fill.id, selectedId, input), onSuccess: () => client.invalidateQueries({ queryKey: ["form-fill", fill.id] }) });
  const approve = useMutation({ mutationFn: () => api.approveAndExport(fill.id), onSuccess: () => client.invalidateQueries({ queryKey: ["form-fill", fill.id] }) });
  const inspection = selected?.field.location.kind === "pdf_rect" ? { format: "pdf" as const, page_count: Math.max(...fill.fields.flatMap((x) => [x.field.location.page ?? 1])) } : { format: "xlsx" as const, sheets: [...new Set(fill.fields.map((x) => x.field.location.sheet).filter(Boolean))].map((name) => ({ name: name!, state: "visible", protected: false })) };
  const [value, setValue] = useState(selected?.write_value == null ? "" : String(selected.write_value));
  const [linkedFactId, setLinkedFactId] = useState("");
  const [unusedFactId, setUnusedFactId] = useState("");
  const [unusedFieldId, setUnusedFieldId] = useState(fill.fields[0]?.field.id ?? "");
  const [unusedValue, setUnusedValue] = useState("");
  const linkUnused = useMutation({ mutationFn: () => api.updateFormFillField(fill.id, unusedFieldId, { write_value: unusedValue, evidence_fact_ids: [unusedFactId] }), onSuccess: () => { client.invalidateQueries({ queryKey: ["form-fill", fill.id] }); setUnusedFactId(""); } });
  useEffect(() => setValue(selected?.write_value == null ? "" : String(selected.write_value)), [selected?.field.id, selected?.write_value]);
  useEffect(() => setLinkedFactId(selected?.evidence_fact_ids?.[0] ?? ""), [selected?.field.id, selected?.evidence_fact_ids]);
  const save = () => update.mutate({ write_value: value, evidence_fact_ids: linkedFactId ? [linkedFactId] : [] });
  const confidenceOf = (item: FormFillField): "high" | "medium" | "low" => item.confidence ?? (item.classification === "tentative" || item.answer_kind === "tentative" ? "low" : item.classification === "inferred" || item.answer_kind === "inferred" ? "medium" : "high");
  const needsReview = (item: FormFillField) => item.review_status !== "accepted" && (confidenceOf(item) !== "high" || (item.field.required && (item.write_value == null || item.write_value === "")));
  const reviewCount = fill.fields.filter(needsReview).length;
  const visibleFields = fill.fields.filter((item) => (listMode === "all" || needsReview(item)) && `${item.field.label} ${String(item.write_value ?? "")}`.toLowerCase().includes(search.toLowerCase()));
  const fieldViews = fill.fields.map((item) => ({ ...item.field, current_value: item.write_value, review_confidence: confidenceOf(item), review_status: item.review_status, source_preview: item.snippets?.[0] ?? item.original_snippets?.[0], title: [item.snippets?.map((s) => s.text).join("\n"), item.explanation, item.assumption].filter(Boolean).join("\n") || (item.origin === "human" ? "Entered by reviewer" : "No source snippet") }));
  const evidence = normalizeEvidence(fill);
  const groups = ["business", "people", "vehicles", "coverage", "other"];
  const category = (fact: EvidenceFact) => { const r = `${fact.entity_role} ${fact.key} ${fact.label}`.toLowerCase(); if (/coverage|policy|limit|claim|loss|liability|cargo|deductible|insurance/.test(r)) return "coverage"; if (/business|company|applicant|insured|owner/.test(r)) return "business"; if (/driver|person|people|officer|employee/.test(r)) return "people"; if (/vehicle|truck|auto|unit/.test(r)) return "vehicles"; return "other"; };
  const substantiveEvidence = evidence.filter((item) => item.substantive !== false && isSubstantiveFact(item.fact));
  const selectEvidence = (item: FormFill["evidence"][number]) => {
    const firstFieldId = item.field_ids
      .filter((id) => fill.fields.some((candidate) => candidate.field.id === id))
      .sort((a, b) => fill.fields.findIndex((candidate) => candidate.field.id === a) - fill.fields.findIndex((candidate) => candidate.field.id === b))[0];
    if (firstFieldId) { setSelectedId(firstFieldId); return; }
    setUnusedFactId(item.fact.id);
    setUnusedFieldId(fill.fields.find((candidate) => candidate.field.id === selectedId && candidate.field.writable !== false && !["action", "signature"].includes(candidate.field.field_type))?.field.id ?? fill.fields.find((candidate) => candidate.field.writable !== false && !["action", "signature"].includes(candidate.field.field_type))?.field.id ?? "");
    setUnusedValue(String(item.fact.value ?? ""));
  };
  const resize = (fieldId: string, geometry: FormFieldGeometry) => api.updateFormFillGeometry(fill.id, fieldId, geometry).then(() => client.invalidateQueries({ queryKey: ["form-fill", fill.id] }));
  const dirty = Boolean(selected && value !== (selected.write_value == null ? "" : String(selected.write_value)));
  const download = async () => {
    try {
      if (dirty && selected) await update.mutateAsync({ write_value: value, evidence_fact_ids: linkedFactId ? [linkedFactId] : [] });
      await approve.mutateAsync();
    } catch { /* Errors are shown in the review panel. */ }
  };
  return <div className="inspector mapping-inspector"><header><Brand /><div className="crumb"><ChevronLeft size={16} /><Link to="/form-fills">Form Fills</Link><span>/</span><strong>{fill.template_name}</strong></div><div className="header-actions"><span className={`snapshot-badge ${fill.status === "approved" ? "saved" : ""}`}>{update.isPending ? "Saving…" : dirty ? "Unsaved" : "Saved"}</span><button className="primary small" onClick={download} disabled={approve.isPending || update.isPending || ["mapping", "mapping_failed"].includes(fill.status)}>{approve.isPending ? "Generating…" : <><CheckCircle2 size={15} /> Download workbook</>}</button></div></header>{reviewCount > 0 && <div className="warning"><AlertTriangle size={15} /> {reviewCount} answer{reviewCount === 1 ? " needs" : "s need"} review. You can still download the workbook.</div>}<div className="workbench mapping-workbench"><aside className="field-list"><div className="panel-title"><div><p className="eyebrow">Review queue</p><h2>{reviewCount} need review</h2></div></div><div className="review-tabs"><button className={listMode === "review" ? "active" : ""} onClick={() => setListMode("review")}>Needs review</button><button className={listMode === "all" ? "active" : ""} onClick={() => setListMode("all")}>All answers</button></div><input className="review-search" aria-label="Search answers" placeholder="Search answers" value={search} onChange={(event) => setSearch(event.target.value)} /><div className="field-scroll">{visibleFields.length ? visibleFields.map((item) => <button key={item.field.id} className={`field-row mapping-row ${item.field.id === selectedId ? "active" : ""} confidence-${confidenceOf(item)}`} onClick={() => setSelectedId(item.field.id)}><span className="field-number">{item.review_status === "accepted" ? "✓" : confidenceOf(item) === "high" ? "" : "!"}</span><span><strong>{item.field.label}</strong><small>{item.write_value == null || item.write_value === "" ? "Blank" : String(item.write_value)}</small></span><AnswerBadge kind={confidenceOf(item)} /></button>) : <p className="review-empty">{listMode === "review" ? "All flagged answers are resolved." : "No matching answers."}</p>}</div></aside><main className="preview-panel"><div className="preview-toolbar"><div><strong>Workbook</strong><span> · select a highlighted cell to review or edit</span></div><div className="legend"><i className="inferred" /> Medium confidence <i className="tentative" /> Low confidence</div></div><WorkbookPreview artifactId={fill.target_artifact_id} fields={fieldViews} selected={selectedId} onSelect={setSelectedId} sheets={inspection.sheets ?? []} /></main><aside className="properties"><div className="panel-title"><div><p className="eyebrow">Human review</p><h2>{selected?.field.label}</h2></div></div>{selected && <div className="mapping-properties"><p className={`review-confidence ${confidenceOf(selected)}`}>{selected.review_status === "accepted" ? "Reviewed" : `${confidenceOf(selected).toUpperCase()} confidence${confidenceOf(selected) === "high" ? "" : " · review"}`}</p><label>Value<input value={value} onChange={(e) => setValue(e.target.value)} onKeyDown={(event) => { if (event.key === "Enter") save(); }} /></label><button className="primary small" onClick={save} disabled={update.isPending || selected.field.writable === false || ["action", "signature"].includes(selected.field.field_type)}><Save size={14} /> {update.isPending ? "Saving…" : "Save value"}</button><div className="sheet-mark-tools"><span>Add to selected cell</span>{([ ["✓", "✓"], ["Y", "Y"], ["N", "N"] ] as const).map(([label, mark]) => <button key={label} onClick={() => { setValue(mark); update.mutate({ write_value: mark, evidence_fact_ids: [], review_status: "accepted" }); }} disabled={update.isPending || selected.field.writable === false}>{label}</button>)}</div><div className="review-actions"><button onClick={() => update.mutate({ write_value: value, evidence_fact_ids: linkedFactId ? [linkedFactId] : [], review_status: "accepted" })} disabled={update.isPending}>Accept</button><button onClick={() => { setValue(""); update.mutate({ write_value: "", evidence_fact_ids: [], review_status: "accepted" }); }} disabled={update.isPending}>Clear</button></div>{(selected.explanation || selected.assumption) && <p className="review-reason">{selected.explanation || selected.assumption}</p>}{selected.snippets?.length ? <div className="evidence-card"><span>Source screenshot / excerpt</span>{selected.snippets.map((snippet, index) => <div key={index}><SourceCrop snippet={snippet} /><q>{snippet.text}</q>{snippet.artifact_id && <a className="review-source-link" href={`${API}/api/v1/artifacts/${snippet.artifact_id}/content${snippet.page ? `#page=${snippet.page}` : ""}`} target="_blank" rel="noreferrer">Open source{snippet.page ? ` · page ${snippet.page}` : ""} ↗</a>}</div>)}</div> : <div className="location-card"><span>Source</span><strong>{selected.origin === "human" ? "Entered by reviewer" : "No linked snippet"}</strong></div>}{update.error && <div className="error-banner">{update.error.message}</div>}</div>}{approve.error && <div className="error-banner">{approve.error.message}</div>}<div className="evidence-panel"><p className="eyebrow">Extracted information</p><p>{substantiveEvidence.filter((item) => item.used).length} used · {substantiveEvidence.filter((item) => !item.used).length} unused</p>{groups.map((group) => { const facts = substantiveEvidence.filter((item) => category(item.fact) === group); return facts.length ? <div key={group}><strong>{group}</strong>{facts.map((item) => <div className={`fact-use ${item.used ? "used" : "unused"} ${item.disposition === "partial" ? "partial" : ""}`} key={item.fact.id} role="button" tabIndex={0} onClick={() => selectEvidence(item)}><span>{item.fact.label}: {String(item.fact.value ?? "Explicitly absent")}</span><small>{item.used ? `Used in ${item.field_ids.length} field${item.field_ids.length === 1 ? "" : "s"}` : "Unused"}</small></div>)}</div> : null; })}</div>{unusedFactId && <div className="unused-fact-link"><strong>Use selected intake fact</strong><label>Field<select value={unusedFieldId} onChange={(event) => setUnusedFieldId(event.target.value)}>{fill.fields.filter((item) => item.field.writable !== false && !["action", "signature"].includes(item.field.field_type)).map((item) => <option key={item.field.id} value={item.field.id}>{item.field.label}</option>)}</select></label><label>Value<input value={unusedValue} onChange={(event) => setUnusedValue(event.target.value)} /></label><button className="primary small" onClick={() => linkUnused.mutate()} disabled={linkUnused.isPending || !unusedFieldId}>Save to field</button>{linkUnused.error && <div className="error-banner">{linkUnused.error.message}</div>}</div>}{fill.output_available && <a className="primary download-link" href={api.formFillOutput(fill.id)}><Download size={15} /> Download workbook</a>}{fill.output_error && <div className="error-banner">{fill.output_error}</div>}</aside></div></div>;
}

function normalizeEvidence(fill: FormFill): FormFill["evidence"] {
  const dispositions = new Map((fill.mapping_metadata?.fact_dispositions ?? []).map((item) => [item.fact_id, item]));
  const supplemental = fill.mapping_metadata?.supplemental_facts ?? [];
  const rows = fill.evidence.map((item) => {
    if (item.fact) return item;
    const supplemental = item.supplemental_fact;
    if (!supplemental) return item;
    return { ...item, fact: { id: `supplemental:${supplemental.key}:${supplemental.label}`, key: supplemental.key, label: supplemental.label, value: supplemental.value, raw_value: supplemental.raw_value ?? String(supplemental.value ?? ""), value_type: typeof supplemental.value, entity_id: "supplemental", entity_role: "other", provenance: [], confidence: 1, uncertainty: [], contradicts: [], accepted: true, semantics: [] } };
  });
  for (const item of supplemental) {
    const alreadyPresent = rows.some((row) => row.fact && row.fact.key === item.key && row.fact.label === item.label && String(row.fact.value ?? "") === String(item.value ?? ""));
    if (alreadyPresent) continue;
    const id = `supplemental:${item.key}:${item.label}`;
    rows.push({ fact: { id, key: item.key, label: item.label, value: item.value, raw_value: item.raw_value ?? String(item.value ?? ""), value_type: typeof item.value, entity_id: "supplemental", entity_role: "other", provenance: [], confidence: 1, uncertainty: [], contradicts: [], accepted: true, semantics: [] }, used: false, field_ids: [], disposition: "needs_review", explanation: item.explanation, substantive: true });
  }
  return rows.map((item) => {
    const disposition = dispositions.get(item.fact?.id);
    return disposition ? { ...item, disposition: disposition.disposition, field_ids: item.field_ids.length ? item.field_ids : disposition.field_ids ?? [], reason: item.reason ?? disposition.reason } : item;
  });
}

function isSubstantiveFact(fact: EvidenceFact) {
  if (fact.value !== null && fact.value !== undefined && String(fact.value).trim() !== "") return true;
  const raw = fact.raw_value?.trim().toLowerCase();
  // A non-empty raw value can be an intentional answer such as “None” or
  // “Not applicable”; only truly blank intake rows should disappear.
  return Boolean(raw);
}

function answerKind(field: { answer_kind?: string; classification?: string; origin?: string }) {
  const value = field.answer_kind ?? field.classification;
  if (value === "inferred" || value === "tentative" || value === "supported") return value;
  return field.origin === "human" ? "supported" : "supported";
}

function AnswerBadge({ kind }: { kind: string }) {
  const label = kind === "inferred" || kind === "medium" ? "Medium" : kind === "tentative" || kind === "low" ? "Low" : "High";
  return <span className={`answer-badge ${kind}`}>{label}</span>;
}

function AnswerReview({ field }: { field: FormFill["fields"][number] }) {
  const kind = answerKind(field);
  if (kind === "supported" && !field.explanation && !field.assumption) return null;
  return <div className={`answer-review ${kind}`}><AnswerBadge kind={kind} />{(field.explanation || field.assumption) && <span>{field.explanation || field.assumption}</span>}</div>;
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
  return <><div className="panel-title"><div><p className="eyebrow">Field definition</p><h2>Edit field</h2></div><button className="icon-button danger" aria-label="Delete field" onClick={() => remove(field.id)}><X /></button></div><div className="form-stack"><div className={`review-chip ${field.review_state === "confirmed" ? "confirmed" : ""}`}>{field.review_state === "confirmed" ? "Confirmed" : `${field.label_origin === "layout" ? "Proposed from layout" : "Needs review"}${confidence == null ? "" : ` · ${confidence}%`}`}</div><label>Label<input value={field.label} onChange={(e) => setReviewedLabel(e.target.value)} /></label>{field.native_name && field.native_name !== field.label && <div className="native-name"><span>Native field</span><code>{field.native_name}</code></div>}{field.label_evidence?.length ? <div className="evidence-card"><span>Why this label?</span>{field.label_evidence.map((evidence, index) => <button key={`${evidence.relation}-${index}`} onClick={() => document.querySelector<HTMLButtonElement>(`[aria-label="${CSS.escape(field.label)}"]`)?.focus()}><strong>{evidence.relation.replaceAll("_", " ")}</strong><q>{evidence.text}</q><small>Page {evidence.page} · {evidence.source}</small></button>)}</div> : null}{field.widget_options?.some((option) => option.label || option.export_value) ? <div className="choice-map"><span>Visible choice → PDF value</span>{field.widget_options.filter((option) => option.label || option.export_value).map((option, index) => <div key={`${option.export_value}-${index}`}><strong>{option.label ?? "Unlabeled"}</strong><code>{option.export_value ?? "unknown"}</code></div>)}</div> : null}<label>Type<select value={field.field_type} onChange={(e) => set("field_type", e.target.value as FieldType)}>{["text", "number", "date", "boolean", "choice", "signature", "action", "unknown"].map((type) => <option key={type}>{type}</option>)}</select></label><label>Semantic type<input placeholder="e.g. applicant.legal_name" value={field.semantic_type ?? ""} onChange={(e) => setSemanticType(e.target.value)} /></label><label>Reporting period<input placeholder="e.g. 2026 or 2026-Q3" value={String(field.constraints?.reporting_period ?? "")} onChange={(e) => setConstraint("reporting_period", e.target.value)} /></label>{field.constraints?.format_hint === "date" && <label>Date write format<input aria-label="Date write format" placeholder="e.g. ISO or mm/dd/yyyy" value={String(field.constraints.date_format ?? "")} onChange={(e) => setConstraint("date_format", e.target.value)} /><small>Set the format required by this field. It is used when preparing its PDF write value.</small></label>}<label>Requiredness<select value={field.required === null || field.required === undefined ? "unknown" : String(field.required)} onChange={(e) => set("required", e.target.value === "unknown" ? null : e.target.value === "true")}><option value="unknown">Unknown</option><option value="true">Required</option><option value="false">Optional</option></select></label><label className="toggle-row"><span><strong>Writable</strong><small>Approved fill destination</small></span><input type="checkbox" checked={field.writable} disabled={field.field_type === "signature" || field.field_type === "action"} onChange={(e) => set("writable", e.target.checked)} /></label><div className="location-card"><span>Source location{(field.widget_count ?? 1) > 1 ? ` · ${field.widget_count} widgets` : ""}</span><strong>{field.location.kind === "pdf_rect" ? `Page ${field.location.page} · [${field.location.rect?.join(", ")}]` : `${field.location.sheet}!${field.location.cell_range}`}</strong><small>Original coordinate system retained</small></div><label>Notes<textarea rows={3} value={field.notes ?? ""} onChange={(e) => set("notes", e.target.value)} /></label></div></>;
}

function PdfPreview({ artifactId, fields, selected, onSelect, pages, onResize }: { artifactId: string; fields: TemplateField[]; selected: string; onSelect: (id: string) => void; pages: number; onResize?: (fieldId: string, geometry: FormFieldGeometry) => void }) {
  const [page, setPage] = useState(fields.find((f) => f.id === selected)?.location.page ?? 1);
  const [drafts, setDrafts] = useState<Record<string, [number, number, number, number]>>({});
  useEffect(() => { const next = fields.find((f) => f.id === selected)?.location.page; if (next) setPage(next); }, [selected, fields]);
  const pageFields = fields.flatMap((field) => (field.widgets?.length ? field.widgets : [field.location]).map((location, index) => ({ field, location: field.id in drafts && index === 0 ? { ...location, rect: sourceRectToLocation(drafts[field.id]!, location), coordinate_system: "normalized-top-left" } : location, index })).filter(({ location }) => location.kind === "pdf_rect" && location.page === page));
  useEffect(() => { document.querySelector<HTMLElement>(`.pdf-field[data-field-id="${CSS.escape(selected)}"]`)?.scrollIntoView?.({ block: "center", inline: "center", behavior: "smooth" }); }, [selected, page]);
  return <div className="pdf-wrap"><div className="page-nav"><button disabled={page <= 1} onClick={() => setPage(page - 1)}>←</button><span>Page {page} of {pages}</span><button disabled={page >= pages} onClick={() => setPage(page + 1)}>→</button></div><div className="paper"><img alt={`PDF page ${page}`} src={`${API}/api/v1/artifacts/${artifactId}/pages/${page}.png?scale=1.5`} />{pageFields.map(({ field, location, index }) => { const active = selected === field.id; const box = pdfRectBox(location); return <button data-field-id={field.id} title={(field as TemplateField & { title?: string }).title ?? `${field.label}${field.current_value == null ? "" : `: ${String(field.current_value)}`}`} aria-label={field.label} key={`${field.id}-${index}`} onClick={() => onSelect(field.id)} className={`pdf-field ${active ? "active" : ""}`} style={pdfRectStyle(location)}>{field.current_value !== null && field.current_value !== undefined && <FieldValue value={String(field.current_value)} />}{active && index === 0 && Boolean(location.rotation) && <i className="field-geometry-disabled">Resize unavailable on rotated field</i>}{active && index === 0 && !Boolean(location.rotation) && onResize && <ResizeHandles box={box} onChange={(next) => setDrafts((current) => ({ ...current, [field.id]: next }))} onCommit={(next) => onResize(field.id, { page, rect: sourceBoxFromDisplay(next, location), coordinate_system: "normalized-top-left" })} />}</button>; })}</div></div>;
}

function ResizeHandles({ box, onChange, onCommit }: { box: [number, number, number, number]; onChange: (box: [number, number, number, number]) => void; onCommit: (box: [number, number, number, number]) => void }) {
  const start = useRef<{ x: number; y: number; paperWidth: number; paperHeight: number; box: [number, number, number, number]; corner: string } | null>(null);
  const latest = useRef(box);
  const move = (event: PointerEvent) => { const current = start.current; if (!current) return; const dx = (event.clientX - current.x) / current.paperWidth; const dy = (event.clientY - current.y) / current.paperHeight; let [x, y, width, height] = current.box; const min = 0.01; if (current.corner.includes("l")) { const next = Math.max(0, Math.min(x + width - min, x + dx)); width += x - next; x = next; } if (current.corner.includes("r")) width = Math.max(min, Math.min(1 - x, width + dx)); if (current.corner.includes("t")) { const next = Math.max(0, Math.min(y + height - min, y + dy)); height += y - next; y = next; } if (current.corner.includes("b")) height = Math.max(min, Math.min(1 - y, height + dy)); latest.current = [x, y, width, height]; onChange(latest.current); };
  const stop = () => { if (start.current) onCommit(latest.current); start.current = null; window.removeEventListener("pointermove", move); window.removeEventListener("pointerup", stop); };
  const handle = (corner: string) => <span className={`resize-handle ${corner}`} onPointerDown={(event) => { event.preventDefault(); event.stopPropagation(); const paper = (event.currentTarget as HTMLElement).closest(".paper")!.getBoundingClientRect(); latest.current = box; start.current = { x: event.clientX, y: event.clientY, paperWidth: paper.width, paperHeight: paper.height, box, corner }; window.addEventListener("pointermove", move); window.addEventListener("pointerup", stop); }} />;
  return <>{handle("tl")}{handle("tr")}{handle("bl")}{handle("br")}</>;
}

function FieldValue({ value }: { value: string }) {
  const ref = useRef<HTMLSpanElement>(null);
  const textRef = useRef<HTMLSpanElement>(null);
  const [overflow, setOverflow] = useState(false);
  useEffect(() => { const node = ref.current; const textNode = textRef.current; const parent = node?.parentElement; if (!node || !textNode || !parent) return; const measure = () => { setOverflow(textNode.scrollWidth > node.clientWidth + 1 || textNode.scrollHeight > node.clientHeight + 1); }; measure(); const observer = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(measure); observer?.observe(node); observer?.observe(parent); window.addEventListener("resize", measure); return () => { observer?.disconnect(); window.removeEventListener("resize", measure); }; }, [value]);
  return <><span ref={ref} className="field-value" title={overflow ? value : undefined}><span ref={textRef} className="field-value-text">{value}</span></span>{overflow && <i className="field-overflow" title="Value extends beyond this field; drag a corner to resize">!</i>}</>;
}

function pdfRectBox(location: Location): [number, number, number, number] {
  const [x1, y1, x2, y2] = location.rect ?? [0, 0, 0, 0];
  if (["reducto-normalized-top-left", "normalized-top-left"].includes(location.coordinate_system ?? "")) return [x1, y1, x2 - x1, y2 - y1];
  const width = location.page_width ?? 612, height = location.page_height ?? 792;
  const rotation = ((location.rotation ?? 0) % 360 + 360) % 360;
  if (["pixels-top-left", "pdf-top-left", "top-left", "provider-top-left"].includes(location.coordinate_system ?? "")) return [x1 / width, y1 / height, (x2 - x1) / width, (y2 - y1) / height];
  if (rotation === 90) return [y1 / height, x1 / width, (y2 - y1) / height, (x2 - x1) / width];
  if (rotation === 180) return [(width - x2) / width, y1 / height, (x2 - x1) / width, (y2 - y1) / height];
  if (rotation === 270) return [(height - y2) / height, (width - x2) / width, (y2 - y1) / height, (x2 - x1) / width];
  return [x1 / width, (height - y2) / height, (x2 - x1) / width, (y2 - y1) / height];
}

function sourceBoxFromDisplay(box: [number, number, number, number], location: Location): [number, number, number, number] {
  void location;
  return box;
}

function sourceRectToLocation(box: [number, number, number, number], location: Location): number[] {
  const [x, y, width, height] = box; return [x, y, x + width, y + height];
}

function pdfRectStyle(location: Location) {
  const [x1, y1, x2, y2] = location.rect ?? [0, 0, 0, 0];
  if (["reducto-normalized-top-left", "normalized-top-left"].includes(location.coordinate_system ?? "")) {
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
  if (["pdf-top-left", "top-left", "provider-top-left"].includes(location.coordinate_system ?? "")) {
    return { left: percent(x1, width), top: percent(y1, height), width: percent(x2 - x1, width), height: percent(y2 - y1, height) };
  }
  if (rotation === 90) return { left: percent(y1, height), top: percent(x1, width), width: percent(y2 - y1, height), height: percent(x2 - x1, width) };
  if (rotation === 180) return { left: percent(width - x2, width), top: percent(y1, height), width: percent(x2 - x1, width), height: percent(y2 - y1, height) };
  if (rotation === 270) return { left: percent(height - y2, height), top: percent(width - x2, width), width: percent(y2 - y1, height), height: percent(x2 - x1, width) };
  return { left: percent(x1, width), top: percent(height - y2, height), width: percent(x2 - x1, width), height: percent(y2 - y1, height) };
}

function WorkbookPreview({ artifactId, fields, selected, onSelect, sheets }: { artifactId: string; fields: Array<TemplateField & { current_value?: unknown; review_confidence?: "high" | "medium" | "low"; review_status?: string | null; source_preview?: FormFillSnippet; title?: string }>; selected: string; onSelect: (id: string) => void; sheets: Array<{ name: string; state: string; protected: boolean }> }) {
  const target = fields.find((f) => f.id === selected);
  const [sheet, setSheet] = useState(target?.location.sheet ?? sheets[0]?.name ?? "");
  const [hovered, setHovered] = useState("");
  useEffect(() => { if (target?.location.sheet) setSheet(target.location.sheet); }, [target?.location.sheet]);
  const anchor = cellAnchor(target?.location.cell_range);
  const grid = useQuery({ queryKey: ["grid", artifactId, sheet, anchor.row, anchor.column], queryFn: () => api.grid(artifactId, sheet, anchor.row, anchor.column), enabled: Boolean(sheet) });
  const byCell = new Map(fields.filter((f) => f.location.sheet === sheet).map((f) => [cellAnchor(f.location.cell_range).coordinate, f]));
  useEffect(() => { document.querySelector<HTMLElement>(`.grid-scroll [data-field-id="${CSS.escape(selected)}"]`)?.scrollIntoView?.({ block: "center", inline: "center", behavior: "smooth" }); }, [selected, sheet, grid.data]);
  const hoveredField = fields.find((field) => field.id === hovered);
  return <div className="workbook"><div className="sheet-tabs">{sheets.map((item) => <button className={sheet === item.name ? "active" : ""} key={item.name} onClick={() => setSheet(item.name)}>{item.state !== "visible" && <span>◌</span>}{item.name}{item.protected && <Lock size={11} />}</button>)}</div><div className="grid-scroll">{grid.isLoading ? <div className="center"><LoaderCircle className="spin" /></div> : <table><tbody>{grid.data?.rows.map((row, rowIndex) => <tr key={rowIndex}>{row.map((cell: GridCell) => { const field = byCell.get(cell.coordinate); const preview = field?.current_value; const confidence = field?.review_confidence ?? "high"; const unresolved = field?.review_status !== "accepted" && confidence !== "high"; return <td data-field-id={field?.id} key={cell.coordinate} tabIndex={field ? 0 : undefined} title={field ? `${field.title ?? cell.coordinate}${field.review_confidence && field.review_confidence !== "high" ? `\n${field.review_confidence} confidence · review` : ""}` : `${cell.coordinate}${cell.formula ? " · formula" : ""}`} className={`${field ? "detected-cell" : ""} ${field?.id === selected ? "selected-cell" : ""} ${field && unresolved ? `confidence-${confidence}` : ""} ${cell.formula ? "formula" : ""}`} onClick={() => field && onSelect(field.id)} onFocus={() => field && setHovered(field.id)} onBlur={() => setHovered("")} onMouseEnter={() => setHovered(field?.id ?? "")} onMouseLeave={() => setHovered("")}><small>{cell.coordinate}</small><span>{preview !== null && preview !== undefined ? String(preview) : cell.value == null ? "" : String(cell.value)}</span></td>; })}</tr>)}</tbody></table>}</div>{hoveredField && <div className="workbook-source-hover"><strong>{hoveredField.label} · {hoveredField.review_confidence} confidence</strong><SourceCrop snippet={hoveredField.source_preview} />{hoveredField.source_preview?.text && <q>{hoveredField.source_preview.text}</q>}</div>}</div>;
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
