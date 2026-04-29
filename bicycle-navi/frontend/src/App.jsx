import { useState, useCallback } from "react";
import SearchForm from "./components/SearchForm";
import MapView from "./components/MapView";
import ViolationAlert from "./components/ViolationAlert";
import ModeSwitcher from "./components/ModeSwitcher";
import RidingView from "./components/RidingView";
import { fetchRoute } from "./api/route";
import { useGeoAutoMode } from "./hooks/useGeoAutoMode";

export default function App() {
  const [routeData, setRouteData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  // UIモード: "preparing" = 出発前/停車中, "riding" = 走行中
  const [mode, setMode] = useState("preparing");
  // 走行中モード用: 現在のinstruction index
  const [currentInstructionIndex, setCurrentInstructionIndex] = useState(0);
  // 音声案内ON/OFF
  const [voiceEnabled, setVoiceEnabled] = useState(true);
  // GPS現在位置 { lat, lng, speed, accuracy } | null
  const [currentPosition, setCurrentPosition] = useState(null);

  // GPS位置情報コールバック（useGeoAutoModeに渡すため useCallback で安定化）
  const handlePosition = useCallback((pos) => {
    setCurrentPosition(pos);
  }, []);

  // GPS速度によるモード自動切り替え
  const { handleManualModeChange, geoError, isGeoActive, manualLocked } =
    useGeoAutoMode({ setMode, onPosition: handlePosition });

  const handleSearch = async (oLat, oLng, dLat, dLng) => {
    setLoading(true);
    setError(null);
    try {
      const data = await fetchRoute(oLat, oLng, dLat, dLng);
      setRouteData(data);
      setCurrentInstructionIndex(0);
    } catch (e) {
      setError("ルート取得に失敗しました: " + e.message);
    } finally {
      setLoading(false);
    }
  };

  // 走行中モードで「次の案内へ」進む（デモ用）
  const handleNextInstruction = () => {
    const instructions = routeData?.compliant_route?.instructions || [];
    if (currentInstructionIndex < instructions.length - 1) {
      setCurrentInstructionIndex((prev) => prev + 1);
    }
  };

  // 出発前・停車中モードの表示
  const renderPreparingMode = () => (
    <>
      <SearchForm onSearch={handleSearch} />
      {loading && <p>検索中...</p>}
      {error && <p style={{ color: "red" }}>{error}</p>}
      {routeData?.rerouted && (
        <p style={{ color: "#0066cc", fontWeight: "bold" }}>
          法規に合わせてルートを変更しました
        </p>
      )}
      {routeData && (
        <ViolationAlert
          violations={routeData.violations}
          compliant={routeData.compliant}
          recommendations={routeData.recommendations}
        />
      )}
      <MapView
        originalRoute={routeData?.original_route}
        compliantRoute={routeData?.compliant_route}
        violations={routeData?.violations}
      />
    </>
  );

  // 走行中モードの表示
  const renderRidingMode = () => (
    <RidingView
      routeData={routeData}
      currentInstructionIndex={currentInstructionIndex}
      onNextInstruction={handleNextInstruction}
      violations={routeData?.violations || []}
      voiceEnabled={voiceEnabled}
      onVoiceToggle={() => setVoiceEnabled((v) => !v)}
      currentPosition={currentPosition}
    />
  );

  return (
    <div style={{ position: "relative" }}>
      <header
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          padding: "8px 16px",
          borderBottom: "1px solid #ddd",
        }}
      >
        <h1 style={{ margin: 0, fontSize: "1.5rem" }}>自転車ナビ</h1>
        <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
          {/* GPS状態インジケーター */}
          {geoError ? (
            <span style={styles.geoErrorBadge} title={geoError}>
              GPS×
            </span>
          ) : isGeoActive ? (
            <span
              style={styles.geoActiveBadge}
              title={manualLocked ? "手動切り替え中（自動切り替え一時停止）" : "GPS自動切り替え有効"}
            >
              {manualLocked ? "GPS手動" : "GPS自動"}
            </span>
          ) : (
            <span style={styles.geoWaitingBadge} title="GPS待機中">
              GPS待機
            </span>
          )}
          <ModeSwitcher
            mode={mode}
            onModeChange={handleManualModeChange}
            hasRoute={!!routeData}
          />
        </div>
      </header>
      <main style={{ padding: mode === "riding" ? 0 : "16px" }}>
        {mode === "preparing" ? renderPreparingMode() : renderRidingMode()}
      </main>
    </div>
  );
}

const styles = {
  geoActiveBadge: {
    fontSize: "0.75rem",
    padding: "3px 8px",
    borderRadius: "12px",
    backgroundColor: "#e8f5e9",
    color: "#2e7d32",
    border: "1px solid #a5d6a7",
  },
  geoWaitingBadge: {
    fontSize: "0.75rem",
    padding: "3px 8px",
    borderRadius: "12px",
    backgroundColor: "#fff8e1",
    color: "#f57f17",
    border: "1px solid #ffe082",
  },
  geoErrorBadge: {
    fontSize: "0.75rem",
    padding: "3px 8px",
    borderRadius: "12px",
    backgroundColor: "#ffebee",
    color: "#c62828",
    border: "1px solid #ef9a9a",
  },
};
