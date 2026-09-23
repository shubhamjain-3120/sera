import { useEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";
import { PDFDocument } from "pdf-lib";
import { PDFViewer, type PDFViewerRef } from "@embedpdf/react-pdf-viewer";
import { api } from "./api";
import { editPdfPage, type PageAction } from "./pdfPageTools";

type ExportCapability = { saveAsCopy: () => { toPromise: () => Promise<ArrayBuffer> } };

export function FilledPdfEditor() {
  const { formFillId = "" } = useParams();
  const fill = useQuery({ queryKey: ["form-fill", formFillId], queryFn: () => api.formFill(formFillId) });
  const viewer = useRef<PDFViewerRef>(null);
  const sourceUrl = useRef("");
  const [source, setSource] = useState<string>();
  const [viewerReady, setViewerReady] = useState(false);
  const [pageCount, setPageCount] = useState(0);
  const [page, setPage] = useState(1);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!fill.data?.output_available || fill.data.target_kind !== "pdf") return;
    let cancelled = false;
    const load = async () => {
      try {
        const response = await fetch(`${api.formFillOutput(formFillId)}?preview=1`);
        if (!response.ok) throw new Error("Could not open the generated PDF");
        const bytes = await response.arrayBuffer();
        const pdf = await PDFDocument.load(bytes);
        if (cancelled) return;
        const objectUrl = URL.createObjectURL(new Blob([bytes], { type: "application/pdf" }));
        if (sourceUrl.current) URL.revokeObjectURL(sourceUrl.current);
        sourceUrl.current = objectUrl;
        setPageCount(pdf.getPageCount());
        setViewerReady(false);
        setSource(objectUrl);
      } catch (cause) {
        if (!cancelled) setError(cause instanceof Error ? cause.message : "Could not open the generated PDF");
      }
    };
    void load();
    return () => { cancelled = true; };
  }, [fill.data?.output_available, fill.data?.target_kind, formFillId]);
  useEffect(() => () => { if (sourceUrl.current) URL.revokeObjectURL(sourceUrl.current); }, []);

  const exportedBytes = async () => {
    const registry = await viewer.current?.registry;
    const exporter = registry?.getPlugin("export")?.provides?.() as ExportCapability | undefined;
    if (!exporter) throw new Error("PDF editor is still loading");
    return exporter.saveAsCopy().toPromise();
  };

  const replaceSource = (bytes: Uint8Array<ArrayBuffer>, count: number) => {
    const next = URL.createObjectURL(new Blob([bytes], { type: "application/pdf" }));
    if (sourceUrl.current) URL.revokeObjectURL(sourceUrl.current);
    sourceUrl.current = next;
    setViewerReady(false);
    setSource(next);
    setPageCount(count);
    setPage((current) => Math.min(current, count));
  };

  const editPage = async (action: PageAction) => {
    setBusy(true);
    setError("");
    try {
      const result = await editPdfPage(await exportedBytes(), page, action);
      replaceSource(result.bytes, result.pageCount);
      setPage(result.nextPage);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not change the page");
    } finally {
      setBusy(false);
    }
  };

  const download = async () => {
    setBusy(true);
    setError("");
    try {
      const bytes = await exportedBytes();
      const url = URL.createObjectURL(new Blob([bytes], { type: "application/pdf" }));
      const link = document.createElement("a");
      link.href = url;
      link.download = `${(fill.data?.template_name || "filled-form").replace(/[^a-z0-9_-]+/gi, "-")}-edited.pdf`;
      document.body.append(link);
      link.click();
      link.remove();
      setTimeout(() => URL.revokeObjectURL(url), 60_000);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not download the edited PDF");
    } finally {
      setBusy(false);
    }
  };

  return <div className="filled-pdf-editor">
    <header><Link to={`/form-fills/${formFillId}`}>← Back to form fill</Link><strong>Edit filled PDF</strong><button className="primary small" onClick={() => void download()} disabled={!viewerReady || busy}>Download edited copy</button></header>
    {fill.isLoading && <div className="center">Loading filled PDF…</div>}
    {fill.error && <div className="error-banner">{fill.error.message}</div>}
    {fill.data && (!fill.data.output_available || fill.data.target_kind !== "pdf") && <div className="center">Generate a PDF from this form fill before editing it.</div>}
    {error && <div className="error-banner" role="alert">{error}</div>}
    {fill.data?.output_available && !source && !error && <div className="center">Opening PDF editor…</div>}
    {source && <><div className="pdf-page-tools"><span>Page</span><select aria-label="Page to edit" value={page} onChange={(event) => setPage(Number(event.target.value))}>{Array.from({ length: pageCount }, (_, index) => <option key={index} value={index + 1}>{index + 1} of {pageCount}</option>)}</select><button disabled={!viewerReady || busy || page <= 1} onClick={() => void editPage("up")}>Move up</button><button disabled={!viewerReady || busy || page >= pageCount} onClick={() => void editPage("down")}>Move down</button><button disabled={!viewerReady || busy} onClick={() => void editPage("rotate")}>Rotate page</button><button disabled={!viewerReady || busy || pageCount <= 1} onClick={() => void editPage("delete")}>Delete page</button><span className="pdf-page-hint">Apply marked redactions before downloading. The generated PDF stays unchanged.</span></div><div className="embedpdf-area"><PDFViewer key={source} ref={viewer} onReady={() => setViewerReady(true)} config={{ src: source, theme: { preference: "light" }, tabBar: "never", export: { defaultFileName: "edited-form.pdf" } }} style={{ width: "100%", height: "100%" }} /></div></>}
  </div>;
}
