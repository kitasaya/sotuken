# 自転車ナビゲーションシステム 開発指示書

日本の交通法規に準拠した自転車ナビゲーションシステム。
卒業研究（青山学院大学・宮治研究室）のシステム実装プロジェクト。

---

## プロジェクト現状（2026-05-07）

STEP 1〜5（環境構築・MVP実装・走行中UI）はすべて完了済み。
法規チェック精度向上フェーズのタスク1〜4もすべて完了。
タスクA（experiment.py の v3 判定ロジック統一 + v1/v3 比較エンドポイント追加）も完了。

**現在は評価実験の実施フェーズ。**
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
