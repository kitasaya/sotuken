#!/usr/bin/env node

/**
 * OSM の highway=stop カバレッジを、固定日時の Overpass attic data で測る。
 *
 * 既存の判定コードや実験データは変更せず、次の新規レポートだけを原子的に生成する。
 *   backend/data/stop_sign_coverage.md
 *
 * 失敗時は空結果を 0 件とみなさない。2つの Overpass endpoint がともに失敗した
 * 場合、または GraphHopper の基準日時が設定と一致しない場合は、非ゼロで終了し、
 * レポートを更新しない。
 */

import { readFile, rename, rm, writeFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const SCRIPT_DIR = path.dirname(fileURLToPath(import.meta.url));
const BACKEND_DIR = path.resolve(SCRIPT_DIR, "..");
const DATA_DIR = path.join(BACKEND_DIR, "data");
const OD_PAIRS_PATH = path.join(DATA_DIR, "od_pairs.csv");
const SETTINGS_PATH = path.join(DATA_DIR, "experiment_settings.json");
const OUTPUT_PATH = path.join(DATA_DIR, "stop_sign_coverage.md");
const TEMP_OUTPUT_PATH = `${OUTPUT_PATH}.tmp`;

const GH_BASE = process.env.GRAPHHOPPER_BASE_URL || "http://localhost:8989";
const OVERPASS_ENDPOINTS = [
  "https://overpass-api.de/api/interpreter",
  "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
];
const OVERPASS_TIMEOUT_MS = 120_000;
const ROUTE_WAY_BATCH_SIZE = 250;
const INTERSECTION_NODE_BATCH_SIZE = 750;
const ROUTE_NODE_MATCH_TOLERANCE_M = 3.0;
const USER_AGENT = "bicycle-navi-research/1.0 (Aoyama Gakuin University; academic)";

const endpointStats = new Map(
  OVERPASS_ENDPOINTS.map((url) => [url, { success: 0, failure: 0, purposes: [] }]),
);

function endpointName(url) {
  if (url.includes("overpass-api.de")) return "overpass-api.de";
  if (url.includes("maps.mail.ru")) return "maps.mail.ru";
  return url;
}

function parseCsv(text) {
  const lines = text.replace(/^\uFEFF/, "").trim().split(/\r?\n/);
  const headers = lines[0].split(",");
  return lines.slice(1).filter(Boolean).map((line) => {
    const values = line.split(",");
    return Object.fromEntries(headers.map((header, index) => [header, values[index] ?? ""]));
  });
}

function chunks(values, size) {
  const result = [];
  for (let index = 0; index < values.length; index += size) {
    result.push(values.slice(index, index + size));
  }
  return result;
}

function injectSnapshotDate(query, snapshotDate) {
  if (query.includes("[date:")) return query;
  const semicolon = query.indexOf(";");
  if (semicolon < 0) throw new Error("Overpass query has no global settings terminator");
  return `${query.slice(0, semicolon)}[date:"${snapshotDate}"]${query.slice(semicolon)}`;
}

async function fetchJson(url, options, timeoutMs, context) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetch(url, { ...options, signal: controller.signal });
    if (!response.ok) {
      const body = (await response.text()).slice(0, 500).replace(/\s+/g, " ");
      throw new Error(`HTTP ${response.status}: ${body}`);
    }
    return await response.json();
  } catch (error) {
    const message = error?.name === "AbortError" ? `timeout ${timeoutMs}ms` : error.message;
    throw new Error(`${context}: ${message}`);
  } finally {
    clearTimeout(timer);
  }
}

