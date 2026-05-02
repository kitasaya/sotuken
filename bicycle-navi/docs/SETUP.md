# 環境構築手順

このドキュメントは、本システムを再構築する際の手順をまとめたもの。
通常の開発時には参照不要。

---

## 前提環境

- Docker Desktop（Windows / Mac）
- Python 3.10 以上
- Node.js 18 以上
- 作業ディレクトリ：`C:\Users\masa2\Desktop\卒研\bicycle-navi`（Windows の場合）

---

## STEP 1: GraphHopper を Docker で起動する

### 1-1. OSM データの配置

`graphhopper/` ディレクトリに関東エリアの OSM データを配置：

```
https://download.geofabrik.de/asia/japan/kanto-latest.osm.pbf
```

ファイル名：`graphhopper/kanto-latest.osm.pbf`

### 1-2. `graphhopper/config.yml`

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

### 1-3. `docker-compose.yml`

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

### 1-4. 起動と動作確認

```bash
docker-compose up
```

初回はグラフビルドに **30分〜1時間** かかる。

```bash
curl "http://localhost:8989/route?point=35.6762,139.6503&point=35.6895,139.6917&profile=bike&locale=ja"
```

JSON レスポンスが返れば成功。

### 注意点

- `vehicle=bike` は旧仕様。**`profile=bike`** を使うこと
- ヒープメモリは `-Xmx4g` 以上が必要
- `--encoded-values` CLI フラグは非対応。`-c config.yml` 経由で設定する
- グラフビルド中は `/route` が 503 を返すので、完了を待つこと

---

## STEP 2: FastAPI バックエンドの起動

### 2-1. 仮想環境のセットアップ

```bash
cd backend
python -m venv .venv
.venv\Scripts\activate          # Windows
# source .venv/bin/activate     # Mac/Linux
pip install -r requirements.txt
```

### 2-2. 起動方法

**通常起動：**

```bash
cd backend
python -m uvicorn main:app --reload --port 8000
```

**Windows のバックグラウンド起動（ターミナルを閉じても継続）：**

```powershell
Start-Process -FilePath 'python' -ArgumentList '-m uvicorn main:app --reload --port 8000' -WorkingDirectory 'C:\Users\masa2\Desktop\卒研\bicycle-navi\backend' -WindowStyle Hidden
```

### 2-3. 動作確認

`http://localhost:8000/docs` で Swagger UI が開けば成功。

---

## STEP 3: React フロントエンドの起動

### 3-1. セットアップ

```bash
cd frontend
npm install
```

### 3-2. 起動

```bash
npm run dev
```

`http://localhost:5173` でアプリが表示されれば成功。

### 3-3. スマートフォンからのアクセス

```bash
npm run dev -- --host
```

スマホから `https://<PCのIP>:5173` でアクセス可能。
GPS 取得には HTTPS が必須のため、`@vitejs/plugin-basic-ssl` を有効にしている。
バックエンドへの API リクエストは `vite.config.js` の `/api` プロキシ設定で転送される。

---

## STEP 4: 動作確認シナリオ

以下の住所でテストする：

- 出発地：渋谷駅
- 目的地：新宿駅

**期待動作：**

1. 住所入力でジオコーディングされ、地図上にルートが表示される
2. 一方通行違反箇所に赤いマーカーと警告が表示される
3. 法規準拠ルート（青・太線）と元の最短ルート（グレー・細線）が両方表示される
4. リルートした場合「⚡ 法規に合わせてルートを変更しました」と表示される

---

## トラブルシューティング

### GraphHopper が起動しない

- Docker Desktop が起動しているか確認
- `docker-compose logs` でエラーを確認
- グラフビルド中は `/route` エンドポイントが 503 を返すので、完了まで待つ
- Docker なしで GraphHopper Public API を使う場合は `backend/services/graphhopper.py` の `GH_BASE` を変更し、API キーを設定する

### バックエンドで `ModuleNotFoundError`

- `.venv` が有効化されているか確認（プロンプト先頭に `(.venv)` が表示されるはず）
- `pip install -r backend/requirements.txt` を再実行

### フロントエンドが起動しない

- `frontend/` で `npm install` を実行したか確認

### Overpass API がタイムアウトする

- Overpass 公開サーバーは混雑時に遅延や 504 を返す
- バックエンドは複数エンドポイントを自動フォールバック実装済み
- タイムアウトした場合は法規チェックをスキップしてルートのみ表示する（正常な挙動）

### リルートが遅い（数秒〜十数秒）

- 法規準拠ルート計算時は GraphHopper の CH を無効化しているため、Dijkstra/A\* で計算される
- これは既知の制限（論文にも記載予定）

---

## Git 管理対象外（再構築時に各自取得が必要）

| パス | 説明 | 取得方法 |
|---|---|---|
| `backend/.venv/` | Python 仮想環境 | `python -m venv .venv` → `pip install` |
| `frontend/node_modules/` | npm パッケージ | `npm install` |
| `graphhopper/graph-cache/` | GH グラフキャッシュ（バイナリ・大容量） | `docker-compose up` で自動生成 |
| `graphhopper/*.osm.pbf` | OSM データ（数百 MB） | `docker-compose` 起動時に自動ダウンロード |
| `.env` 等 | API キーなどの機密情報 | 別途共有 |
