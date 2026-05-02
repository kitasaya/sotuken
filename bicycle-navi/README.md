# 自転車ナビゲーションシステム

日本の交通法規（一方通行・二段階右折）に準拠した自転車向けナビゲーションシステム。
青山学院大学・宮治研究室 卒業研究（2026年度）。

---

## 概要

- 出発地・目的地を入力すると、法規違反を回避した推奨ルートを地図上に表示する
- GraphHopper（セルフホスト）でルート生成、Overpass API で OSM タグを取得して法規チェックを行う
- 違反箇所があれば自動リルートし、元ルートとの距離差を表示する
- 走行中は矢印＋音声案内の最小限 UI に切り替わる（道路交通法の画面注視規制に配慮）

---

## 起動手順

詳細は [`docs/SETUP.md`](docs/SETUP.md) を参照。

### 1. GraphHopper（Docker）

```bash
docker-compose up -d
```

初回はグラフビルドに 30 分〜1 時間かかる。
`http://localhost:8989` が応答すれば起動完了。

### 2. バックエンド（FastAPI）

```bash
cd backend
python -m venv .venv
.venv\Scripts\activate        # Windows
pip install -r requirements.txt
python -m uvicorn main:app --reload --port 8000
```

`http://localhost:8000/docs` で Swagger UI が開けば起動完了。

### 3. フロントエンド（React + Vite）

```bash
cd frontend
npm install
npm run dev
```

`http://localhost:5173` でアプリが表示されれば起動完了。

---

## フロントエンドの使い方

1. 出発地・目的地を住所または座標で入力して「ルート検索」
2. **preparing モード**（出発前）：
   - 地図上に最短ルート（橙）と法規準拠ルート（青）が表示される
   - 違反箇所に赤（確実）/ 橙（要確認）のマーカーが表示される
   - 下部パネルで違反一覧・推奨情報を確認できる
3. 「走行開始」ボタンまたは GPS 速度 5km/h 以上で **riding モード** に自動切り替え：
   - 次の曲がり角の矢印と距離のみを大きく表示
   - 音声案内が自動で流れる（ON/OFF 切り替え可能）

---

## 評価実験（CSV 出力）の使い方

### プリセット O-D ペアで一括実行

関東圏 15 ルートがあらかじめ登録されている（[`backend/data/od_pairs.csv`](backend/data/od_pairs.csv)）。

**Swagger UI から実行する場合：**

1. `http://localhost:8000/docs` を開く
2. `POST /api/experiment/batch/od-pairs/csv` を選択
3. 「Execute」をクリック → CSV ファイルがダウンロードされる

**curl から実行する場合：**

```bash
curl -X POST http://localhost:8000/api/experiment/batch/od-pairs/csv \
  -o experiment_results.csv
```

### 任意の O-D ペアで実行

```bash
curl -X POST http://localhost:8000/api/experiment/batch/csv \
  -H "Content-Type: application/json" \
  -d '{
    "routes": [
      {
        "label": "渋谷→新宿",
        "road_type": "幹線道路中心",
        "origin_lat": 35.6580, "origin_lng": 139.7016,
        "dest_lat": 35.6895, "dest_lng": 139.7006
      }
    ]
  }' \
  -o result.csv
```

### 出力 CSV の列

| 列名                        | 説明                                           |
| --------------------------- | ---------------------------------------------- |
| `label`                     | O-D ペアの名称                                 |
| `road_type`                 | 道路種別（幹線道路中心 / 住宅街中心 / 混在型） |
| `algo_version`              | アルゴリズムバージョン（改善前後の比較に使用） |
| `original_distance_m`       | 最短ルートの距離（m）                          |
| `compliant_distance_m`      | 法規準拠ルートの距離（m）                      |
| `distance_diff_m`           | 迂回距離（m）＝ 法規準拠 − 最短                |
| `distance_diff_pct`         | 迂回率（%）                                    |
| `violation_count`           | 違反検出数（合計）                             |
| `violation_count_high_conf` | confidence ≥ 0.7 の違反数（確実）              |
| `violation_count_low_conf`  | confidence < 0.7 の違反数（要確認）            |
| `violation_types`           | 違反種別（oneway / two_step_turn）             |
| `rerouted`                  | リルートが行われたか（True / False）           |
| `error`                     | エラーメッセージ（正常時は空）                 |

### O-D ペアの追加・変更

[`backend/data/od_pairs.csv`](backend/data/od_pairs.csv) を直接編集する。

```
label,road_type,origin_lat,origin_lng,dest_lat,dest_lng
渋谷→新宿,幹線道路中心,35.6580,139.7016,35.6895,139.7006
```

---

## 法規チェックの仕組み

### 判定方式

1. GraphHopper の `details.osm_way_id` でルートが通った道路の OSM ID を取得（edge_id ベース）
2. Overpass API で各 way のタグ + 形状（geometry）を一括取得
3. 各チェック関数で違反を判定し、`confidence` スコアを付与

### チェック項目

| 種別             | 判定条件                                                               |
| ---------------- | ---------------------------------------------------------------------- |
| 一方通行違反     | `oneway=yes/-1` の道路を逆方向に走行（進行方向照合済み）               |
| 二段階右折義務   | 右折 instruction の地点で `highway=primary/secondary` または `lanes≥3` |
| 自転車レーン推奨 | `cycleway=lane/track`（違反ではなく情報として表示）                    |

### confidence スコア

| スコア  | 条件                                       |
| ------- | ------------------------------------------ |
| **1.0** | edge_id 一致 + 進行方向照合済み            |
| **0.7** | edge_id 一致のみ（二段階右折・レーン判定） |
| **0.4** | 近傍 way 推定（フォールバック時）          |

UI では 0.7 以上を赤（確実な違反）、0.4 を橙（要確認）で表示する。

---

## API エンドポイント

| メソッド | パス                                 | 説明                                       |
| -------- | ------------------------------------ | ------------------------------------------ |
| POST     | `/api/route`                         | ルート生成 + 法規チェック + 必要時リルート |
| POST     | `/api/geocode`                       | 住所/地名 → 座標変換（Nominatim）          |
| POST     | `/api/experiment/batch`              | バッチ比較実験（JSON 出力）                |
| POST     | `/api/experiment/batch/csv`          | バッチ比較実験（CSV 出力）                 |
| POST     | `/api/experiment/batch/od-pairs`     | プリセット 15 O-D 一括実行（JSON）         |
| POST     | `/api/experiment/batch/od-pairs/csv` | プリセット 15 O-D 一括実行（CSV 出力）     |

Swagger UI: `http://localhost:8000/docs`

---

## ドキュメント

| ファイル                                       | 内容                             |
| ---------------------------------------------- | -------------------------------- |
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | システム構成・実装詳細           |
| [`docs/SETUP.md`](docs/SETUP.md)               | 環境構築手順（再構築時のみ参照） |
| [`docs/CHANGELOG.md`](docs/CHANGELOG.md)       | 実装履歴                         |
| [`CLAUDE.md`](CLAUDE.md)                       | Claude Code への開発指示         |
