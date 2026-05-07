# 自転車ナビゲーションシステム 開発指示書

日本の交通法規に準拠した自転車ナビゲーションシステム。
卒業研究（青山学院大学・宮治研究室）のシステム実装プロジェクト。

---

## プロジェクト現状（2026-05-07）

STEP 1〜5（環境構築・MVP実装・走行中UI）はすべて完了済み。
法規チェック精度向上フェーズのタスク1〜4もすべて完了。
タスクA（experiment.py の v3 判定ロジック統一 + v1/v3 比較エンドポイント追加）も完了。
タスクB（GraphHopper の osm_way_id 有効化・グラフ再ビルド）も完了。

**現在は評価実験の実施フェーズ。v3 判定で edge_id ベース判定が正常に動作する。**
完了タスクの履歴は `docs/CHANGELOG.md` を参照。
システム構成と既存実装の説明は `docs/ARCHITECTURE.md` を参照。
環境構築手順は `docs/SETUP.md` を参照（再構築時のみ）。

---

## 研究上の最重要課題：法規チェックの偽陽性削減

現状の `backend/services/law_checker.py` は、ルート上の点を最大10点サンプリングし、
各点の最寄り way の OSM タグだけで違反判定している。
このため、以下の偽陽性が発生する：

1. **並行way問題**：脇道や上下分離した一方通行ペアの反対側 way のタグを誤って拾う
2. **進行方向未照合問題**：`oneway=yes` の道路を順方向に走っていても「逆走」と判定される
3. **二段階右折の過検出問題**：幹線道路を直進・左折しているだけでも「二段階右折違反」と判定される
4. **サンプリング粒度問題**：10点固定では短い違反区間を見逃すか、無関係な区間を誤検出する

これらは「成果の難易度が低い」という宮治先生の指摘に対する直接的な改善材料でもある。
edge_id ベース判定 + 進行方向照合 + instruction連動判定 で解決する。

---

## 完了済みタスク（2026-05-07）

タスク1〜4およびタスクAはすべて実装完了。詳細は `docs/CHANGELOG.md` を参照。

- **タスク1（完了）**: oneway 判定に進行方向照合 + 自転車除外タグ対応
- **タスク2（完了）**: 二段階右折判定を右折 instruction に限定
- **タスク3（完了）**: 違反判定に confidence スコア（0.4/0.7/1.0）を導入
- **タスク4（完了）**: バッチ実験テストケース拡充（関東圏 15 O-D ペア・`od_pairs.csv`）
- **タスクA（完了）**: `experiment.py` を route.py と同じ v3 判定ロジックに統一 + v1/v3 比較エンドポイント追加
- **タスクB（完了）**: GraphHopper の `osm_way_id` encoded value を有効化・グラフ再ビルド（`comparison.using_edge_ids=true` を確認）

---

## 現役タスク（優先度順）

実装着手前に **必ず** 対象ファイルを `view` で読み、既存実装を把握すること。

### ~~タスク1: oneway 判定に進行方向照合を追加【完了】~~

`oneway=yes` の道路でも順方向に走っていれば違反ではない。
way の進行方向とルートの進行方向の内積を取って判定する。

**変更対象ファイル：**

- `backend/services/overpass.py`
- `backend/services/law_checker.py`

**手順：**

1. `overpass.py` で way 取得時に `out geom;` または `out body geom;` で node 列（geometry）を取得
2. `law_checker.py` の `check_oneway_violation` を以下のロジックに変更：
   - way の geometry の始点→終点ベクトルを計算
   - ルート上の該当区間の進行方向ベクトルを計算
   - 内積が負なら逆走候補
   - `oneway=-1` の場合は判定を反転
   - `oneway:bicycle=no`、`cycleway=opposite`、`cycleway=opposite_lane`、`cycleway=opposite_track` のいずれかがあれば違反としない
3. ベクトル計算は単純な2次元内積でよい（測地線補正は不要）

**完了条件：**

- `oneway=yes` の道路を順方向に走った場合、違反として検出されない
- 逆方向に走った場合のみ違反として検出される

---

### ~~タスク2: 二段階右折判定を右折 instruction に限定【完了】~~

