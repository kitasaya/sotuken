# 完了タスク履歴

実装完了済みのタスクを時系列で記録する。
新規タスクの実装前に既存実装の経緯を確認したいときに参照する。

---

## STEP 1〜4: MVP 構築（〜2026-04-14）

### STEP 1: GraphHopper のセルフホスト

- 2026-04-12 完了
- Docker（`israelhikingmap/graphhopper:latest`）で `http://localhost:8989` に起動
- `graphhopper/config.yml` でカスタム設定（bike プロファイル）
- OSM 関東データ（`kanto-latest.osm.pbf`）ビルド済み

### STEP 2: FastAPI バックエンド

- 2026-04-12 完了
- `http://localhost:8000` で稼働
- 4ルーター（`route` / `geocode` / `experiment` / Swagger UI）

### STEP 3: React フロントエンド

- 2026-04-12 完了
- Vite + Leaflet.js + axios
- `http://localhost:5173` で稼働

### STEP 4: 動作確認

- 2026-04-12 完了
- 渋谷→新宿で青いルート表示
- 一方通行違反箇所に赤マーカー表示（2件検出）
- 違反内容のポップアップ表示

### 法規チェック実装

- **一方通行違反（`oneway=yes`）**：2026-04-12 実装
- **自転車レーン推奨（`cycleway=lane/track`）**：2026-04-12 実装、`recommendations` フィールドで返却
- **二段階右折要否（`highway=primary/secondary` または `lanes>=3`）**：2026-04-12 実装
  - 渋谷→新宿: violations 5件（oneway×4, two_step_turn×1）
  - 東京駅→渋谷: violations 7件（oneway×6, two_step_turn×1）

### リルート実装

- 2026-04-12 完了
- GraphHopper の `custom_model + areas` で違反座標付近（±0.001度 ≈ 約100m）をブロック
- `ch.disable=True` 必須
- 渋谷→新宿: 元4617m → 法規準拠4922m（+305m迂回）を確認

### 評価実験用バッチ出力

- 2026-04-13 完了
- `POST /api/experiment/batch`（JSON）
- `POST /api/experiment/batch/csv`（CSV ダウンロード）

### ジオコーディング

- 2026-04-13 完了
- Nominatim API で住所・地名入力に対応

### Overpass 高速化

- 2026-04-14 完了
- 逐次呼び出し（約60秒）→ Union 構文によるバルク化（3.4秒）に改善

---

## 歩道走行チェックの研究スコープ除外（2026-04-23）

- `check_sidewalk_violation` は実装済みだが、研究スコープから除外
- `backend/services/law_checker.py` に関数定義は残存
- `backend/routers/route.py` および `backend/routers/experiment.py` の呼び出しはコメントアウト済み
- 除外理由：
  - 「走っているのが歩道か車道か」は利用者が現場で視認判断できるため、ナビの経路選択で排除すべき問題ではない
  - `sidewalk=no` は「歩道が存在しない道路」という物理的状態を示すタグであり、「自転車の歩道走行が法的に禁止されている」を意味しない（タグの意味と法規の方向が逆）

---

## STEP 5: UI モード分離の実装（2026-04-28〜2026-04-29）

### 背景

宮治先生から以下3点の指摘（2026-04-28）：
1. アクションの形が視覚的にするものだと扱いづらい（自転車に取り付けるのも危険）
2. バイク・自転車用ナビは走行中は矢印しか出さず、それ自体は法に抵触しない
3. 適用する方法もあるのだということは示せたほうが良い

### 法的根拠の確認

- スマホホルダーで自転車に固定すること自体は道路交通法上の違反ではない
- 違反になるのは「保持（手で持って運転）」と「注視（固定していても画面を継続的に見続ける）」の2点
- 道路交通法第71条第5号の5「画像表示用装置に表示された画像を注視しないこと」
- 「2秒以上の画面注視」が違反と判断される目安として広く認識されている
- ホルダーに固定したスマホで音声ナビを利用するのは合法

### 業界標準の調査結果

