# 卒研: `get_bulk_way_data` の最近傍way選択を垂線距離ベースに修正 + R2再検証

## 背景

現地検証（`investigation_mismatch_B7.md`）により、Googleルート採点で検出された
oneway違反18件のうち7件（コードB）が **分離ウェイ（幹線道路の対向車線）の
誤マッチング** に起因することが判明した。うち6件は原因が確定している。

根本原因は `services/overpass.py` の `get_bulk_way_data` が、最近傍wayを
**「点から各wayのノード（頂点）までの距離」** で選択しており、
**「点から各wayの線分（セグメント）への垂線距離」** を使っていないこと。

分離ウェイはノード間隔が疎な区間があると、実際の線（道路）は近くを通っていても
近傍にノードが無いために「遠い」と判定され、ノード密度の高い対向車線が
誤選択される。実測では垂線距離0.8mの正しいwayではなく垂線距離7.66mの
対向車線が選ばれるケースを確認済み。

**本タスクの目的**：この選択ロジックを垂線距離ベースに修正し、修正前後で
R2の18件がどう変わるかを比較する。**修正それ自体が検証結果になる。**

---

## 重要な制約

- **`external_route_scorer.py`・`law_checker.py`・`route_analyzer.py`・
  `rerouter.py` のロジックは変更しない。** 修正は `services/overpass.py` の
  距離計算部分に限定する。
- **公開関数のシグネチャ（引数・戻り値の形式）は変更しない。**
  `get_bulk_way_data(points, radius) -> list[dict]` の入出力形式を維持する。
  戻り値は各座標に対応する `{"tags": {...}, "geometry": [[lon, lat], ...]}` のまま。
- **既存の実験データCSVを直接上書きしない。** R2再実行の結果は
  新規ファイル（`google_comparison_after_fix.csv` 等）に出力し、
  既存の `google_comparison.csv` は無傷のまま残す。
- 修正前の挙動を再現できるよう、変更前に該当関数の現状をコメントまたは
  git で保全すること。
- 大きな変更の前に、変更方針を要約してユーザーに確認すること。

---

## Step 1: 現状の実装を確認

`services/overpass.py` の `get_bulk_way_data` を `view` で読む。
現在の距離計算は以下（要旨）：

```python
for elem in elements:
    geom_nodes = elem.get("geometry", [])
    for node in geom_nodes:
        d = (node["lat"] - lat) ** 2 + (node["lon"] - lng) ** 2  # ← ノード距離
        if d < best_dist:
            best_dist = d
            best = {...}
```

同一ファイル内に `get_bulk_way_data_with_id`（調査用に作成された同一ロジックの
派生）が存在する場合は、それも確認する。

---

## Step 2: 垂線距離ヘルパーの実装

点から線分への垂線距離を計算する関数を追加する。
`investigation_mismatch_B7.md` の調査で使用した `point_to_segment_dist_m` と
同等のロジックを用いる。

### 要件

- 入力：判定点 `(lat, lng)`、線分の両端 `(lat1, lng1)`・`(lat2, lng2)`
- 出力：点から線分への最短距離（メートル）
- **緯度補正**：経度差には `cos(latitude)` を掛けて距離の歪みを補正する
  （北緯35度付近では経度1度が緯度1度の約0.82倍）
- 線分が退化している（両端が同一点）場合は点間距離を返す
- 射影が線分の外側に落ちる場合は、近い方の端点までの距離を返す（クランプ処理）

### 実装例（参考・要検証）

```python
import math

def _point_to_segment_dist_m(lat, lng, lat1, lng1, lat2, lng2):
    """点(lat,lng)から線分[(lat1,lng1),(lat2,lng2)]への最短距離（m）。"""
    # 緯度補正込みでメートル平面に近似変換
    lat0 = math.radians((lat1 + lat2) / 2)
    kx = 111_320 * math.cos(lat0)  # 経度1度あたりのメートル
    ky = 110_540                    # 緯度1度あたりのメートル
    px, py = lng * kx, lat * ky
    ax, ay = lng1 * kx, lat1 * ky
    bx, by = lng2 * kx, lat2 * ky
    dx, dy = bx - ax, by - ay
    seg_len_sq = dx * dx + dy * dy
    if seg_len_sq == 0:
        return math.hypot(px - ax, py - ay)
    t = ((px - ax) * dx + (py - ay) * dy) / seg_len_sq
    t = max(0.0, min(1.0, t))  # 線分外にはみ出したらクランプ
    projx, projy = ax + t * dx, ay + t * dy
    return math.hypot(px - projx, py - projy)
```

