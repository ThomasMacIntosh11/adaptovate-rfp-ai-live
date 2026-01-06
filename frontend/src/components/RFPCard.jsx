// frontend/src/components/RFPCard.jsx
const formatDateLabel = (value) => {
  if (!value) return "";
  const parsed = new Date(value);
  if (!Number.isNaN(parsed.valueOf())) {
    return parsed.toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" });
  }
  return String(value).split("T")[0];
};

export default function RFPCard({ rfp, onSave, saving }) {
  const title = rfp?.title || "Untitled";
  const agency = rfp?.agency || "";
  const url = rfp?.url || "";
  const score = typeof rfp?.score === "number" ? `${rfp.score.toFixed(1)}%` : "—";
  const posted = formatDateLabel(rfp?.posted_date);
  const due = formatDateLabel(rfp?.due_date);
  const tags = (Array.isArray(rfp?.focus_tags) && rfp.focus_tags.length ? rfp.focus_tags : ["Strategy"]).slice(0, 3);
  const summary = (rfp?.summary || rfp?.description || "").trim();

  const onClick = () => {
    if (url && /^https?:\/\//i.test(url)) {
      window.open(url, "_blank", "noopener,noreferrer");
    } else {
      alert("Pending URL");
    }
  };

  return (
    <div className="card">
      <h3 className="card-title">{title}</h3>
      {agency ? <div className="card-agency">{agency}</div> : null}

      <div className="card-tags">
        {tags.map((tag) => (
          <span key={tag} className="card-pill">{tag}</span>
        ))}
      </div>

      {summary ? <p className="card-summary">{summary}</p> : null}

      <div className="card-meta">
        <div className="meta-block">
          <span className="meta-label">Relevance</span>
          <span className="meta-value">{score}</span>
        </div>
        <div className="meta-block">
          <span className="meta-label">Posted</span>
          <span className="meta-value">{posted || "—"}</span>
        </div>
        <div className="meta-block">
          <span className="meta-label">Due</span>
          <span className={`meta-value${due ? "" : " muted"}`}>{due || "—"}</span>
        </div>
      </div>

      <div className="card-actions">
        <button onClick={onClick} className={url ? "primary" : "muted"}>
          {url ? "View RFP" : "Pending URL"}
        </button>
        {onSave ? (
          <button onClick={onSave} className="secondary" disabled={saving}>
            {saving ? "Saving…" : "Save RFP"}
          </button>
        ) : null}
      </div>
    </div>
  );
}
