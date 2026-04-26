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
   version: "3"
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
    geocode.py
    experiment.py
  services/
    graphhopper.py   # GHへのリクエスト
    overpass.py      # OSMタグ取得（バルク化・リトライ済み）
    law_checker.py   # 法規判定ロジック
    rerouter.py      # 再ルーティングロジック
    geocoder.py      # Nominatim ジオコーディング
  requirements.txt
```

**`requirements.txt`:**

```
fastapi
uvicorn
httpx
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
    MapView.jsx        # Leaflet地図表示（元ルート/準拠ルート/違反マーカー）
    SearchForm.jsx     # 住所入力タブ・緯度経度入力タブ
    ViolationAlert.jsx # 法規違反・推奨情報の表示
  api/
    route.js           # バックエンドAPIクライアント
```

**起動確認:**

```bash
cd frontend
npm run dev
```

`http://localhost:5173` でアプリが表示されればOK。

---

### STEP 4: 動作確認シナリオ

以下の住所でテストすること:

- 出発地: `渋谷駅`
- 目的地: `新宿駅`

**期待する動作:**

1. 住所入力でジオコーディングされ、地図上にルートが表示される
2. 一方通行違反箇所に赤いマーカーと警告が表示される
3. 法規準拠ルート（青・太線）と元の最短ルート（グレー・細線）が両方表示される
4. リルートした場合「⚡ 法規に合わせてルートを変更しました」と表示される

---

## 研究スコープ：対応する法規チェック

### ✅ 対象とする法規（経路選択に関わるもの）

| チェック項目     | 使用OSMタグ                                  | 判定条件                                             |
| ---------------- | -------------------------------------------- | ---------------------------------------------------- |
| 一方通行違反     | `oneway=yes`                                 | 逆走ルートを排除（`oneway:bicycle=no` で自転車除外） |
| 二段階右折義務   | `highway=primary/secondary` または `lanes≥3` | 幹線道路・多車線で違反フラグ                         |
| 自転車レーン推奨 | `cycleway=lane/track`                        | 違反ではなく推奨情報として返却                       |

### ❌ 対象外とする法規（研究スコープ除外）

| チェック項目              | 除外理由                                                                                                                                                                                                                                                                                                                                                                                                                                                            |
| ------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 歩道走行（`sidewalk=no`） | 「走っているのが歩道か車道か」は利用者が現場で視認して判断できるため、ナビの経路選択で排除すべき問題ではない。また `sidewalk=no` は「歩道が存在しない道路」という物理的状態を示すタグであり、「自転車の歩道走行が法的に禁止されている」を意味しない（タグの意味と法規の方向が逆）。実装済みだが研究スコープから除外。コードは `law_checker.py` に `check_sidewalk_violation` として残すが、`route.py` および `experiment.py` から呼び出しをコメントアウトすること。 |

---

## 既知の制限・注意事項（論文の限界として記載予定）

- OSMの `oneway` タグが付いていない道路は検出できない
- Overpass APIへのリクエストはレートリミットあり（本番では間引きが必要）
- GraphHopperのセルフホスト初回起動はOSMデータのビルドに時間がかかる（関東は30分〜1時間程度）
- GraphHopperが起動しない場合は Public API（`https://graphhopper.com/api/1/route`）にAPIキーを取得して差し替えること
- リルート時は `ch.disable=True`（Dijkstra/A\*）のため法規準拠ルート計算が遅い（数秒〜十数秒）
- Overpass一括クエリの座標対応は「wayの中心座標との最近傍マッチング」のため、
  長い道路セグメントでは中心が遠い場合がある（精度上の限界）
- Overpass 公開サーバーが全滅した場合は法規チェックをスキップして元ルートを返す
  （論文の限界として記載予定）

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
`services/overpass.py` に `_post_with_retry` を実装済み。
3エンドポイント（overpass-api.de → kumi.systems → maps.mail.ru）を順次試行し、
全滅した場合は空結果を返してチェックをスキップ・ルートのみ表示する。

### Overpass バルク化による高速化

逐次呼び出し（約60秒）→ Union構文による一括クエリ（3.4秒）に改善済み。
`services/overpass.py` の `get_bulk_way_tags` を使用すること。
法規チェック関数は `tags_list` 引数でタグを受け取る形式に統一済み。

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

## 現在の進捗（2026-04-23時点）

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
- **法規チェック①**: 一方通行違反（`oneway=yes`）実装・動作確認済み（2026-04-12）
- **法規チェック②**: 自転車レーン推奨（`cycleway=lane/track`）実装済み（2026-04-12）
  - 推奨情報として `recommendations` フィールドで返却
- **法規チェック③**: 二段階右折要否判定実装済み（2026-04-12）
  - `highway=primary/secondary` または `lanes>=3` で違反フラグ
  - 渋谷→新宿: violations 5件（oneway×4, two_step_turn×1）
  - 東京駅→渋谷: violations 7件（oneway×6, two_step_turn×1）← 幹線道路で検出確認
- **歩道走行チェック**: `check_sidewalk_violation` を実装したが研究スコープ除外（2026-04-23）
  - `backend/services/law_checker.py` に関数は残存
  - `backend/routers/route.py` および `backend/routers/experiment.py` の呼び出しはコメントアウト済み
  - 除外理由：歩道走行は利用者が現場で視認判断できる問題であり、経路選択系の法規に該当しない
- **リルート**: 違反エッジ除外による再ルーティング実装済み（2026-04-12）
  - GraphHopper の `custom_model + areas` で違反座標付近（±0.001度≈100m）をブロック
  - `ch.disable=True` が必要（CHモードでは custom_model 非対応のため）
  - 渋谷→新宿: 元4617m → 法規準拠4922m（+305m迂回）を確認
- **評価実験用バッチ出力**: 複数ルートの比較データをCSV/JSONで出力する機能実装済み（2026-04-13）
  - `POST /api/experiment/batch`（JSON）
  - `POST /api/experiment/batch/csv`（CSVダウンロード）
- **ジオコーディング**: 住所・地名入力対応（Nominatim API）実装・動作確認済み（2026-04-13）
- **Overpass高速化**: バルク化・並列化により約60秒 → 3.4秒に改善済み（2026-04-14）

### 次に取り組むタスク

- [ ] 法規チェックの追加（一時停止・左側通行等）
- [ ] フロントエンド改善（違反箇所の可視化UI）
- [ ] バッチ実験エンドポイントのテストケース拡充
- [ ] 精度検証・デバッグ
- [ ] スマートフォン対応UI

---

## Claude Code への指示

STEP 1〜4 は**実装・動作確認済み**。次回からは次フェーズの拡張タスクに取り組むこと。
各タスクを実装する前に必ず現在のファイルを読み、既存の実装を把握してから変更すること。
エラーが発生した場合は、エラーメッセージを解析して自律的に修正すること。

**重要**: `check_sidewalk_violation` は `law_checker.py` に定義されているが、
研究スコープ除外のため `route.py` および `experiment.py` から呼び出してはならない。
誤って呼び出しを復活させないよう注意すること。