- ヤマハ「つながるバイクアプリ」、パイオニア「MOTTO GO」、Yahoo! MAP 自転車向け、Google Maps、Beeline Moto II
- いずれも「ターンバイターン方式の矢印表示＋音声案内」が業界標準
- 「音声中心、画面はチラ見で済む情報密度」という方針が確立されている

### フェーズ5-1：UI モードの基本実装（2026-04-28）

- `App.jsx` に `mode` state（`riding` / `preparing`）追加
- `ModeSwitcher.jsx` 新規作成（モード切り替えボタン）
- `RidingView.jsx` 新規作成（走行中モード画面）
  - 大きな矢印コンポーネント（直進・左折・右折・二段階右折）
  - 次の交差点までの距離表示
  - 地図表示の最小化（現在地周辺のみ）
  - デモ用「次の案内へ」ボタン
- 既存の `MapView.jsx` / `ViolationAlert.jsx` を `preparing` モード専用に集約
- バックエンドレスポンスから「次の交差点情報」を取得（GraphHopper の `instructions` 活用）

### フェーズ5-2：音声案内の実装（2026-04-29）

- `frontend/src/services/voiceGuide.js` 新規作成（Web Speech API ラッパー）
  - `VoiceGuide` シングルトン（`speak` / `cancel` / `setEnabled`）
  - `buildAnnouncementText(instruction, isTwoStep)` - instruction → 音声文言生成
  - `buildApproachText(distanceRemaining, nextInstruction, isTwoStep)` - 事前通知文言
  - `buildRerouteText(violations)` - リルート理由文言生成
- 二段階右折手順の音声ガイド文言（暫定：「一度左端に寄り、交差点を直進してから右折」）
- 一方通行回避時の理由説明（リルート時に自動発話）
- 音声 ON/OFF 切り替えボタン・再読み上げボタン
- `App.jsx` に `voiceEnabled` state 追加

### フェーズ5-3：モード自動切り替え（2026-04-29）

- `frontend/src/services/geoTracker.js` 新規作成（Geolocation watchPosition ラッパー）
- `frontend/src/hooks/useGeoAutoMode.js` 新規作成
  - 速度 5km/h 以上 → riding
  - 停止 5秒以上 → preparing
- 手動切り替え後 2分間は自動切り替え無効（暫定 N=2分）
- `speed == null` の場合（iOS 等）は自動切り替えをスキップ
- `App.jsx` に GPS 状態インジケーター（GPS自動 / GPS手動 / GPS待機 / GPS×）追加
- `RidingView.jsx` に GPS 位置マーカー・速度表示・事前通知（100m / 30m）追加

### フェーズ5-4：UI 細部調整・スマートフォン対応（2026-04-29）

- `frontend/vite.config.js` に `/api` プロキシ設定追加（スマホ LAN アクセス対応）
- `frontend/src/api/route.js` の API ベース URL を相対パス `/api` に変更（CORS 問題解消）
- `frontend/index.html`：`lang="ja"` 修正、`maximum-scale=1.0`、iOS メタタグ追加
- `RidingView.jsx`：警告バナーを全幅デザインに改善、二段階右折手順パネル追加
- タッチ操作対応：`touch-action: manipulation`、`user-select: none`、最小タッチターゲット 48px
- iOS Safari ホームバー対応：`safe-area-inset-bottom` を padding に適用
- `@vitejs/plugin-basic-ssl` を `vite.config.js` に追加（スマホ GPS 取得に HTTPS 必須）
- `README.md` にスマートフォン確認手順を追記

### 研究上の位置付け

- 評価実験の主軸：複数の出発地・目的地ペアでの既存ナビとの経路比較（ルート生成の妥当性検証）
- UI 設計の役割：研究成果が実走行に適用可能であることを示す材料
- 論文への反映：手法の章で「実走行に適用可能な UI 設計を行った」と記載し、走行中 UI のスクリーンショット・設計方針を提示

---

## タスク1: edge_id ベース判定への移行（2026-04-29）

### 背景

従来の点ベース判定（ルート座標を最大10点サンプリングして周辺 way を推定）は、
並行する別 way のタグを誤って拾う「並行 way 問題」があった。
ルートが実際に通った way の ID（`osm_way_id`）を直接使うことで精度を改善した。