二段階右折は「右折時のみ」の義務。直進・左折・通過時には判定しない。

**変更対象ファイル：**

- `backend/services/law_checker.py`
- `backend/routers/route.py`

**手順：**

1. GraphHopper の `instructions` 配列から `sign=2`（TURN_RIGHT）または `sign=3`（TURN_SHARP_RIGHT）の地点を抽出
2. `check_two_step_turn` を「右折 instruction の地点で、かつ進入する道路が `highway=primary/secondary` または `lanes>=3` の場合のみ違反フラグ」に変更
3. instruction の地点座標から、進入元 way ではなく **進入先 way** のタグを参照する点に注意

**完了条件：**

- 幹線道路を直進・左折するだけのルートで二段階右折違反が検出されない
- 幹線道路で右折するルートでのみ違反として検出される

---

### ~~タスク3: 違反判定に confidence スコアを導入【完了】~~

判定の確実性に応じてスコアを付け、UI と評価実験で活用する。

**変更対象ファイル：**

- `backend/services/law_checker.py`
- `backend/routers/route.py`
- `backend/routers/experiment.py`
- `frontend/src/components/ViolationAlert.jsx`
- `frontend/src/components/MapView.jsx`

**スコア定義：**
| 条件 | confidence |
|---|---|
| edge_id 一致 + 進行方向照合済み + 自転車除外タグ確認済み | 1.0 |
| edge_id 一致のみ | 0.7 |
| 近傍 way 推定（フォールバック） | 0.4 |

**手順：**

1. `law_checker.py` の各違反辞書に `confidence: float` フィールドを追加
2. UI 側で confidence ≥ 0.7 と < 0.7 を色分け（確実な違反は赤、疑わしい違反は橙など）
3. バッチ実験では confidence 別の集計列を CSV に追加
4. 論文ではこのスコア体系自体を「タグベース近似判定の限界を定量化する手法」として位置付ける

**完了条件：**

- レスポンスの violations / recommendations に confidence が含まれる
- フロントエンドで confidence による表示分けが動作する
- バッチ CSV に confidence 別カウントが出力される

---

### ~~タスク4: バッチ実験エンドポイントのテストケース拡充【完了】~~

タスク1〜4の効果を定量比較するために、O-D ペアの拡充を行う。

**変更対象ファイル：**

- `backend/routers/experiment.py`
- 新規 CSV：`backend/data/od_pairs.csv`（関東圏の複数 O-D ペア）

**手順：**

1. 関東圏で道路環境の異なる 10〜20 ペアの O-D を選定（幹線道路中心・住宅街中心・混在型）
2. `od_pairs.csv` として保存
3. バッチエンドポイントで CSV を読み込んで一括実行できるようにする
4. 出力 CSV に「タスク1〜4 適用前後の違反数差分」を残せる構造にする

---

## ~~タスクA: `experiment.py` を route.py と同じ v3 判定ロジックに揃える【完了】~~

### 背景

現状の `backend/routers/experiment.py` の `batch_experiment` は、点ベース判定（v1）のままで動いている。一方、`backend/routers/route.py` の `calculate_route` は edge_id ベース判定 + 進行方向照合 + 右折 instruction 連動（v3）に更新済み。

このため、バッチ実験エンドポイントが返す結果には `algo_version="v3-edge_id+direction+instruction+confidence"` というラベルが付いているにもかかわらず、実際には v1 のロジックで判定されている。論文の評価実験データとして提出すると主張と実装の乖離になるため、最優先で修正する必要がある。

加えて、論文では v1 と v3 の比較を示したいので、**両バージョンを CSV 上で切り替えて実行できる構造**にする。

### 変更方針

ロジックの重複を避けるため、`route.py` の判定ロジックを `services/route_analyzer.py` に新規抽出し、`route.py` と `experiment.py` の両方から呼ぶ構造にする。`route_analyzer.py` には判定方式を切り替えるフラグを持たせる。

### 変更対象ファイル

- `backend/services/route_analyzer.py`（新規作成）
- `backend/routers/route.py`（既存ロジックを route_analyzer に委譲）
- `backend/routers/experiment.py`（v1/v3 切替対応 + route_analyzer 利用）
- `backend/data/od_pairs.csv`（変更なし、再利用）

