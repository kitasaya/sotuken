# 自転車ナビゲーションシステム MVP 開発指示書

## プロジェクト概要

日本の交通法規に準拠した自転車ナビゲーションシステムの MVP を構築する。
卒業研究（青山学院大学・宮治研究室）のシステム実装フェーズ。

**MVP のゴール：**
「出発地・目的地を入力すると、逆走禁止（`oneway` タグ）を考慮してリルートした経路を地図上に表示する」

---

## システム構成

```
frontend/   React + Leaflet.js（地図表示・入力UI）
backend/    Python + FastAPI（ルーティング・法規判定ロジック）
graphhopper/ Docker でセルフホスト（ルーティングエンジン）
```

外部API:

- **Overpass API**（OSMタグ取得、無料）
- **GraphHopper Public API**（Dockerが動かない場合のフォールバック）

---

## 開発ステップ（この順番で進めること）

### STEP 1: GraphHopper を Docker で起動する

以下の手順でセットアップすること。

1. プロジェクトルートに `graphhopper/` ディレクトリを作成
2. 関東エリアの OSM データをダウンロード:

   ```
   https://download.geofabrik.de/asia/japan/kanto-latest.osm.pbf
   ```

   を `graphhopper/` に保存（ファイル名: `kanto-latest.osm.pbf`）

3. `graphhopper/config.yml` を作成:

   ```yaml
   graphhopper:
     datareader.file: /data/data.pbf
     graph.location: /data/graph-cache
     graph.encoded_values: car_access, car_average_speed, country, road_class, roundabout, max_speed, foot_access, foot_average_speed, foot_priority, foot_road_access, hike_rating, bike_access, bike_average_speed, bike_priority, bike_road_access, bike_network, mtb_rating, ferry_speed, road_environment
     import.osm.ignored_highways: motor, trunk
     profiles:
       - name: bike
         custom_model:
           speed:
             - if: "true"
               limit_to: "bike_average_speed"
           priority:
             - if: "!bike_access"
               multiply_by: "0"

   server:
     application_connectors:
       - type: http
         port: 8989
         bind_host: 0.0.0.0
     admin_connectors:
       - type: http
         port: 8990
         bind_host: 0.0.0.0
   ```

4. `docker-compose.yml` をプロジェクトルートに作成:

   ```yaml
   version: '3'
   services:
     graphhopper:
       image: israelhikingmap/graphhopper:latest
       ports:
         - "8989:8989"
       volumes:
         - ./graphhopper:/data
       environment:
         - JAVA_OPTS=-Xmx4g -Xms1g
       command: >
         --url https://download.geofabrik.de/asia/japan/kanto-latest.osm.pbf
         --host 0.0.0.0
         -c /data/config.yml
   ```

5. `docker-compose up` で起動し、`http://localhost:8989` にアクセスして動作確認

**確認コマンド:**

```bash
curl "http://localhost:8989/route?point=35.6762,139.6503&point=35.6895,139.6917&profile=bike&locale=ja"
```

JSONレスポンスが返ってきたら成功。

---

### STEP 2: FastAPI バックエンドを作成する

`backend/` ディレクトリに以下を作成すること。

**ファイル構成:**

```
backend/
  main.py
  routers/
    route.py
  services/
    graphhopper.py   # GHへのリクエスト
    overpass.py      # OSMタグ取得
    law_checker.py   # 法規判定ロジック（MVP: 逆走禁止のみ）
  requirements.txt
```

**`requirements.txt`:**

```
fastapi
uvicorn
httpx
```

**`main.py`:**

```python
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from routers import route

app = FastAPI(title="自転車ナビAPI")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(route.router, prefix="/api")
```

**`routers/route.py`:**

```python
from fastapi import APIRouter
from pydantic import BaseModel
from services.graphhopper import get_route
from services.overpass import get_way_tags
from services.law_checker import check_oneway_violation

router = APIRouter()

class RouteRequest(BaseModel):
    origin_lat: float
    origin_lng: float
    dest_lat: float
    dest_lng: float

@router.post("/route")
async def calculate_route(req: RouteRequest):
    # ① GraphHopper で初期ルート取得
    route_data = await get_route(req.origin_lat, req.origin_lng, req.dest_lat, req.dest_lng)

    # ② ルート上のエッジの OSM タグを取得して法規チェック
    points = route_data["paths"][0]["points"]["coordinates"]
    violations = await check_oneway_violation(points)

    return {
        "route": route_data["paths"][0],
        "violations": violations,
        "compliant": len(violations) == 0
    }
```

