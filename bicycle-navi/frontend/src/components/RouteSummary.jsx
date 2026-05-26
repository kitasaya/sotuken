const formatDistance = (m) => {
  if (m == null) return "-";
  if (m >= 1000) return `${(m / 1000).toFixed(1)} km`;
  return `${Math.round(m)} m`;
};

const formatTime = (ms) => {
  if (ms == null) return "-";
  const min = Math.round(ms / 60000);
  if (min >= 60) {
    const h = Math.floor(min / 60);
    const m = min % 60;
    return `約 ${h} 時間 ${m} 分`;
  }
  return `約 ${min} 分`;
};

export default function RouteSummary({ route, violationCount }) {
  if (!route) return null;
  return (
    <div className="route-summary">
      <div className="route-summary-main">
        <span className="route-distance">{formatDistance(route.distance)}</span>
        <span className="route-time">{formatTime(route.time)}</span>
      </div>
      {violationCount != null && violationCount > 0 && (
        <div className="route-summary-violations">
          ⚠️ 違反候補 {violationCount} 件
        </div>
      )}
      {violationCount === 0 && (
        <div className="route-summary-ok">✅ 法規違反なし</div>
      )}
    </div>
  );
}
