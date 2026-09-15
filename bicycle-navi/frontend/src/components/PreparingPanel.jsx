import { useEffect, useRef } from "react";
import SearchForm from "./SearchForm";
import RouteSummary from "./RouteSummary";
import ViolationCard from "./ViolationCard";
import GuidanceCard from "./GuidanceCard";

export default function PreparingPanel({
  routeData,
  loading,
  error,
  onSearch,
  currentPosition,
  focusedViolationIndex,
  onViolationCardClick,
  focusedGuidanceIndex,
  onGuidanceCardClick,
  recommendations,
}) {
  const violations = routeData?.violations || [];
  // 第2層の案内。violations とは別配列のまま扱い、件数も合算しない。
  const guidance = routeData?.guidance || [];
  const route = routeData?.compliant_route;
  const cardRefs = useRef({});
  const guidanceCardRefs = useRef({});

  // フォーカスされた違反カードが画面に入るよう自動スクロール
  useEffect(() => {
    if (focusedViolationIndex == null) return;
    const el = cardRefs.current[focusedViolationIndex];
    if (el?.scrollIntoView) {
      el.scrollIntoView({ behavior: "smooth", block: "center" });
    }
  }, [focusedViolationIndex]);

  // 案内カードも同様にスクロール（違反カードと同じ処理）
  useEffect(() => {
    if (focusedGuidanceIndex == null) return;
    const el = guidanceCardRefs.current[focusedGuidanceIndex];
    if (el?.scrollIntoView) {
      el.scrollIntoView({ behavior: "smooth", block: "center" });
    }
  }, [focusedGuidanceIndex]);

  return (
    <>
      <div className="sheet-section">
        {route ? (
          <RouteSummary
            route={route}
            violationCount={violations.length}
            guidanceCount={guidance.length}
          />
        ) : (
          <div className="route-summary-empty">
            出発地と目的地を入力してください
          </div>
        )}
      </div>

      {(loading || error || routeData?.rerouted) && (
        <div className="sheet-section">
          {loading && <p style={{ margin: 0 }}>検索中...</p>}
          {error && <p style={{ color: "#d32f2f", margin: 0 }}>{error}</p>}
          {routeData?.rerouted && (
            <p style={{ color: "#1976d2", fontWeight: 600, margin: 0 }}>
              法規に合わせてルートを変更しました
            </p>
          )}
        </div>
      )}

      <div className="sheet-section">
        <SearchForm onSearch={onSearch} currentPosition={currentPosition} />
      </div>

      {violations.length > 0 && (
        <div className="sheet-section">
          <div className="violation-list-header">
            違反箇所 {violations.length} 件
          </div>
          <div className="violation-list">
            {violations.map((v, i) => (
              <ViolationCard
                key={`${v.lat}-${v.lng}-${i}`}
                violation={v}
                index={i}
                focused={i === focusedViolationIndex}
                cardRef={(el) => {
                  if (el) cardRefs.current[i] = el;
                  else delete cardRefs.current[i];
                }}
                onClick={onViolationCardClick}
              />
            ))}
          </div>
        </div>
      )}

      {guidance.length > 0 && (
        <div className="sheet-section">
          <div className="violation-list-header">
            🔔 走行時の案内 {guidance.length} 件
          </div>
          <div className="guidance-list">
            {guidance.map((g, i) => (
              <GuidanceCard
                key={`${g.node_id}-${g.distance_from_start_m}-${i}`}
                guidance={g}
                index={i}
                focused={i === focusedGuidanceIndex}
                cardRef={(el) => {
                  if (el) guidanceCardRefs.current[i] = el;
                  else delete guidanceCardRefs.current[i];
                }}
                onClick={onGuidanceCardClick}
              />
            ))}
          </div>
        </div>
      )}

      {recommendations && recommendations.length > 0 && (
        <div className="sheet-section">
          <div className="violation-list-header">
            🚴 自転車レーン {recommendations.length} 箇所
          </div>
          <ul className="recommendation-list">
            {recommendations.map((r, i) => (
              <li key={i}>{r.message}</li>
            ))}
          </ul>
        </div>
      )}

      <div className="map-credit">
        ©{" "}
        <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener noreferrer">
          OpenStreetMap
        </a>{" "}
        contributors |{" "}
        <a href="https://leafletjs.com" target="_blank" rel="noopener noreferrer">
          Leaflet
        </a>
      </div>
    </>
  );
}