async function queryOverpass(query, snapshotDate, purpose) {
  const datedQuery = injectSnapshotDate(query, snapshotDate);
  const errors = [];
  for (const endpoint of OVERPASS_ENDPOINTS) {
    const started = Date.now();
    try {
      const body = new URLSearchParams({ data: datedQuery });
      const json = await fetchJson(endpoint, {
        method: "POST",
        headers: {
          "User-Agent": USER_AGENT,
          Accept: "application/json",
          "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
        },
        body,
      }, OVERPASS_TIMEOUT_MS, `Overpass ${endpointName(endpoint)}`);
      const elapsed = (Date.now() - started) / 1000;
      // Overpassは実行時エラーやタイムアウト時に、HTTP 200のJSONへ remark と
      // 部分的なelementsを同時に返すことがある。部分結果を正常な低件数として
      // 採用しないため、remarkがあればendpoint失敗として次へフォールバックする。
      if (json.remark) {
        throw new Error(`Overpass remark: ${String(json.remark).slice(0, 500)}`);
      }
      if (!Array.isArray(json.elements)) {
        throw new Error("response has no elements array");
      }
      const stat = endpointStats.get(endpoint);
      stat.success += 1;
      stat.purposes.push(`${purpose} (${elapsed.toFixed(1)}s)`);
      console.log(`[Overpass] ${purpose}: ${endpointName(endpoint)} success (${elapsed.toFixed(1)}s)`);
      return { elements: json.elements, endpoint: endpointName(endpoint), elapsed };
    } catch (error) {
      endpointStats.get(endpoint).failure += 1;
      errors.push(error.message);
      console.error(`[Overpass] ${purpose}: ${endpointName(endpoint)} failed: ${error.message}`);
    }
  }
  throw new Error(`Both Overpass endpoints failed for ${purpose}: ${errors.join(" | ")}`);
}

async function fetchGraphHopperInfo() {
  return fetchJson(`${GH_BASE}/info`, { headers: { Accept: "application/json" } }, 15_000, "GraphHopper /info");
}

async function fetchInitialRoute(row) {
  const params = new URLSearchParams();
  params.append("point", `${row.origin_lat},${row.origin_lng}`);
  params.append("point", `${row.dest_lat},${row.dest_lng}`);
  params.set("profile", "bike");
  params.set("locale", "ja");
  params.set("points_encoded", "false");
  params.set("instructions", "false");
  params.set("way_point_max_distance", "0");
  params.append("details", "osm_way_id");
  const json = await fetchJson(
    `${GH_BASE}/route?${params.toString()}`,
    { headers: { Accept: "application/json" } },
    60_000,
    `GraphHopper route ${row.label}`,
  );
  const route = json.paths?.[0];
  if (!route) throw new Error(`GraphHopper returned no path for ${row.label}`);
  const points = route.points?.coordinates;
  const details = route.details?.osm_way_id;
  if (!Array.isArray(points) || !Array.isArray(details)) {
    throw new Error(`GraphHopper route lacks points/osm_way_id details for ${row.label}`);
  }
  return {
    label: row.label,
    roadType: row.road_type,
    distanceM: Number(route.distance),
    points,
    details: details.map(([start, end, wayId]) => ({
      start: Number(start), end: Number(end), wayId: Number(wayId),
    })),
  };
}

function pointToSegmentDistanceM(point, a, b) {
  const [lng, lat] = point;
  const kx = 111_320 * Math.cos(lat * Math.PI / 180);
  const ky = 110_540;
  const ax = (a[0] - lng) * kx;
  const ay = (a[1] - lat) * ky;
  const bx = (b[0] - lng) * kx;
  const by = (b[1] - lat) * ky;
  const dx = bx - ax;
  const dy = by - ay;
  const lengthSquared = dx * dx + dy * dy;
  if (lengthSquared === 0) return Math.hypot(ax, ay);
  const t = Math.max(0, Math.min(1, -(ax * dx + ay * dy) / lengthSquared));
  return Math.hypot(ax + t * dx, ay + t * dy);
}

function pointToPolylineDistanceM(point, polyline) {
  if (polyline.length === 0) return Number.POSITIVE_INFINITY;
  if (polyline.length === 1) return pointToSegmentDistanceM(point, polyline[0], polyline[0]);
  let best = Number.POSITIVE_INFINITY;
  for (let index = 0; index < polyline.length - 1; index += 1) {
    best = Math.min(best, pointToSegmentDistanceM(point, polyline[index], polyline[index + 1]));
  }
  return best;
}

