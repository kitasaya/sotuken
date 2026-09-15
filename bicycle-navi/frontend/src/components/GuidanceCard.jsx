// 第2層（走行ガイダンス）の案内地点カード。
// ViolationCard と同じ体裁だが、guidance には confidence が存在しないため
// 信頼度バッジは持たない（違反件数の定義に案内を混ぜないための線引き）。
const TYPE_LABEL = {
  stop_sign: "一時停止",
  level_crossing: "踏切",
};

const TYPE_ICON = {
  stop_sign: "🛑",
  level_crossing: "🚃",
};

// 文言は事実ベースに留める。`highway=stop` が付いていても現地の法的義務が
// 確認できない場合があるため、「標識があります」という観測事実で表現する。
const TYPE_MESSAGE = {
  stop_sign: "一時停止の標識があります。停止線の直前で停止してください",
  level_crossing:
    "踏切です。直前で停止し、安全を確認してください（信号機のある踏切は信号に従います）",
};

export default function GuidanceCard({
  guidance,
  index,
  focused,
  cardRef,
  onClick,
}) {
  const typeLabel = TYPE_LABEL[guidance.type] || "案内";
  const typeIcon = TYPE_ICON[guidance.type] || "🔔";
  const message = TYPE_MESSAGE[guidance.type] || "走行時に注意してください";

  return (
    <button
      ref={cardRef}
      type="button"
      className={`guidance-card${focused ? " focused" : ""}`}
      onClick={() => onClick?.(index)}
      aria-pressed={focused}
    >
      <div className="gc-header">
        <span className="gc-badge">
          {typeIcon} {typeLabel}
        </span>
      </div>
      <div className="gc-message">{message}</div>
      <div className="gc-coords">
        {guidance.lat.toFixed(5)}, {guidance.lng.toFixed(5)}
      </div>
    </button>
  );
}
