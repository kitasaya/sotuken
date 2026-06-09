import { useEffect, useRef, useState } from "react";
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

/**
 * コンパスインジケーター
 * heading-up 時、画面内で「北がどの方向か」を示す小型コンパス。
 * normalizedHeading: 連続累積角度（CSS transition が 0/360 境界で跳ばないよう正規化済み）
 */
function Compass({ normalizedHeading }) {
  return (
    <div style={styles.compassWrapper}>
      {/* N ラベル：heading が増えるほど反時計回りに回転 → 北が示される方向 */}
      <div
        style={{
          ...styles.compassNeedle,
          transform: `rotate(${-normalizedHeading}deg)`,
          transition: "transform 0.4s ease-out",
        }}
      >
        ↑
      </div>
      <span style={styles.compassLabel}>N</span>
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
  onModeChange,
  geoBadge,
  hasRoute,
}) {
  const instructions = routeData?.compliant_route?.instructions || [];
  const currentInstruction = instructions[currentInstructionIndex];
  const nextInstruction = instructions[currentInstructionIndex + 1];
  const route = routeData?.compliant_route;
  const routeCoords = route?.points?.coordinates || [];

  const rerouteAnnouncedRef = useRef(false);
  const prevRouteDataRef = useRef(null);
  const approachNotifiedRef = useRef({ m100: false, m30: false });

  // heading-up: GPS heading を連続累積角度に変換（0/360 境界ジャンプ防止）
  const [normalizedHeading, setNormalizedHeading] = useState(null);
  const prevHeadingRef = useRef(null);

  useEffect(() => {
    const h = currentPosition?.heading;
    if (h == null) {
      prevHeadingRef.current = null;
      setNormalizedHeading(null);
      return;
    }
    if (prevHeadingRef.current == null) {
      prevHeadingRef.current = h;
      setNormalizedHeading(h);
      return;
    }
    let delta = h - prevHeadingRef.current;
    if (delta > 180) delta -= 360;
    if (delta < -180) delta += 360;
    prevHeadingRef.current += delta;
    setNormalizedHeading(prevHeadingRef.current);
  }, [currentPosition]);

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
        {/* フルスクリーン時のオーバーレイ */}
        <div style={styles.topOverlay}>
          <span style={styles.overlayTitle}>自転車ナビ</span>
          <div style={styles.overlayRight}>
            {geoBadge}
            <button
              onClick={() => onModeChange?.("preparing")}
              style={styles.backButton}
            >
              地図に戻る
            </button>
          </div>
        </div>
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

  // heading-up: scale(1.5) で回転時の四隅の空白を塗りつぶす（√2 ≈ 1.414 以上必要）
  const mapTransform = normalizedHeading != null
    ? `rotate(${-normalizedHeading}deg) scale(1.5)`
    : "none";

  return (
    <div style={styles.container}>
      {/* フルスクリーン用オーバーレイ: GPS badge + 地図に戻るボタン */}
      <div style={styles.topOverlay}>
        <span style={styles.overlayTitle}>自転車ナビ</span>
        <div style={styles.overlayRight}>
          {geoBadge}
          <button
            onClick={() => onModeChange?.("preparing")}
            disabled={!hasRoute}
            style={{
              ...styles.backButton,
              opacity: hasRoute ? 1 : 0.5,
              cursor: hasRoute ? "pointer" : "not-allowed",
            }}
          >
            地図に戻る
          </button>
        </div>
      </div>

      {/* 矢印表示エリア（二段階右折時は縦スペースを節約するため縮小） */}
      <div style={{
        ...styles.arrowContainer,
        ...(isTwoStepTurn
          ? { ...styles.twoStepBorder, padding: "10px 24px 8px" }
          : hasWarning
          ? styles.warningBorder
          : {}),
      }}>
        <div style={{
          ...styles.arrow,
          fontSize: isTwoStepTurn ? "5rem" : "8rem",
        }}>{config.arrow}</div>
        <div style={{
          ...styles.directionLabel,
          fontSize: isTwoStepTurn ? "1.3rem" : "1.6rem",
        }}>{config.label}</div>
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

      {/* heading-up ミニ地図 */}
      <div style={styles.mapWrapper}>
        {/* 回転・スケールする内側ラッパー */}
        <div
          style={{
            ...styles.rotatingMapInner,
            transform: mapTransform,
            transition: normalizedHeading != null ? "transform 0.4s ease-out" : "none",
          }}
        >
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

        {/* コンパスインジケーター（回転しないオーバーレイ） */}
        {normalizedHeading != null && (
          <Compass normalizedHeading={normalizedHeading} />
        )}

        {/* 進行方向マーカー（常に画面上部中央に固定） */}
        {normalizedHeading != null && (
          <div style={styles.headingArrow}>▲</div>
        )}
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
    height: "100%",
    backgroundColor: "#0d0d1a",
    color: "white",
    userSelect: "none",
    WebkitUserSelect: "none",
  },
  // フルスクリーン用 top オーバーレイ
  topOverlay: {
    flex: "0 0 auto",
    display: "flex",
    justifyContent: "space-between",
    alignItems: "center",
    padding: "8px 16px",
    paddingTop: "max(8px, env(safe-area-inset-top))",
    backgroundColor: "rgba(22, 33, 62, 0.95)",
    borderBottom: "1px solid #1a3a6e",
  },
  overlayTitle: {
    fontSize: "0.95rem",
    fontWeight: "bold",
    color: "#90caf9",
  },
  overlayRight: {
    display: "flex",
    alignItems: "center",
    gap: "8px",
  },
  backButton: {
    padding: "6px 14px",
    fontSize: "0.85rem",
    borderRadius: "16px",
    border: "none",
    backgroundColor: "#2196F3",
    color: "white",
    touchAction: "manipulation",
    minHeight: "38px",
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
    fontFamily: "system-ui, -apple-system, sans-serif",
  },
  directionLabel: {
    fontSize: "1.6rem",
    fontWeight: "bold",
    marginTop: "6px",
    color: "#90caf9",
    letterSpacing: "0.05em",
  },
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
  // heading-up 地図セクション
  mapWrapper: {
    flex: 1,
    position: "relative",
    overflow: "hidden",
    minHeight: "120px",
  },
  rotatingMapInner: {
    position: "absolute",
    inset: 0,
    willChange: "transform",
    transformOrigin: "center center",
  },
  // コンパスインジケーター（右上固定）
  compassWrapper: {
    position: "absolute",
    top: 10,
    right: 10,
    width: 38,
    height: 38,
    borderRadius: "50%",
    backgroundColor: "rgba(0, 0, 0, 0.65)",
    display: "flex",
    flexDirection: "column",
    alignItems: "center",
    justifyContent: "center",
    zIndex: 1000,
    pointerEvents: "none",
  },
  compassNeedle: {
    fontSize: "1.1rem",
    color: "#ff5252",
    lineHeight: 1,
    userSelect: "none",
  },
  compassLabel: {
    fontSize: "0.55rem",
    color: "#ff5252",
    fontWeight: "bold",
    lineHeight: 1,
    marginTop: "1px",
  },
  // 進行方向（上）を示す三角形マーカー（画面上部中央固定）
  headingArrow: {
    position: "absolute",
    top: 6,
    left: "50%",
    transform: "translateX(-50%)",
    fontSize: "1.1rem",
    color: "#4fc3f7",
    zIndex: 1000,
    pointerEvents: "none",
    lineHeight: 1,
    textShadow: "0 0 4px rgba(0,0,0,0.8)",
  },
  controls: {
    flex: "0 0 auto",
    padding: "10px 12px",
    backgroundColor: "#16213e",
    borderTop: "1px solid #1a3a6e",
    paddingBottom: "max(10px, env(safe-area-inset-bottom))",
  },
  voiceRow: {
    display: "flex",
    gap: "8px",
    marginBottom: "8px",
  },
  voiceButton: {
    flex: 1,
    minHeight: "48px",
    padding: "10px",
    fontSize: "1rem",
    color: "white",
    border: "none",
    borderRadius: "8px",
    cursor: "pointer",
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
