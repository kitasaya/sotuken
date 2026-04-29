import { useState } from "react";
import SearchForm from "./components/SearchForm";
import MapView from "./components/MapView";
import ViolationAlert from "./components/ViolationAlert";
import ModeSwitcher from "./components/ModeSwitcher";
import RidingView from "./components/RidingView";
import { fetchRoute } from "./api/route";

export default function App() {
  const [routeData, setRouteData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  // UIモード: "preparing" = 出発前/停車中, "riding" = 走行中
  const [mode, setMode] = useState("preparing");
  // 走行中モード用: 現在のinstruction index
  const [currentInstructionIndex, setCurrentInstructionIndex] = useState(0);

  const handleSearch = async (oLat, oLng, dLat, dLng) => {
    setLoading(true);
    setError(null);
    try {
      const data = await fetchRoute(oLat, oLng, dLat, dLng);
      setRouteData(data);
      setCurrentInstructionIndex(0); // ルート取得時にリセット
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
    />
  );

  return (
    <div style={{ position: "relative" }}>
      <header style={{
        display: "flex",
        justifyContent: "space-between",
        alignItems: "center",
        padding: "8px 16px",
        borderBottom: "1px solid #ddd"
      }}>
        <h1 style={{ margin: 0, fontSize: "1.5rem" }}>自転車ナビ</h1>
        <ModeSwitcher mode={mode} onModeChange={setMode} hasRoute={!!routeData} />
      </header>
      <main style={{ padding: mode === "riding" ? 0 : "16px" }}>
        {mode === "preparing" ? renderPreparingMode() : renderRidingMode()}
      </main>
    </div>
  );
}