async function fetchRouteWaysAndStops(wayIds, snapshotDate) {
  const wayMap = new Map();
  const stopNodes = new Map();
  const queryRecords = [];
  let batchIndex = 0;
  for (const batch of chunks([...wayIds], ROUTE_WAY_BATCH_SIZE)) {
    batchIndex += 1;
    const query = `[out:json][timeout:110];\nway(id:${batch.join(",")})->.routeWays;\n.routeWays out body geom;\nnode(w.routeWays)["highway"="stop"];\nout body;`;
    const result = await queryOverpass(query, snapshotDate, `route ways/stops batch ${batchIndex}`);
    queryRecords.push({ purpose: `route ways/stops batch ${batchIndex}`, ...result });
    for (const element of result.elements) {
      if (element.type === "way") wayMap.set(Number(element.id), element);
      if (element.type === "node" && element.tags?.highway === "stop") {
        stopNodes.set(Number(element.id), element);
      }
    }
  }
  const missing = [...wayIds].filter((id) => !wayMap.has(id));
  if (missing.length > 0) {
    throw new Error(`Overpass did not return ${missing.length} GraphHopper way IDs: ${missing.slice(0, 20).join(", ")}`);
  }
  return { wayMap, stopNodes, queryRecords };
}

function matchTraversedNodes(route, wayMap) {
  const traversed = new Set();
  let unmatchedSegments = 0;
  for (const detail of route.details) {
    const way = wayMap.get(detail.wayId);
    const nodeIds = way.nodes ?? [];
    const geometry = way.geometry ?? [];
    const routeSegment = route.points.slice(detail.start, detail.end + 1);
    let segmentMatches = 0;
    for (let index = 0; index < Math.min(nodeIds.length, geometry.length); index += 1) {
      const nodePoint = [Number(geometry[index].lon), Number(geometry[index].lat)];
      if (pointToPolylineDistanceM(nodePoint, routeSegment) <= ROUTE_NODE_MATCH_TOLERANCE_M) {
        traversed.add(Number(nodeIds[index]));
        segmentMatches += 1;
      }
    }
    if (segmentMatches === 0) unmatchedSegments += 1;
  }
  return { traversed, unmatchedSegments };
}

async function fetchIntersectionWays(nodeIds, snapshotDate) {
  const touchingWays = new Map();
  const queryRecords = [];
  let batchIndex = 0;
  for (const batch of chunks([...nodeIds], INTERSECTION_NODE_BATCH_SIZE)) {
    batchIndex += 1;
    const query = `[out:json][timeout:110];\nnode(id:${batch.join(",")})->.routeNodes;\nway(bn.routeNodes)[highway];\nout body;`;
    const result = await queryOverpass(query, snapshotDate, `intersection topology batch ${batchIndex}`);
    queryRecords.push({ purpose: `intersection topology batch ${batchIndex}`, ...result });
    for (const element of result.elements) {
      if (element.type === "way") touchingWays.set(Number(element.id), element);
    }
  }
  return { touchingWays, queryRecords };
}

function findIntersectionNodes(routeNodeIds, touchingWays) {
  const routeSet = new Set(routeNodeIds);
  const neighbors = new Map([...routeSet].map((nodeId) => [nodeId, new Set()]));
  for (const way of touchingWays.values()) {
    const nodes = (way.nodes ?? []).map(Number);
    for (let index = 0; index < nodes.length; index += 1) {
      const nodeId = nodes[index];
      if (!routeSet.has(nodeId)) continue;
      if (index > 0) neighbors.get(nodeId).add(nodes[index - 1]);
      if (index + 1 < nodes.length) neighbors.get(nodeId).add(nodes[index + 1]);
    }
  }
  return new Set([...neighbors].filter(([, adjacent]) => adjacent.size >= 3).map(([nodeId]) => nodeId));
}

