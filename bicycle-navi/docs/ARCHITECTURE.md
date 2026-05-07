# システム構成・実装ドキュメント

本ドキュメントは、自転車ナビゲーションシステムの構成と既存実装の説明をまとめたもの。
新規タスクの実装前に該当箇所を参照すること。

---

## 全体構成

```
ユーザ入力（住所・地名）
        ↓  Nominatim API（ジオコーディング）
FastAPI バックエンド（http://localhost:8000）
   ├─ GraphHopper（Docker、http://localhost:8989） → ① 初期ルート生成
   ├─ Overpass API                                → ② OSMタグ一括取得
   └─ 法規チェックエンジン（asyncio.gather）       → ③ 違反箇所を検出
        ↓ 違反あり
    GraphHopper（custom_model + areas）            → ④ 再ルーティング
        ↓
React + Leaflet.js フロントエンド（http://localhost:5173） → ⑤ 地図上に表示
```

---

## ディレクトリ構成

```
bicycle-navi/
├── CLAUDE.md                    # Claude Code への指示書（現役タスク）
├── docs/
│   ├── ARCHITECTURE.md          # 本ドキュメント
│   ├── SETUP.md                 # 環境構築手順
│   └── CHANGELOG.md             # 完了タスク履歴
├── backend/
│   ├── main.py
│   ├── routers/
│   │   ├── route.py             # /api/route（ルート生成 + 法規チェック）
│   │   ├── geocode.py           # /api/geocode（Nominatim ラッパー）
│   │   └── experiment.py        # /api/experiment/batch（バッチ評価実験）
│   ├── services/
│   │   ├── graphhopper.py       # GraphHopper API クライアント
│   │   ├── overpass.py          # Overpass API クライアント（バルク化・リトライ済み）
│   │   ├── law_checker.py       # 法規判定ロジック（4関数）
│   │   ├── route_analyzer.py    # ルート解析コア（v1/v3 切替対応・route.py と experiment.py から共用）
│   │   ├── rerouter.py          # 違反エッジ除外による再ルーティング
│   │   └── geocoder.py          # Nominatim クライアント
│   └── requirements.txt
├── frontend/
│   └── src/
│       ├── App.jsx              # mode state（riding / preparing）管理
│       ├── components/
│       │   ├── MapView.jsx          # preparing モード専用：ルート全体表示
│       │   ├── SearchForm.jsx       # 住所/座標入力タブ
│       │   ├── ViolationAlert.jsx   # preparing モード専用：違反一覧
│       │   ├── RidingView.jsx       # riding モード専用：矢印 + 距離
│       │   └── ModeSwitcher.jsx     # モード切り替えボタン
│       ├── services/
│       │   ├── voiceGuide.js        # Web Speech API ラッパー
│       │   └── geoTracker.js        # Geolocation watchPosition ラッパー
│       ├── hooks/
│       │   └── useGeoAutoMode.js    # GPS 速度判定によるモード自動切り替え
│       └── api/
│           └── route.js             # バックエンド API クライアント（相対パス /api）
├── graphhopper/
│   ├── config.yml                   # bike プロファイル設定（osm_way_id を encoded_values に含む）
│   └── default-gh/                  # ビルド済みグラフキャッシュ（自動生成・GH v12 のデフォルト保存先）
└── docker-compose.yml
```

---

## バックエンド：法規チェックの現在の実装

### `services/law_checker.py`

4つの非同期関数を持つ。すべて `points: list` と任意の `tags_list` を受け取り、
`tags_list` が None なら Overpass を呼び出す。

| 関数 | 種別 | 判定条件 | OSM タグ |
|---|---|---|---|
| `check_oneway_violation` | 違反 | `oneway=yes/true/1/-1` の way を逆走している場合 | `oneway`, `oneway:bicycle`, `cycleway` |
| `check_sidewalk_violation` | 【**呼び出し禁止**】 | `sidewalk=no` の way 上の点 | `sidewalk` |
| `check_cycleway_recommendation` | 推奨 | `cycleway=lane/track` の way 上の点 | `cycleway` |
| `check_two_step_turn` | 違反 | 右折 instruction 地点で `highway=primary/secondary` または `lanes>=3` | `highway`, `lanes` |

**現在の挙動（2026-05-07）：**

判定ロジックは `services/route_analyzer.py` の `analyze_route` に集約されており、`route.py` と `experiment.py` の両方から呼ばれる。

- GraphHopper の `details.osm_way_id` からルートが通った way の ID を取得（edge_id ベース判定）
- `get_way_tags_by_ids` で way ID から直接タグ + geometry を取得（並行 way 問題を解消）
- `check_oneway_violation` は way geometry の始点→終点ベクトルとルート進行方向の内積で逆走を判定
  - `oneway:bicycle=no` / `cycleway=opposite/opposite_lane/opposite_track` の場合は違反としない
- `check_two_step_turn` は GraphHopper の `instructions` から `sign=2/3`（右折系）の地点のみを対象とする
- `osm_way_id` が取得できない場合のみ従来の点ベース判定（`_sample` + `get_bulk_way_tags`）にフォールバック
- `route.py` レスポンスの `comparison.using_edge_ids` で判定方式を確認できる
- `comparison.algo_version` に `"v1"` または `"v3"` が含まれる

**confidence スコア（2026-05-02）：**

