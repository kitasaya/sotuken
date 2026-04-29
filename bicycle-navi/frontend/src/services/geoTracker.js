/**
 * Geolocation API ラッパー（シングルトン）
 * フェーズ5-3: GPS位置情報と速度の取得
 */
class GeoTracker {
  constructor() {
    this._watchId = null;
    this._listeners = new Set();
    this._supported =
      typeof navigator !== "undefined" && "geolocation" in navigator;
  }

  get isSupported() {
    return this._supported;
  }

  get isWatching() {
    return this._watchId != null;
  }

  /**
   * 位置情報の監視を開始する。既に開始済みの場合は何もしない。
   * @param {function} onError - エラー時コールバック (message: string)
   */
  start(onError) {
    if (!this._supported) {
      onError?.("このブラウザはGeolocationに対応していません");
      return;
    }
    if (this._watchId != null) return;

    this._watchId = navigator.geolocation.watchPosition(
      (pos) => {
        const { latitude, longitude, speed, accuracy } = pos.coords;
        const data = {
          lat: latitude,
          lng: longitude,
          // speed は m/s（デバイスが取得できない場合は null）
          speed: speed,
          accuracy,
          timestamp: pos.timestamp,
        };
        this._listeners.forEach((cb) => cb(data));
      },
      (err) => {
        onError?.(err.message);
      },
      {
        enableHighAccuracy: true,
        maximumAge: 1000,
        timeout: 10000,
      }
    );
  }

  stop() {
    if (this._watchId != null) {
      navigator.geolocation.clearWatch(this._watchId);
      this._watchId = null;
    }
  }

  addListener(cb) {
    this._listeners.add(cb);
  }

  removeListener(cb) {
    this._listeners.delete(cb);
  }
}

export default new GeoTracker();
