import axios from "axios";

const BASE = "http://localhost:8000/api";

export const fetchRoute = async (originLat, originLng, destLat, destLng) => {
  const res = await axios.post(`${BASE}/route`, {
    origin_lat: originLat,
    origin_lng: originLng,
    dest_lat: destLat,
    dest_lng: destLng,
  });
  return res.data;
};