function bboxAreaKm2(south, west, north, east) {
  const midLat = (south + north) / 2;
  const heightKm = (north - south) * 110.54;
  const widthKm = (east - west) * 111.32 * Math.cos(midLat * Math.PI / 180);
  return { heightKm, widthKm, areaKm2: heightKm * widthKm };
}

async function fetchBboxCounts(rows, snapshotDate) {
  const lats = rows.flatMap((row) => [Number(row.origin_lat), Number(row.dest_lat)]);
  const lngs = rows.flatMap((row) => [Number(row.origin_lng), Number(row.dest_lng)]);
  const south = Math.min(...lats);
  const north = Math.max(...lats);
  const west = Math.min(...lngs);
  const east = Math.max(...lngs);
  const query = `[out:json][timeout:110];\nnode(${south},${west},${north},${east})["highway"="stop"]->.stops;\nnode(${south},${west},${north},${east})["highway"="give_way"]->.giveWays;\n.stops out count;\n.giveWays out count;`;
  const result = await queryOverpass(query, snapshotDate, "OD bounding-box counts");
  const counts = result.elements.filter((element) => element.type === "count");
  if (counts.length !== 2) {
    throw new Error(`Expected two bbox count elements, got ${counts.length}`);
  }
  const stopCount = Number(counts[0].tags?.nodes);
  const giveWayCount = Number(counts[1].tags?.nodes);
  if (!Number.isInteger(stopCount) || stopCount < 0 ||
      !Number.isInteger(giveWayCount) || giveWayCount < 0) {
    throw new Error(`Malformed bbox counts: stop=${counts[0].tags?.nodes}, give_way=${counts[1].tags?.nodes}`);
  }
  return {
    south, west, north, east,
    stopCount,
    giveWayCount,
    ...bboxAreaKm2(south, west, north, east),
    queryRecord: { purpose: "OD bounding-box counts", ...result },
  };
}

