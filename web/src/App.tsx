import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, Route, Routes, useNavigate, useParams } from "react-router-dom";
import { AlertTriangle, Archive, Check, ChevronLeft, FileSpreadsheet, FileText, LoaderCircle, Lock, Plus, Save, Search, ShieldCheck, Upload, X } from "lucide-react";
import { API, api, type GridCell } from "./api";
import type { Artifact, Draft, FieldType, Location, TemplateField } from "./types";

function App() {
  return <Routes><Route path="/" element={<Library />} /><Route path="/templates/:artifactId/:draftId" element={<Inspector />} /></Routes>;
}

function Brand() {
  return <Link className="brand" to="/"><span className="brand-mark"><span /></span><span>formwork<small>Document intelligence</small></span></Link>;
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
    <header><Brand /><div className="phase-badge"><span /> Phase 1 · Template inspector</div></header>
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
  const setReviewedLabel = (value: string) => update({ ...field, label: value, label_origin: "human", review_state: "confirmed" });
  const setSemanticType = (value: string) => update({ ...field, semantic_type: value || null, semantic_type_origin: value ? "human" : "unknown", semantic_type_confidence: value ? 1 : null });
  const confidence = field.label_confidence == null ? null : Math.round(field.label_confidence * 100);
  return <><div className="panel-title"><div><p className="eyebrow">Field definition</p><h2>Edit field</h2></div><button className="icon-button danger" aria-label="Delete field" onClick={() => remove(field.id)}><X /></button></div><div className="form-stack"><div className={`review-chip ${field.review_state === "confirmed" ? "confirmed" : ""}`}>{field.review_state === "confirmed" ? "Confirmed" : `${field.label_origin === "layout" ? "Proposed from layout" : "Needs review"}${confidence == null ? "" : ` · ${confidence}%`}`}</div><label>Label<input value={field.label} onChange={(e) => setReviewedLabel(e.target.value)} /></label>{field.native_name && field.native_name !== field.label && <div className="native-name"><span>Native field</span><code>{field.native_name}</code></div>}{field.label_evidence?.length ? <div className="evidence-card"><span>Why this label?</span>{field.label_evidence.map((evidence, index) => <button key={`${evidence.relation}-${index}`} onClick={() => document.querySelector<HTMLButtonElement>(`[aria-label="${CSS.escape(field.label)}"]`)?.focus()}><strong>{evidence.relation.replaceAll("_", " ")}</strong><q>{evidence.text}</q><small>Page {evidence.page} · {evidence.source}</small></button>)}</div> : null}{field.widget_options?.some((option) => option.label || option.export_value) ? <div className="choice-map"><span>Visible choice → PDF value</span>{field.widget_options.filter((option) => option.label || option.export_value).map((option, index) => <div key={`${option.export_value}-${index}`}><strong>{option.label ?? "Unlabeled"}</strong><code>{option.export_value ?? "unknown"}</code></div>)}</div> : null}<label>Type<select value={field.field_type} onChange={(e) => set("field_type", e.target.value as FieldType)}>{["text", "number", "date", "boolean", "choice", "signature", "action", "unknown"].map((type) => <option key={type}>{type}</option>)}</select></label><label>Semantic type<input placeholder="e.g. applicant.legal_name" value={field.semantic_type ?? ""} onChange={(e) => setSemanticType(e.target.value)} /></label><label>Requiredness<select value={field.required === null || field.required === undefined ? "unknown" : String(field.required)} onChange={(e) => set("required", e.target.value === "unknown" ? null : e.target.value === "true")}><option value="unknown">Unknown</option><option value="true">Required</option><option value="false">Optional</option></select></label><label className="toggle-row"><span><strong>Writable</strong><small>Approved fill destination</small></span><input type="checkbox" checked={field.writable} disabled={field.field_type === "signature" || field.field_type === "action"} onChange={(e) => set("writable", e.target.checked)} /></label><div className="location-card"><span>Source location{(field.widget_count ?? 1) > 1 ? ` · ${field.widget_count} widgets` : ""}</span><strong>{field.location.kind === "pdf_rect" ? `Page ${field.location.page} · [${field.location.rect?.join(", ")}]` : `${field.location.sheet}!${field.location.cell_range}`}</strong><small>Original coordinate system retained</small></div><label>Notes<textarea rows={3} value={field.notes ?? ""} onChange={(e) => set("notes", e.target.value)} /></label></div></>;
}

function PdfPreview({ artifactId, fields, selected, onSelect, pages }: { artifactId: string; fields: TemplateField[]; selected: string; onSelect: (id: string) => void; pages: number }) {
  const [page, setPage] = useState(fields.find((f) => f.id === selected)?.location.page ?? 1);
  useEffect(() => { const next = fields.find((f) => f.id === selected)?.location.page; if (next) setPage(next); }, [selected, fields]);
  const pageFields = fields.flatMap((field) => (field.widgets?.length ? field.widgets : [field.location]).filter((location) => location.kind === "pdf_rect" && location.page === page).map((location, index) => ({ field, location, index })));
  return <div className="pdf-wrap"><div className="page-nav"><button disabled={page <= 1} onClick={() => setPage(page - 1)}>←</button><span>Page {page} of {pages}</span><button disabled={page >= pages} onClick={() => setPage(page + 1)}>→</button></div><div className="paper"><img alt={`PDF page ${page}`} src={`${API}/api/v1/artifacts/${artifactId}/pages/${page}.png?scale=1.5`} />{pageFields.map(({ field, location, index }) => <button title={field.label} aria-label={field.label} key={`${field.id}-${index}`} onClick={() => onSelect(field.id)} className={`pdf-field ${selected === field.id ? "active" : ""}`} style={pdfRectStyle(location)} />)}</div></div>;
}

function pdfRectStyle(location: Location) {
  const [x1, y1, x2, y2] = location.rect ?? [0, 0, 0, 0];
  const width = location.page_width ?? 612;
  const height = location.page_height ?? 792;
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
  return <div className="workbook"><div className="sheet-tabs">{sheets.map((item) => <button className={sheet === item.name ? "active" : ""} key={item.name} onClick={() => setSheet(item.name)}>{item.state !== "visible" && <span>◌</span>}{item.name}{item.protected && <Lock size={11} />}</button>)}</div><div className="grid-scroll">{grid.isLoading ? <div className="center"><LoaderCircle className="spin" /></div> : <table><tbody>{grid.data?.rows.map((row, rowIndex) => <tr key={rowIndex}>{row.map((cell: GridCell) => { const field = byCell.get(cell.coordinate); return <td key={cell.coordinate} title={`${cell.coordinate}${cell.formula ? " · formula" : ""}`} className={`${field ? "detected-cell" : ""} ${field?.id === selected ? "selected-cell" : ""} ${cell.formula ? "formula" : ""}`} onClick={() => field && onSelect(field.id)}><small>{cell.coordinate}</small><span>{cell.value == null ? "" : String(cell.value)}</span></td>; })}</tr>)}</tbody></table>}</div></div>;
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
