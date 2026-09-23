import { useEffect, useRef, useState, type MouseEvent, type PointerEvent as ReactPointerEvent } from "react";
import { Link } from "react-router-dom";
import { API, api } from "./api";
import "./ReviewWorkspace.css";
import type { FormFill, FormFillField, FormFillSnippet, PageAnnotation, ReviewStyle, Location } from "./types";

type Rect = [number, number, number, number];
type FieldPatch = { write_value?: unknown; evidence_fact_ids?: string[]; style?: ReviewStyle; review_status?: "accepted" | "needs_review"; geometry?: { page: number; rect: Rect; coordinate_system: "normalized-top-left" } };
type History = { kind: "field"; id: string; before: FieldPatch; after: FieldPatch } | { kind: "annotations"; before: PageAnnotation[]; after: PageAnnotation[] };
const standardStyle: ReviewStyle = { font: "Helvetica", size: 11, bold: false, italic: false };

function box(location: Location): Rect {
  const [x1, y1, x2, y2] = location.rect ?? [0, 0, 0, 0];
  if (["normalized-top-left", "reducto-normalized-top-left"].includes(location.coordinate_system ?? "")) return [x1, y1, x2 - x1, y2 - y1];
  const width = location.page_width ?? 612, height = location.page_height ?? 792;
  if (["pixels-top-left", "pdf-top-left", "top-left", "provider-top-left"].includes(location.coordinate_system ?? "")) return [x1 / width, y1 / height, (x2 - x1) / width, (y2 - y1) / height];
  const rotation = ((location.rotation ?? 0) % 360 + 360) % 360;
  if (rotation === 90) return [y1 / height, x1 / width, (y2 - y1) / height, (x2 - x1) / width];
  if (rotation === 180) return [(width - x2) / width, y1 / height, (x2 - x1) / width, (y2 - y1) / height];
  if (rotation === 270) return [(height - y2) / height, (width - x2) / width, (y2 - y1) / height, (x2 - x1) / width];
  return [x1 / width, (height - y2) / height, (x2 - x1) / width, (y2 - y1) / height];
}
function rectStyle([x, y, width, height]: Rect) { return { left: `${x * 100}%`, top: `${y * 100}%`, width: `${width * 100}%`, height: `${height * 100}%` }; }
function confidence(field: FormFillField): "high" | "medium" | "low" {
  if (field.confidence) return field.confidence;
  return field.classification === "tentative" || field.answer_kind === "tentative" ? "low" : field.classification === "inferred" || field.answer_kind === "inferred" ? "medium" : "high";
}
function unresolved(field: FormFillField) { return field.review_status !== "accepted" && (confidence(field) !== "high" || (field.field.required && (field.write_value == null || field.write_value === ""))); }
function provenance(snippets: FormFillSnippet[]) { return snippets.find((item) => item.artifact_id && item.page && item.rect?.length === 4 && (!item.kind || item.kind === "pdf_rect")); }
export function SourceCrop({ snippet }: { snippet?: FormFillSnippet }) {
  if (!snippet) return <p className="review-source-empty">Exact source region unavailable. {" "}Use the excerpt below to review this answer.</p>;
  const [x, y, width, height] = box({ kind: "pdf_rect", ...snippet } as Location);
  if (x < 0 || y < 0 || width <= 0 || height <= 0 || x + width > 1 || y + height > 1) return <p className="review-source-empty">Exact source region unavailable. Use the excerpt below to review this answer.</p>;
  const image = `${API}/api/v1/artifacts/${snippet.artifact_id}/pages/${snippet.page}/crop.png?x=${Math.max(0, x)}&y=${Math.max(0, y)}&width=${Math.min(width, 1 - x)}&height=${Math.min(height, 1 - y)}`;
  return <img className="review-source-crop" alt={`Source screenshot, page ${snippet.page}`} src={image} />;
}

