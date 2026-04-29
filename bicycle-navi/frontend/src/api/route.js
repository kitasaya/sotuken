import axios from "axios";

// Vite の proxy 設定により /api → http://localhost:8000/api に中継される
// スマホ等の別端末から LAN 経由でアクセスした場合も同じ相対パスで動作する
const BASE = "/api";

export const fetchRoute = async (originLat, originLng, destLat, destLng) => {
  const res = await axios.post(`${BASE}/route`, {
    origin_lat: originLat,
    origin_lng: originLng,
    dest_lat: destLat,
    dest_lng: destLng,
  });
  return res.data;
};
