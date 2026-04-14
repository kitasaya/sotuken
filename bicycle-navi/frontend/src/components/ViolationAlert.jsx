const RULE_ICONS = {
  oneway: "🚫",
  sidewalk: "🚶",
  two_step_turn: "🔄",
};

export default function ViolationAlert({ violations, compliant, recommendations }) {
  if (!violations) return null;

  return (
    <div style={{ padding: "8px" }}>
      {compliant ? (
        <div style={{ color: "green" }}>✅ 法規違反なし</div>
      ) : (
        <div style={{ color: "red" }}>
          ⚠️ 法規違反の可能性: {violations.length}件
          <ul>
            {violations.map((v, i) => (
              <li key={i}>
                {RULE_ICONS[v.rule] ?? "⚠️"} {v.message}
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
