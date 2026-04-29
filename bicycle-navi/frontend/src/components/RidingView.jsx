import { MapContainer, TileLayer, Polyline, CircleMarker } from "react-leaflet";
import "leaflet/dist/leaflet.css";

/**
 * GraphHopper instruction sign の値と矢印の対応
 * -3: 鋭く左折, -2: 左折, -1: やや左, 0: 直進
 * 1: やや右, 2: 右折, 3: 鋭く右折, 4: 目的地到着, 5: Uターン
 * 6: ラウンドアバウト(左回り), -6: ラウンドアバウト(右回り)
 */
const DIRECTION_CONFIG = {
  "-3": { arrow: "↰", label: "鋭く左折", rotate: -135 },
  "-2": { arrow: "←", label: "左折", rotate: -90 },
  "-1": { arrow: "↖", label: "やや左", rotate: -45 },
  0: { arrow: "↑", label: "直進", rotate: 0 },
  1: { arrow: "↗", label: "やや右", rotate: 45 },
  2: { arrow: "→", label: "右折", rotate: 90 },
  3: { arrow: "↱", label: "鋭く右折", rotate: 135 },
  4: { arrow: "◎", label: "目的地", rotate: 0 },
  5: { arrow: "↩", label: "Uターン", rotate: 180 },
  6: { arrow: "↻", label: "ロータリー", rotate: 0 },
  "-6": { arrow: "↺", label: "ロータリー", rotate: 0 },
};

// 距離のフォーマット（m → km変換含む）
const formatDistance = (meters) => {
  if (meters == null) return "-";
  if (meters >= 1000) {
    return `${(meters / 1000).toFixed(1)} km`;
  }
  return `${Math.round(meters)} m`;
};

// 座標配列を [lat, lng] 形式に変換
const toPositions = (route) =>
  route?.points?.coordinates?.map(([lng, lat]) => [lat, lng]) || [];

// GraphHopperのinstructionからLeaflet形式 [lat, lng] の座標を取得
// instructionはpointsフィールドを持たず、interval[0]がrouteCoords配列のインデックス
const getInstructionCoord = (instruction, routeCoords) => {
  const idx = instruction?.interval?.[0];
  if (idx == null || !routeCoords?.[idx]) return null;
  const [lng, lat] = routeCoords[idx];
  return [lat, lng];
};

// 法規違反が次のinstructionに関係するか判定
const hasViolationNearby = (instruction, violations, routeCoords) => {
  if (!instruction || !violations?.length) return false;
  const coord = getInstructionCoord(instruction, routeCoords);
  if (!coord) return false;
  const [lat, lng] = coord;
  return violations.some((v) => {
    return Math.abs(v.lat - lat) < 0.001 && Math.abs(v.lng - lng) < 0.001;
  });
};

// 二段階右折が必要か判定
const needsTwoStepTurn = (instruction, violations, routeCoords) => {
  if (instruction?.sign !== 2) return false;
  return violations?.some(
    (v) => v.rule === "two_step_turn" && hasViolationNearby(instruction, [v], routeCoords)
  );
};