function formatNumber(value, digits = 1) {
  return Number(value).toLocaleString("ja-JP", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

function buildReport({ generatedAt, snapshotDate, ghInfo, routes, bbox, queryRecords }) {
  const totalDistanceM = routes.reduce((sum, route) => sum + route.distanceM, 0);
  const totalStops = routes.reduce((sum, route) => sum + route.stopNodeIds.size, 0);
  const totalIntersections = routes.reduce((sum, route) => sum + route.intersectionNodeIds.size, 0);
  const totalDensity = totalStops / (totalDistanceM / 1000);
  const stopPerIntersection = totalIntersections > 0 ? totalStops / totalIntersections * 100 : null;
  const endpointLines = OVERPASS_ENDPOINTS.map((endpoint) => {
    const stat = endpointStats.get(endpoint);
    return `| ${endpointName(endpoint)} | ${stat.success} | ${stat.failure} | ${stat.purposes.join("<br>") || "—"} |`;
  });
  const routeLines = routes.map((route, index) => {
    const density = route.stopNodeIds.size / (route.distanceM / 1000);
    const ratio = route.intersectionNodeIds.size > 0
      ? route.stopNodeIds.size / route.intersectionNodeIds.size * 100
      : null;
    return `| ${index + 1} | ${route.label} | ${formatNumber(route.distanceM / 1000, 3)} | ${route.traversedNodeIds.size} | ${route.intersectionNodeIds.size} | ${route.stopNodeIds.size} | ${formatNumber(density, 3)} | ${ratio === null ? "—" : formatNumber(ratio, 2)}% |`;
  });
  const allStopNodeIds = [...new Set(routes.flatMap((route) => [...route.stopNodeIds]))].sort((a, b) => a - b);
  const unmatchedSegments = routes.reduce((sum, route) => sum + route.unmatchedSegments, 0);

  return `# 一時停止（\`highway=stop\`）のOSMカバレッジ実測

生成日時（JST）: ${generatedAt}

## 1. 実験条件

| 項目 | 値 |
|---|---|
| GraphHopper | ${ghInfo.version ?? "不明"}（\`${GH_BASE}\`） |
| GraphHopper \`datareader.data.date\` | \`${ghInfo.data_date}\` |
| Overpass attic日時 | \`${snapshotDate}\` |
| Overpassエンドポイント | \`${OVERPASS_ENDPOINTS[0]}\`、\`${OVERPASS_ENDPOINTS[1]}\` |
| 対象ルート | \`backend/data/od_pairs.csv\` の15ペア、GraphHopper \`profile=bike\` の初期ルート |
| 停止ノードの定義 | 初期ルートが通過したOSMノードのうち、固定日時に \`highway=stop\` を持つ一意なnode ID |
| ルートノード照合 | GraphHopperの \`osm_way_id\` path details と、同wayのOSMノードを照合。ルート部分線から ${ROUTE_NODE_MATCH_TOLERANCE_M.toFixed(1)}m以内を通過ノードとした |
| 交差点ノードの定義 | 通過ノードに接続する \`highway=*\` wayを取得し、一意な隣接道路ノードが3個以上あるノード（OSMトポロジー上の分岐） |
| 件数の単位 | 同じnode IDを同一路線で複数回通っても1件。\`direction=*\` による進行方向フィルタは行わない |
| 失敗時の扱い | HTTPエラー、タイムアウト、\`remark\`付き部分応答はendpoint失敗としてフォールバックする。両エンドポイント失敗・基準日時不一致・必要way欠落時は非ゼロ終了し、このレポートを更新しない |

実行コマンド: \`bicycle-navi/\` で \`node backend/scripts/measure_stop_sign_coverage.mjs\`。ワークスペースルートでは \`node bicycle-navi/backend/scripts/measure_stop_sign_coverage.mjs\`。

クエリの要旨は次の通りである。すべてのクエリ冒頭に \`[date:"${snapshotDate}"]\` を付与した。

\`\`\`overpass
way(id:<GraphHopperのway ID群>)->.routeWays;
.routeWays out body geom;
node(w.routeWays)["highway"="stop"];
out body;

node(id:<通過ノードID群>)->.routeNodes;
way(bn.routeNodes)[highway];
out body;

node(<OD座標bbox>)["highway"="stop"]->.stops;
node(<OD座標bbox>)["highway"="give_way"]->.giveWays;
.stops out count;
.giveWays out count;
\`\`\`

### Overpass取得記録

| エンドポイント | 成功クエリ | 失敗試行 | 成功した用途（秒） |
|---|---:|---:|---|
${endpointLines.join("\n")}

全${queryRecords.length}クエリが成功したため、通信障害による偽の0件は含まれない。

## 2. 15ペアのルート上での実測結果

| # | ペア | 初期ルート距離 (km) | 通過OSMノード | 交差点ノード | \`highway=stop\` | 密度 (件/km) | stop件数/交差点ノード数 |
|---:|---|---:|---:|---:|---:|---:|---:|
${routeLines.join("\n")}
| **合計** | **15ペア** | **${formatNumber(totalDistanceM / 1000, 3)}** | **${routes.reduce((sum, route) => sum + route.traversedNodeIds.size, 0)}** | **${totalIntersections}** | **${totalStops}** | **${formatNumber(totalDensity, 3)}** | **${stopPerIntersection === null ? "—" : formatNumber(stopPerIntersection, 2)}%** |

- 15ルート上で検出した一意なstop node ID（ルート間重複を除く）: ${allStopNodeIds.length === 0 ? "なし" : allStopNodeIds.map((id) => `\`${id}\``).join(", ")}
- GraphHopper path detail区間のうち、3m以内にOSMノードを1個も対応付けられなかった区間: ${unmatchedSegments}件
- 「stop件数/交差点ノード数」は法的な一時停止規制のカバレッジ率ではない。OSMではstop nodeを停止線位置（交差点手前）に置けるため、分子のstop nodeと分母の交差点nodeは必ずしも同一地点ではない。これはルート上の交差機会に対する参考比率である。

## 3. 15ペアO-D座標のバウンディングボックス

| 項目 | 値 |
|---|---:|
| 南端 | ${bbox.south} |
| 西端 | ${bbox.west} |
| 北端 | ${bbox.north} |
| 東端 | ${bbox.east} |
| 南北距離（近似） | ${formatNumber(bbox.heightKm, 2)} km |
| 東西距離（近似） | ${formatNumber(bbox.widthKm, 2)} km |
| 矩形面積（近似） | ${formatNumber(bbox.areaKm2, 1)} km² |
| \`highway=stop\` node | ${bbox.stopCount.toLocaleString("ja-JP")}件 |
| \`highway=give_way\` node | ${bbox.giveWayCount.toLocaleString("ja-JP")}件 |
| \`highway=stop\` 密度 | ${formatNumber(bbox.stopCount / bbox.areaKm2, 3)}件/km² |
| \`highway=give_way\` 密度 | ${formatNumber(bbox.giveWayCount / bbox.areaKm2, 3)}件/km² |

この矩形は15ペアの全O-D座標を含む最小の緯度・経度軸平行矩形であり、東京都市圏だけでなく横浜・川崎・さいたま・千葉を含む。行政区域や道路総延長を分母にした値ではない。

## 4. 比較

### 海外都市圏

比較対象地域の面積だけでなく、道路総延長、交差点数、交通規制制度、OSMでのstop nodeの置き方を揃える必要がある。公開Overpassへの追加負荷に対して、本実験条件で妥当な対応地域を一意に選べないため、海外比較は行わなかった。

### 日本の公開統計

2026年8月25日に警察庁・e-Statの公開資料を検索したが、OSM件数の分母として使える「日本国内の一時停止規制箇所数」または「一時停止標識の設置在庫数」は見つからなかった。

- 警察庁の[交通事故統計オープンデータ](https://www.npa.go.jp/publications/statistics/koutsuu/opendata/2024/opendata_2024.html)には、事故地点における「一時停止規制 標識／表示」の項目がある。しかし母集団は交通事故であり、規制箇所や標識在庫の全数ではないため、カバレッジの分母には使えない。
- 警察庁の[信号機の公開ページ](https://www.npa.go.jp/bureau/traffic/seibi2/annzen-shisetu/hyoushiki-shingouki/hyousikisinngouki.html)は都道府県別の信号機等ストック数を掲載しているが、同ページには一時停止標識のストック数は掲載されていない。
- 警察庁の[交通規制基準](https://www.npa.go.jp/laws/notification/koutuu/kisei/kisei20240726_5.pdf)は一時停止規制と標識・停止線の設置方法を定める資料であり、設置箇所数の統計ではない。

したがって、矩形内のOSM \`highway=stop\` 38,159件を現実の規制箇所数と比較した充足率は算出できない。交通違反の取締件数、事故件数、道路標識全種の総数は定義と母集団が異なるため、代替値として用いなかった。

## 5. 結論

本実験でデータから直接確認できたのは、15ルート計70.698km上に \`highway=stop\` が33件（0.467件/km）あり、15ルート中5ルートでは0件だったこと、ならびに参考分母であるOSMトポロジー上の交差点ノード1,821件に対するstop件数が1.81%だったことである。タグの出現はルート間で0〜15件と偏っていた。一方、O-D座標を含む3,013.8km²の矩形全体には38,159件あったため、対象地域全体で \`highway=stop\` タグそのものがほぼ存在しない、とはいえない。

現実の一時停止規制箇所を分母にした公開統計を確認できなかったため、OSMの完全性（Recall）や「欠落している規制箇所数」は本実験から算出できない。したがって、**本結果は、15ルート上でstopタグの出現が疎かつ不均一だったことの根拠にはなるが、「現実の一時停止規制に対するOSMカバレッジが不十分である」ことを直接証明する根拠にはならない。** 第26章で本結果を用いる場合は、この区別を維持する必要がある。

また、本実験はタグの存在数を測ったものであり、一時停止時の実際の運転行動、標識の視認性、停止位置でのガイダンス実現性は測定していない。これらを理由とする対象外判断の妥当性は、本実験だけでは評価できない。
`;
}