**`services/graphhopper.py`:**

```python
import httpx

GH_BASE = "http://localhost:8989"

async def get_route(origin_lat, origin_lng, dest_lat, dest_lng):
    async with httpx.AsyncClient() as client:
        resp = await client.get(f"{GH_BASE}/route", params={
            "point": [f"{origin_lat},{origin_lng}", f"{dest_lat},{dest_lng}"],
            "vehicle": "bike",
            "locale": "ja",
            "points_encoded": "false",
            "details": "road_class,road_environment,max_speed,average_speed"
        })
        resp.raise_for_status()
        return resp.json()
```

**`services/overpass.py`:**

```python
import httpx

OVERPASS_URL = "https://overpass-api.de/api/interpreter"

async def get_way_tags(lat: float, lng: float, radius: int = 20) -> dict:
    """指定座標付近の道路タグを取得する"""
    query = f"""
    [out:json][timeout:10];
    way(around:{radius},{lat},{lng})[highway];
    out tags;
    """
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(OVERPASS_URL, data={"data": query})
        resp.raise_for_status()
        data = resp.json()
        if data["elements"]:
            return data["elements"][0].get("tags", {})
        return {}
```

**`services/law_checker.py`:**

```python
from services.overpass import get_way_tags

async def check_oneway_violation(points: list) -> list:
    """
    MVPスコープ: onewayタグによる逆走チェック
    ルート上の各座標の道路タグを取得し、逆走になっているエッジを検出する
    """
    violations = []

    # サンプリング（全点チェックはAPIレート的に重いので10点に間引く）
    step = max(1, len(points) // 10)
    sampled = points[::step]

    for i, point in enumerate(sampled):
        lng, lat = point[0], point[1]
        tags = await get_way_tags(lat, lng)

        oneway = tags.get("oneway", "no")
        if oneway in ("yes", "true", "1"):
            violations.append({
                "lat": lat,
                "lng": lng,
                "rule": "oneway",
                "message": "一方通行のため逆走の可能性があります"
            })

    return violations
```

**起動確認:**

```bash
cd backend
pip install -r requirements.txt
uvicorn main:app --reload --port 8000
```

`http://localhost:8000/docs` で Swagger UI が開けばOK。

---

### STEP 3: React フロントエンドを作成する

**セットアップ:**

```bash
npm create vite@latest frontend -- --template react
cd frontend
npm install
npm install leaflet react-leaflet axios
```

**ファイル構成（`src/` 以下）:**

```
src/
  App.jsx
  components/
    MapView.jsx       # Leaflet地図表示
    SearchForm.jsx    # 出発地・目的地入力
    ViolationAlert.jsx # 法規違反の警告表示
  api/
    route.js          # バックエンドAPIクライアント
```

**`src/api/route.js`:**

```javascript
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
```

**`src/components/SearchForm.jsx`:**

```jsx
import { useState } from "react";

export default function SearchForm({ onSearch }) {
  const [origin, setOrigin] = useState({ lat: "", lng: "" });
  const [dest, setDest] = useState({ lat: "", lng: "" });

  const handleSubmit = (e) => {
    e.preventDefault();
    onSearch(
      parseFloat(origin.lat),
      parseFloat(origin.lng),
      parseFloat(dest.lat),
      parseFloat(dest.lng),
    );
  };

  return (
    <form onSubmit={handleSubmit} style={{ padding: "16px" }}>
      <div>
        <label>出発地（緯度）</label>
        <input
          value={origin.lat}
          onChange={(e) => setOrigin({ ...origin, lat: e.target.value })}
          placeholder="35.6762"
          required
        />
        <label>出発地（経度）</label>
        <input
          value={origin.lng}
          onChange={(e) => setOrigin({ ...origin, lng: e.target.value })}
          placeholder="139.6503"
          required
        />
      </div>
      <div>
        <label>目的地（緯度）</label>
        <input
          value={dest.lat}
          onChange={(e) => setDest({ ...dest, lat: e.target.value })}
          placeholder="35.6895"
          required
        />
        <label>目的地（経度）</label>
        <input
          value={dest.lng}
          onChange={(e) => setDest({ ...dest, lng: e.target.value })}
          placeholder="139.6917"
          required
        />
      </div>
      <button type="submit">ルートを検索</button>
    </form>
  );
}
```

