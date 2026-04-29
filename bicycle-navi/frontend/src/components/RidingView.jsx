import { useEffect, useRef } from "react";
import {
  MapContainer,
  TileLayer,
  Polyline,
  CircleMarker,
  useMap,
} from "react-leaflet";
import "leaflet/dist/leaflet.css";
import voiceGuide, {
  buildAnnouncementText,
  buildRerouteText,
  buildApproachText,
} from "../services/voiceGuide";

/**
 * GraphHopper instruction sign → 表示設定
 */
const DIRECTION_CONFIG = {
  "-3": { arrow: "↰", label: "鋭く左折" },
  "-2": { arrow: "←", label: "左折" },
  "-1": { arrow: "↖", label: "やや左" },
  0:    { arrow: "↑", label: "直進" },
  1:    { arrow: "↗", label: "やや右" },
  2:    { arrow: "→", label: "右折" },
  3:    { arrow: "↱", label: "鋭く右折" },
  4:    { arrow: "◎", label: "目的地" },
  5:    { arrow: "↩", label: "Uターン" },
  6:    { arrow: "↻", label: "ロータリー" },
  "-6": { arrow: "↺", label: "ロータリー" },
};

const formatDistance = (meters) => {
  if (meters == null) return "-";
  if (meters >= 1000) return `${(meters / 1000).toFixed(1)} km`;
  return `${Math.round(meters)} m`;
};

const toPositions = (route) =>
  route?.points?.coordinates?.map(([lng, lat]) => [lat, lng]) || [];

const getInstructionCoord = (instruction, routeCoords) => {
  const idx = instruction?.interval?.[0];
  if (idx == null || !routeCoords?.[idx]) return null;
  const [lng, lat] = routeCoords[idx];
  return [lat, lng];
};

const hasViolationNearby = (instruction, violations, routeCoords) => {
  if (!instruction || !violations?.length) return false;
  const coord = getInstructionCoord(instruction, routeCoords);
  if (!coord) return false;
  const [lat, lng] = coord;
  return violations.some(
    (v) => Math.abs(v.lat - lat) < 0.001 && Math.abs(v.lng - lng) < 0.001
  );
};

const needsTwoStepTurn = (instruction, violations, routeCoords) => {
  if (instruction?.sign !== 2) return false;
  return violations?.some(
    (v) =>
      v.rule === "two_step_turn" &&
      hasViolationNearby(instruction, [v], routeCoords)
  );
};

const distanceBetween = (lat1, lng1, lat2, lng2) => {
  const R = 6371000;
  const dLat = ((lat2 - lat1) * Math.PI) / 180;
  const dLng = ((lng2 - lng1) * Math.PI) / 180;
  const a =
    Math.sin(dLat / 2) ** 2 +
    Math.cos((lat1 * Math.PI) / 180) *
      Math.cos((lat2 * Math.PI) / 180) *
      Math.sin(dLng / 2) ** 2;
  return R * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
};

/** GPS位置変化時に地図の中心を更新するLeaflet内部コンポーネント */
function MapCenter({ center }) {
  const map = useMap();
  useEffect(() => {
    map.setView(center, map.getZoom(), { animate: true });
  }, [center, map]);
  return null;
}

/** 二段階右折の手順説明パネル */
function TwoStepGuide() {
  return (
    <div style={styles.twoStepGuide}>
      <div style={styles.twoStepGuideTitle}>二段階右折の手順</div>
      <div style={styles.twoStepSteps}>
        <div style={styles.twoStepStep}>
          <span style={styles.stepNum}>1</span>
          <span>交差点を左端で直進する</span>
        </div>
        <div style={styles.twoStepStep}>
          <span style={styles.stepNum}>2</span>
          <span>右方向を向いて停止する</span>
        </div>
        <div style={styles.twoStepStep}>
          <span style={styles.stepNum}>3</span>
          <span>信号が青になったら進む</span>
        </div>
      </div>
    </div>
  );
}