export function ReviewWorkspace({ initial }: { initial: FormFill }) {
  const [fill, setFill] = useState(initial);
  const [selectedId, setSelectedId] = useState(initial.fields[0]?.field.id ?? "");
  const [page, setPage] = useState(initial.fields[0]?.field.location.page ?? 1);
  const [draft, setDraft] = useState("");
  const [tool, setTool] = useState<"select" | "text" | "check" | "Y" | "N">("select");
  const [listMode, setListMode] = useState<"review" | "all">("review");
  const [rightMode, setRightMode] = useState<"answer" | "intake">("answer");
  const [search, setSearch] = useState("");
  const [zoom, setZoom] = useState(100);
  const [saveStatus, setSaveStatus] = useState<"saved" | "saving" | "retry">("saved");
  const [error, setError] = useState("");
  const [showDownloadWarning, setShowDownloadWarning] = useState(false);
  const [hoverId, setHoverId] = useState("");
  const [previewRects, setPreviewRects] = useState<Record<string, Rect>>({});
  const [undoStack, setUndoStack] = useState<History[]>([]);
  const [redoStack, setRedoStack] = useState<History[]>([]);
  const pending = useRef<Promise<void>>(Promise.resolve());
  const failed = useRef<(() => Promise<unknown>) | null>(null);
  const queue = useRef<Array<() => Promise<unknown>>>([]);
  const processing = useRef(false);
  const valueBefore = useRef("");
  const annotations = (fill.mapping_metadata?.annotations ?? []) as PageAnnotation[];
  const selected = fill.fields.find((item) => item.field.id === selectedId);
  const selectedAnnotation = annotations.find((item) => item.id === selectedId);
  const pageCount = fill.page_count ?? Math.max(1, ...fill.fields.map((item) => item.field.location.page ?? 1));
  const reviewCount = fill.fields.filter(unresolved).length;
  const selectedSnippet = selected ? provenance([...(selected.snippets ?? []), ...(selected.original_snippets ?? [])]) : undefined;

  useEffect(() => { setFill(initial); setSelectedId(initial.fields[0]?.field.id ?? ""); }, [initial.id]);
  useEffect(() => { const next = selected?.write_value == null ? "" : String(selected.write_value); setDraft(next); valueBefore.current = next; if (selected?.field.location.page) setPage(selected.field.location.page); }, [selectedId]);

  const runQueue = async () => {
    if (processing.current) return;
    processing.current = true;
    setSaveStatus("saving");
    while (queue.current.length) {
      const work = queue.current[0];
      try { await work(); queue.current.shift(); failed.current = null; }
      catch (cause) { failed.current = work; setSaveStatus("retry"); setError(cause instanceof Error ? cause.message : "Could not save review changes"); break; }
    }
    if (!failed.current) { setSaveStatus("saved"); setError(""); }
    processing.current = false;
  };
  const enqueue = (work: () => Promise<unknown>) => {
    queue.current.push(work);
    if (!processing.current && !failed.current) pending.current = runQueue();
    return pending.current;
  };
  const applyField = (id: string, patch: FieldPatch, before?: FieldPatch) => {
    const current = fill.fields.find((item) => item.field.id === id);
    if (!current) return;
    const old: FieldPatch = before ?? {
      ...(Object.hasOwn(patch, "write_value") ? { write_value: current.write_value, evidence_fact_ids: current.evidence_fact_ids ?? [] } : {}),
      ...(patch.style ? { style: current.style ?? standardStyle } : {}),
      ...(patch.review_status ? { review_status: current.review_status ?? "needs_review" } : {}),
      ...(patch.geometry ? { geometry: { page: current.field.location.page ?? 1, rect: box(current.field.location), coordinate_system: "normalized-top-left" } as const } : {}),
    };
    setFill((state) => ({ ...state, status: "mapped", output_available: false, fields: state.fields.map((item) => {
      if (item.field.id !== id) return item;
      return { ...item,
        ...(Object.hasOwn(patch, "write_value") ? { write_value: patch.write_value, origin: "human", evidence_fact_ids: patch.evidence_fact_ids ?? [], model_proposal: item.origin === "model" ? { evidence_fact_ids: item.evidence_fact_ids, source_block_ids: item.source_block_ids } : item.model_proposal, original_snippets: item.origin === "model" ? item.snippets : item.original_snippets, snippets: patch.evidence_fact_ids ? item.snippets : [] } : {}),
        ...(patch.style ? { style: patch.style } : {}),
        ...(patch.review_status ? { review_status: patch.review_status } : {}),
        ...(patch.geometry ? { field: { ...item.field, location: { ...item.field.location, page: patch.geometry.page, rect: [patch.geometry.rect[0], patch.geometry.rect[1], patch.geometry.rect[0] + patch.geometry.rect[2], patch.geometry.rect[1] + patch.geometry.rect[3]], coordinate_system: "normalized-top-left" } } } : {}),
      };
    }) }));
    void enqueue(() => api.updateFormFillField(fill.id, id, patch));
    setUndoStack((stack) => [...stack, { kind: "field", id, before: old, after: patch }]); setRedoStack([]);
  };
  const applyAnnotations = (next: PageAnnotation[], record = true) => {
    const before = annotations;
    setFill((state) => ({ ...state, status: "mapped", output_available: false, mapping_metadata: { ...state.mapping_metadata, annotations: next } }));
    void enqueue(() => api.updateFormAnnotations(fill.id, next));
    if (record) { setUndoStack((stack) => [...stack, { kind: "annotations", before, after: next }]); setRedoStack([]); }
  };
  const replay = (action: History, direction: "before" | "after") => {
    if (action.kind === "annotations") applyAnnotations(action[direction], false);
    else {
      const patch = action[direction];
      setFill((state) => ({ ...state, status: "mapped", output_available: false, fields: state.fields.map((item) => {
        if (item.field.id !== action.id) return item;
        return { ...item, ...(Object.hasOwn(patch, "write_value") ? { write_value: patch.write_value } : {}),
          ...(patch.style ? { style: patch.style } : {}), ...(patch.review_status ? { review_status: patch.review_status } : {}),
          ...(patch.geometry ? { field: { ...item.field, location: { ...item.field.location, page: patch.geometry.page, rect: [patch.geometry.rect[0], patch.geometry.rect[1], patch.geometry.rect[0] + patch.geometry.rect[2], patch.geometry.rect[1] + patch.geometry.rect[3]], coordinate_system: "normalized-top-left" } } } : {}),
        };
      }) }));
      void enqueue(() => api.updateFormFillField(fill.id, action.id, patch));
    }
  };
  const undo = () => { const action = undoStack.at(-1); if (!action) return; setUndoStack((stack) => stack.slice(0, -1)); setRedoStack((stack) => [...stack, action]); replay(action, "before"); };
  const redo = () => { const action = redoStack.at(-1); if (!action) return; setRedoStack((stack) => stack.slice(0, -1)); setUndoStack((stack) => [...stack, action]); replay(action, "after"); };
  const commitValue = () => { if (!selected || selected.field.field_type === "boolean" || selected.field.field_type === "choice" || draft === valueBefore.current) return; applyField(selected.field.id, { write_value: draft, review_status: "accepted" }); valueBefore.current = draft; };
  const setSelection = (id: string) => { commitValue(); setSelectedId(id); setRightMode("answer"); };
  const style = selected?.style ?? selectedAnnotation?.style ?? standardStyle;
  const updateStyle = (next: ReviewStyle) => { if (selected) applyField(selected.field.id, { style: next }); else if (selectedAnnotation) applyAnnotations(annotations.map((item) => item.id === selectedId ? { ...item, style: next } : item)); };
  const startDrag = (event: ReactPointerEvent, id: string, original: Rect, mode: "move" | "resize", field: boolean) => {
    event.preventDefault(); event.stopPropagation();
    const paper = (event.currentTarget as HTMLElement).closest(".review-paper")!.getBoundingClientRect();
    const x = event.clientX, y = event.clientY;
    let latest: Rect = original;
    const move = (pointer: PointerEvent) => {
      const dx = (pointer.clientX - x) / paper.width, dy = (pointer.clientY - y) / paper.height;
      latest = mode === "move" ? [Math.max(0, Math.min(1 - original[2], original[0] + dx)), Math.max(0, Math.min(1 - original[3], original[1] + dy)), original[2], original[3]] : [original[0], original[1], Math.max(0.01, Math.min(1 - original[0], original[2] + dx)), Math.max(0.01, Math.min(1 - original[1], original[3] + dy))];
      setPreviewRects((current) => ({ ...current, [id]: latest }));
    };
    const end = () => {
      window.removeEventListener("pointermove", move); window.removeEventListener("pointerup", end);
      setPreviewRects((current) => { const copy = { ...current }; delete copy[id]; return copy; });
      if (latest.every((value, index) => Math.abs(value - original[index]) < 0.0001)) return;
      if (field) applyField(id, { geometry: { page, rect: latest, coordinate_system: "normalized-top-left" } });
      else applyAnnotations(annotations.map((item) => item.id === id ? { ...item, rect: latest } : item));
    };
    window.addEventListener("pointermove", move); window.addEventListener("pointerup", end);
  };
  const place = (event: MouseEvent<HTMLElement>) => {
    if (tool === "select") return;
    const paper = (event.currentTarget.closest(".review-paper") ?? event.currentTarget).getBoundingClientRect();
    const x = Math.max(0, Math.min(0.92, (event.clientX - paper.left) / paper.width));
    const y = Math.max(0, Math.min(0.96, (event.clientY - paper.top) / paper.height));
    const annotation: PageAnnotation = { id: crypto.randomUUID(), kind: tool, text: tool === "check" ? "✓" : tool === "text" ? "Text" : tool, page, rect: [x, y, tool === "text" ? 0.2 : 0.045, 0.035], style: standardStyle };
    applyAnnotations([...annotations, annotation]); setSelectedId(annotation.id); setTool("select");
  };
  const markField = (field: FormFillField, event: MouseEvent<HTMLElement>) => {
    if (tool === "select") { setSelection(field.field.id); return; }
    if (field.field.writable === false) return;
    if (tool === "text") { setSelection(field.field.id); setTool("select"); return; }
    if (field.field.field_type === "boolean" || field.field.field_type === "choice" || /yes|no|check/i.test(field.field.label)) {
      const wanted = tool === "N" ? "no" : "yes";
      const matchingChoice = field.field.choice_options?.find((option) => [wanted, wanted === "yes" ? "y" : "n"].includes(option.display_value.toLowerCase()));
      const value = field.field.field_type === "boolean" ? tool !== "N" : matchingChoice?.display_value ?? (tool === "check" ? "Yes" : tool);
      applyField(field.field.id, { write_value: value, review_status: "accepted" }); setDraft(String(value)); valueBefore.current = String(value); setTool("select"); setSelectedId(field.field.id);
    } else place(event);
  };
  const download = async (force = false) => {
    commitValue();
    if (reviewCount && !force) { setShowDownloadWarning(true); return; }
    try {
      await pending.current;
      if (failed.current || queue.current.length) throw new Error("Save failed. Retry the save before downloading.");
      setSaveStatus("saving");
      await api.approveAndExport(fill.id);
      setSaveStatus("saved");
      setShowDownloadWarning(false);
      window.location.href = api.formFillOutput(fill.id);
    } catch (cause) { setSaveStatus(failed.current ? "retry" : "saved"); setError(cause instanceof Error ? cause.message : "Could not create PDF"); }
  };
  const visible = fill.fields.filter((item) => (listMode === "all" || unresolved(item)) && `${item.field.label} ${String(item.write_value ?? "")}`.toLowerCase().includes(search.toLowerCase()));
  const evidence = fill.evidence.filter((item) => item.substantive !== false);

  return <div className="inspector review-workspace">
    <header><Link className="review-back" to="/form-fills">← Form fills</Link><strong>{fill.template_name}</strong><span className={`review-save ${saveStatus}`}>{saveStatus === "saved" ? "Saved" : saveStatus === "saving" ? "Saving…" : "Save failed"}</span><button className="secondary" onClick={undo} disabled={!undoStack.length}>Undo</button><button className="secondary" onClick={redo} disabled={!redoStack.length}>Redo</button>{saveStatus === "retry" && <button className="secondary" onClick={() => { failed.current = null; pending.current = runQueue(); }}>Retry save</button>}<button className="primary small" onClick={() => void download()}>Download PDF</button></header>
    <div className="review-columns"><aside className="review-left"><div className="review-heading"><strong>Review queue</strong><small>{reviewCount} need review</small></div><div className="review-tabs"><button className={listMode === "review" ? "active" : ""} onClick={() => setListMode("review")}>Needs review</button><button className={listMode === "all" ? "active" : ""} onClick={() => setListMode("all")}>All answers</button></div><input className="review-search" aria-label="Search answers" placeholder="Search answers" value={search} onChange={(event) => setSearch(event.target.value)} /><div className="review-list">{visible.length ? visible.map((item) => <button key={item.field.id} className={`review-row ${selectedId === item.field.id ? "active" : ""} ${confidence(item)}`} onClick={() => setSelection(item.field.id)}><span className="review-dot" /><span><strong>{item.field.label}</strong><small>{item.write_value === null || item.write_value === "" ? "Blank" : String(item.write_value)} · {item.explanation || item.assumption || (confidence(item) === "low" ? "Check source" : "Inferred")}</small></span></button>) : <p className="review-empty">{listMode === "review" ? "All flagged answers are resolved." : "No matching answers."}</p>}</div></aside>
      <main className="review-main"><div className="review-toolbar"><span>Page {page} of {pageCount}</span><button onClick={() => setPage(Math.max(1, page - 1))} disabled={page <= 1}>←</button><button onClick={() => setPage(Math.min(pageCount, page + 1))} disabled={page >= pageCount}>→</button><span className="review-separator" /><label>Zoom <select value={zoom} onChange={(event) => setZoom(Number(event.target.value))}><option value={75}>75%</option><option value={100}>100%</option><option value={125}>125%</option><option value={150}>150%</option></select></label><span className="review-separator" />{(["select", "text", "check", "Y", "N"] as const).map((option) => <button key={option} className={tool === option ? "active" : ""} onClick={() => setTool(option)}>{option === "select" ? "Select" : option === "text" ? "+ Text" : option === "check" ? "+ ✓" : `+ ${option}`}</button>)}</div><div className="review-page-scroll"><div className="review-paper" style={{ width: `${Math.round(816 * zoom / 100)}px` }} onClick={place}><img src={`${API}/api/v1/artifacts/${fill.target_artifact_id}/pages/${page}.png?scale=1.5`} alt={`Carrier form page ${page}`} />{fill.fields.filter((item) => (item.field.location.page ?? 1) === page).map((item) => {
        const id = item.field.id, selectedHere = selectedId === id, rectangle = previewRects[id] ?? box(item.field.location), canEdit = item.field.writable !== false && !["action", "signature"].includes(item.field.field_type), source = provenance(item.snippets ?? []);
        return <div key={id} className={`review-field ${selectedHere ? "selected" : ""} ${unresolved(item) ? confidence(item) : "high"}`} style={rectStyle(rectangle)} onClick={(event) => { event.stopPropagation(); markField(item, event); }} tabIndex={0} onFocus={() => setHoverId(id)} onBlur={() => setHoverId("")} onMouseEnter={() => setHoverId(id)} onMouseLeave={() => setHoverId("")}>
          {selectedHere && canEdit && <span className="review-move" role="button" aria-label={`Move ${item.field.label}`} onPointerDown={(event) => startDrag(event, id, rectangle, "move", true)}>⠿</span>}
          {selectedHere && canEdit ? item.field.field_type === "boolean" ? <select aria-label={`Edit ${item.field.label} on form`} value={item.write_value === true ? "yes" : item.write_value === false ? "no" : ""} onClick={(event) => event.stopPropagation()} onChange={(event) => applyField(id, { write_value: event.target.value === "" ? null : event.target.value === "yes", review_status: "accepted" })}><option value="">Blank</option><option value="yes">✓ Yes</option><option value="no">No</option></select> : item.field.field_type === "choice" && item.field.choice_options?.length ? <select aria-label={`Edit ${item.field.label} on form`} value={draft} onClick={(event) => event.stopPropagation()} onChange={(event) => { setDraft(event.target.value); applyField(id, { write_value: event.target.value, review_status: "accepted" }); }}><option value="">Blank</option>{item.field.choice_options.map((option) => <option key={option.export_value} value={option.display_value}>{option.display_value}</option>)}</select> : <input aria-label={`Edit ${item.field.label} on form`} value={draft} onChange={(event) => setDraft(event.target.value)} onBlur={commitValue} onKeyDown={(event) => { if (event.key === "Enter") event.currentTarget.blur(); }} style={{ fontFamily: style.font, fontSize: style.size * zoom / 100, fontWeight: style.bold ? 700 : 400, fontStyle: style.italic ? "italic" : "normal" }} /> : <span className="review-field-value">{item.write_value == null || item.write_value === "" ? "" : typeof item.write_value === "boolean" ? item.write_value ? "✓" : "" : String(item.write_value)}</span>}
          {selectedHere && canEdit && <span className="review-resize" role="button" aria-label={`Resize ${item.field.label}`} onPointerDown={(event) => startDrag(event, id, rectangle, "resize", true)} />}
          {(hoverId === id && source) && <div className="review-hover-source"><SourceCrop snippet={source} /><small>{item.snippets?.[0]?.text}</small></div>}
        </div>;
      })}{annotations.filter((item) => item.page === page).map((item) => { const rectangle = previewRects[item.id] ?? item.rect; const active = selectedId === item.id; return <div key={item.id} className={`review-annotation ${active ? "selected" : ""}`} style={{ ...rectStyle(rectangle), fontFamily: item.style.font, fontSize: item.style.size * zoom / 100, fontWeight: item.style.bold ? 700 : 400, fontStyle: item.style.italic ? "italic" : "normal" }} onClick={(event) => { event.stopPropagation(); setSelectedId(item.id); setTool("select"); }}>
        {active && <span className="review-move" role="button" aria-label="Move annotation" onPointerDown={(event) => startDrag(event, item.id, rectangle, "move", false)}>⠿</span>}{item.text}{active && <span className="review-resize" role="button" aria-label="Resize annotation" onPointerDown={(event) => startDrag(event, item.id, rectangle, "resize", false)} />}
      </div>; })}</div></div></main>
      <aside className="review-right"><div className="review-tabs"><button className={rightMode === "answer" ? "active" : ""} onClick={() => setRightMode("answer")}>Answer</button><button className={rightMode === "intake" ? "active" : ""} onClick={() => setRightMode("intake")}>Intake facts</button></div><div className="review-right-scroll">{rightMode === "answer" && selected && <><h2>{selected.field.label}</h2><p className={`review-confidence ${confidence(selected)}`}>{selected.review_status === "accepted" ? "Reviewed" : confidence(selected) === "high" ? "High confidence" : confidence(selected) === "medium" ? "Medium confidence · review" : "Low confidence · review"}</p><label className="review-control">Value{selected.field.field_type === "boolean" ? <select value={selected.write_value === true ? "yes" : selected.write_value === false ? "no" : ""} onChange={(event) => applyField(selected.field.id, { write_value: event.target.value === "" ? null : event.target.value === "yes", review_status: "accepted" })}><option value="">Blank</option><option value="yes">Yes ✓</option><option value="no">No</option></select> : selected.field.field_type === "choice" && selected.field.choice_options?.length ? <select value={draft} onChange={(event) => { setDraft(event.target.value); applyField(selected.field.id, { write_value: event.target.value, review_status: "accepted" }); }}><option value="">Blank</option>{selected.field.choice_options.map((option) => <option key={option.export_value} value={option.display_value}>{option.display_value}</option>)}</select> : <input value={draft} onChange={(event) => setDraft(event.target.value)} onFocus={() => { valueBefore.current = selected.write_value == null ? "" : String(selected.write_value); }} onBlur={commitValue} />}</label><div className="review-actions"><button onClick={() => { commitValue(); applyField(selected.field.id, { review_status: "accepted" }); }}>Accept</button><button onClick={() => { setDraft(""); applyField(selected.field.id, { write_value: "", review_status: "accepted" }); valueBefore.current = ""; }}>Clear</button></div>{(selected.explanation || selected.assumption) && <p className="review-reason">{selected.explanation || selected.assumption}</p>}<div className="review-style"><strong>Text style</strong><select aria-label="Font" value={style.font} onChange={(event) => updateStyle({ ...style, font: event.target.value as ReviewStyle["font"] })}><option>Helvetica</option><option>Times-Roman</option><option>Courier</option></select><input aria-label="Font size" type="number" min="6" max="36" value={style.size} onChange={(event) => { const size = Number(event.target.value); if (size >= 6 && size <= 36) updateStyle({ ...style, size }); }} /><button className={style.bold ? "active" : ""} onClick={() => updateStyle({ ...style, bold: !style.bold })}>B</button><button className={style.italic ? "active" : ""} onClick={() => updateStyle({ ...style, italic: !style.italic })}><i>I</i></button></div>{draft.length * style.size * .55 > box(selected.field.location)[2] * 816 && <div className="review-reason">Text may not fit. <button onClick={() => updateStyle({ ...style, size: Math.max(6, Math.floor(box(selected.field.location)[2] * 816 / Math.max(1, draft.length * .55))) })}>Fit text</button></div>}<strong className="review-section-title">Source screenshot</strong><SourceCrop snippet={selectedSnippet} />{selectedSnippet && ((selected.evidence_fact_ids?.[0] || selected.model_proposal?.evidence_fact_ids?.[0]) ? <Link className="review-source-link" to={`/evidence/${selectedSnippet.artifact_id}/${selectedSnippet.snapshot_id}?fact=${encodeURIComponent(selected.evidence_fact_ids?.[0] || selected.model_proposal?.evidence_fact_ids?.[0] || "")}`} target="_blank">Open source · page {selectedSnippet.page} ↗</Link> : <a className="review-source-link" href={`${API}/api/v1/artifacts/${selectedSnippet.artifact_id}/content#page=${selectedSnippet.page}`} target="_blank" rel="noreferrer">Open source · page {selectedSnippet.page} ↗</a>)}{(selected.snippets ?? []).map((snippet, index) => <blockquote key={index}>{snippet.text}</blockquote>)}</>}
      {rightMode === "answer" && selectedAnnotation && <><h2>{selectedAnnotation.kind === "check" ? "Check mark" : selectedAnnotation.kind === "text" ? "Added text" : `${selectedAnnotation.kind} mark`}</h2><label className="review-control">Text<input value={selectedAnnotation.text} onChange={(event) => applyAnnotations(annotations.map((item) => item.id === selectedId ? { ...item, text: event.target.value } : item))} /></label><div className="review-actions"><button onClick={() => { const duplicate = { ...selectedAnnotation, id: crypto.randomUUID(), rect: [Math.min(0.8, selectedAnnotation.rect[0] + 0.03), Math.min(0.9, selectedAnnotation.rect[1] + 0.03), selectedAnnotation.rect[2], selectedAnnotation.rect[3]] as Rect }; applyAnnotations([...annotations, duplicate]); setSelectedId(duplicate.id); }}>Duplicate</button><button onClick={() => { applyAnnotations(annotations.filter((item) => item.id !== selectedId)); setSelectedId(""); }}>Delete</button></div><div className="review-style"><strong>Text style</strong><select aria-label="Font" value={style.font} onChange={(event) => updateStyle({ ...style, font: event.target.value as ReviewStyle["font"] })}><option>Helvetica</option><option>Times-Roman</option><option>Courier</option></select><input aria-label="Font size" type="number" min="6" max="36" value={style.size} onChange={(event) => { const size = Number(event.target.value); if (size >= 6 && size <= 36) updateStyle({ ...style, size }); }} /><button onClick={() => updateStyle({ ...style, bold: !style.bold })}>B</button><button onClick={() => updateStyle({ ...style, italic: !style.italic })}><i>I</i></button></div></>}
      {rightMode === "intake" && <><h2>Extracted information</h2><p>{evidence.filter((item) => item.used).length} used · {evidence.filter((item) => !item.used).length} unused</p>{evidence.map((item) => <button className="review-fact" key={item.fact.id} onClick={() => { const target = item.field_ids[0]; if (target) setSelection(target); else if (selected) { applyField(selected.field.id, { write_value: item.fact.value, evidence_fact_ids: [item.fact.id], review_status: "accepted" }); setDraft(String(item.fact.value ?? "")); valueBefore.current = String(item.fact.value ?? ""); } setRightMode("answer"); }}><strong>{item.fact.label}</strong><span>{String(item.fact.value ?? "Blank")}</span><small>{item.used ? "Used" : "Use in selected answer"}</small></button>)}</>}</div></aside></div>
    {error && <div className="review-toast" role="alert">{error}</div>}
    {showDownloadWarning && <div className="review-modal-backdrop"><div className="review-modal" role="dialog" aria-modal="true"><h2>{reviewCount} answers still need review</h2><p>You can keep reviewing or download the form with these answers as shown.</p><div><button className="secondary" onClick={() => setShowDownloadWarning(false)}>Continue reviewing</button><button className="primary" onClick={() => void download(true)}>Download anyway</button></div></div></div>}
  </div>;
}