### 実装手順

#### 手順1: `services/route_analyzer.py` を新規作成

`route.py` の `calculate_route` 関数のうち、GraphHopper 呼び出し以降の「way_id 取得 → Overpass 取得 → 進行方向ベクトル計算 → 右折 instruction 抽出 → 法規チェック呼び出し → リルート」までのロジックを、以下のシグネチャの関数として抽出する：

```python
async def analyze_route(
    origin_lat: float,
    origin_lng: float,
    dest_lat: float,
    dest_lng: float,
    *,
    algo_version: str = "v3",  # "v1" | "v3"
) -> dict
```

戻り値の dict 構造は `route.py` の現在のレスポンスと同じ（`original_route`, `compliant_route`, `route`, `violations`, `compliant`, `recommendations`, `rerouted`, `comparison`）にする。

`algo_version` による分岐ロジック：

- **`algo_version == "v3"`**: 現在の `route.py` の挙動と完全に同じ。edge_id ベースで取得できれば edge_id 判定、取れなければ点ベースにフォールバック。`check_oneway_violation` には geometries / travel_vectors を渡す。`check_two_step_turn` には右折 instruction の地点と進入先 way のタグのみを渡す。
- **`algo_version == "v1"`**: v3 改善前の挙動を再現する。具体的には：
  - GraphHopper の details は使わず、ルート座標から `_sample(points)` で最大10点を抽出
  - `get_bulk_way_tags` で点ベースに Overpass 一括取得
  - `check_oneway_violation` には `tags_list` のみを渡す（geometries / travel_vectors は渡さない → 進行方向照合がスキップされ confidence=0.4 になる）
  - `check_two_step_turn` には**全サンプル点と tags_list** を渡す（右折 instruction 限定をしない＝過検出する旧挙動を再現）
  - `comparison` の `using_edge_ids` は False で固定

`comparison` dict には新たに `algo_version: str` フィールドを追加する。

#### 手順2: `routers/route.py` を route_analyzer 経由に書き換え

`calculate_route` の中身を `analyze_route(req.origin_lat, req.origin_lng, req.dest_lat, req.dest_lng, algo_version="v3")` の呼び出しと結果返却だけにする。フォールバックや右折抽出のロジックは route_analyzer に移る。

リクエスト側で algo_version を選べるようにする必要はない（`route.py` は常に v3 固定）。

#### 手順3: `routers/experiment.py` を route_analyzer 経由に書き換え

##### 3-1: `RoutePoint` モデル

変更なし。

##### 3-2: `BatchRequest` モデル

オプションフィールドを追加：

```python
class BatchRequest(BaseModel):
    routes: list[RoutePoint]
    algo_version: str = "v3"  # "v1" | "v3"
```

##### 3-3: `batch_experiment` 関数を書き換え

各ルートに対して `analyze_route(..., algo_version=req.algo_version)` を呼び、その戻り値の `comparison` と `violations` から既存の CSV 列を埋める。`ALGO_VERSION` 定数は削除し、リクエストの `algo_version` を CSV の `algo_version` 列に記録する。

エラーハンドリングは既存通り、例外時は `error` 列に文字列を記録。

##### 3-4: `_load_od_pairs` ヘルパー

変更なし。

##### 3-5: 新規エンドポイント `POST /api/experiment/batch/od-pairs/compare/csv` を追加

v1 と v3 を**続けて両方実行し、1つの CSV に並べて出力**する比較用エンドポイント。論文の表作成で直接使う。

```python
@router.post("/experiment/batch/od-pairs/compare/csv")
async def batch_od_pairs_compare_csv():
    """同じ O-D ペアを v1 と v3 の両方で実行し、1つの CSV にまとめて返す"""
    routes = _load_od_pairs()
    v1_results = (await batch_experiment(BatchRequest(routes=routes, algo_version="v1")))["results"]
    v3_results = (await batch_experiment(BatchRequest(routes=routes, algo_version="v3")))["results"]
    combined = v1_results + v3_results
    # 既存の CSV 出力ロジックを使い、combined を渡す
    ...
```