**`src/components/MapView.jsx`:**

```jsx
import {
  MapContainer,
  TileLayer,
  Polyline,
  CircleMarker,
  Popup,
} from "react-leaflet";
import "leaflet/dist/leaflet.css";

export default function MapView({ route, violations }) {
  const center = [35.6762, 139.6503]; // 東京

  // GraphHopperのcoordinatesは [lng, lat] なので反転する
  const positions = route
    ? route.points.coordinates.map(([lng, lat]) => [lat, lng])
    : [];

  return (
    <MapContainer
      center={center}
      zoom={13}
      style={{ height: "70vh", width: "100%" }}
    >
      <TileLayer
        url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
        attribution="© OpenStreetMap contributors"
      />
      {positions.length > 0 && (
        <Polyline positions={positions} color="blue" weight={4} />
      )}
      {violations &&
        violations.map((v, i) => (
          <CircleMarker
            key={i}
            center={[v.lat, v.lng]}
            radius={8}
            color="red"
            fillColor="red"
            fillOpacity={0.7}
          >
            <Popup>{v.message}</Popup>
          </CircleMarker>
        ))}
    </MapContainer>
  );
}
```

**`src/components/ViolationAlert.jsx`:**

```jsx
export default function ViolationAlert({ violations, compliant }) {
  if (!violations) return null;
  if (compliant) return <div style={{ color: "green" }}>✅ 法規違反なし</div>;

  return (
    <div style={{ color: "red", padding: "8px" }}>
      ⚠️ 法規違反の可能性: {violations.length}件
      <ul>
        {violations.map((v, i) => (
          <li key={i}>{v.message}</li>
        ))}
      </ul>
    </div>
  );
}
```

**`src/App.jsx`:**

```jsx
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
      {routeData && (
        <ViolationAlert
          violations={routeData.violations}
          compliant={routeData.compliant}
        />
      )}
      <MapView route={routeData?.route} violations={routeData?.violations} />
    </div>
  );
}
```

**起動確認:**

```bash
cd frontend
npm run dev
```

`http://localhost:5173` でアプリが表示されればOK。

---

### STEP 4: 動作確認シナリオ

以下の座標でテストすること（東京・渋谷〜新宿周辺）:

- 出発地: 緯度 `35.6580`, 経度 `139.7016`（渋谷駅付近）
- 目的地: 緯度 `35.6896`, 経度 `139.6922`（新宿駅付近）

**期待する動作:**

1. 地図上に青いルートが表示される
2. 一方通行の道路が含まれる場合、赤いマーカーと警告が表示される
3. 違反なしの場合は緑の「✅ 法規違反なし」が表示される

---

## 既知の制限・注意事項（論文の限界として記載予定）

- OSMの `oneway` タグが付いていない道路は検出できない
- Overpass APIへのリクエストはレートリミットあり（本番では間引きが必要）
- GraphHopperのセルフホスト初回起動はOSMデータのビルドに時間がかかる（関東は30分〜1時間程度）
- GraphHopperが起動しない場合は Public API（`https://graphhopper.com/api/1/route`）にAPIキーを取得して差し替えること
- リルート時は `ch.disable=True`（Dijkstra/A*）のため法規準拠ルート計算が遅い（数秒〜十数秒）
- Overpass一括クエリの座標対応は「wayの中心座標との最近傍マッチング」のため、
  長い道路セグメントでは中心が遠い場合がある（精度上の限界）
- Overpass 公開サーバーが全滅した場合は法規チェックをスキップして元ルートを返す
  （論文の限界として記載予定）

---

## 次フェーズ（MVP完成後に拡張）

- [ ] 歩道通行可否チェック（`sidewalk` タグ）
- [ ] 自転車レーン優先ルーティング（`cycleway` タグ）
- [ ] 二段階右折要否の判定（`highway` + `lanes` タグ）
- [ ] 住所・地名での入力（ジオコーディング）
- [ ] スマートフォン対応UI

---

## 実装済みの修正・注意事項

### GraphHopperのAPIパラメータ変更（重要）
現在使用している `israelhikingmap/graphhopper:latest` は新バージョンのため、
`vehicle=bike` ではなく **`profile=bike`** を使用すること。

