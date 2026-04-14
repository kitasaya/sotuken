import { useState } from "react";
import SearchForm from "./components/SearchForm";
import MapView from "./components/MapView";
import ViolationAlert from "./components/ViolationAlert";
import { fetchRoute } from "./api/route";

export default function App() {
  const [routeData, setRouteData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  const handleSearch = async (oLat, oLng, dLat, dLng) => {
    setLoading(true);
    setError(null);
    try {
      const data = await fetchRoute(oLat, oLng, dLat, dLng);
      setRouteData(data);
    } catch (e) {
      setError("ルート取得に失敗しました: " + e.message);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div>
      <h1>🚲 自転車ナビ MVP</h1>
      <SearchForm onSearch={handleSearch} />
      {loading && <p>検索中...</p>}
      {error && <p style={{ color: "red" }}>{error}</p>}
      {routeData?.rerouted && (
        <p style={{ color: "#0066cc", fontWeight: "bold" }}>
          ⚡ 法規に合わせてルートを変更しました
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
    </div>
  );
}
