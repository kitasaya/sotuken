import { useEffect } from "react";
import {
  MapContainer,
  TileLayer,
  Polyline,
  CircleMarker,
  useMap,
} from "react-leaflet";
import "leaflet/dist/leaflet.css";

// GraphHopperのcoordinatesは [lng, lat] なので反転する
const toPositions = (route) =>
  route ? route.points.coordinates.map(([lng, lat]) => [lat, lng]) : [];

/** focusVersion が変わるたびに target にズーム＋センタリング */
function MapFocusController({ target, version }) {
  const map = useMap();
  useEffect(() => {
    if (!target || version <= 0) return;
    const zoom = Math.max(map.getZoom(), 16);
    map.flyTo([target.lat, target.lng], zoom, { duration: 0.6 });
  }, [version]); // eslint-disable-line react-hooks/exhaustive-deps
  return null;
}

/** ルート取得時にルート全体が画面に収まるようフィット */
function MapRouteFitController({ positions, version }) {
  const map = useMap();
  useEffect(() => {
    if (!positions?.length || version <= 0) return;
    map.fitBounds(positions, { padding: [40, 40], maxZoom: 16 });
  }, [version]); // eslint-disable-line react-hooks/exhaustive-deps
  return null;
}

export default function MapView({
  originalRoute,
  compliantRoute,
  violations,
  currentPosition,
  focusTarget,
  focusVersion = 0,
  routeFitVersion = 0,
  onViolationClick,
  focusedViolationIndex = null,
}) {
  const center = [35.6762, 139.6503]; // 東京

  const originalPositions = toPositions(originalRoute);
  const compliantPositions = toPositions(compliantRoute);
  const fitTarget =
    compliantPositions.length > 0 ? compliantPositions : originalPositions;

  return (
    <MapContainer
      center={center}
      zoom={13}
      style={{ height: "100%", width: "100%" }}
      zoomControl={false}
      attributionControl={false}
    >
      <TileLayer
        url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
        attribution="© OpenStreetMap contributors"
      />
      <MapFocusController target={focusTarget} version={focusVersion} />
      <MapRouteFitController positions={fitTarget} version={routeFitVersion} />
      {/* 元の最短ルート（オレンジ・中太線） */}
      {originalPositions.length > 0 && (
        <Polyline
          positions={originalPositions}
          color="#e65100"
          weight={4}
          opacity={0.8}
        />
      )}
      {/* 法規準拠ルート（青・太線） */}
      {compliantPositions.length > 0 && (
        <Polyline positions={compliantPositions} color="#1976d2" weight={5} />
      )}
      {/* 違反マーカー（confidence >= 0.7: 赤、< 0.7: 黄） */}
      {violations &&
        violations.map((v, i) => {
          const high = (v.confidence ?? 0.4) >= 0.7;
          const color = high ? "#d32f2f" : "#f9a825";
          const isFocused = i === focusedViolationIndex;
          return (
            <CircleMarker
              key={`${v.lat}-${v.lng}-${i}`}
              center={[v.lat, v.lng]}
              radius={isFocused ? 13 : 9}
              color={isFocused ? "#1976d2" : color}
              fillColor={color}
              fillOpacity={0.85}
              weight={isFocused ? 4 : 2}
              eventHandlers={{
                click: () => onViolationClick?.(i),
              }}
            />
          );
        })}
      {/* 現在地ピン（GPS取得時） */}
      {currentPosition && (
        <CircleMarker
          center={[currentPosition.lat, currentPosition.lng]}
          radius={9}
          color="#ffffff"
          fillColor="#1976d2"
          fillOpacity={1}
          weight={3}
        />
      )}
    </MapContainer>
  );
}
