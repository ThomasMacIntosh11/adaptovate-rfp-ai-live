// frontend/src/App.jsx
import { useEffect, useMemo, useState } from "react";
import "./App.css";
import RFPCard from "./components/RFPCard.jsx";
import ProgressBar from "./components/ProgressBar.jsx";

const API_BASE = import.meta.env.VITE_API_BASE || "http://127.0.0.1:8000";
const PAGE_SIZE = 100;

export default function App() {
  const [rfps, setRfps] = useState([]);
  const [saved, setSaved] = useState([]);
  const [detail, setDetail] = useState(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [noteText, setNoteText] = useState("");
  const [uploading, setUploading] = useState(false);
  const [loading, setLoading] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState("");
  const [q, setQ] = useState("");
  const [savingId, setSavingId] = useState(null);

  const [page, setPage] = useState(0);
  const [totalCount, setTotalCount] = useState(0);
  const [progress, setProgress] = useState({ total: 0, done: 0, stage: "" });

  const loadRfps = async (targetPage = page) => {
    try {
      setLoading(true);
      setError("");
      const offset = targetPage * PAGE_SIZE;
      const res = await fetch(`${API_BASE}/rfps?limit=${PAGE_SIZE}&offset=${offset}`);
      if (!res.ok) throw new Error(`GET /rfps ${res.status}`);
      const data = await res.json();
      const total = res.headers.get("X-Total-Count");
      if (total) setTotalCount(Number(total));
      setRfps(Array.isArray(data) ? data : []);
    } catch (e) {
      console.error(e);
      setError("Could not load RFPs. Check backend.");
    } finally {
      setLoading(false);
    }
  };

  const loadSaved = async () => {
    try {
      const res = await fetch(`${API_BASE}/saved`);
      if (!res.ok) throw new Error(`GET /saved ${res.status}`);
      const data = await res.json();
      setSaved(Array.isArray(data) ? data : []);
    } catch (e) {
      console.error(e);
    }
  };

  const refreshRfps = async () => {
    try {
      setRefreshing(true);
      setError("");
      const res = await fetch(`${API_BASE}/refresh`, { method: "POST" });
      let msg = "";
      try {
        const body = await res.json();
        msg = body?.message || "";
        if (body?.ok === false && body?.error) msg = `${msg} (${body.error})`;
      } catch { /* ignore */ }
      if (!res.ok) throw new Error(msg || `POST /refresh ${res.status}`);
      setPage(0);
      await loadRfps(0);
      await loadSaved();
      if (detail?.rfp_id) {
        await fetchSavedDetail(detail.rfp_id);
      }
    } catch (e) {
      console.error(e);
      setError(e?.message || "Refresh failed. See browser console and backend logs.");
    } finally {
      setRefreshing(false);
    }
  };

  const handleSave = async (rfpId) => {
    try {
      setSavingId(rfpId);
      const res = await fetch(`${API_BASE}/rfps/${rfpId}/save`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ generate_summary: true }),
      });
      if (!res.ok) throw new Error(`Save failed ${res.status}`);
      await loadSaved();
    } catch (e) {
      console.error(e);
      setError(e?.message || "Could not save RFP.");
    } finally {
      setSavingId(null);
    }
  };

  const handleUploadDocument = async (rfpId, file) => {
    if (!file) return;
    const form = new FormData();
    form.append("file", file);
    try {
      setUploading(true);
      const res = await fetch(`${API_BASE}/saved/${rfpId}/upload`, {
        method: "POST",
        body: form,
      });
      if (!res.ok) throw new Error(`Upload failed ${res.status}`);
      const data = await res.json();
      setDetail(data);
      await loadSaved();
    } catch (e) {
      console.error(e);
      setError(e?.message || "Upload failed.");
    } finally {
      setUploading(false);
    }
  };

  const handleAddNote = async (rfpId) => {
    const note = noteText.trim();
    if (!note) return;
    try {
      const res = await fetch(`${API_BASE}/saved/${rfpId}/notes`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ note }),
      });
      if (!res.ok) throw new Error(`Add note failed ${res.status}`);
      const data = await res.json();
      setDetail(data);
      setNoteText("");
    } catch (e) {
      console.error(e);
      setError(e?.message || "Could not add note.");
    }
  };

  const handleRemoveSaved = async (rfpId) => {
    try {
      await fetch(`${API_BASE}/saved/${rfpId}`, { method: "DELETE" });
      await loadSaved();
    } catch (e) {
      console.error(e);
    }
  };

  // Poll backend progress while refreshing
  useEffect(() => {
    if (!refreshing) return;
    let t = null;
    const tick = async () => {
      try {
        const r = await fetch(`${API_BASE}/progress`);
        if (r.ok) {
          const j = await r.json();
          setProgress(j || { total: 0, done: 0, stage: "" });
        }
      } catch { /* ignore */ }
      t = setTimeout(tick, 500);
    };
    tick();
    return () => { if (t) clearTimeout(t); };
  }, [refreshing]);

  useEffect(() => {
    if (!refreshing) setProgress({ total: 0, done: 0, stage: "" });
  }, [refreshing]);

  useEffect(() => { loadRfps(page); }, [page]);
  useEffect(() => { loadSaved(); }, []);

  const fetchSavedDetail = async (rfpId) => {
    try {
      setDetailLoading(true);
      const res = await fetch(`${API_BASE}/saved/${rfpId}`);
      if (!res.ok) throw new Error(`GET /saved/${rfpId} ${res.status}`);
      const data = await res.json();
      setDetail(data);
      setNoteText("");
    } catch (e) {
      console.error(e);
      setError(e?.message || "Unable to load saved RFP.");
    } finally {
      setDetailLoading(false);
    }
  };

  const openSavedDetail = (item) => {
    fetchSavedDetail(item.rfp_id);
  };

  const closeDetail = () => {
    setDetail(null);
    setNoteText("");
  };

  const filtered = useMemo(() => {
    const term = q.trim().toLowerCase();
    const today = new Date();
    today.setHours(0, 0, 0, 0);
    const active = rfps.filter((r) => {
      if (!r?.due_date) return true;
      const parsed = new Date(r.due_date);
      if (Number.isNaN(parsed.valueOf())) return true;
      return parsed >= today;
    });
    if (!term) return active;
    return active.filter((r) => {
      const hay = `${r?.title || ""} ${r?.summary || ""} ${r?.agency || ""}`.toLowerCase();
      return hay.includes(term);
    });
  }, [q, rfps]);

  const totalPages = Math.max(1, Math.ceil((totalCount || 0) / PAGE_SIZE));
  const goPrev = () => setPage((p) => Math.max(0, p - 1));
  const goNext = () => setPage((p) => ((p + 1) < totalPages ? p + 1 : p));

  const percent = (() => {
    const { total, done } = progress || {};
    if (!total || total <= 0) return 0;
    return Math.min(100, Math.max(0, Math.round((done / total) * 100)));
  })();

  return (
    <div className="app-shell">
      <aside className="saved-panel">
        <div className="saved-header">
          <h2>Saved Opportunities</h2>
          <span>{saved.length}</span>
        </div>
        <div className="saved-list">
          {saved.length === 0 && <p className="saved-empty">No saved RFPs yet. Save cards to curate your shortlist.</p>}
          {saved.map((item) => (
            <div key={item.id} className="saved-card">
              <div className="saved-card-head">
                <div>
                  <h3>{item.title}</h3>
                  <p>{item.agency}</p>
                </div>
                <button className="icon-btn" onClick={() => handleRemoveSaved(item.rfp_id)} title="Remove">
                  ✕
                </button>
              </div>
              <div className="saved-actions">
                <button className="secondary" onClick={() => openSavedDetail(item)}>
                  View Details
                </button>
                <button className="text-link" onClick={() => window.open(item.url, "_blank", "noopener,noreferrer")}>
                  Original ↗
                </button>
              </div>
              <div className="saved-meta">
                <span>Posted: {item.posted_date || "—"}</span>
                <span>Due: {item.due_date || "—"}</span>
              </div>
            </div>
          ))}
        </div>
      </aside>
      <main className="app-main">
        <div className="app-wrap">
          <h1>ADAPTOVATE RFP Intelligence</h1>
          {refreshing && <ProgressBar percent={percent} label={progress?.stage || "Refreshing…"} />}

          <div className="actions">
            <button onClick={refreshRfps} disabled={refreshing}>
              {refreshing ? "Refreshing…" : "Refresh RFPs"}
            </button>
            <button onClick={() => { setPage(0); loadRfps(0); }} disabled={loading || refreshing}>
              {loading ? "Loading…" : "Reload List"}
            </button>

            <input
              className="search"
              placeholder="Quick filter (title, summary, agency)…"
              value={q}
              onChange={(e) => setQ(e.target.value)}
            />
          </div>

          <div className="pagination">
            <button onClick={goPrev} disabled={page === 0 || loading || refreshing}>Previous</button>
            <span>Page {page + 1} of {totalPages}</span>
            <button onClick={goNext} disabled={loading || refreshing || page + 1 >= totalPages}>Next</button>
          </div>

          {error && (
            <div className="error">
              {error}
            </div>
          )}

          {!loading && filtered.length === 0 && (
            <p className="empty">No RFPs yet. Click “Refresh RFPs” or adjust your filters.</p>
          )}

          <div className="list">
            {filtered.map((r) => (
              <RFPCard
                key={r.id ?? `${r.title}-${r.url}`}
                rfp={r}
                onSave={() => handleSave(r.id)}
                saving={savingId === r.id}
              />
            ))}
          </div>

          <div className="pagination bottom">
            <button onClick={goPrev} disabled={page === 0 || loading || refreshing}>Previous</button>
            <span>Page {page + 1} of {totalPages}</span>
            <button onClick={goNext} disabled={loading || refreshing || page + 1 >= totalPages}>Next</button>
          </div>
        </div>
      </main>
      {detail ? (
        <div className="detail-overlay">
          <div className="detail-panel">
            <div className="detail-header">
              <div>
                <h2>{detail.title}</h2>
                <p>{detail.agency}</p>
              </div>
              <button className="icon-btn large" onClick={closeDetail}>✕</button>
            </div>
            <div className="detail-grid">
              <div className="detail-section">
                <h4>Opportunity</h4>
                <div className="detail-meta">
                  <span>Posted: {detail.posted_date || "—"}</span>
                  <span>Due: {detail.due_date || "—"}</span>
                </div>
                <p>{detail.description || "No description available."}</p>
                {detail.url ? (
                  <button className="text-link" onClick={() => window.open(detail.url, "_blank", "noopener,noreferrer")}>
                    Open Original ↗
                  </button>
                ) : null}
              </div>
              <div className="detail-section">
                <h4>Documents</h4>
                <div className="upload-box">
                  <label className="upload-label">
                    <input
                      type="file"
                      disabled={uploading}
                      onChange={(e) => {
                        if (e.target.files?.[0]) {
                          handleUploadDocument(detail.rfp_id, e.target.files[0]);
                          e.target.value = "";
                        }
                      }}
                    />
                    {uploading ? "Uploading…" : "Upload Document"}
                  </label>
                </div>
                {detail.documents?.length ? (
                  <ul className="doc-list">
                    {detail.documents.map((name) => (
                      <li key={name}>
                        <a href={`${API_BASE}/saved/${detail.rfp_id}/documents/${encodeURIComponent(name)}`} target="_blank" rel="noreferrer">
                          {name}
                        </a>
                      </li>
                    ))}
                  </ul>
                ) : (
                  <p className="muted">No documents yet.</p>
                )}
              </div>
            </div>
            <div className="detail-grid">
              <div className="detail-section">
                <div className="section-head">
                  <h4>Full Summary</h4>
                  <button
                    type="button"
                    className="copy-btn"
                    onClick={() => detail.ai_full_summary && navigator.clipboard.writeText(detail.ai_full_summary)}
                    disabled={!detail.ai_full_summary}
                    title="Copy summary"
                  >
                    Copy
                  </button>
                </div>
                {detail.ai_full_summary ? <pre className="saved-summary">{detail.ai_full_summary}</pre> : <p className="muted">Upload docs to generate a full summary.</p>}
              </div>
              <div className="detail-section">
                <h4>Strategic Insights</h4>
                {detail.ai_insights ? <pre className="saved-summary">{detail.ai_insights}</pre> : <p className="muted">Upload docs to generate insights.</p>}
              </div>
            </div>
            <div className="detail-section">
              <h4>Notes</h4>
              <div className="note-form">
                <textarea placeholder="Add note…" value={noteText} onChange={(e) => setNoteText(e.target.value)} />
                <button className="primary" onClick={() => handleAddNote(detail.rfp_id)} disabled={!noteText.trim()}>
                  Add Note
                </button>
              </div>
              {detail.notes?.length ? (
                <ul className="note-list">
                  {detail.notes.map((note) => (
                    <li key={note.id}>
                      <span className="note-date">{note.created_at}</span>
                      <p>{note.note}</p>
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="muted">No notes yet.</p>
              )}
            </div>
          </div>
        </div>
      ) : null}
    </div>
  );
}
