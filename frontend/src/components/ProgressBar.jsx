// frontend/src/components/ProgressBar.jsx
export default function ProgressBar({ percent = 0, label = "" }) {
  const p = Math.max(0, Math.min(100, Math.round(percent)));
  return (
    <>
      <div className="progress-wrap">
        <div className="progress-bar" style={{ width: `${p}%` }} />
      </div>
      <div className="progress-info">
        <span>{p}%</span>
        {!!label && <span className="progress-label">{label}</span>}
      </div>
    </>
  );
}