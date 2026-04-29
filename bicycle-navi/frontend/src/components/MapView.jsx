import {
  MapContainer,
  TileLayer,
  Polyline,
  CircleMarker,
  Popup,
} from "react-leaflet";
import "leaflet/dist/leaflet.css";

// GraphHopperのcoordinatesは [lng, lat] なので反転する
const toPositions = (route) =>
  route ? route.points.coordinates.map(([lng, lat]) => [lat, lng]) : [];

export default function MapView({ originalRoute, compliantRoute, violations }) {
  const center = [35.6762, 139.6503]; // 東京

  const originalPositions = toPositions(originalRoute);
  const compliantPositions = toPositions(compliantRoute);

  return (
    <MapContainer
      center={center}
      zoom={13}
      style={{ height: "70vh", width: "100%" }}
    >
      <TileLayer
        url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
        attribution="© OpenStreetMap contributors"
      />
      {/* 元の最短ルート（オレンジ・中太線） */}
      {originalPositions.length > 0 && (
        <Polyline positions={originalPositions} color="#e65100" weight={4} opacity={0.8} />
      )}
      {/* 法規準拠ルート（青・太線） */}
      {compliantPositions.length > 0 && (
        <Polyline positions={compliantPositions} color="blue" weight={5} />
      )}
      {/* 違反マーカー */}
      {violations &&
        violations.map((v, i) => (
          <CircleMarker
            key={i}
            center={[v.lat, v.lng]}
            radius={8}
            color="red"
            fillColor="red"
            fillOpacity={0.7}
          >
            <Popup>{v.message}</Popup>
          </CircleMarker>
        ))}
      {/* 凡例 */}
      <div
        style={{
          position: "absolute",
          bottom: "24px",
          right: "8px",
          zIndex: 1000,
          background: "white",
          padding: "8px 12px",
          borderRadius: "6px",
          boxShadow: "0 1px 4px rgba(0,0,0,0.3)",
          fontSize: "12px",
          lineHeight: "1.8",
        }}
      >
        <div><span style={{ color: "blue", fontWeight: "bold" }}>━━</span> 法規準拠ルート</div>
        <div><span style={{ color: "#e65100", fontWeight: "bold" }}>━━</span> 最短ルート</div>
        <div><span style={{ color: "red" }}>●</span> 法規違反箇所</div>
      </div>
    </MapContainer>
  );
}