### 変更内容

**`backend/services/graphhopper.py`**
- リクエストパラメータに `"details": ["osm_way_id"]` を追加
- GH レスポンスの `paths[0].details.osm_way_id` に `[[start_idx, end_idx, way_id], ...]` 形式で格納される

**`backend/services/overpass.py`**
- `get_way_tags_by_ids(way_ids: list[int]) -> dict[int, dict]` を新規追加
- `way(id:1234,5678,...); out tags;` クエリで way ID から直接タグを取得
- 戻り値は `{way_id: tags_dict}`

**`backend/routers/route.py`**
- `get_way_tags_by_ids` をインポート追加
- `osm_way_id` details が取得できた場合：
  - way_id → 区間中点座標の辞書を構築
  - `get_way_tags_by_ids` で全 way のタグを一括取得
  - 既存の法規チェック関数に `(check_points, tags_list)` として渡す
- `osm_way_id` が取得できない場合は従来の点ベース判定にフォールバック
- レスポンスの `comparison.using_edge_ids` で判定方式を確認できる

**`backend/services/law_checker.py`**
- 変更なし（既存の `(points, tags_list)` インターフェースのまま利用）

### 設計上の注意

- `config.yml` への `osm_way_id` 追記・再ビルドは不要（GH が edge 固有情報として保持）
- way_id の代表座標は区間の中点インデックスで選択（違反マーカーの表示位置）
- フォールバック時は `using_edge_ids: false` がレスポンスに含まれる

---

## CLAUDE.md 構造リファクタリング（2026-04-29）

- 単一の CLAUDE.md を以下に分割：
  - `CLAUDE.md`：Claude Code への現役指示のみ
  - `docs/ARCHITECTURE.md`：システム構成・既存実装の説明
  - `docs/SETUP.md`：環境構築手順
  - `docs/CHANGELOG.md`：完了タスク履歴（本ファイル）
- 法規チェックの偽陽性削減を「研究上の最重要課題」として明記
- タスク1〜5（edge_id ベース判定 / 進行方向照合 / instruction 連動 / confidence スコア / O-D 拡充）を優先度順に整理

---

## タスク1（旧）→ タスク2（新）: oneway 進行方向照合（2026-05-02）

### 背景

edge_id ベース判定に移行した後も、`oneway=yes` の way であれば進行方向を問わず違反と判定していた。
順方向で走行している場合にも「逆走の可能性」と誤検出する偽陽性が問題だった。

### 変更内容

**`backend/services/overpass.py`**
- `get_way_tags_by_ids` を `out tags geom;` に変更し、way の node 列（geometry）も取得するよう変更
- 戻り値を `{way_id: tags_dict}` から `{way_id: {"tags": {...}, "geometry": [[lon, lat], ...]}}` 形式に変更

**`backend/services/law_checker.py`**
- `check_oneway_violation` に省略可能パラメータ `geometries` / `travel_vectors` を追加
- 進行方向照合ロジック：way geometry の始点→終点ベクトルとルートの進行方向ベクトルの内積を計算し、正なら順方向（違反なし）、負なら逆走（違反）と判定
- `oneway=-1` の場合は判定を反転
- `oneway:bicycle=no`、`cycleway=opposite/opposite_lane/opposite_track` のいずれかがあれば違反としない

**`backend/routers/route.py`**
- `way_id_to_data` を事前初期化（edge_id 失敗時のフォールバック対応）
- edge_id 判定時に way_id 区間（start_idx〜end_idx）の端点差分ベクトルを `travel_vectors` として計算
- `way_id_to_data` から geometry リストを抽出し、両者を `check_oneway_violation` に渡す

### 完了条件の確認

- `oneway=yes` の道路を順方向に走行した場合：違反として検出されない ✅
- `oneway=yes` の道路を逆方向に走行した場合のみ：違反として検出される ✅
- `oneway:bicycle=no` / `cycleway=opposite*` の道路：自転車除外として違反なし ✅

---

## タスク3: confidence スコアの導入（2026-05-02）