export default function RidingView({
  routeData,
  currentInstructionIndex,
  onNextInstruction,
  violations,
  voiceEnabled,
  onVoiceToggle,
  currentPosition,
}) {
  const instructions = routeData?.compliant_route?.instructions || [];
  const currentInstruction = instructions[currentInstructionIndex];
  const nextInstruction = instructions[currentInstructionIndex + 1];
  const route = routeData?.compliant_route;
  const routeCoords = route?.points?.coordinates || [];

  const rerouteAnnouncedRef = useRef(false);
  const prevRouteDataRef = useRef(null);
  const approachNotifiedRef = useRef({ m100: false, m30: false });

  useEffect(() => {
    voiceGuide.setEnabled(voiceEnabled);
  }, [voiceEnabled]);

  useEffect(() => {
    if (!routeData || routeData === prevRouteDataRef.current) return;
    prevRouteDataRef.current = routeData;
    rerouteAnnouncedRef.current = false;
    if (routeData.rerouted) {
      const text = buildRerouteText(routeData.violations);
      if (text) {
        voiceGuide.speak(text);
        rerouteAnnouncedRef.current = true;
      }
    }
  }, [routeData]);

  useEffect(() => {
    if (!currentInstruction) return;
    approachNotifiedRef.current = { m100: false, m30: false };
    const isTwoStep = needsTwoStepTurn(currentInstruction, violations, routeCoords);
    const text = buildAnnouncementText(currentInstruction, isTwoStep);
    if (currentInstructionIndex === 0 && rerouteAnnouncedRef.current) {
      const timer = setTimeout(() => voiceGuide.speak(text), 2500);
      return () => clearTimeout(timer);
    }
    voiceGuide.speak(text);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [currentInstructionIndex, routeData]);

  useEffect(() => {
    if (!currentPosition || !currentInstruction) return;
    const targetInstruction = nextInstruction || currentInstruction;
    const targetCoord = getInstructionCoord(targetInstruction, routeCoords);
    if (!targetCoord) return;
    const dist = distanceBetween(
      currentPosition.lat, currentPosition.lng,
      targetCoord[0], targetCoord[1]
    );
    const isTwoStep = needsTwoStepTurn(currentInstruction, violations, routeCoords);
    if (dist <= 30 && !approachNotifiedRef.current.m30) {
      approachNotifiedRef.current.m30 = true;
      const text = buildApproachText(dist, currentInstruction, isTwoStep);
      if (text) voiceGuide.speak(text);
    } else if (dist <= 100 && !approachNotifiedRef.current.m100 && !approachNotifiedRef.current.m30) {
      approachNotifiedRef.current.m100 = true;
      const text = buildApproachText(dist, currentInstruction, isTwoStep);
      if (text) voiceGuide.speak(text);
    }
  }, [currentPosition]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    return () => voiceGuide.cancel();
  }, []);

  if (!currentInstruction) {
    return (
      <div style={styles.container}>
        <div style={styles.noRouteMessage}>ルートを検索してください</div>
      </div>
    );
  }

  const sign = currentInstruction.sign ?? 0;
  const config = DIRECTION_CONFIG[sign] || DIRECTION_CONFIG[0];
  const streetName = currentInstruction.street_name || "";
  const hasWarning = hasViolationNearby(currentInstruction, violations, routeCoords);
  const isTwoStepTurn = needsTwoStepTurn(currentInstruction, violations, routeCoords);

  let displayDistance = currentInstruction.distance;
  if (currentPosition && nextInstruction) {
    const targetCoord = getInstructionCoord(nextInstruction, routeCoords);
    if (targetCoord) {
      displayDistance = distanceBetween(
        currentPosition.lat, currentPosition.lng,
        targetCoord[0], targetCoord[1]
      );
    }
  }

  const handleRepeat = () => {
    voiceGuide.speak(buildAnnouncementText(currentInstruction, isTwoStepTurn));
  };

  const gpsCenter = currentPosition
    ? [currentPosition.lat, currentPosition.lng]
    : null;
  const instructionCenter =
    getInstructionCoord(currentInstruction, routeCoords) || [35.6762, 139.6503];
  const mapCenter = gpsCenter || instructionCenter;

  return (
    <div style={styles.container}>
      {/* 矢印表示エリア */}
      <div style={{
        ...styles.arrowContainer,
        ...(isTwoStepTurn ? styles.twoStepBorder : hasWarning ? styles.warningBorder : {}),
      }}>
        <div style={styles.arrow}>{config.arrow}</div>
        <div style={styles.directionLabel}>{config.label}</div>
      </div>

      {/* 警告バナー（法規違反リスク箇所） */}
      {isTwoStepTurn && (
        <div style={styles.twoStepBanner}>
          ⚠ この交差点は二段階右折が必要です
        </div>
      )}
      {hasWarning && !isTwoStepTurn && (
        <div style={styles.warningBanner}>
          ⚠ この先に法規注意箇所があります
        </div>
      )}

      {/* 距離表示 */}
      <div style={styles.distanceContainer}>
        <div style={styles.distanceValue}>{formatDistance(displayDistance)}</div>
        {streetName && <div style={styles.streetName}>{streetName}</div>}
        {currentPosition && (
          <div style={styles.gpsIndicator}>
            GPS取得中
            {currentPosition.speed != null && (
              <span> · {(currentPosition.speed * 3.6).toFixed(1)} km/h</span>
            )}
          </div>
        )}
      </div>

      {/* 二段階右折の手順説明 */}
      {isTwoStepTurn && <TwoStepGuide />}

      {/* ミニ地図 */}
      <div style={styles.miniMapContainer}>
        <MapContainer
          center={mapCenter}
          zoom={17}
          style={{ height: "100%", width: "100%" }}
          zoomControl={false}
          dragging={false}
          scrollWheelZoom={false}
        >
          <TileLayer
            url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
            attribution="© OSM"
          />
          {/* instructionが変わった時 or GPS更新時に地図中心を追従させる */}
          <MapCenter center={mapCenter} />
          {route && (
            <Polyline positions={toPositions(route)} color="#2196F3" weight={6} />
          )}
          {/* ターンポイントマーカー */}
          <CircleMarker
            center={instructionCenter}
            radius={9}
            color="#ff9800"
            fillColor="#ff9800"
            fillOpacity={0.8}
          />
          {/* 現在GPS位置マーカー */}
          {gpsCenter ? (
            <CircleMarker
              center={gpsCenter}
              radius={11}
              color="white"
              fillColor="#2196F3"
              fillOpacity={1}
              weight={3}
            />
          ) : (
            <CircleMarker
              center={instructionCenter}
              radius={11}
              color="white"
              fillColor="#2196F3"
              fillOpacity={1}
              weight={3}
            />
          )}
        </MapContainer>
      </div>

      {/* コントロールエリア */}
      <div style={styles.controls}>
        {/* 音声案内ボタン行 */}
        <div style={styles.voiceRow}>
          <button
            onClick={onVoiceToggle}
            style={{
              ...styles.voiceButton,
              backgroundColor: voiceEnabled ? "#388e3c" : "#555",
            }}
            disabled={!voiceGuide.isSupported}
            title={voiceGuide.isSupported ? undefined : "このブラウザは音声案内非対応"}
          >
            {voiceEnabled ? "🔊 音声ON" : "🔇 音声OFF"}
          </button>
          <button
            onClick={handleRepeat}
            style={{
              ...styles.repeatButton,
              opacity: voiceEnabled && voiceGuide.isSupported ? 1 : 0.4,
            }}
            disabled={!voiceEnabled || !voiceGuide.isSupported}
          >
            再読み上げ
          </button>
        </div>

        {/* 次の案内へ（デモ用） */}
        <button
          onClick={onNextInstruction}
          disabled={currentInstructionIndex >= instructions.length - 1}
          style={{
            ...styles.nextButton,
            opacity: currentInstructionIndex >= instructions.length - 1 ? 0.4 : 1,
          }}
        >
          次の案内へ ({currentInstructionIndex + 1} / {instructions.length})
        </button>
        {nextInstruction && (
          <div style={styles.nextPreview}>
            次: {DIRECTION_CONFIG[nextInstruction.sign]?.label || "直進"}
            {nextInstruction.street_name && ` — ${nextInstruction.street_name}`}
          </div>
        )}
      </div>
    </div>
  );
}