**注意**：この実装は参考。既存コードに緯度補正の定数や距離関数
（`_haversine_m` 等）が既にあれば、それらと整合させること。

---

## Step 3: 最近傍way選択の書き換え

`get_bulk_way_data` の距離計算を、ノード距離から**線分（隣接ノード間の全セグメント）
への垂線距離の最小値**に変更する。

### 変更後のロジック（要旨）

```python
for lng, lat in points:
    best = {"tags": {}, "geometry": []}
    best_dist = float("inf")
    for elem in elements:
        geom_nodes = elem.get("geometry", [])
        if len(geom_nodes) < 2:
            # ノード1個以下ならノード距離にフォールバック
            for node in geom_nodes:
                d = _point_to_segment_dist_m(lat, lng, node["lat"], node["lon"],
                                             node["lat"], node["lon"])
                if d < best_dist:
                    best_dist = d
                    best = {"tags": elem.get("tags", {}),
                            "geometry": [[n["lon"], n["lat"]] for n in geom_nodes]}
            continue
        # 隣接ノード間の各セグメントへの垂線距離の最小を取る
        for i in range(len(geom_nodes) - 1):
            n1, n2 = geom_nodes[i], geom_nodes[i + 1]
            d = _point_to_segment_dist_m(lat, lng,
                                         n1["lat"], n1["lon"],
                                         n2["lat"], n2["lon"])
            if d < best_dist:
                best_dist = d
                best = {"tags": elem.get("tags", {}),
                        "geometry": [[n["lon"], n["lat"]] for n in geom_nodes]}
    result.append(best)
```

`get_bulk_way_data_with_id` が存在する場合も同一の修正を適用する。

### 確認事項

- 戻り値の `geometry` の座標順（`[lon, lat]`）が変更前と同一であること
- `radius` パラメータの扱いは変更しない
- パフォーマンス：セグメント総数分のループになるが、`radius=20` で取得する
  way 数は限られるため実用上問題ないはず。極端に遅くなる場合は報告すること

---

## Step 4: 単体確認（修正が効いているかの直接検証）

`investigation_mismatch_B7.md` で「ノード距離では誤ったwayが近いが、垂線距離では
正しいwayが近い」と実証された4点で、修正後に**正しいway（順走方向）**が
選択されることを確認する。

| label | 判定点(lat,lng) | 修正前に選ばれたway | 期待される修正後のway |
|---|---|---|---|
| 東京→渋谷(外苑東通り) | 35.66017, 139.7404 | 271979254 | 858775692（同名・順走） |
| 品川→東京(中央通り) | 35.66705, 139.7607 | 667962675 | 667962674（同名・順走） |
| 川崎→武蔵小杉(南武沿線道路) | 35.55607, 139.6776 | 263457845 | 31875063（同名・順走） |
| 千葉→幕張本郷(国道126号) | 35.62677, 140.1147 | 22961575 | 23052404（同名・順走） |

各点で `get_bulk_way_data` を単体呼び出しし、返ってきた way の `name` タグと
geometry の方向が順走側になっているかを確認する。結果を記録すること。

**期待通りにならない点があれば、その点の詳細（実際に選ばれたway・距離）を
報告し、原因を調査すること。** 修正が不完全か、その点が別要因（保留2点の
ような例外）である可能性がある。

---

## Step 5: R2の再実行（修正前後の比較）

修正後の `get_bulk_way_data` を使い、`google_routes_input.csv` の polyline に対して
R2採点を再実行する。18件のoneway違反がどう変化するかを記録する。

### 手順

1. `google_routes_input.csv` の15ペア分の polyline を読み込む
2. 各ペアについて `score_external_route(coords)` を実行
   （`external_route_scorer.py` は変更しないが、内部で呼ぶ `get_bulk_way_data` が
   修正済みになっているため、採点結果が変わるはず）
3. 各ペアの `oneway_violation_count` を修正前後で比較

### 出力

`backend/data/google_comparison_after_fix.csv` に以下を出力する
（**既存の `google_comparison.csv` は上書きしない**）：