CSV のフィールド構成は既存と同じ（`algo_version` 列で v1 と v3 を区別）。実装としては `batch_experiment_csv` のロジックを `_results_to_csv_response(results)` ヘルパーに切り出して、新エンドポイントから再利用する形が綺麗。

##### 3-6: 既存エンドポイントのデフォルト動作

- `POST /api/experiment/batch` / `POST /api/experiment/batch/csv`: リクエストの `algo_version` を尊重（指定なしなら v3）
- `POST /api/experiment/batch/od-pairs` / `POST /api/experiment/batch/od-pairs/csv`: v3 で実行（既存の挙動を維持）

#### 手順4: `services/law_checker.py` の確認

変更不要なはず。現在のシグネチャで v1 / v3 両対応が可能：

- `check_oneway_violation(points, tags_list)` のみで呼ぶ → confidence=0.4 になる（v1 相当）
- `check_oneway_violation(points, tags_list, geometries=..., travel_vectors=...)` で呼ぶ → confidence=0.7 or 1.0（v3 相当）
- `check_two_step_turn(points, tags_list)` で全点を渡す → 過検出する v1 相当
- `check_two_step_turn(two_step_pts, two_step_tags)` で右折点のみ渡す → v3 相当

ただし、現状の `check_two_step_turn` は `tags_list is None` で confidence=0.4、提供済みなら 0.7 という分岐になっており、v1 で点ベース全件渡す場合も confidence=0.7 が付いてしまう。**v1 の挙動を正確に再現するため**、v1 ではあえて `tags_list=None` を渡して関数内で Overpass 再取得させるか、または `algo_version="v1"` の場合は判定後に violations の confidence を全件 0.4 で上書きするのが簡単。**後者を採用する**。

route_analyzer の v1 分岐内で、`check_oneway_violation` と `check_two_step_turn` の戻り値の各 violation の `confidence` を 0.4 に上書きする処理を入れる。

### 完了条件

1. `POST /api/route` の挙動が変更前と完全に同じ（リグレッションなし）。渋谷→新宿で従来と同じ違反数・距離が返る。
2. `POST /api/experiment/batch/od-pairs/csv` のレスポンスが、`route.py` 側と同じ v3 判定を反映している。具体的には、渋谷→新宿の `using_edge_ids=True` 相当の挙動になり、low confidence の違反数が変更前 CSV から減るはず。
3. `POST /api/experiment/batch/od-pairs/compare/csv` を叩くと、15 O-D × 2 algo_version = 30行の CSV が返る。各行は `algo_version` 列で区別できる。
4. v1 行の violations はすべて confidence < 0.7（low_conf 列に集計される）。v3 行は high_conf と low_conf に分かれる。
5. 既存の `POST /api/experiment/batch` JSON 出力も同様に動く。

### 実装着手前の確認事項

- 実装前に必ず `backend/routers/route.py` と `backend/services/law_checker.py` を `view` で読み、現在の v3 判定ロジックを完全に把握すること
- `route_analyzer.py` への抽出は、ロジックを**コピーするのではなく移動する**こと（route.py に同じコードが二重に残らないようにする）
- 動作確認シナリオ：渋谷→新宿、東京駅→渋谷で `POST /api/route` を叩いて変更前と違反数が一致することを確認してから、バッチエンドポイントを叩く

### 注意事項（不変の制約）

- `check_sidewalk_violation` は呼び出さない（研究スコープ除外）
- フロントエンドの変更は不要（バックエンド API のレスポンス構造は変わらない）
- `comparison.using_edge_ids` フィールドは v3 では従来通り True/False を返す。v1 では常に False。

---

## ~~タスクB: GraphHopper の osm_way_id 取得を有効化する【完了】~~

### 背景

`POST /api/experiment/batch/od-pairs/compare/csv` のサーバログを確認したところ、**v3 判定の全 O-D ペア（15件）で edge_id ベース判定に入れず、点ベース判定にフォールバックしている**ことが判明した。具体的には以下の現象：

```
GET http://localhost:8989/route?...&details=osm_way_id "HTTP/1.1 400 Bad Request"
WARNING:services.graphhopper:details=osm_way_id が拒否されました。details なしで再試行します
INFO:services.route_analyzer:点ベース判定（フォールバック）: ...
INFO:services.route_analyzer:法規チェック完了(v3): ... (edge_id=False)
```