### 背景

判定の確実性を定量化し、UI での表示分けと評価実験の分析に活用する。

### スコア定義

| 条件 | confidence |
|---|---|
| edge_id 一致 + 進行方向照合済み | 1.0 |
| edge_id 一致のみ（方向照合不可または二段階右折・レーン判定） | 0.7 |
| 近傍 way 推定（フォールバック） | 0.4 |

### 変更内容

**`backend/services/law_checker.py`**
- `check_oneway_violation`：`geometries`/`travel_vectors` が None なら 0.4、提供されていれば 0.7、かつ方向照合が実行された場合は 1.0
- `check_two_step_turn`：`tags_list` が None（Overpass 再取得）なら 0.4、提供済みなら 0.7
- `check_cycleway_recommendation`：同上のロジックで 0.4 / 0.7
- 各 violation / recommendation dict に `"confidence": float` フィールドを追加

**`backend/routers/experiment.py`**
- バッチ結果に `violation_count_high_conf`（≥0.7）と `violation_count_low_conf`（<0.7）列を追加
- CSV エクスポートのフィールドにも同列を追加

**`frontend/src/components/ViolationAlert.jsx`**
- `confidence >= 0.7`：赤（確実な違反）
- `confidence < 0.7`：橙 + 「（要確認）」ラベル（疑わしい違反）

**`frontend/src/components/MapView.jsx`**
- 違反マーカーを confidence で色分け（赤/橙）
- 凡例に「違反（確実）」「違反（要確認）」を追加

### 完了条件の確認

- レスポンスの violations / recommendations に confidence が含まれる ✅
- フロントエンドで confidence による表示分けが動作する ✅
- バッチ CSV に confidence 別カウントが出力される ✅

---

## タスク2（旧）→ タスク4（新）: 二段階右折判定を右折 instruction に限定（2026-05-02）

### 背景

従来の `check_two_step_turn` はルート上の全 way を検査し、`highway=primary/secondary` または `lanes>=3` の道路を通過するだけで「二段階右折違反」と判定していた。幹線道路を直進・左折するだけでも過検出が発生していた。

### 変更内容

**`backend/routers/route.py`**
- GraphHopper レスポンスの `instructions` 配列から `sign=2`（TURN_RIGHT）または `sign=3`（TURN_SHARP_RIGHT）の地点のみを抽出
- 各右折地点の `interval[0]`（進入先 way の先頭インデックス）を使い、`way_id_details` を線形探索して進入先 way_id を特定
- `way_id_to_data` から進入先 way のタグを取得して `check_two_step_turn` に渡す
- フォールバック時（edge_id 非対応）は右折地点の座標リストを渡し、`check_two_step_turn` 内で Overpass 再取得

### 完了条件の確認

- 幹線道路を直進・左折するだけのルートで二段階右折違反が検出されない ✅
- 幹線道路で右折するルートでのみ違反として検出される ✅

---

## タスク4: バッチ実験エンドポイントのテストケース拡充（2026-05-02）

### 変更内容

**`backend/data/od_pairs.csv`（新規作成）**
- 関東圏 15 O-D ペアを3カテゴリで選定：
  - 幹線道路中心（5件）：渋谷→新宿、東京→渋谷、新宿→池袋、品川→東京、渋谷→六本木
  - 住宅街中心（5件）：下北沢→三軒茶屋、高円寺→中野、荻窪→阿佐ヶ谷、自由が丘→等々力、浦和→さいたま新都心
  - 混在型（5件）：吉祥寺→三鷹、立川→国分寺、横浜→みなとみらい、川崎→武蔵小杉、千葉→幕張本郷
- 列: `label`, `road_type`, `origin_lat`, `origin_lng`, `dest_lat`, `dest_lng`

**`backend/routers/experiment.py`**
- `RoutePoint` に `road_type: str = ""` を追加
- `ALGO_VERSION` 定数を追加（`"v3-edge_id+direction+instruction+confidence"`）
  - 改善前後の CSV を比較する際に algo_version 列で差分を識別できる
