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

### ⚠️ 固定方針（再現性のため意図的に固定しています）

以下の2つは**意図的にバージョン固定**しており、安易に更新してはいけません。

| 項目 | 固定値 | 理由 |
|---|---|---|
| Docker イメージ | `israelhikingmap/graphhopper:11.0` | `:latest` は GraphHopper の master ブランチから随時ビルドされるスナップショットであり、リリース版ではない。実際に 2026-06-18 の自動更新でグラフ格納形式が変わり（geometry version 8→9）、既存グラフが `Unexpected version for 'geometry'` エラーで読めなくなった |
| OSM データ | `graphhopper/kanto-260801.osm.pbf`（2026-08-01 版） | `kanto-latest.osm.pbf` は毎日内容が変わる。Geofabrik の日付付きファイルも約3か月で消えるため、ホスト側に保存した実ファイルが唯一の保証 |

取得日・チェックサムなどの来歴は [`DATA_PROVENANCE.md`](DATA_PROVENANCE.md) に記録している。

**更新する場合の手順：**

1. 新しい pbf を日付付きファイル名でダウンロードし、SHA-256 を算出する
2. [`DATA_PROVENANCE.md`](DATA_PROVENANCE.md) に取得日時・SHA-256・イメージタグを追記する
3. `docker-compose.yml` の `image` タグと `--input` のファイル名を更新する
4. `graphhopper/default-gh/` を削除してグラフを再構築する
5. **`RESEARCH.md` の基準時点（使用 OSM データのスナップショット日）を更新する**
6. 過去の実測値は新データと整合しなくなるため、再計測が必要かを必ず判断する

`:latest` に戻したり `--url` を復活させたりすると、この保証はすべて失われる。

### 1-1. OSM データの取得（初回のみ・手動）

**自動ダウンロードはしない。** 以下を一度だけ実行して `graphhopper/` に保存する。

```bash
curl -L -o bicycle-navi/graphhopper/kanto-260801.osm.pbf https://download.geofabrik.de/asia/japan/kanto-260801.osm.pbf
```

約 482 MB（481,636,841 バイト）。`.gitignore` で除外されているため git には入らない。

取得後、Geofabrik が公開している MD5 と照合する：

```bash
curl -s https://download.geofabrik.de/asia/japan/kanto-260801.osm.pbf.md5
```

```bash
md5sum bicycle-navi/graphhopper/kanto-260801.osm.pbf
```

> **補足**：Geofabrik は日付付き extract を「直近1週間の日次」と「直近約3か月の月初」しか保持していない。
> 2026-08-01 版も 11 月頃には取得できなくなるため、ホスト上のファイルを消さないこと。

### 1-2. `graphhopper/config.yml`

リポジトリに含まれている（git 管理対象）。内容は以下の通り。

```yaml
graphhopper:
  # 以下2行は graphhopper.sh が起動時に -D で常に上書きするため実効性なし（記録として残置）。
  #   datareader.file → docker-compose.yml の --input が -Ddw.graphhopper.datareader.file に入る
  #   graph.location  → スクリプト既定の /data/default-gh が -Ddw.graphhopper.graph.location に入る
  datareader.file: /data/data.pbf
  graph.location: /data/default-gh
  graph.encoded_values: car_access, car_average_speed, country, road_class, roundabout, max_speed, foot_access, foot_average_speed, foot_priority, foot_road_access, hike_rating, bike_access, bike_average_speed, bike_priority, bike_road_access, bike_network, mtb_rating, ferry_speed, road_environment, osm_way_id
  import.osm.ignored_highways: motor, trunk
  path_details: osm_way_id
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
version: '3'
services:
  graphhopper:
    image: israelhikingmap/graphhopper:11.0
    ports:
      - "8989:8989"
    volumes:
      - ./graphhopper:/data
    environment:
      - JAVA_OPTS=-Xmx4g -Xms1g
    command: >
      --input /data/kanto-260801.osm.pbf
      --host 0.0.0.0
      -c /data/config.yml
```

### 1-4. 起動と動作確認

```bash
docker-compose up
```

初回はグラフビルドが走る。本研究の設定では CH/LM の事前計算を行わないため、
実測で **約1分**（2026-08-18 の再構築時：52秒）で完了する。
完了すると `graphhopper/default-gh/` にキャッシュが生成され、2回目以降は約3秒で起動する。

> **注意**：ヘルスチェック（`/health`）はグラフビルド中でも 200 を返すため、
> 「healthy」になっただけではビルド完了の判定にならない。
> `docker-compose logs` に `Started Server` が出るのを確認すること。

```bash
curl "http://localhost:8989/route?point=35.6762,139.6503&point=35.6895,139.6917&profile=bike&locale=ja"
```

JSON レスポンスが返れば成功。

### 注意点

- `vehicle=bike` は旧仕様。**`profile=bike`** を使うこと
- ヒープメモリは `-Xmx4g` 以上が必要
- `--encoded-values` CLI フラグは非対応。`-c config.yml` 経由で設定する
- グラフビルド中は `/route` が 503 を返すので、完了を待つこと
- `config.yml` の `datareader.file` / `graph.location` は**効かない**。入力 pbf は `docker-compose.yml` の `--input`、グラフ出力先は常に `/data/default-gh` になる
- **イメージタグを変えたら `default-gh/` を必ず削除する。** バージョン不一致時 GraphHopper は自動再構築せず、`IllegalStateException` で起動に失敗する

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
| `graphhopper/default-gh/` | GH グラフキャッシュ（バイナリ・大容量） | `docker-compose up` で自動生成 |
| `graphhopper/kanto-260801.osm.pbf` | OSM データ（約 482 MB） | **STEP 1-1 の手順で手動取得**（自動ダウンロードはしない） |
| `.env` 等 | API キーなどの機密情報 | 別途共有 |