const styles = {
  container: {
    display: "flex",
    flexDirection: "column",
    height: "calc(100vh - 60px)",
    backgroundColor: "#0d0d1a",
    color: "white",
    // スマホでの文字選択を無効化（矢印などを誤タップ選択しない）
    userSelect: "none",
    WebkitUserSelect: "none",
  },
  noRouteMessage: {
    flex: 1,
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    fontSize: "1.4rem",
    color: "#666",
  },
  arrowContainer: {
    flex: "0 0 auto",
    display: "flex",
    flexDirection: "column",
    alignItems: "center",
    justifyContent: "center",
    padding: "20px 24px 16px",
    backgroundColor: "#16213e",
    borderBottom: "3px solid #1a3a6e",
  },
  warningBorder: {
    borderBottomColor: "#f9a825",
    backgroundColor: "#1e1a00",
  },
  twoStepBorder: {
    borderBottomColor: "#ff5722",
    backgroundColor: "#1e0e00",
  },
  arrow: {
    fontSize: "8rem",
    lineHeight: 1,
    color: "#4fc3f7",
    // フォント指定でプラットフォーム間の矢印表示差異を抑制
    fontFamily: "system-ui, -apple-system, sans-serif",
  },
  directionLabel: {
    fontSize: "1.6rem",
    fontWeight: "bold",
    marginTop: "6px",
    color: "#90caf9",
    letterSpacing: "0.05em",
  },
  // 警告バナー（全幅）
  warningBanner: {
    flex: "0 0 auto",
    padding: "10px 16px",
    backgroundColor: "#f9a825",
    color: "#1a1200",
    fontSize: "1rem",
    fontWeight: "bold",
    textAlign: "center",
    letterSpacing: "0.03em",
  },
  twoStepBanner: {
    flex: "0 0 auto",
    padding: "10px 16px",
    backgroundColor: "#ff5722",
    color: "white",
    fontSize: "1rem",
    fontWeight: "bold",
    textAlign: "center",
    letterSpacing: "0.03em",
  },
  distanceContainer: {
    flex: "0 0 auto",
    textAlign: "center",
    padding: "14px 16px",
    backgroundColor: "#0a1f44",
    borderBottom: "1px solid #1a3a6e",
  },
  distanceValue: {
    fontSize: "3.5rem",
    fontWeight: "bold",
    color: "#4fc3f7",
    lineHeight: 1,
    fontVariantNumeric: "tabular-nums",
  },
  streetName: {
    fontSize: "1.1rem",
    color: "#90a4ae",
    marginTop: "4px",
  },
  gpsIndicator: {
    fontSize: "0.8rem",
    color: "#66bb6a",
    marginTop: "4px",
  },
  // 二段階右折手順パネル
  twoStepGuide: {
    flex: "0 0 auto",
    backgroundColor: "#1a0a00",
    borderBottom: "1px solid #ff5722",
    padding: "10px 16px",
  },
  twoStepGuideTitle: {
    fontSize: "0.85rem",
    color: "#ff8a65",
    fontWeight: "bold",
    marginBottom: "6px",
  },
  twoStepSteps: {
    display: "flex",
    gap: "8px",
  },
  twoStepStep: {
    flex: 1,
    display: "flex",
    flexDirection: "column",
    alignItems: "center",
    gap: "4px",
    fontSize: "0.78rem",
    color: "#ffccbc",
    textAlign: "center",
  },
  stepNum: {
    display: "inline-flex",
    alignItems: "center",
    justifyContent: "center",
    width: "22px",
    height: "22px",
    borderRadius: "50%",
    backgroundColor: "#ff5722",
    color: "white",
    fontWeight: "bold",
    fontSize: "0.85rem",
  },
  miniMapContainer: {
    flex: 1,
    minHeight: "120px",
  },
  controls: {
    flex: "0 0 auto",
    padding: "10px 12px",
    backgroundColor: "#16213e",
    borderTop: "1px solid #1a3a6e",
    // iOS Safari のホームバー対応
    paddingBottom: "max(10px, env(safe-area-inset-bottom))",
  },
  voiceRow: {
    display: "flex",
    gap: "8px",
    marginBottom: "8px",
  },
  voiceButton: {
    flex: 1,
    // スマホタッチターゲット最小 48px
    minHeight: "48px",
    padding: "10px",
    fontSize: "1rem",
    color: "white",
    border: "none",
    borderRadius: "8px",
    cursor: "pointer",
    // ダブルタップズームを防止
    touchAction: "manipulation",
    transition: "opacity 0.15s",
  },
  repeatButton: {
    flex: 1,
    minHeight: "48px",
    padding: "10px",
    fontSize: "1rem",
    backgroundColor: "#0a2a5e",
    color: "#4fc3f7",
    border: "1.5px solid #4fc3f7",
    borderRadius: "8px",
    cursor: "pointer",
    touchAction: "manipulation",
  },
  nextButton: {
    width: "100%",
    minHeight: "52px",
    padding: "12px",
    fontSize: "1.1rem",
    fontWeight: "bold",
    backgroundColor: "#2e7d32",
    color: "white",
    border: "none",
    borderRadius: "8px",
    cursor: "pointer",
    touchAction: "manipulation",
    letterSpacing: "0.03em",
  },
  nextPreview: {
    marginTop: "8px",
    fontSize: "0.85rem",
    color: "#78909c",
    textAlign: "center",
  },
};
