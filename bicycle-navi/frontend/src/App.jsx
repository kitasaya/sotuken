import { useState, useCallback } from "react";
import MapView from "./components/MapView";
import PreparingPanel from "./components/PreparingPanel";
import ModeSwitcher from "./components/ModeSwitcher";
import RidingView from "./components/RidingView";
import BottomSheet from "./components/BottomSheet";
import { fetchRoute } from "./api/route";
import { useGeoAutoMode } from "./hooks/useGeoAutoMode";
import "./App.css";

// BottomSheet と同期する FAB 位置計算用（peek/half/full の vh 比）
const SNAP_VH = { peek: 18, half: 50, full: 90 };

export default function App() {
  const [routeData, setRouteData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [mode, setMode] = useState("preparing");
  const [currentInstructionIndex, setCurrentInstructionIndex] = useState(0);
  const [voiceEnabled, setVoiceEnabled] = useState(true);
  const [currentPosition, setCurrentPosition] = useState(null);
  const [sheetSnap, setSheetSnap] = useState("half");
  const [focusVersion, setFocusVersion] = useState(0);
  const [routeFitVersion, setRouteFitVersion] = useState(0);
  // 地図の flyTo 先（現在地 FAB と違反カードタップで共用）
  const [mapFocusTarget, setMapFocusTarget] = useState(null);
  // 違反リストで現在ハイライト中のインデックス
  const [focusedViolationIndex, setFocusedViolationIndex] = useState(null);

  const handlePosition = useCallback((pos) => {
    setCurrentPosition(pos);
  }, []);

  const { handleManualModeChange, geoError, isGeoActive, manualLocked } =
    useGeoAutoMode({ setMode, onPosition: handlePosition });

  const handleSearch = async (oLat, oLng, dLat, dLng) => {
    setLoading(true);
    setError(null);
    try {
      const data = await fetchRoute(oLat, oLng, dLat, dLng);
      setRouteData(data);
      setCurrentInstructionIndex(0);
      setRouteFitVersion((v) => v + 1);
      setSheetSnap("half");
      setFocusedViolationIndex(null);
    } catch (e) {
      setError("ルート取得に失敗しました: " + e.message);
    } finally {
      setLoading(false);
    }
  };

  const handleNextInstruction = () => {
    const instructions = routeData?.compliant_route?.instructions || [];
    if (currentInstructionIndex < instructions.length - 1) {
      setCurrentInstructionIndex((prev) => prev + 1);
    }
  };

  const handleCurrentLocation = () => {
    if (!currentPosition) return;
    setMapFocusTarget({ lat: currentPosition.lat, lng: currentPosition.lng });
    setFocusVersion((prev) => prev + 1);
  };

  // 地図上の違反マーカーをタップ → 該当カードをハイライトし full に展開
  const handleViolationMarkerClick = (index) => {
    setFocusedViolationIndex(index);
    setSheetSnap("full");
  };

  // BottomSheet 内の違反カードをタップ → 地図をその地点に flyTo
  const handleViolationCardClick = (index) => {
    const v = routeData?.violations?.[index];
    if (!v) return;
    setFocusedViolationIndex(index);
    setMapFocusTarget({ lat: v.lat, lng: v.lng });
    setFocusVersion((prev) => prev + 1);
  };

  const renderGeoBadge = () => {
    if (geoError) {
      return (
        <span style={badgeStyles.error} title={geoError}>
          GPS×
        </span>
      );
    }
    if (isGeoActive) {
      return (
        <span
          style={badgeStyles.active}
          title={
            manualLocked
              ? "手動切り替え中（自動切り替え一時停止）"
              : "GPS自動切り替え有効"
          }
        >
          {manualLocked ? "GPS手動" : "GPS自動"}
        </span>
      );
    }
    return (
      <span style={badgeStyles.waiting} title="GPS待機中">
        GPS待機
      </span>
    );
  };

  // riding モード（U3）：フルスクリーン
  if (mode === "riding") {
    return (
      <div className="app-root" style={{ background: "#0d0d1a" }}>
        <RidingView
          routeData={routeData}
          currentInstructionIndex={currentInstructionIndex}
          onNextInstruction={handleNextInstruction}
          violations={routeData?.violations || []}
          voiceEnabled={voiceEnabled}
          onVoiceToggle={() => setVoiceEnabled((v) => !v)}
          currentPosition={currentPosition}
          onModeChange={handleManualModeChange}
          geoBadge={renderGeoBadge()}
          hasRoute={!!routeData}
        />
      </div>
    );
  }

  // preparing モード：地図ファースト + ボトムシート + FAB
  return (
    <div className="app-root">
      <div className="map-layer">
        <MapView
          originalRoute={routeData?.original_route}
          compliantRoute={routeData?.compliant_route}
          violations={routeData?.violations}
          currentPosition={currentPosition}
          focusTarget={mapFocusTarget}
          focusVersion={focusVersion}
          routeFitVersion={routeFitVersion}
          onViolationClick={handleViolationMarkerClick}
          focusedViolationIndex={focusedViolationIndex}
        />
      </div>

      <div className="top-bar">
        <h1>自転車ナビ</h1>
        <div className="top-bar-right">
          {renderGeoBadge()}
          <ModeSwitcher
            mode={mode}
            onModeChange={handleManualModeChange}
            hasRoute={!!routeData}
          />
        </div>
      </div>

      {routeData && (
        <div className="map-legend">
          <div className="map-legend-row">
            <span
              className="map-legend-swatch"
              style={{ background: "#1976d2" }}
            />
            法規準拠ルート
          </div>
          <div className="map-legend-row">
            <span
              className="map-legend-swatch"
              style={{ background: "#e65100" }}
            />
            最短ルート
          </div>
          <div className="map-legend-row">
            <span
              className="map-legend-dot"
              style={{ background: "#d32f2f" }}
            />
            違反（確実）
          </div>
          <div className="map-legend-row">
            <span
              className="map-legend-dot"
              style={{ background: "#f9a825" }}
            />
            違反（要確認）
          </div>
        </div>
      )}

      <div
        className="map-attribution"
        style={{ bottom: `calc(${SNAP_VH[sheetSnap]}vh + 4px)` }}
      >
        ©{" "}
        <a
          href="https://www.openstreetmap.org/copyright"
          target="_blank"
          rel="noopener noreferrer"
        >
          OpenStreetMap
        </a>{" "}
        contributors |{" "}
        <a
          href="https://leafletjs.com"
          target="_blank"
          rel="noopener noreferrer"
        >
          Leaflet
        </a>
      </div>

      <div
        className="fab-stack"
        style={{ bottom: `calc(${SNAP_VH[sheetSnap]}vh + 16px)` }}
      >
        {currentPosition && (
          <button
            className="fab"
            onClick={handleCurrentLocation}
            aria-label="現在地に移動"
            title="現在地に移動"
          >
            📍
          </button>
        )}
      </div>

      <BottomSheet snap={sheetSnap} onSnapChange={setSheetSnap}>
        <PreparingPanel
          routeData={routeData}
          loading={loading}
          error={error}
          onSearch={handleSearch}
          currentPosition={currentPosition}
          focusedViolationIndex={focusedViolationIndex}
          onViolationCardClick={handleViolationCardClick}
          recommendations={routeData?.recommendations}
        />
      </BottomSheet>
    </div>
  );
}

const badgeStyles = {
  active: {
    fontSize: "0.75rem",
    padding: "3px 8px",
    borderRadius: "12px",
    backgroundColor: "#e8f5e9",
    color: "#2e7d32",
    border: "1px solid #a5d6a7",
  },
  waiting: {
    fontSize: "0.75rem",
    padding: "3px 8px",
    borderRadius: "12px",
    backgroundColor: "#fff8e1",
    color: "#f57f17",
    border: "1px solid #ffe082",
  },
  error: {
    fontSize: "0.75rem",
    padding: "3px 8px",
    borderRadius: "12px",
    backgroundColor: "#ffebee",
    color: "#c62828",
    border: "1px solid #ef9a9a",
  },
};
