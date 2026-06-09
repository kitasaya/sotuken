import { useEffect, useRef } from "react";
import SearchForm from "./SearchForm";
import RouteSummary from "./RouteSummary";
import ViolationCard from "./ViolationCard";

export default function PreparingPanel({
  routeData,
  loading,
  error,
  onSearch,
  currentPosition,
  focusedViolationIndex,
  onViolationCardClick,
  recommendations,
}) {
  const violations = routeData?.violations || [];
  const route = routeData?.compliant_route;
  const cardRefs = useRef({});

  // フォーカスされた違反カードが画面に入るよう自動スクロール
  useEffect(() => {
    if (focusedViolationIndex == null) return;
    const el = cardRefs.current[focusedViolationIndex];
    if (el?.scrollIntoView) {
      el.scrollIntoView({ behavior: "smooth", block: "center" });
    }
  }, [focusedViolationIndex]);

  return (
    <>
      <div className="sheet-section">
        {route ? (
          <RouteSummary route={route} violationCount={violations.length} />
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
