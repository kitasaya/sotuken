/**
 * モード切り替えボタン
 * - preparing: 出発前・停車中モード（詳細表示）
 * - riding: 走行中モード（シンプル矢印表示）
 */
export default function ModeSwitcher({ mode, onModeChange, hasRoute }) {
  const isPreparing = mode === "preparing";

  const handleToggle = () => {
    onModeChange(isPreparing ? "riding" : "preparing");
  };

  return (
    <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
      <span style={{ fontSize: "0.85rem", color: "#666" }}>
        {isPreparing ? "出発前" : "走行中"}
      </span>
      <button
        onClick={handleToggle}
        disabled={!hasRoute}
        title={!hasRoute ? "ルートを検索してください" : "モード切り替え"}
        style={{
          padding: "8px 16px",
          fontSize: "0.9rem",
          borderRadius: "20px",
          border: "none",
          cursor: hasRoute ? "pointer" : "not-allowed",
          backgroundColor: isPreparing ? "#4CAF50" : "#2196F3",
          color: "white",
          opacity: hasRoute ? 1 : 0.5,
          transition: "background-color 0.2s",
        }}
      >
        {isPreparing ? "走行開始" : "地図に戻る"}
      </button>
    </div>
  );
}