export default function RidingView({
  routeData,
  currentInstructionIndex,
  onNextInstruction,
  violations,
}) {
  const instructions = routeData?.compliant_route?.instructions || [];
  const currentInstruction = instructions[currentInstructionIndex];
  const nextInstruction = instructions[currentInstructionIndex + 1];
  const route = routeData?.compliant_route;

  // 現在のinstructionがない場合
  if (!currentInstruction) {
    return (
      <div style={styles.container}>
        <div style={styles.noRouteMessage}>
          ルートを検索してください
        </div>
      </div>
    );
  }

  const routeCoords = route?.points?.coordinates || [];
  const sign = currentInstruction.sign ?? 0;
  const config = DIRECTION_CONFIG[sign] || DIRECTION_CONFIG[0];
  const distance = currentInstruction.distance;
  const streetName = currentInstruction.street_name || "";
  const hasWarning = hasViolationNearby(currentInstruction, violations, routeCoords);
  const isTwoStepTurn = needsTwoStepTurn(currentInstruction, violations, routeCoords);

  // 地図の中心座標: interval[0]のインデックスでrouteCoords配列を参照
  const mapCenter = getInstructionCoord(currentInstruction, routeCoords) || [35.6762, 139.6503];

  return (
    <div style={styles.container}>
      {/* 矢印表示エリア */}
      <div style={{
        ...styles.arrowContainer,
        ...(hasWarning ? styles.warningBorder : {}),
      }}>
        {isTwoStepTurn && (
          <div style={styles.twoStepBadge}>二段階右折</div>
        )}
        <div style={styles.arrow}>{config.arrow}</div>
        <div style={styles.directionLabel}>{config.label}</div>
        {hasWarning && !isTwoStepTurn && (
          <div style={styles.warningBadge}>注意</div>
        )}
      </div>

      {/* 距離表示 */}
      <div style={styles.distanceContainer}>
        <div style={styles.distanceValue}>{formatDistance(distance)}</div>
        {streetName && (
          <div style={styles.streetName}>{streetName}</div>
        )}
      </div>

      {/* ミニ地図（現在地周辺のみ） */}
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
          {/* ルート線 */}
          {route && (
            <Polyline
              positions={toPositions(route)}
              color="blue"
              weight={5}
            />
          )}
          {/* 現在位置マーカー */}
          <CircleMarker
            center={mapCenter}
            radius={10}
            color="#2196F3"
            fillColor="#2196F3"
            fillOpacity={0.8}
          />
        </MapContainer>
      </div>

      {/* 次の案内へ（デモ用ボタン） */}
      <div style={styles.controls}>
        <button
          onClick={onNextInstruction}
          disabled={currentInstructionIndex >= instructions.length - 1}
          style={{
            ...styles.nextButton,
            opacity: currentInstructionIndex >= instructions.length - 1 ? 0.5 : 1,
          }}
        >
          次の案内へ ({currentInstructionIndex + 1}/{instructions.length})
        </button>
        {nextInstruction && (
          <div style={styles.nextPreview}>
            次: {DIRECTION_CONFIG[nextInstruction.sign]?.label || "直進"}
            {nextInstruction.street_name && ` - ${nextInstruction.street_name}`}
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
    height: "calc(100vh - 60px)", // ヘッダー分を引く
    backgroundColor: "#1a1a2e",
    color: "white",
  },
  noRouteMessage: {
    flex: 1,
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    fontSize: "1.5rem",
    color: "#888",
  },
  arrowContainer: {
    flex: "0 0 auto",
    display: "flex",
    flexDirection: "column",
    alignItems: "center",
    justifyContent: "center",
    padding: "24px",
    backgroundColor: "#16213e",
    position: "relative",
    borderBottom: "2px solid #0f3460",
  },
  warningBorder: {
    borderColor: "#f9a825",
    backgroundColor: "#2c2c1e",
  },
  arrow: {
    fontSize: "8rem",
    lineHeight: 1,
    color: "#4fc3f7",
    textShadow: "0 0 20px rgba(79, 195, 247, 0.5)",
  },
  directionLabel: {
    fontSize: "1.5rem",
    marginTop: "8px",
    color: "#aaa",
  },
  twoStepBadge: {
    position: "absolute",
    top: "12px",
    right: "12px",
    backgroundColor: "#ff5722",
    color: "white",
    padding: "6px 12px",
    borderRadius: "4px",
    fontSize: "1rem",
    fontWeight: "bold",
  },
  warningBadge: {
    position: "absolute",
    top: "12px",
    right: "12px",
    backgroundColor: "#f9a825",
    color: "#1a1a2e",
    padding: "6px 12px",
    borderRadius: "4px",
    fontSize: "0.9rem",
    fontWeight: "bold",
  },
  distanceContainer: {
    flex: "0 0 auto",
    textAlign: "center",
    padding: "16px",
    backgroundColor: "#0f3460",
  },
  distanceValue: {
    fontSize: "3rem",
    fontWeight: "bold",
    color: "#4fc3f7",
  },
  streetName: {
    fontSize: "1.2rem",
    color: "#aaa",
    marginTop: "4px",
  },
  miniMapContainer: {
    flex: 1,
    minHeight: "150px",
  },
  controls: {
    flex: "0 0 auto",
    padding: "12px",
    backgroundColor: "#16213e",
    borderTop: "1px solid #0f3460",
  },
  nextButton: {
    width: "100%",
    padding: "12px",
    fontSize: "1.1rem",
    backgroundColor: "#4CAF50",
    color: "white",
    border: "none",
    borderRadius: "8px",
    cursor: "pointer",
  },
  nextPreview: {
    marginTop: "8px",
    fontSize: "0.9rem",
    color: "#888",
    textAlign: "center",
  },
};