- バッチ出力に `road_type` / `algo_version` 列を追加
- `_load_od_pairs()` ヘルパーで od_pairs.csv を読み込む
- `POST /api/experiment/batch/od-pairs` - プリセット O-D ペアを一括実行（JSON）
- `POST /api/experiment/batch/od-pairs/csv` - プリセット O-D ペアを一括実行（CSV ダウンロード）

### 新規エンドポイント

| メソッド | パス | 用途 |
|---|---|---|
| POST | `/api/experiment/batch/od-pairs` | od_pairs.csv の全 O-D を一括実行（JSON） |
| POST | `/api/experiment/batch/od-pairs/csv` | od_pairs.csv の全 O-D を一括実行（CSV 出力） |

---

## タスクA: experiment.py の v3 判定ロジック統一 + v1/v3 比較エンドポイント（2026-05-07）

### 背景

`experiment.py` の `batch_experiment` が点ベース判定（v1）のままで動いていた。
一方 `route.py` は edge_id ベース判定 + 進行方向照合 + 右折 instruction 連動（v3）に更新済み。
レスポンスに `algo_version="v3-edge_id+direction+instruction+confidence"` と表示されながら実態は v1 という乖離があり、論文の評価データとして提出すると主張と実装が一致しない問題があった。

### 変更内容

**`backend/services/route_analyzer.py`（新規作成）**
- `analyze_route(origin_lat, origin_lng, dest_lat, dest_lng, *, algo_version="v3") -> dict` を定義
- v3 ロジック（`_analyze_v3`）：`route.py` から移植。edge_id ベース判定 + 進行方向照合 + 右折 instruction 連動。フォールバック（点ベース）も含む
- v1 ロジック（`_analyze_v1`）：点ベース10点サンプリング + 全点に対して二段階右折判定（右折 instruction 限定なし）+ 全 violations の confidence を 0.4 に上書き
- `_build_response` 共通ヘルパーでリルートとレスポンス組み立てを共通化
- `comparison` dict に `algo_version` フィールドを追加

**`backend/routers/route.py`**
- 181行 → 22行に削減
- `calculate_route` は `analyze_route(..., algo_version="v3")` を呼ぶだけに変更
- ロジックの重複を完全に排除（移動、コピーではない）

**`backend/routers/experiment.py`**
- `BatchRequest` に `algo_version: str = "v3"` フィールドを追加
- `batch_experiment` は `analyze_route` を呼ぶ形に変更（v1/v3 切替対応）
- `ALGO_VERSION` 定数を削除し、`comparison.algo_version` から動的に取得
- `_results_to_csv_response(results, filename)` ヘルパーを追加（CSV 生成の共通化）
- 新エンドポイント `POST /api/experiment/batch/od-pairs/compare/csv` を追加

### 新規エンドポイント

| メソッド | パス | 用途 |
|---|---|---|
| POST | `/api/experiment/batch/od-pairs/compare/csv` | 同じ O-D ペアを v1 と v3 の両方で実行し 30行 CSV を返す（論文比較用） |

### v1/v3 の違い（experiment.py での挙動）

| 項目 | v1 | v3 |
|---|---|---|
| Overpass 取得方式 | 点ベース（`_sample` 10点 + `get_bulk_way_tags`） | edge_id ベース（`get_way_tags_by_ids`）、失敗時のみ点ベースにフォールバック |
| 進行方向照合 | なし | あり（way geometry とルート進行方向の内積） |
| 二段階右折判定 | 全サンプル点を対象（過検出） | 右折 instruction 地点のみ（`sign=2/3`） |
| confidence | 全件 0.4 に上書き | 0.4 / 0.7 / 1.0 の3段階 |
| `comparison.using_edge_ids` | 常に False | True / False（フォールバック時） |

### 完了条件の確認

- `POST /api/route` の挙動が変更前と同じ（リグレッションなし） ✅
- `POST /api/experiment/batch/od-pairs/csv` が v3 判定を反映 ✅
- `POST /api/experiment/batch/od-pairs/compare/csv` が 15×2=30行の CSV を返す ✅
- v1 行は `violation_count_high_conf=0`、v3 行は high/low に分かれる ✅
