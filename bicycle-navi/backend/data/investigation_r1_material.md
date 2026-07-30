# R1（検出精度評価）の材料が取得可能かの現状調査

調査日：2026-07-30
調査者：Claude Code（コード変更なし・既存データ書き換えなし）
対象コミット：`242004b`（ブランチ `claude/r1-material-investigation-7ef966`、作業ツリーは clean）

**記述ルール：** 「事実」＝コードを読んで確認した内容、または実際にAPIを叩いて得た観測値。
「推論」＝そこからの解釈。各節で明示的に分けて記述する。

---

## 0. 結論サマリ

| 項目 | 結論 |
|---|---|
| 法規判定の実行対象 | **初期ルート（リルート前）**。事実 |
| リルート後の再判定 | **行っていない**。事実 |
| `violations` の上書き | **上書きされていない**。リルート後に `triggered_reroute` キーが追加されるのみ。事実 |
| 初期ルートのジオメトリ | **レスポンスに残っている**（`original_route.points.coordinates`）。破棄されていない。事実 |
| 初期ルートの `osm_way_id` details | **残っている**（`original_route.details.osm_way_id`）。事実 |
| violation の位置特定情報 | `lat` / `lng` / `way_id`（edge_id モード時）あり。ジオメトリ配列上の index は**なし**。事実 |
| `google_comparison.csv` の `system_violation_count` | **リルート前（初期ルート）の違反数**。事実 |
| MapView の2本表示コード | **現存する。削除されていない**。事実 |
| **R1材料の判定** | **A：現状のコードで取得可能**（コード変更は不要） |

**重要な訂正事項（依頼文の前提との差分）：**

依頼文では「`google_comparison.csv` の `system_violation_count` はリルート後のルートに
対する値」とされていたが、**コードを追った限りこれは成立しない**。`analyze_route` は
初期ルートに対してのみ法規判定を行い、リルート後に再判定していない。したがって
`comparison.violation_count`（＝CSVの `system_violation_count` の算出元）は
**初期ルートに対する判定結果**である。詳細は §1・§3。

一方で「15ペア中 oneway は横浜→みなとみらいの1件のみ」という観測自体は事実であり、
これは**リルートによって消えたのではなく、初期ルートの段階でそもそも oneway 違反が
1件しか検出されていない**ことによる（§3・§5の根拠を参照）。

---

## 1. `route_analyzer.py` のデータフロー

### 1.1 `analyze_route` の処理順序（事実）