| 条件 | confidence |
|---|---|
| edge_id 一致 + 進行方向照合済み | 1.0 |
| edge_id 一致のみ | 0.7 |
| 近傍 way 推定（フォールバック） | 0.4 |

各 violation / recommendation の辞書に `confidence` フィールドが含まれる。

### `services/overpass.py`

- 3エンドポイント順次フォールバック：`overpass-api.de` → `kumi.systems` → `maps.mail.ru`
- `_post_with_retry` でリトライ実装済み
- `get_way_tags_by_ids(way_ids)` で OSM way ID リストから直接タグ + geometry を取得（edge_id ベース判定用）
  - 戻り値: `{way_id: {"tags": {...}, "geometry": [[lon, lat], ...]}}`
- `get_bulk_way_tags(points)` で Union クエリによる一括取得（フォールバック用・3.4秒/リクエスト）
- 全滅した場合は空結果を返してチェックをスキップし、ルートのみ表示する仕様

### `services/rerouter.py`

- 違反座標の周囲（±0.001度 ≈ 約100m）を `custom_model + areas` でブロック
- GraphHopper の CH モードは custom_model 非対応のため `ch.disable=True` を必須とする
- リルート計算は Dijkstra/A\* となるため数秒〜十数秒かかる（既知の制限）

---

## バックエンド：API エンドポイント

| メソッド | パス | 用途 |
|---|---|---|
| POST | `/api/route` | ルート生成 + 法規チェック + 必要時リルート |
| POST | `/api/geocode` | 住所/地名 → 座標変換 |
| POST | `/api/experiment/batch` | バッチ比較実験（JSON 出力） |
| POST | `/api/experiment/batch/csv` | バッチ比較実験（CSV 出力） |
| POST | `/api/experiment/batch/od-pairs` | od_pairs.csv のプリセット O-D 一括実行（JSON、v3 固定） |
| POST | `/api/experiment/batch/od-pairs/csv` | od_pairs.csv のプリセット O-D 一括実行（CSV 出力、v3 固定） |
| POST | `/api/experiment/batch/od-pairs/compare/csv` | 同じ O-D ペアを v1 と v3 の両方で実行し 30行 CSV を返す（論文比較用） |

Swagger UI: `http://localhost:8000/docs`

---

## フロントエンド：UI モード設計

### 2モード設計の背景

宮治先生から「画面注視は危険」「業界標準は矢印中心の音声案内」との指摘を受け、
利用シーンに応じて2モードに分割した（2026-04-28）。

法的根拠：道路交通法第71条第5号の5「画像表示用装置に表示された画像を注視しないこと」。
スマホホルダー固定自体は違反ではないが、2秒以上の継続注視が違反と判断される目安。

### riding モード（走行中）

- 大きな矢印1つで次の進路を示す（直進・左折・右折・二段階右折）
- 次の交差点までの距離を大きな数字で表示
- 違反交差点では矢印に警告マークを重ねる
- 地図表示は最小限（現在地 + 数十メートル）
- Web Speech API による音声案内（事前通知 100m / 30m）

### preparing モード（出発前・停車中）

- ルート全体を地図上に表示
- 違反箇所をピン表示
- 二段階右折の手順をステップ形式で表示
- 違反一覧パネルで violations / recommendations を全件確認可能

### モード切り替え

- `useGeoAutoMode.js`：GPS 速度 5km/h 以上 → riding、停止 5秒以上 → preparing
- 手動切り替え後 2分間は自動切り替えを無効化
- `speed == null`（iOS 等）の場合は自動切り替えをスキップ

---

## 研究スコープ

### ✅ 対応する法規（経路選択に関わるもの）

| チェック項目 | 使用 OSM タグ | 判定条件 |
|---|---|---|
| 一方通行違反 | `oneway=yes/-1`, `oneway:bicycle`, `cycleway=opposite*` | 進行方向照合で逆走のみ検出（`oneway:bicycle=no` 等で自転車除外） |
| 二段階右折義務 | `highway=primary/secondary` または `lanes≥3` | 右折 instruction の地点のみ判定（直進・左折は除外） |
| 自転車レーン推奨 | `cycleway=lane/track` | 違反ではなく推奨情報として返却 |

### ❌ 対象外とする法規

| チェック項目 | 除外理由 |
|---|---|
| 歩道走行（`sidewalk=no`） | 利用者が現場で視認判断できる問題でナビの経路選択で排除すべき問題ではない。また `sidewalk=no` は「歩道が存在しない道路」という物理的状態であり「歩道走行が法的に禁止」を意味しない（タグの意味と法規の方向が逆）。 |

---

## 既知の制限（論文の限界として記載予定）

- OSM の `oneway` タグが付いていない道路は検出できない
- Overpass 公開サーバーのレートリミット・タイムアウトの影響を受ける
- GraphHopper セルフホストの初回起動は OSM データのビルドに 30分〜1時間かかる
- リルート時は `ch.disable=True` のため計算が遅い（数秒〜十数秒）
- Overpass 一括クエリは「way の中心座標との最近傍マッチング」のため、長い道路セグメントでは精度が落ちる（edge_id ベース判定で主経路は解消済み。フォールバック時のみ残存）
- Overpass 公開サーバーが全滅した場合は法規チェックをスキップして元ルートを返す

---

## 今後の課題（論文に記載予定、現フェーズでは実装しない）

- 自転車が実際に走る車道左端へのルートオフセット表示
- 交差点内の二段階右折動線の可視化