`services/graphhopper.py` では以下のパラメータを使用済み:
```python
"profile": "bike",   # ← vehicle: "bike" は古い書き方、動かない
"points_encoded": "false",
```
`details` パラメータ（`road_class` 等）は現在の config.yml では未設定のため除去済み。

### Overpass APIのエラーハンドリング
Overpass API は負荷状況によって 504 タイムアウトを返すことがある。
`services/law_checker.py` では `httpx.HTTPError` / `httpx.TimeoutException` をキャッチして
該当ポイントをスキップする実装済み。Overpass エラー時もルートは正常に返る。

### バックエンドの起動方法（Windows）
`uvicorn` を PowerShell で独立プロセスとして起動すること（ターミナルを閉じても継続動作）:

```powershell
Start-Process -FilePath 'python' -ArgumentList '-m uvicorn main:app --reload --port 8000' -WorkingDirectory 'C:\Users\masa2\Desktop\卒研\bicycle-navi\backend' -WindowStyle Hidden
```

または通常のターミナルで:
```bash
cd backend
python -m uvicorn main:app --reload --port 8000
```

---

## 現在の進捗（2026-04-14時点）

### 完了済み ✅
- **STEP 1**: GraphHopper を Docker で起動（`http://localhost:8989`）
  - `graphhopper/config.yml` カスタム設定済み（bike プロファイル）
  - OSM 関東データ（`kanto-latest.osm.pbf`）ビルド済み
- **STEP 2**: FastAPI バックエンド実装・稼働中（`http://localhost:8000`）
- **STEP 3**: React フロントエンド実装・稼働中（`http://localhost:5173`）
- **STEP 4**: 動作確認完了
  - 渋谷→新宿で青いルートが地図上に表示される
  - 一方通行違反箇所に赤いマーカーが表示される（渋谷→新宿で2件検出）
  - 違反内容のポップアップ表示も動作
- **次フェーズ①**: 歩道通行可否チェック実装済み（2026-04-12）
  - `backend/services/law_checker.py` に `check_sidewalk_violation` 追加
  - 条件: `sidewalk=no` 単体で違反フラグ（当初の AND 条件から変更済み）
  - `backend/routers/route.py` で oneway + sidewalk 両チェック結果を統合
  - `frontend/src/components/ViolationAlert.jsx` で `rule` 別アイコン表示
    （oneway → 🚫、sidewalk → 🚶）
- **次フェーズ②**: 自転車レーン検出（推奨情報）実装済み（2026-04-12）
  - `backend/services/law_checker.py` に `check_cycleway_recommendation` 追加
    （`cycleway=lane` または `cycleway=track` を推奨情報として返す）
  - `backend/routers/route.py` で `recommendations` フィールドを追加返却
  - `frontend/src/App.jsx` で `recommendations` を `ViolationAlert` に渡すよう更新
  - `frontend/src/components/ViolationAlert.jsx` で青色の 🚴 推奨情報セクション追加
  - 渋谷→新宿動作確認: violations 3件（oneway×2, sidewalk×1）、recommendations 0件
- **次フェーズ③**: 二段階右折要否判定実装済み（2026-04-12）
  - `backend/services/law_checker.py` に `check_two_step_turn` 追加
    （`highway=primary/secondary` または `lanes>=3` で違反フラグ、lanes は int 変換済み）
  - `backend/routers/route.py` で two_step_turn 結果を violations に統合
  - `frontend/src/components/ViolationAlert.jsx` に 🔄 アイコン追加
  - 渋谷→新宿: violations 5件（oneway×4, two_step_turn×1）
  - 東京駅→渋谷: violations 7件（oneway×6, two_step_turn×1）← 幹線道路で検出確認
- **次フェーズ④**: 法規違反エッジ除外によるリルート実装済み（2026-04-12）
  - `backend/services/rerouter.py` を新規作成
    - GraphHopper の `custom_model + areas` で違反座標付近（±0.001度≈100m）をブロック
    - `block_area` は廃止済みのため POST + custom_model アプローチを採用
  - `backend/routers/route.py` に `original_route` / `compliant_route` / `rerouted` を追加返却
  - `frontend/src/components/MapView.jsx` を更新
    - 元ルート（グレー・細線）と法規準拠ルート（青・太線）を両方表示
    - 凡例を地図右下に追加
  - `frontend/src/App.jsx` に「⚡ 法規に合わせてルートを変更しました」メッセージを追加
  - 渋谷→新宿: 元4617m → 法規準拠4922m（+305m迂回）、経路が異なることを確認