| label | oneway_before | oneway_after | 差分 | 備考 |
|---|---|---|---|---|

さらに、18件の個別violationについて、修正前後で「検出されたか/消えたか」を
以下の形式で記録する：

| # | way_id | 現地判定コード | 修正前 | 修正後 | 期待 |
|---|---|---|---|---|---|
| 3 | 22961575 | B | 検出 | 消滅 | B群は消えるはず |
| 6 | 667962675 | B | 検出 | 消滅 | B群は消えるはず |
| ... | | | | | |
| 1 | 138533178 | A | 検出 | 検出のまま | A群は残るはず（OSMデータ不備なので） |
| 2 | 28413948 | C | 検出 | 検出のまま | C群は残るはず（真の違反なので） |

### 期待される結果

- **B群6件（原因確定分）**：消滅するはず（正しい順走wayにマッチするため）
- **B群保留2件（#5, #10）**：どちらに転ぶか不明。結果を記録
- **A群6件**：検出されたまま残るはず（OSMデータ不備でありマッチング問題ではない）
- **C群2件**：検出されたまま残るはず（真の違反）
- **D群1件（#12）**：不明。結果を記録

**この「A群・C群は残り、B群は消える」というパターンが確認できれば、
修正が的確であったことの強力な証拠になる。** 論文の検証結果として使える。

---

## Step 6: 自システム側への影響確認（重要）

`get_bulk_way_data` は `external_route_scorer.py` 以外に、
`law_checker.py` のフォールバック経路（`get_bulk_way_tags` 経由）からも
呼ばれる。自システムのルーティング判定に予期しない影響が出ていないか確認する。

### 手順

- `od_pairs.csv` の15ペアで `analyze_route`（v3）を実行
- 修正前の `google_comparison.csv` の system 4列と比較
- **system側の違反数・距離が変化していないことを確認する**

### 判定

- 変化なし → 想定通り（自システムは主に `osm_way_id` ベースで判定するため、
  `get_bulk_way_data` はフォールバック時しか使わない）
- 変化あり → フォールバックが発動しているペアがある。どのペアか特定し、
  変化が改善か劣化かを個別に確認して報告する

自システムのルート判定は `get_way_tags_by_ids`（edge_id ベース）が主経路であり、
`get_bulk_way_data` はフォールバックのはずなので、原則として system 側は
変化しない見込み。変化があればその事実自体を記録する。

---

## 成果物

1. **修正済み `services/overpass.py`**（`_point_to_segment_dist_m` 追加、
   `get_bulk_way_data`（+ 存在すれば `_with_id`）の距離計算を垂線距離ベースに変更）
2. **`backend/data/google_comparison_after_fix.csv`**（Step 5 の比較表）
3. **`backend/data/fix_verification.md`** に以下をまとめる：
   - Step 4 の単体確認結果（4点が正しいwayにマッチしたか）
   - Step 5 の18件個別の修正前後比較表
   - Step 6 の自システム影響確認結果
   - 総括：B群が消えA群・C群が残ったか。保留2点・D群がどうなったか
4. **`docs/CHANGELOG.md` に追記**（修正内容と検証結果の要約）

---

## 完了条件

- `get_bulk_way_data` が垂線距離ベースで最近傍wayを選択する
- Step 4 の4点で正しい順走wayが選択される（例外があれば理由を明記）
- Step 5 でB群（確定6件）のoneway違反が消滅し、A群・C群が残ることを確認
- Step 6 で自システム側の実測値に予期しない変化がないことを確認
- 既存の `google_comparison.csv` および他のロジックファイルが無変更である

---

## 補足：この修正が論文に与える意味

修正後にB群が消えれば、Googleルート上の「マッチング誤りによる偽陽性」を
実装レベルで解消したことになる。修正前18件→修正後の残存件数（A群6 + C群2 +
保留分）という変化は、そのまま論文の検証結果になる。

ただし **A群6件（OSMデータ不備）はこの修正では消えない**。これはアルゴリズムでは
解消できない「データの不完全性に起因する限界」であり、限界1として論文に記述する
対象である。修正で消えるもの（実装起因）と消えないもの（データ起因）が
明確に分離することが、第VII部の考察（限界1 vs 限界2の対比）を実証的に裏づける。