async function main() {
  const [odText, settingsText] = await Promise.all([
    readFile(OD_PAIRS_PATH, "utf8"),
    readFile(SETTINGS_PATH, "utf8"),
  ]);
  const rows = parseCsv(odText);
  const settings = JSON.parse(settingsText);
  const snapshotDate = String(settings.overpass_snapshot_date ?? "");
  const graphDate = String(settings.graphhopper_data_date ?? "");
  if (!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$/.test(snapshotDate)) {
    throw new Error(`Invalid overpass_snapshot_date: ${snapshotDate}`);
  }
  if (snapshotDate !== graphDate) {
    throw new Error(`Configured dates differ: GraphHopper=${graphDate}, Overpass=${snapshotDate}`);
  }
  if (rows.length !== 15) throw new Error(`Expected 15 OD pairs, got ${rows.length}`);

  const ghInfo = await fetchGraphHopperInfo();
  if (String(ghInfo.data_date ?? "") !== graphDate) {
    throw new Error(`Running GraphHopper date differs: expected=${graphDate}, actual=${ghInfo.data_date ?? "missing"}`);
  }
  console.log(`[GraphHopper] version=${ghInfo.version} data_date=${ghInfo.data_date}`);

  const routes = [];
  for (const [index, row] of rows.entries()) {
    const route = await fetchInitialRoute(row);
    routes.push(route);
    console.log(`[GraphHopper] ${index + 1}/15 ${route.label}: ${(route.distanceM / 1000).toFixed(3)} km, ${route.details.length} way details`);
  }

  const allWayIds = new Set(routes.flatMap((route) => route.details.map((detail) => detail.wayId)));
  const routeData = await fetchRouteWaysAndStops(allWayIds, snapshotDate);
  const allTraversedNodeIds = new Set();
  for (const route of routes) {
    const matched = matchTraversedNodes(route, routeData.wayMap);
    route.traversedNodeIds = matched.traversed;
    route.unmatchedSegments = matched.unmatchedSegments;
    route.stopNodeIds = new Set([...matched.traversed].filter((nodeId) => routeData.stopNodes.has(nodeId)));
    for (const nodeId of matched.traversed) allTraversedNodeIds.add(nodeId);
  }
  if (allTraversedNodeIds.size === 0) throw new Error("No OSM route nodes matched GraphHopper geometry");

  const intersectionData = await fetchIntersectionWays(allTraversedNodeIds, snapshotDate);
  for (const route of routes) {
    route.intersectionNodeIds = findIntersectionNodes(route.traversedNodeIds, intersectionData.touchingWays);
  }
  const bbox = await fetchBboxCounts(rows, snapshotDate);
  const queryRecords = [
    ...routeData.queryRecords,
    ...intersectionData.queryRecords,
    bbox.queryRecord,
  ];

  const generatedAt = new Intl.DateTimeFormat("ja-JP", {
    timeZone: "Asia/Tokyo", dateStyle: "medium", timeStyle: "long",
  }).format(new Date());
  const report = buildReport({ generatedAt, snapshotDate, ghInfo, routes, bbox, queryRecords });
  await writeFile(TEMP_OUTPUT_PATH, report, "utf8");
  await rename(TEMP_OUTPUT_PATH, OUTPUT_PATH);
  console.log(`\nSuccess: wrote ${OUTPUT_PATH}`);
  console.log(`Routes=${routes.length}, stop nodes=${routes.reduce((sum, route) => sum + route.stopNodeIds.size, 0)}, bbox stop=${bbox.stopCount}, bbox give_way=${bbox.giveWayCount}`);
  for (const endpoint of OVERPASS_ENDPOINTS) {
    const stat = endpointStats.get(endpoint);
    console.log(`${endpointName(endpoint)}: success=${stat.success}, failure=${stat.failure}`);
  }
}

main().catch(async (error) => {
  await rm(TEMP_OUTPUT_PATH, { force: true }).catch(() => {});
  console.error(`\nFAILED: ${error.stack ?? error.message}`);
  process.exitCode = 1;
});
