const RULE_LABEL = {
  oneway: "一方通行",
  two_step_turn: "二段階右折",
  sidewalk: "歩道走行",
};

const RULE_ICON = {
  oneway: "🚫",
  two_step_turn: "🔄",
  sidewalk: "🚶",
};

export default function ViolationCard({
  violation,
  index,
  focused,
  cardRef,
  onClick,
}) {
  const ruleLabel = RULE_LABEL[violation.rule] || "違反";
  const ruleIcon = RULE_ICON[violation.rule] || "⚠️";
  const conf = violation.confidence ?? 0.4;
  const isHigh = conf >= 0.7;

  return (
    <button
      ref={cardRef}
      type="button"
      className={`violation-card${focused ? " focused" : ""}`}
      onClick={() => onClick?.(index)}
      aria-pressed={focused}
    >
      <div className="vc-header">
        <span className={`vc-badge ${isHigh ? "high" : "low"}`}>
          {ruleIcon} {ruleLabel}
          {!isHigh && <span className="vc-tentative">（要確認）</span>}
        </span>
        <span className="vc-confidence">信頼度 {(conf * 100).toFixed(0)}%</span>
      </div>
      <div className="vc-message">{violation.message}</div>
      <div className="vc-coords">
        {violation.lat.toFixed(5)}, {violation.lng.toFixed(5)}
      </div>
    </button>
  );
}