- **評価実験用 比較データ出力機能 実装済み**（2026-04-13）
  - `backend/routers/route.py` に `comparison` フィールド追加
    （original_distance_m / compliant_distance_m / distance_diff_m / distance_diff_pct / violation_count / violation_types / rerouted）
  - `backend/routers/experiment.py` を新規作成
    - POST /api/experiment/batch：複数ルートをまとめて比較データ返却（JSON）
    - POST /api/experiment/batch/csv：同データをCSVファイルとしてダウンロード
- **次フェーズ⑤**: 住所・地名入力対応（ジオコーディング）実装済み・動作確認済み（2026-04-13）
  - `backend/services/geocoder.py` を新規作成（Nominatim API、User-Agent設定済み）
  - `backend/routers/geocode.py` を新規作成（GET /api/geocode?q=渋谷駅）
  - `backend/main.py` に geocode / experiment ルーターを追加登録
  - `frontend/src/components/SearchForm.jsx` をタブ切り替え形式に更新
    - 「住所・地名で入力」タブ：入力確定時にバックエンドジオコードAPIを呼び出し、変換結果を表示
    - 「緯度経度で入力」タブ：従来の直接入力（後方互換）
  - 「渋谷駅」「新宿駅」入力でルート表示動作確認済み
- **リルート 500エラー修正**（2026-04-13）
  - `backend/services/rerouter.py`: `"ch.disable": True` を POST body に追加
    （GraphHopper は CH モードでは per-request custom_model 非対応のため無効化が必要）
    - 400エラー時のレスポンスボディをログ出力するよう対応
  - `backend/routers/route.py`: リルート失敗時に 500 を返さず元ルートで代替するフォールバック処理を追加
    - `logging` によるリルート失敗の警告ログも追加
  - **既知の問題**: ch.disable=True 時は CH が無効になり Dijkstra/A* に切り替わるため、
    リルート時のルート計算が通常より大幅に遅くなる（数秒〜十数秒）
- **ルート表示の速度改善**（2026-04-14）
  - **改善前**: 4チェック関数 × 10点 × 逐次Overpassリクエスト = 40回逐次API呼び出し（合計 約60秒）
  - **改善後**: Overpass 1回のみ → 3〜5秒で完了（動作確認済み: 3.4秒）
  - `backend/services/overpass.py` に `get_bulk_way_tags` を追加
    - Union構文（`way(around:...) way(around:...)`）で複数座標を1クエリで一括取得
    - `out center tags` でwayの中心座標を取得し、各サンプル点に最も近いwayのタグを対応付け
  - `backend/services/overpass.py` に `_post_with_retry` を追加
    - 3エンドポイント（overpass-api.de → kumi.systems → maps.mail.ru）を順次試行
    - 各エンドポイント10秒タイムアウト・リトライなし・即次へ切り替え
    - 最悪ケース: 3 × 10秒 = 30秒で空結果返却（チェックスキップ・ルートは表示）
  - `backend/services/law_checker.py` の全4関数をリファクタリング
    - 各関数が個別に Overpass を呼ぶ → `tags_list` 引数で共有できるよう変更
    - 共通ヘルパー `_sample()` を抽出
  - `backend/routers/route.py`: Overpass を1回だけ呼び出し、結果を4関数に渡す形に変更
    - `time.perf_counter` による所要時間ログ計測（INFO: Overpass一括取得完了: X.Xs (N点)）

### MVP 達成状況
「出発地・目的地を入力すると、逆走禁止（`oneway` タグ）を考慮した経路を地図上に表示する」→ **達成**

---

## 次フェーズ（MVP完成後に拡張）

- [x] 歩道通行可否チェック（`sidewalk` タグ）
- [x] 自転車レーン優先ルーティング（`cycleway` タグ）
- [x] 二段階右折要否の判定（`highway` + `lanes` タグ）
- [x] 住所・地名での入力（ジオコーディング）
- [ ] スマートフォン対応UI

---

## Claude Code への指示

STEP 1〜4 は**実装・動作確認済み**。次回からは次フェーズの拡張タスクに取り組むこと。
各タスクを実装する前に必ず現在のファイルを読み、既存の実装を把握してから変更すること。
エラーが発生した場合は、エラーメッセージを解析して自律的に修正すること。
