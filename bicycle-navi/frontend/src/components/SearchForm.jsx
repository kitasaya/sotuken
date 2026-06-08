import { useState } from "react";
import axios from "axios";

const BASE = "/api";

async function fetchGeocode(query) {
  const res = await axios.get(`${BASE}/geocode`, { params: { q: query } });
  return res.data; // { lat, lng, display_name }
}

const gpsButtonStyle = {
  flexShrink: 0,
  padding: "6px 10px",
  fontSize: "0.82rem",
  background: "#e3f2fd",
  color: "#1565c0",
  border: "1px solid #90caf9",
  borderRadius: "6px",
  cursor: "pointer",
  whiteSpace: "nowrap",
  touchAction: "manipulation",
};

export default function SearchForm({ onSearch, currentPosition }) {
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

  // 住所入力が変わったら resolved をクリア（再ジオコーディング対象に戻す）
  const handleOriginChange = (e) => {
    setOriginQuery(e.target.value);
    setOriginResolved(null);
  };

  const handleDestChange = (e) => {
    setDestQuery(e.target.value);
    setDestResolved(null);
  };

  // 出発地に現在地をセット（住所モード）
  const handleUseCurrentPosAddress = () => {
    if (!currentPosition) return;
    setOriginQuery("現在地");
    setOriginResolved({
      lat: currentPosition.lat,
      lng: currentPosition.lng,
      display_name: "現在地（GPS）",
    });
  };

  // 出発地に現在地をセット（座標モード）
  const handleUseCurrentPosCoords = () => {
    if (!currentPosition) return;
    setOrigin({
      lat: String(currentPosition.lat.toFixed(6)),
      lng: String(currentPosition.lng.toFixed(6)),
    });
  };

  const handleAddressSubmit = async (e) => {
    e.preventDefault();
    setGeocoding(true);
    setGeocodeError(null);
    try {
      // resolved 済みのものはジオコーディングをスキップ
      const [o, d] = await Promise.all([
        originResolved ? Promise.resolve(originResolved) : fetchGeocode(originQuery),
        destResolved ? Promise.resolve(destResolved) : fetchGeocode(destQuery),
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
            <label style={{ display: "block", marginBottom: "4px", fontSize: "0.9rem" }}>
              出発地
            </label>
            <div style={{ display: "flex", gap: "6px", alignItems: "center" }}>
              <input
                value={originQuery}
                onChange={handleOriginChange}
                placeholder="例: 渋谷駅"
                required
                style={{ flex: 1, minWidth: 0 }}
              />
              {currentPosition && (
                <button type="button" onClick={handleUseCurrentPosAddress} style={gpsButtonStyle}>
                  📍 現在地
                </button>
              )}
            </div>
            {originResolved && (
              <span style={{ fontSize: "0.8em", color: "#555" }}>
                → {originResolved.display_name.slice(0, 50)}
                {originResolved.display_name.length > 50 ? "…" : ""}
              </span>
            )}
          </div>

          <div style={{ marginBottom: "8px" }}>
            <label style={{ display: "block", marginBottom: "4px", fontSize: "0.9rem" }}>
              目的地
            </label>
            <input
              value={destQuery}
              onChange={handleDestChange}
              placeholder="例: 新宿駅"
              required
              style={{ width: "100%", boxSizing: "border-box" }}
            />
            {destResolved && (
              <span style={{ fontSize: "0.8em", color: "#555" }}>
                → {destResolved.display_name.slice(0, 50)}
                {destResolved.display_name.length > 50 ? "…" : ""}
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
            <div style={{ display: "flex", alignItems: "center", gap: "8px", marginBottom: "4px" }}>
              <label style={{ fontSize: "0.9rem" }}>出発地</label>
              {currentPosition && (
                <button type="button" onClick={handleUseCurrentPosCoords} style={gpsButtonStyle}>
                  📍 現在地
                </button>
              )}
            </div>
            <div style={{ display: "flex", gap: "8px" }}>
              <input
                value={origin.lat}
                onChange={(e) => setOrigin({ ...origin, lat: e.target.value })}
                placeholder="緯度 35.6580"
                required
                style={{ flex: 1, minWidth: 0 }}
              />
              <input
                value={origin.lng}
                onChange={(e) => setOrigin({ ...origin, lng: e.target.value })}
                placeholder="経度 139.7016"
                required
                style={{ flex: 1, minWidth: 0 }}
              />
            </div>
          </div>
          <div style={{ marginBottom: "8px" }}>
            <label style={{ display: "block", marginBottom: "4px", fontSize: "0.9rem" }}>
              目的地
            </label>
            <div style={{ display: "flex", gap: "8px" }}>
              <input
                value={dest.lat}
                onChange={(e) => setDest({ ...dest, lat: e.target.value })}
                placeholder="緯度 35.6896"
                required
                style={{ flex: 1, minWidth: 0 }}
              />
              <input
                value={dest.lng}
                onChange={(e) => setDest({ ...dest, lng: e.target.value })}
                placeholder="経度 139.6922"
                required
                style={{ flex: 1, minWidth: 0 }}
              />
            </div>
          </div>
          <button type="submit">ルートを検索</button>
        </form>
      )}
    </div>
  );
}