対象：[route_analyzer.py:36-61](../services/route_analyzer.py#L36)

```
analyze_route(origin, dest, algo_version="v3")
 │
 ├─① get_route(...)  … GraphHopper へ GET /route
 │     params: profile=bike, points_encoded=false, details=[osm_way_id, road_class]
 │     → route_data（初期ルート。以降この1本だけが判定対象）
 │
 ├─② points = route_data["paths"][0]["points"]["coordinates"]
 │
 ├─③ algo_version により分岐
 │     v3 → _analyze_v3(route_data, points, ...)
 │     v1 → _analyze_v1(route_data, points, ...)
 │
 └─④ 結果をそのまま返す
```

`_analyze_v3`（[route_analyzer.py:68-222](../services/route_analyzer.py#L68)）の内部：

```
 ├─ way_id_details = route_data["paths"][0]["details"]["osm_way_id"]   ← 初期ルート
 ├─ road_class_details も同様に初期ルートから取得
 ├─ instructions から右折（sign 2/3）地点を抽出 → two_step_pts       ← 初期ルート
 ├─ using_edge_ids=True の場合:
 │    unique_way_ids（初期ルートが通る way の一覧）を Overpass に by-ID 問い合わせ
 │    check_points  = 各 way の中間点（初期ルートの points から）
 │    geometries    = Overpass の way ジオメトリを初期ルート通過区間にトリム
 │    travel_vectors= 初期ルートの区間始点→終点ベクトル
 │    （Overpass 全滅時は例外→ using_edge_ids=False にフォールバック）
 ├─ using_edge_ids=False の場合:
 │    _sample(points)（初期ルートを最大10点にサンプリング）＋ get_bulk_way_data
 │
 ├─ asyncio.gather(
 │      check_oneway_violation(check_points, tags_list, geometries, travel_vectors),
 │      check_two_step_turn(two_step_pts, two_step_tags_arg),
 │      check_cycleway_recommendation(check_points, tags_list))
 │    ← 入力は **すべて初期ルート由来**
 │
 ├─ using_edge_ids なら violations に way_id を付与（座標キーで逆引き）
 ├─ violations = oneway_violations + two_step_violations
 └─ _build_response(route_data, violations, recommendations, ...) を呼ぶ
```

### 1.2 質問への直接回答（事実）

**Q. 法規判定は初期ルートに対して実行されているか**
→ **はい。** `check_oneway_violation` / `check_two_step_turn` /
`check_cycleway_recommendation` に渡される `check_points` / `tags_list` /
`geometries` / `travel_vectors` / `two_step_pts` は、すべて `get_route` が返した
初期ルート `route_data` から構築されている（[route_analyzer.py:195-199](../services/route_analyzer.py#L195)）。
リルートは §1.3 の通り `_build_response` の中で判定より**後**に実行される。

**Q. リルート後に再判定しているか**
→ **いいえ。** `_build_response`（[route_analyzer.py:262-313](../services/route_analyzer.py#L262)）
の中に `check_*` の呼び出しは一切ない。`get_compliant_route` の戻り値
（`compliant_route`）に対して法規判定を走らせるコードは存在しない。

**Q. `violations` 配列は初期／リルート後／上書き のどれか**
→ **初期ルートに対する判定結果であり、上書きされない。**
`_build_response` が `violations` に対して行う唯一の変更は、
各要素へ `triggered_reroute` キーを**追加**する処理だけ
（[route_analyzer.py:284-286](../services/route_analyzer.py#L284)）。
要素の削除・置換・再計算は行われない。

```python
# 各 violation に「リルートの原因になったか」フラグを付与（F2 フロント差別化用）
for v in violations:
    v["triggered_reroute"] = (v["rule"] == "oneway" and rerouted)
```

**（推論）** このため `violations` は「初期ルート上で検出された違反の全件」であり、
R1（検出器の Precision/Recall）が測るべき対象そのものである。
`triggered_reroute=True` の要素は「検出され、かつ回避された違反」を意味する。

### 1.3 リルートの位置づけ（事実）

`_build_response` の前半（[route_analyzer.py:267-282](../services/route_analyzer.py#L267)）：

1. `original_route = route_data["paths"][0]`（初期ルートをそのまま保持）
2. `reroute_violations = [v for v in violations if v["rule"] == "oneway"]`
   （二段階右折はリルート対象外＝第2層。CLAUDE.md の二層分界どおり）
3. `reroute_violations` が空でなければ `get_compliant_route(...)` を呼ぶ
   → 成功時 `compliant_route = compliant_data["paths"][0]`、`rerouted = True`
   → 例外時 `compliant_route = original_route`、`rerouted = False`（警告ログのみ）
4. `reroute_violations` が空なら `compliant_route = original_route`
   （**同一オブジェクトへの参照**。コピーではない）

`get_compliant_route`（[rerouter.py:22-98](../services/rerouter.py#L22)）は違反座標を中心と
した約100m四方のポリゴンを `custom_model.areas` でブロックし、`ch.disable=true` で
POST /route する。**この POST には `details` パラメータが含まれていない**
（[rerouter.py:80-87](../services/rerouter.py#L80)）。

### 1.4 `_build_response` が返すフィールドの由来対応表（事実）

| フィールド | 型 | 由来 | 備考 |
|---|---|---|---|
| `original_route` | dict | **初期ルート** | `route_data["paths"][0]` そのもの。`distance` / `time` / `points.coordinates` / `instructions` / `details.osm_way_id` / `details.road_class` / `bbox` / `snapped_waypoints` を含む |
| `compliant_route` | dict | リルート後（発生時）／**初期ルート**（未発生時は同一オブジェクト） | リルート発生時は `details` が空 `{}`（§1.3参照） |
| `route` | dict | `compliant_route` と**同一オブジェクト** | 後方互換用の別名 |
| `violations` | list | **初期ルート** | 各要素に `triggered_reroute` が追加済み |
| `compliant` | bool | **初期ルート** | `len(violations) == 0`。＝「初期ルートが違反ゼロだったか」 |
| `recommendations` | list | **初期ルート** | `check_cycleway_recommendation` の結果 |
| `rerouted` | bool | 処理結果 | リルートが成功したか |
| `comparison.original_distance_m` | float | **初期ルート** | `original_route["distance"]` を丸めた値 |
| `comparison.compliant_distance_m` | float | リルート後／初期 | `compliant_route["distance"]` |
| `comparison.distance_diff_m` | float | 両者の差 | `comp_dist - orig_dist` |
| `comparison.distance_diff_pct` | float | 両者の差 | `diff_m / orig_dist * 100` |
| `comparison.violation_count` | int | **初期ルート** | `len(violations)` |
| `comparison.violation_types` | list | **初期ルート** | `{v["rule"] for v in violations}` |
| `comparison.rerouted` | bool | 処理結果 | 上の `rerouted` と同値 |
| `comparison.using_edge_ids` | bool | **初期ルート** | 初期ルートの判定が edge_id ベースだったか |
| `comparison.algo_version` | str | 設定値 | `"v1"` / `"v3"` |

**注意（事実）：** `compliant` / `violation_count` / `violation_types` はいずれも
「初期ルート由来」であり、**「法規準拠ルートが準拠しているか」を表す値ではない**。
`compliant: false` かつ `rerouted: true` の応答は「初期ルートに違反があり、回避した」
という意味になる（後述の横浜→みなとみらいの実測がその例）。

---

## 2. レスポンスに残っている初期ルート情報

### 2.1 質問への直接回答（事実）

**Q. `original_distance_m` / `distance_diff_m` は初期ルートのどの値から算出されているか**
→ `original_distance_m` = `route_data["paths"][0]["distance"]`（GraphHopper が返す
初期ルートの経路長・メートル）を `round(_, 1)` したもの。
`distance_diff_m` = `compliant_route["distance"] - original_route["distance"]`。
（[route_analyzer.py:288-291](../services/route_analyzer.py#L288)）

**Q. 初期ルートのジオメトリ（座標列）がレスポンスに含まれているか**
→ **含まれている。** `original_route.points.coordinates` に
`[[lng, lat], ...]` 形式の全点が入る（`points_encoded=false` のため生座標）。
**どこにも破棄する処理はない。**
実測：横浜→みなとみらいで初期ルート70点・法規準拠ルート54点と、別々の座標列が
返っている（§2.2）。

**Q. 初期ルートの `osm_way_id` details が保持されているか**
→ **保持されている。** `original_route.details.osm_way_id` に
`[start_idx, end_idx, way_id]` のセグメント配列が入る。
実測：横浜→みなとみらいで32セグメント、東京→渋谷で84セグメント。
なお**リルートが発生した場合の `compliant_route.details` は空 `{}`**
（rerouter が `details` を要求しないため。§1.3）。

**Q. 各 violation に検出位置を特定できる情報が付いているか**
→ 以下が付く（事実）。

| キー | 有無 | 内容 |
|---|---|---|
| `lat` / `lng` | 常にあり | oneway：その way が初期ルート上で占める区間の**中間点**の座標（`way_id_info[wid]["point"]`）。two_step_turn：右折 instruction の `interval[0]` に対応する座標 |
| `rule` | 常にあり | `"oneway"` / `"two_step_turn"` |
| `message` | 常にあり | 日本語メッセージ |
| `confidence` | 常にあり | oneway：0.4 / 0.7 / 1.0、two_step_turn：0.4 / 0.7 |
| `way_id` | **edge_id モード時のみ** | `using_edge_ids=True` のときに付与。値が `None` になる場合もある（two_step 側の二分探索で way が特定できないケース） |
| `triggered_reroute` | 常にあり | `rule=="oneway" and rerouted` |
| **ジオメトリ配列上の index** | **なし** | `points.coordinates` の何番目かを示す値は付与されていない |

**（推論）** index はないが、`way_id` があれば `original_route.details.osm_way_id` の
`[start_idx, end_idx, way_id]` から区間 index を逆引きできる。`way_id` が無い行でも
`lat`/`lng` から最近傍点を探せば実用上は特定できる。したがって「位置が失われている」
という状況ではない。

**（事実・補足）** `overpass.py` の `get_bulk_way_data` は
`match_way_id` / `match_dist_m` / `match_margin_m` / `match_ambiguous` を返すが、
`route_analyzer` はこれらを `tags` と `geometry` しか取り出しておらず、
violation には伝播していない。なお v3 の edge_id 経路は
`get_way_tags_by_ids`（ID直指定）を使うため、そもそも座標マッチングを経ていない。

### 2.2 実測：`POST /api/route` のレスポンス全体

実行条件（事実）：
- GraphHopper：既存 Docker コンテナ `bicycle-navi-graphhopper-1` を起動して使用
- バックエンド：本ワークツリーの `backend` を `uvicorn main:app --port 8011` で起動
  （コード変更なし。調査後に停止済み）
- 実行日時：2026-07-30

`points.coordinates` / `instructions` / `details` は先頭数件のみ表示して省略した。
`route` は `compliant_route` と同一オブジェクトのため省略表記にしている。

#### (a) 横浜→みなとみらい（リルート**発生**ペア）

`{"origin_lat":35.4658,"origin_lng":139.6225,"dest_lat":35.4581,"dest_lng":139.6380}`

```json
{
  "original_route": {
    "distance": 2277.718,
    "weight": 5849.0,
    "time": 584539,
    "transfers": 0,
    "legs": [],
    "points_encoded": false,
    "bbox": [139.622436, 35.458035, 139.637878, 35.4658],
    "points": {
      "type": "LineString",
      "coordinates": [
        [139.6225, 35.4658],
        [139.623214, 35.465356],
        "...(全70点。以下省略)..."
      ]
    },
    "instructions": [
      {"distance": 81.343, "heading": 127.35, "sign": 0, "interval": [0, 1],
       "text": "進む", "time": 48806, "street_name": ""},
      {"distance": 23.105, "sign": 2, "interval": [1, 2],
       "text": "右に曲がる", "time": 13863, "street_name": ""},
      "...(全18件。以下省略)..."
    ],
    "details": {
      "osm_way_id": [
        [0, 1, 542882430],
        [1, 2, 247836468],
        [2, 3, 247836476],
        "...(全32セグメント。以下省略)..."
      ],
      "road_class": [
        [0, 1, "pedestrian"],
        [1, 2, "footway"],
        [2, 3, "pedestrian"],
        "...(全13セグメント。以下省略)..."
      ]
    },
    "ascend": 0.0,
    "descend": 0.0,
    "snapped_waypoints": {
      "type": "LineString",
      "coordinates": [[139.6225, 35.4658], [139.637875, 35.458095]]
    }
  },
  "compliant_route": {
    "distance": 2693.553,
    "weight": 7646.0,
    "time": 764315,
    "transfers": 0,
    "legs": [],
    "points_encoded": false,
    "bbox": [139.6225, 35.458035, 139.637878, 35.468127],
    "points": {
      "type": "LineString",
      "coordinates": [
        [139.6225, 35.4658],
        [139.622709, 35.46567],
        "...(全54点。以下省略)..."
      ]
    },
    "instructions": [
      {"distance": 23.852, "heading": 127.35, "sign": 0, "interval": [0, 1],
       "text": "進む", "time": 14311, "street_name": ""},
      {"distance": 41.624, "sign": -2, "interval": [1, 2],
       "text": "左に曲がる", "time": 24974, "street_name": ""},
      "...(全18件。以下省略)..."
    ],
    "details": {},
    "ascend": 0.0,
    "descend": 0.0,
    "snapped_waypoints": {
      "type": "LineString",
      "coordinates": [[139.6225, 35.4658], [139.637875, 35.458095]]
    }
  },
  "route": "…（compliant_route と同一オブジェクト。内容も完全に同じため省略）…",
  "violations": [
    {
      "lat": 35.464037,
      "lng": 139.623297,
      "rule": "oneway",
      "message": "一方通行のため逆走の可能性があります",
      "confidence": 1.0,
      "way_id": 28413951,
      "triggered_reroute": true
    }
  ],
  "compliant": false,
  "recommendations": [],
  "rerouted": true,
  "comparison": {
    "original_distance_m": 2277.7,
    "compliant_distance_m": 2693.6,
    "distance_diff_m": 415.8,
    "distance_diff_pct": 18.26,
    "violation_count": 1,
    "violation_types": ["oneway"],
    "rerouted": true,
    "using_edge_ids": true,
    "algo_version": "v3"
  }
}
```

**この応答から読み取れる事実：**
- `original_route`（70点）と `compliant_route`（54点）は**別々のジオメトリ**として
  両方レスポンスに載っている
- `violations[0].way_id = 28413951` は初期ルート側の way（`RESEARCH.md` 14.2 の県道80号）。
  リルート後のルートは当該 way を回避しているので、**この違反はリルート後のルートには
  存在しない**。つまり `violations` はリルート前の判定結果である（コード読解と一致）
- `compliant_route.details` が `{}` になっている（rerouter が details を要求しないため）

#### (b) 東京→渋谷（リルート**なし**・違反あり）

`{"origin_lat":35.6812,"origin_lng":139.7671,"dest_lat":35.6580,"dest_lng":139.7016}`

```json
{
  "original_route": {
    "distance": 7742.783,
    "weight": 18504.0,
    "time": 1850660,
    "transfers": 0,
    "legs": [],
    "points_encoded": false,
    "bbox": [139.701552, 35.657859, 139.767153, 35.681342],
    "points": {
      "type": "LineString",
      "coordinates": [
        [139.767109, 35.681198],
        [139.767153, 35.681318],
        "...(全156点。以下省略)..."
      ]
    },
    "instructions": [
      {"distance": 13.947, "heading": 16.58, "sign": 0, "interval": [0, 1],
       "text": "進む", "time": 25105, "street_name": ""},
      {"distance": 9.511, "sign": -2, "interval": [1, 2],
       "text": "左に曲がるに曲がって中央通路に入る", "time": 5707, "street_name": "中央通路"},
      "...(全29件。以下省略)..."
    ],
    "details": {
      "osm_way_id": [
        [0, 1, 1382793929],
        [1, 2, 1354654129],
        [2, 3, 1356614978],
        "...(全84セグメント。以下省略)..."
      ],
      "road_class": [
        [0, 1, "steps"],
        [1, 15, "footway"],
        [15, 24, "secondary"],
        "...(全11セグメント。以下省略)..."
      ]
    },
    "ascend": 0.0,
    "descend": 0.0,
    "snapped_waypoints": {
      "type": "LineString",
      "coordinates": [[139.767109, 35.681198], [139.701607, 35.657987]]
    }
  },
  "compliant_route": "…（original_route と同一オブジェクト。distance/points/details すべて同じ）…",
  "route": "…（同上）…",
  "violations": [
    {
      "lat": 35.680211,
      "lng": 139.765155,
      "rule": "two_step_turn",
      "message": "二段階右折が必要な交差点です",
      "confidence": 0.7,
      "way_id": 1104965191,
      "triggered_reroute": false
    },
    {
      "lat": 35.677934,
      "lng": 139.763409,
      "rule": "two_step_turn",
      "message": "二段階右折が必要な交差点です",
      "confidence": 0.7,
      "way_id": 178989940,
      "triggered_reroute": false
    }
  ],
  "compliant": false,
  "recommendations": [],
  "rerouted": false,
  "comparison": {
    "original_distance_m": 7742.8,
    "compliant_distance_m": 7742.8,
    "distance_diff_m": 0.0,
    "distance_diff_pct": 0.0,
    "violation_count": 2,
    "violation_types": ["two_step_turn"],
    "rerouted": false,
    "using_edge_ids": true,
    "algo_version": "v3"
  }
}
```

**事実：** リルートなしの場合、`original_route` と `compliant_route` は
**同一オブジェクト**であり、`details` も両方に載る。座標列は完全一致する
（Python で `==` 比較して True を確認）。

#### (c) 渋谷→新宿（参考・違反ゼロ）

`violations: []`、`comparison.violation_count: 0`、`original_distance_m = compliant_distance_m = 4438.8`、
`rerouted: false`、`using_edge_ids: true`。

**（事実・重要）** `google_comparison.csv` の渋谷→新宿は
`system_distance_m=4175.0` / `system_violation_count=1` だが、今回の実測は
**4438.8m / 違反0件**だった。これは `backend/data/verify_v2_analyze_route.csv`
（既存ファイル）の記録（`new_distance_m=4438.8` / `new_violation_count=0`）と一致する。
**（推論）** CSV の値は 2026-07-07 時点のスナップショットであり、その後の OSM データ／
GraphHopper グラフの更新により現在の実測とはずれている。R1 の材料として使うなら
再実行した値を使うべきである。

---

## 3. `experiment.py` の記録内容

### 3.1 `system_violation_count` の算出元（事実）

`google_comparison.csv` は `experiment.py` が**直接生成しているわけではない**。
確認できた事実は以下のとおり。

- `routers/experiment.py` の `POST /experiment/batch`（[experiment.py:53-109](../routers/experiment.py#L53)）
  が出力する `violation_count` は `comp["violation_count"]`
  ＝ `comparison.violation_count` ＝ `len(violations)`
  ＝ **初期ルートに対する違反数**（§1.4 の対応表）。
- `violation_count_high_conf` は `sum(1 for v in violations if v.get("confidence",0.4) >= 0.7)`
  で、同じく初期ルート由来。
- `/experiment/batch/csv` などが出す CSV の列は `CSV_FIELDNAMES`
  （[experiment.py:20-36](../routers/experiment.py#L20)）で、
  `original_distance_m` / `compliant_distance_m` / `distance_diff_m` / `distance_diff_pct` /
  `violation_count` / `violation_count_high_conf` / `violation_types` / `rerouted` を含む。
- 一方 `google_comparison.csv` の列は
  `label, road_type, system_distance_m, system_time_s, system_violation_count,
  system_violation_count_high_conf, google_distance_m, google_time_s,
  google_oneway_violation_count, google_two_step_violation_count,
  google_total_violation_count, notes, scorer_route_distance_m,
  scorer_sampled_points, scored_at` であり、**`experiment.py` の CSV とは別スキーマ**。
- `scripts/score_google_routes.py` は `google_*` 列と `scorer_*` 列しか書き込まない
  （`SCORED_COLUMNS`、[score_google_routes.py:66-74](../scripts/score_google_routes.py#L66)）。
- `system_*` 4列は `docs/CHANGELOG.md`「R2：system 列の再検証」（2026-07-07）に
  「`analyze_route`（v3）を全15 O-D ペアで再実行して埋めた」と記録されている。
  git 履歴上も `1b4b4ba 本システムの距離時間の記録`（2026-07-07）で
  空欄だった system 4列が一括で埋められている。流し込みに使ったスクリプト
  （`rerun_r2_after_fix.py`）は**リポジトリに存在しない**（CLAUDE.md の注記どおり）。

**結論（事実＋推論）：** `system_violation_count` の算出元は
`analyze_route(...)["comparison"]["violation_count"]` であり、**リルート前（初期ルート）の
違反数**である。書き込み経路のスクリプトは失われているが、値そのものが
`verify_v2_analyze_route.csv` の `csv_system_violation_count` 列として保存されており、
同ファイルの `new_violation_count` と同じ意味で比較されていることからも裏づけられる。

**（事実）** 一方 `system_distance_m` は**リルート後**の値である。
横浜→みなとみらいの `system_distance_m = 2693.6` は `compliant_distance_m`（2693.6）と
一致し、`original_distance_m`（2277.7）とは一致しない。
`verify_v2_analyze_route.csv` でも `new_distance_m=2693.6` /
`new_original_distance_m=2277.7` と別列になっており、CSV の system 列は前者に対応する。

→ **`google_comparison.csv` は「距離＝リルート後、違反数＝リルート前」という
混在した意味づけになっている。** これは列名からは読み取れないので、論文で使う際は注記が要る。

### 3.2 リルート前の違反数を記録している列があるか（事実）

- `google_comparison.csv`：**`system_violation_count` そのものがリルート前の値**。
  「リルート前／後」を区別する列は存在しない（区別する必要がそもそも生じていない。
  リルート後の判定を一度も行っていないため）。
- ただし **`original_distance_m` に相当する列は `google_comparison.csv` に無い**。
  初期ルートの距離は同 CSV からは復元できない
  （`verify_v2_analyze_route.csv` の `new_original_distance_m` には残っている）。
- `violation_types` に相当する列も `google_comparison.csv` に無いため、
  **同 CSV 単体では oneway と two_step_turn の内訳が分からない**。
  内訳は `verify_v2_analyze_route.csv` の `violation_types` 列、および
  `ground_truth_template.csv` の `detected_rule` 列に残っている。

### 3.3 現在のCSVに値は入っているか（事実）

`backend/data/google_comparison.csv`（15行、全行記入済み）の system 側：

| label | system_distance_m | system_violation_count | high_conf |
|---|---|---|---|
| 渋谷→新宿 | 4175.0 | 1 | 1 |
| 東京→渋谷 | 7736.4 | 3 | 3 |
| 新宿→池袋 | 5330.8 | 0 | 0 |
| 品川→東京 | 7801.3 | 1 | 1 |
| 渋谷→六本木 | 3409.2 | 0 | 0 |
| 下北沢→三軒茶屋 | 2207.6 | 1 | 1 |
| 高円寺→中野 | 1891.8 | 0 | 0 |
| 荻窪→阿佐ヶ谷 | 1425.2 | 0 | 0 |
| 自由が丘→等々力 | 1975.7 | 0 | 0 |
| 浦和→さいたま新都心 | 4373.3 | 1 | 1 |
| 吉祥寺→三鷹 | 3959.0 | 1 | 1 |
| 立川→国分寺 | 6747.8 | 0 | 0 |
| 横浜→みなとみらい | 2693.6 | 1 | 1 |
| 川崎→武蔵小杉 | 8049.4 | 1 | 1 |
| 千葉→幕張本郷 | 8338.3 | 0 | 0 |

合計 10件（うち oneway は横浜の1件、残り9件は two_step_turn。
内訳は `ground_truth_template.csv` の `detected_rule` 列で確認）。

**（事実）** 既存の `verify_v2_analyze_route.csv`（再実行時の突き合わせ結果）では、
`violation_types` 列が oneway になっているのは**横浜→みなとみらいのみ**で、
残りはすべて空欄か `two_step_turn`。この CSV の `new_original_distance_m` と
`new_distance_m` は横浜以外すべて一致し（`new_distance_diff_m = 0.0`）、
`rerouted` も横浜のみ True。

**（推論）** つまり「oneway が1件しかない」のは**リルートで消えたからではなく、
初期ルートの段階でそもそも1件しか検出されていない**。依頼文の
「違反を回避した後の結果なので違反がほとんど出ません」という説明は、
コードの挙動とは異なる。R1 で oneway のサンプルが少ないという問題自体は実在するが、
その原因はリルートではなく、**15ペアの初期ルート上に oneway 逆走がほとんど無いこと**にある。

---

## 4. フロントエンドの2本表示

### 4.1 描画コードは現存する（事実）

[MapView.jsx:68-80](../../frontend/src/components/MapView.jsx#L68)：

```jsx
{/* 元の最短ルート（オレンジ・中太線） */}
{originalPositions.length > 0 && (
  <Polyline positions={originalPositions} color="#e65100" weight={4} opacity={0.8} />
)}
{/* 法規準拠ルート（青・太線） */}
{compliantPositions.length > 0 && (
  <Polyline positions={compliantPositions} color="#1976d2" weight={5} />
)}
```

[App.jsx:142-152](../../frontend/src/App.jsx#L142) で
`originalRoute={routeData?.original_route}` /
`compliantRoute={routeData?.compliant_route}` の両方が渡されている。
[App.jsx:167-198](../../frontend/src/App.jsx#L167) には
「法規準拠ルート（青）／最短ルート（橙）」の凡例もあり、CSS（`.map-legend`、
`App.css:95-129`）も生きている。

**条件分岐で無効化されている箇所はない。** データも来ている（§2.2 で
`original_route` がレスポンスに含まれることを実測済み）。

### 4.2 なぜ2本に見えないのか（事実＋推論）

**事実：** リルートが発生しないペアでは `_build_response` が
`compliant_route = original_route`（同一オブジェクト）を返すため、
`originalPositions` と `compliantPositions` は**完全に同じ座標列**になる
（§2.2(b) で `==` 比較 True を確認）。
描画順はオレンジ（`weight=4`）→青（`weight=5`, opacity 既定=1.0）であり、
後から描かれる太い不透明な青線が細いオレンジ線を完全に覆う。

**推論：** したがって「法規準拠ルートしか表示されない」のは**バグでも削除でもなく、
2本が同一経路であることの当然の帰結**。R2 の実測どおり 15ペア中14ペアはリルートなし
なので、通常操作ではほぼ常に1本にしか見えない。
リルートが発生する横浜→みなとみらい（初期70点／準拠54点、§2.2(a)）では、
橙と青が分岐して2本に見えるはずである（実機での目視確認は本調査では未実施）。

**事実（別経路）：** riding モード（`RidingView.jsx`）は
`routeData?.compliant_route` のみを描画しており（[RidingView.jsx:152,387](../../frontend/src/components/RidingView.jsx#L152)）、
そもそも1本しか描かない実装。2本表示は preparing モードの `MapView` のみの機能。

### 4.3 git 履歴（事実）

`MapView.jsx` に触れたコミットは4件のみ：

| コミット | 日付 | メッセージ | 2本表示への影響 |
|---|---|---|---|
| `8a6cb9c` | 2026-04-27 | やり直し | **2本表示を導入**。`originalPositions`／`compliantPositions` と2本の `Polyline`、凡例「最短ルート」を新規追加（当初は灰色・`weight=3`・`opacity=0.6`） |
| `b0c987b` | 2026-04-29 | スマホUI、走行中モードの実装 | 最短ルートの色を灰→**オレンジ `#e65100`**、`weight` 3→4、`opacity` 0.6→0.8 に変更。凡例も同色に |
| `167837b` | 2026-05-02 | 精度向上 | 違反マーカーの色分け（confidence 別）を追加。ルート2本の描画は無変更 |
| `8de5f37` | 2026-05-26 | UI | ボトムシート化に伴うリファクタ。青を `blue`→`#1976d2` に、`Popup`→クリックハンドラに変更。**2本の Polyline は維持** |

`git log -S "originalPositions" --all -- frontend/` と
`git log -S "original_route" --all -- frontend/` の結果はいずれも
`8a6cb9c` と `8de5f37` のみで、**削除コミットは存在しない**。
`git log -S "最短ルート" --all -- frontend/` も同じ2件。

→ **「2本表示機能が削除された」という事実はない。** 導入以来一度も外されていない。

---

## 5. R1の材料が取得可能かの判定

### 判定：**A（現状のコードで取得可能）**

根拠（事実）：

1. `POST /api/route` のレスポンスに、初期ルートに対する判定結果 `violations` が
   そのまま含まれる（リルート後で上書きされていない）。
2. 各 violation に `rule` / `lat` / `lng` / `confidence` / `way_id`（edge_id モード時）
   が付いており、OSM 上の照合に十分な情報がある。
3. 初期ルートのジオメトリ（`original_route.points.coordinates`）と
   way_id 列（`original_route.details.osm_way_id`）も残っており、
   「判定対象になり得た全 way の集合」（＝TN 候補の母集団）も復元できる。
4. 既存データとしても、`backend/data/ground_truth_template.csv` が
   **リルート前の violations から生成された**15ペア分の
   `label` / `way_id` / `point_lat` / `point_lng` / `detected_rule` /
   `system_confidence` / `osm_tags_raw` を保持している
   （`scripts/prepare_ground_truth.py` が `POST /api/route` の `violations[]` を
   そのまま展開したもの。§3 のとおり violations は初期ルート由来）。
5. R1 の集計エンドポイント `POST /api/experiment/ground-truth/compare` も
   `analyze_route(...)["violations"]` を使っており、初期ルートの判定結果を
   ground truth と突き合わせる設計になっている（[experiment.py:245-253](../routers/experiment.py#L245)）。

**コード変更は不要（Cではない）。** 初期ルートの情報はどこでも破棄されていない。

### ただし実務上の注意（事実／推論を分けて記載）

- **（事実）** `google_comparison.csv` の system 4列と現在の実測値は乖離している
  （渋谷→新宿：CSV 4175.0m/違反1件 → 実測 4438.8m/違反0件）。
  同様の乖離は `verify_v2_analyze_route.csv` に15ペア分記録済み。
  **（推論）** OSM データ／グラフの更新によるもの。R1 の Precision/Recall を出すなら
  ground truth の人手判定と**同時期に**再実行した検出結果を使うべき。
  この意味では実質的に「B（再実行すれば取得可能）」の運用になるが、
  いずれにせよ**コード変更は不要**。
- **（事実）** `ground_truth_template.csv` は検出された違反しか行を持たない
  （検出ゼロのペアは `no violations detected by current system` の1行）。
  したがって TP/FP は判定できるが、**FN（見逃し）と TN の母集団はこのファイルからは作れない**。
  R1 で Recall を出すには、初期ルートが通る way のうち検出されなかったものを
  人手で判定する必要がある。材料自体は `original_route.details.osm_way_id` から作れる。
- **（事実）** oneway の検出サンプルは15ペアで1件（横浜→みなとみらい）しかない。
  これは初期ルート段階での事実であり、リルートの影響ではない（§3.3）。
  **（推論）** oneway の Precision/Recall を意味のある精度で出すには、
  逆走が起きやすい O-D ペア（一方通行の多い旧市街など）を追加するか、
  `external_route_scorer` で採点した Google ルート側の違反
  （合計18件・`RESEARCH.md` 13章）を評価対象に含めるか、いずれかの拡張が要る。
  これは**材料取得の可否ではなくサンプル設計の問題**。

### 参考：任意の改善案（Cではないため必須ではない・未実装）

R1 の作業を楽にするだけの提案であり、現状でも材料は取れる。
いずれも公開仕様（既存フィールド）を壊さない**追加のみ**。

1. `violation` に `point_index`（`original_route.points.coordinates` 上の index）を
   追加する。`route_analyzer._analyze_v3` の `way_id_info[wid]["start_idx"/"end_idx"]` と
   `two_step_idxs` に既に持っている値をそのまま載せるだけ。
   → 検出位置が座標の丸め誤差なしに特定できる。
2. `google_comparison.csv` に `system_original_distance_m` と
   `system_violation_types` 列を追加する（値は `comparison` に既にある）。
   → 「距離＝リルート後／違反数＝リルート前」の混在が列名で読み取れるようになる。
3. `rerouter.get_compliant_route` の POST body に `"details": ["osm_way_id"]` を足す。
   → 法規準拠ルート側の way_id も得られ、「リルート後に違反が消えたこと」を
   機械的に検証できるようになる（現状は details が空で検証不能）。

---

## 付録：調査で実行した操作（すべて非破壊）

| 操作 | 内容 |
|---|---|
| 読み取り | `route_analyzer.py` / `route.py` / `experiment.py` / `law_checker.py` / `rerouter.py` / `graphhopper.py` / `overpass.py`（該当箇所） / `MapView.jsx` / `App.jsx` / `App.css` / `RidingView.jsx` |
| 読み取り | `google_comparison.csv` / `od_pairs.csv` / `ground_truth_template.csv` / `verify_v2_analyze_route.csv` / `docs/CHANGELOG.md` / `RESEARCH.md` |
| git | `git log` / `git show` / `git log -S`（読み取りのみ） |
| 実行 | `docker start bicycle-navi-graphhopper-1`（既存コンテナの起動。調査後に停止して元の状態に戻した） |
| 実行 | ワークツリーの backend を `uvicorn --port 8011` で起動し `POST /api/route` を3回実行（横浜→みなとみらい／東京→渋谷／渋谷→新宿）。調査後に停止 |

**コードおよび既存データファイルの変更は一切行っていない**
（本ファイル `investigation_r1_material.md` の新規作成のみ）。
