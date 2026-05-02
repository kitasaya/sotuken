const RULE_ICONS = {
  oneway: "🚫",
  sidewalk: "🚶",
  two_step_turn: "🔄",
};

function violationColor(confidence) {
  return (confidence ?? 0.4) >= 0.7 ? "red" : "#e65100";
}

export default function ViolationAlert({ violations, compliant, recommendations }) {
  if (!violations) return null;

  return (
    <div style={{ padding: "8px" }}>
      {compliant ? (
        <div style={{ color: "green" }}>✅ 法規違反なし</div>
      ) : (
        <div>
          <div style={{ color: "red" }}>⚠️ 法規違反の可能性: {violations.length}件</div>
          <ul style={{ margin: "4px 0" }}>
            {violations.map((v, i) => (
              <li key={i} style={{ color: violationColor(v.confidence) }}>
                {RULE_ICONS[v.rule] ?? "⚠️"} {v.message}
                {v.confidence != null && v.confidence < 0.7 && (
                  <span style={{ fontSize: "11px", marginLeft: "4px", opacity: 0.8 }}>（要確認）</span>
                )}
              </li>
            ))}
          </ul>
        </div>
      )}
      {recommendations && recommendations.length > 0 && (
        <div style={{ color: "blue" }}>
          🚴 自転車レーンあり: {recommendations.length}箇所
          <ul>
            {recommendations.map((r, i) => (
              <li key={i}>🚴 {r.message}</li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
