import { useState, useEffect, useRef, useCallback } from "react";
import geoTracker from "../services/geoTracker";

// 自動切り替えのしきい値
const SPEED_THRESHOLD_MS = 5 / 3.6; // 5km/h を m/s に変換 ≈ 1.39 m/s
const STOP_DURATION_MS = 5000; // 5秒停止で停車中モードへ

/**
 * 手動切り替え後の自動切り替え無効時間
 * 設計判断ポイント1（暫定）: 手動切り替え後2分間は自動切り替えを無効化する
 */
const MANUAL_LOCK_MS = 2 * 60 * 1000;

/**
 * GPS速度によるモード自動切り替えフック
 *
 * @param {object} params
 * @param {function} params.setMode - モード変更関数 ('riding' | 'preparing')
 * @param {function} [params.onPosition] - 位置情報コールバック ({ lat, lng, speed, accuracy })
 * @returns {{
 *   handleManualModeChange: function,
 *   geoError: string|null,
 *   isGeoActive: boolean,
 *   manualLocked: boolean,
 * }}
 */
export function useGeoAutoMode({ setMode, onPosition }) {
  // stale closure を避けるため mode を ref で管理
  const modeRef = useRef("preparing");
  const stopTimerRef = useRef(null);
  const manualLockUntilRef = useRef(null);
  // onPosition を ref で持つことで handlePosition の依存を安定させる
  const onPositionRef = useRef(onPosition);

  const [geoError, setGeoError] = useState(null);
  const [isGeoActive, setIsGeoActive] = useState(false);
  const [manualLocked, setManualLocked] = useState(false);

  useEffect(() => {
    onPositionRef.current = onPosition;
  }, [onPosition]);

  const handlePosition = useCallback(
    (pos) => {
      setIsGeoActive(true);
      onPositionRef.current?.(pos);

      // 手動ロック中は自動切り替えしない
      if (
        manualLockUntilRef.current &&
        Date.now() < manualLockUntilRef.current
      ) {
        return;
      }

      // speed が null の場合（デバイスが速度を提供しない）はスキップ
      if (pos.speed == null) return;

      if (pos.speed >= SPEED_THRESHOLD_MS) {
        // 速度あり → 走行中モードへ切り替え
        if (stopTimerRef.current) {
          clearTimeout(stopTimerRef.current);
          stopTimerRef.current = null;
        }
        if (modeRef.current !== "riding") {
          modeRef.current = "riding";
          setMode("riding");
        }
      } else {
        // 速度なし → 5秒後に停車中モードへ切り替え
        if (modeRef.current === "riding" && !stopTimerRef.current) {
          stopTimerRef.current = setTimeout(() => {
            modeRef.current = "preparing";
            setMode("preparing");
            stopTimerRef.current = null;
          }, STOP_DURATION_MS);
        }
      }
    },
    [setMode]
  );

  useEffect(() => {
    geoTracker.addListener(handlePosition);
    geoTracker.start((err) => setGeoError(err));
    return () => {
      geoTracker.removeListener(handlePosition);
      if (stopTimerRef.current) clearTimeout(stopTimerRef.current);
    };
  }, [handlePosition]);

  /**
   * 手動モード切り替え。MANUAL_LOCK_MS の間、自動切り替えを無効化する。
   */
  const handleManualModeChange = useCallback(
    (newMode) => {
      const unlockAt = Date.now() + MANUAL_LOCK_MS;
      manualLockUntilRef.current = unlockAt;
      modeRef.current = newMode;
      setManualLocked(true);

      if (stopTimerRef.current) {
        clearTimeout(stopTimerRef.current);
        stopTimerRef.current = null;
      }

      setMode(newMode);

      // ロック期間が終わったら manualLocked を解除
      setTimeout(() => {
        manualLockUntilRef.current = null;
        setManualLocked(false);
      }, MANUAL_LOCK_MS);
    },
    [setMode]
  );

  // モードが外部（setMode直接）で変わった場合に modeRef を追従させる
  // ※ setMode を呼ぶのは常に handleManualModeChange か自動切り替えの2通りなので
  //   この useEffect は安全策として保持する
  const syncModeRef = (newMode) => {
    modeRef.current = newMode;
  };

  return { handleManualModeChange, syncModeRef, geoError, isGeoActive, manualLocked };
}
