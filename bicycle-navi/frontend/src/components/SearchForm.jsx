import { useState } from "react";
import axios from "axios";

const BASE = "/api";

async function fetchGeocode(query) {
  const res = await axios.get(`${BASE}/geocode`, { params: { q: query } });
  return res.data; // { lat, lng, display_name }
}

export default function SearchForm({ onSearch }) {
  const [mode, setMode] = useState("address"); // "address" | "coords"

  // 住所モード
  const [originQuery, setOriginQuery] = useState("");
  const [destQuery, setDestQuery] = useState("");
  const [originResolved, setOriginResolved] = useState(null);
  const [destResolved, setDestResolved] = useState(null);
  const [geocoding, setGeocoding] = useState(false);
  const [geocodeError, setGeocodeError] = useState(null);

  // 緯度経度モード
  const [origin, setOrigin] = useState({ lat: "", lng: "" });
  const [dest, setDest] = useState({ lat: "", lng: "" });

  const handleAddressSubmit = async (e) => {
    e.preventDefault();
    setGeocoding(true);
    setGeocodeError(null);
    try {
      const [o, d] = await Promise.all([
        fetchGeocode(originQuery),
        fetchGeocode(destQuery),
      ]);
      setOriginResolved(o);
      setDestResolved(d);
      onSearch(o.lat, o.lng, d.lat, d.lng);
    } catch (err) {
      setGeocodeError(
        err.response?.data?.detail ?? "住所の変換に失敗しました: " + err.message
      );
    } finally {
      setGeocoding(false);
    }
  };

  const handleCoordsSubmit = (e) => {
    e.preventDefault();
    onSearch(
      parseFloat(origin.lat),
      parseFloat(origin.lng),
      parseFloat(dest.lat),
      parseFloat(dest.lng)
    );
  };

  const tabStyle = (active) => ({
    padding: "6px 16px",
    cursor: "pointer",
    borderBottom: active ? "2px solid #0066cc" : "2px solid transparent",
    color: active ? "#0066cc" : "#666",
    fontWeight: active ? "bold" : "normal",
    background: "none",
    border: "none",
    borderBottom: active ? "2px solid #0066cc" : "2px solid #ccc",
    marginRight: "4px",
  });

  return (
    <div style={{ padding: "16px" }}>
      {/* タブ */}
      <div style={{ marginBottom: "12px" }}>
        <button style={tabStyle(mode === "address")} onClick={() => setMode("address")}>
          住所・地名で入力
        </button>
        <button style={tabStyle(mode === "coords")} onClick={() => setMode("coords")}>
          緯度経度で入力
        </button>
      </div>

      {/* 住所モード */}
      {mode === "address" && (
        <form onSubmit={handleAddressSubmit}>
          <div style={{ marginBottom: "8px" }}>
            <label>出発地（住所・地名）</label>
            <input
              value={originQuery}
              onChange={(e) => setOriginQuery(e.target.value)}
              placeholder="例: 渋谷駅"
              required
              style={{ marginLeft: "8px", width: "240px" }}
            />
            {originResolved && (
              <span style={{ fontSize: "0.8em", color: "#555", marginLeft: "8px" }}>
                → {originResolved.display_name.slice(0, 40)}…
              </span>
            )}
          </div>
          <div style={{ marginBottom: "8px" }}>
            <label>目的地（住所・地名）</label>
            <input
              value={destQuery}
              onChange={(e) => setDestQuery(e.target.value)}
              placeholder="例: 新宿駅"
              required
              style={{ marginLeft: "8px", width: "240px" }}
            />
            {destResolved && (
              <span style={{ fontSize: "0.8em", color: "#555", marginLeft: "8px" }}>
                → {destResolved.display_name.slice(0, 40)}…
              </span>
            )}
          </div>
          {geocodeError && (
            <p style={{ color: "red", margin: "4px 0" }}>{geocodeError}</p>
          )}
          <button type="submit" disabled={geocoding}>
            {geocoding ? "変換中..." : "ルートを検索"}
          </button>
        </form>
      )}

      {/* 緯度経度モード */}
      {mode === "coords" && (
        <form onSubmit={handleCoordsSubmit}>
          <div style={{ marginBottom: "8px" }}>
            <label>出発地（緯度）</label>
            <input
              value={origin.lat}
              onChange={(e) => setOrigin({ ...origin, lat: e.target.value })}
              placeholder="35.6580"
              required
              style={{ marginLeft: "8px", width: "100px" }}
            />
            <label style={{ marginLeft: "12px" }}>出発地（経度）</label>
            <input
              value={origin.lng}
              onChange={(e) => setOrigin({ ...origin, lng: e.target.value })}
              placeholder="139.7016"
              required
              style={{ marginLeft: "8px", width: "100px" }}
            />
          </div>
          <div style={{ marginBottom: "8px" }}>
            <label>目的地（緯度）</label>
            <input
              value={dest.lat}
              onChange={(e) => setDest({ ...dest, lat: e.target.value })}
              placeholder="35.6896"
              required
              style={{ marginLeft: "8px", width: "100px" }}
            />
            <label style={{ marginLeft: "12px" }}>目的地（経度）</label>
            <input
              value={dest.lng}
              onChange={(e) => setDest({ ...dest, lng: e.target.value })}
              placeholder="139.6922"
              required
              style={{ marginLeft: "8px", width: "100px" }}
            />
          </div>
          <button type="submit">ルートを検索</button>
        </form>
      )}
    </div>
  );
}