これは GraphHopper が `osm_way_id` を path_details として返せない状態にあることを意味する。原因は `graphhopper/config.yml` の `graph.encoded_values` に `osm_way_id` が含まれていないこと。グラフビルド時に way_id を encoded value として保存していないため、ランタイムで `details=osm_way_id` を要求しても 400 になる。

このまま放置すると、v3 判定が機能しない状態で論文の評価実験データを作ることになり、研究の主張（edge_id ベース判定による並行 way 問題の解消）が実装で支えられなくなる。**最優先で修正する必要がある。**

### 変更対象ファイル

- `graphhopper/config.yml`
- `graphhopper/graph-cache/`（既存キャッシュを削除して再ビルドさせる）
- `backend/services/route_analyzer.py`（ログ出力の確認のみ・基本変更不要）

### 実装手順

#### 手順1: `graphhopper/config.yml` を修正

`graph.encoded_values` の末尾に `osm_way_id` を追加する。例：

```yaml
graphhopper:
  datareader.file: /data/data.pbf
  graph.location: /data/graph-cache
  graph.encoded_values: car_access, car_average_speed, country, road_class, roundabout, max_speed, foot_access, foot_average_speed, foot_priority, foot_road_access, hike_rating, bike_access, bike_average_speed, bike_priority, bike_road_access, bike_network, mtb_rating, ferry_speed, road_environment, osm_way_id
  import.osm.ignored_highways: motor, trunk
  path_details: osm_way_id
  ...
```

`path_details: osm_way_id` の行は既に存在するのでそのまま残す（実害はない）。

#### 手順2: 既存グラフキャッシュを削除

`osm_way_id` を encoded value に追加した場合、**既存の graph-cache を削除しないと再ビルドされない**。GraphHopper は graph-cache が存在するとそれを再利用する仕様なので、削除が必須。

```powershell
# Windows PowerShell
docker-compose down
Remove-Item -Recurse -Force C:\Users\masa2\Desktop\卒研\bicycle-navi\graphhopper\graph-cache
```

または bash 環境：

```bash
docker-compose down
rm -rf graphhopper/graph-cache
```

#### 手順3: GraphHopper を再起動してグラフを再ビルド

```bash
docker-compose up -d
```

**初回ビルドは 30分〜1時間かかる**。`docker-compose logs -f graphhopper` でログを確認し、以下のメッセージが出るまで待つ：

```
Started server at HTTP 0.0.0.0/0.0.0.0:8989
```

ビルド中は `/route` エンドポイントが 503 を返す。

#### 手順4: edge_id 取得が動くことを確認

GraphHopper 起動完了後、curl で確認：

```bash
curl "http://localhost:8989/route?point=35.6580,139.7016&point=35.6895,139.7006&profile=bike&locale=ja&points_encoded=false&details=osm_way_id"
```

期待結果：レスポンス JSON の `paths[0].details.osm_way_id` に `[[start_idx, end_idx, way_id], ...]` 形式の配列が含まれている。

400 Bad Request が返る場合は config の修正が反映されていない、または graph-cache の削除が不完全。

#### 手順5: `POST /api/route` で edge_id 判定が動くことを確認

バックエンドが起動していることを確認した上で：

```bash
curl -X POST http://localhost:8000/api/route \
  -H "Content-Type: application/json" \
  -d '{"origin_lat":35.658,"origin_lng":139.7016,"dest_lat":35.6895,"dest_lng":139.7006}'
```

期待結果：レスポンス JSON の `comparison.using_edge_ids` が **true** であること。

サーバログで以下が出ることを確認：

```
INFO:services.route_analyzer:edge_idベース判定: N ways, X.X秒
INFO:services.route_analyzer:法規チェック完了(v3): oneway=N two_step=N (edge_id=True)
```

`(edge_id=True)` が出ていれば成功。`(edge_id=False)` のままなら失敗なので config / graph-cache を再確認。

#### 手順6: 比較実験の再実行

edge_id 判定が動くことを確認したら、再度比較 CSV を取得する：

```bash
curl -X POST http://localhost:8000/api/experiment/batch/od-pairs/compare/csv \
  -o experiment_v1_vs_v3_with_edge_id.csv
```

サーバログで v3 のペアすべてに `(edge_id=True)` が出ていることを確認する。**1件でも `(edge_id=False)` がある場合は、その O-D ペアの座標が GraphHopper の対応範囲外（kanto-latest.osm.pbf のカバー外）か、その他の障害がある**。ログにエラーが出ていないか確認すること。

### 完了条件

1. `graphhopper/config.yml` の `graph.encoded_values` に `osm_way_id` が含まれている。
2. `graphhopper/graph-cache/` が再生成され、GraphHopper が起動完了している。
3. `curl http://localhost:8989/route?...&details=osm_way_id` が 200 を返し、レスポンスに `paths[0].details.osm_way_id` が含まれている。
4. `POST /api/route` のレスポンスで `comparison.using_edge_ids` が **true** になっている。
5. `POST /api/experiment/batch/od-pairs/compare/csv` を実行したサーバログで、v3 ペアの大半に `法規チェック完了(v3): ... (edge_id=True)` が出ている。
6. 取得した新 CSV を `experiment_v1_vs_v3_with_edge_id.csv` として保存する。

### 注意事項

- `osm_way_id` は GraphHopper の標準 encoded value として最近のバージョンでサポートされている。`israelhikingmap/graphhopper:latest` のバージョンで対応していなければ、エラーメッセージが出るので、その場合は報告すること（その場合は別の取得方法を検討する必要がある）。
- グラフ再ビルドは時間がかかるので、ビルド中に他の作業を進める際は注意。バックエンドや フロントエンドは GraphHopper が起動完了するまで動作しない。
- 既存の `POST /api/experiment/batch/od-pairs/compare/csv` などのエンドポイントは変更不要。問題は GraphHopper 側の設定のみ。
- `route_analyzer.py` の挙動は正しい。edge_id details が取得できれば自動的に edge_id 判定に入り、できなければ点ベースにフォールバックする設計になっている。今回の修正でフォールバック側ではなく本来の edge_id 側が動くようになる。

### 副次的に確認すべき点（解決後）

サーバログを見ると、v1 実行時に Overpass の `kumi.systems` がタイムアウトする頻度が高い（特に 1km 未満の短いルートで発生）。これは Overpass 側の負荷状況による一時的な問題なので、edge_id 判定が動くようになれば Overpass への負荷自体が減り（way_id 直指定のクエリは点ベース広範囲検索より軽量）、改善する可能性がある。

---

## 不変の制約（変更禁止）

- `check_sidewalk_violation` は **呼び出し禁止**（研究スコープ除外）
  - 関数定義は `law_checker.py` に残してよいが `route.py` / `experiment.py` から呼んではならない
  - 除外理由：歩道走行は利用者が現場で視認判断できる問題であり、経路選択系の法規に該当しない
- `MapView.jsx` / `ViolationAlert.jsx` は **preparing モード専用**
- `RidingView.jsx` は **riding モード専用**
- モード切り替えはフロントエンド側のみ。バックエンド API への影響はない
- GraphHopper の `vehicle=bike` パラメータは旧仕様。**`profile=bike`** を使用すること
- リルート計算時は `ch.disable=True` が必須（CH モードでは custom_model 非対応）

---

## 開発時の共通ルール

- 実装前に必ず対象ファイルを `view` で読み、既存実装を把握すること
- エラーが発生した場合は、エラーメッセージを解析して自律的に修正すること
- 大きな変更を加える前に、変更方針を要約してユーザーに確認すること
- 既存の動作確認シナリオ（渋谷→新宿、東京駅→渋谷）でリグレッションが起きていないこと
- バックエンドの起動方法は `docs/SETUP.md` を参照

---

## 参照ドキュメント

- `docs/ARCHITECTURE.md`: システム構成・既存実装の説明・OSM タグの判定ルール
- `docs/SETUP.md`: 環境構築手順（Docker・FastAPI・Vite）
- `docs/CHANGELOG.md`: 完了タスクの履歴
