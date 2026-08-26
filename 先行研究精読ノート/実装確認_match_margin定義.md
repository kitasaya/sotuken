# 実装確認：`match_margin_m` の定義

**確認日：2026-08-26 ／ 対象：`bicycle-navi/backend/` ／ コード変更：なし**

## 1. 結論

`match_margin_m` は、**互いに異なるOSM way IDを持つ第1候補と第2候補の有限線分最小距離の差**である。同じwayがUnionクエリ結果に重複しても、way IDで集約されるため第1・第2候補を同じwayが占めない。

検証CSV 379点では、マージン2m未満の曖昧点188点のうち、同じway同士の比較は0件だった。したがって、既存の曖昧判定188件は「同じwayの重複」による偽陽性ではなく、**異なるway間の僅差**である。再計算による件数変更はなく、コード修正は不要。

## 2. 該当コード

### 2.1 way IDごとの重複除去

対象：`bicycle-navi/backend/services/overpass.py` 414–444行

```python
def _rank_way_candidates(lat: float, lng: float, elements: list) -> list[tuple[float, dict]]:
    best_by_id: dict = {}
    unidentified: list[tuple[float, dict]] = []
    ...
    wid = elem.get("id")
    if wid is None:
        unidentified.append((d, elem))
        continue
    prev = best_by_id.get(wid)
    if prev is None or d < prev[0]:
        best_by_id[wid] = (d, elem)
    ...
    ranked.sort(key=lambda t: t[0])
```

同じ`wid`が複数回現れた場合は最小距離の要素だけを`best_by_id`に残す。よって通常のOverpass way要素ではランキングはway ID単位である。

### 2.2 マージン計算

対象：同ファイル 505–507行

```python
rank1_dist, rank1_elem = ranked[0]
margin = (ranked[1][0] - rank1_dist) if len(ranked) >= 2 else None
ambiguous = margin is not None and margin < MATCH_AMBIGUOUS_MARGIN_M
```

したがって数学的には、異なるway候補を距離順に並べたとき、

`match_margin_m = d(second distinct way) − d(nearest distinct way)`

である。候補が1本以下なら`None`となり、曖昧判定は`False`。

### 2.3 距離の単位

`_point_to_way_dist_m`はwayを構成する全有限線分への距離の最小値を返す（同ファイル400–406行）。`match_margin_m`はこのメートル距離同士の差である。詳細な線分距離・座標変換は `実装確認_最近傍way選択.md` を参照。

## 3. 実データ検算

対象：`bicycle-navi/backend/data/dryrun_nonroad_filter_points.csv`

| 検算項目 | 結果 |
|---|---:|
| 全判定点 | 379 |
| `match_margin_m < 2` | 188 |
| 全点で`rank1_way_id == rank2_way_id` | 0 |
| 曖昧点で`rank1_way_id == rank2_way_id` | 0 |
| 曖昧点でrank2欠損 | 0 |
| マージン最小値 | 0.001 m |
| 中央値 | 2.047 m |
| 最大値 | 75.688 m |

`verify_match_margin_points.csv`でも全379点・曖昧188点・同一way比較0件で一致した。

## 4. 例外と限界

- `elem.get("id") is None` の要素は`unidentified`へ入り、重複除去されない。ただし通常のOverpass `way` elementにはIDがあるため、今回のCSVでは発生していない。
- 2m閾値は異なるway間の識別余裕を表すが、「2mなら正しい」という確率的保証ではない。観測誤差、元ルート形状、OSM形状誤差を統合した校正値ではなく、本研究の運用上の判定保留基準である。
- 188/379=49.6%と多いため、論文では全点の約半数が2m未満だったことを明示し、閾値で除外後の評価母数も併記する。

## 5. 論文用文案

> 各判定点について、OSM wayを構成する有限線分への最小距離をway単位で算出し、距離が最小のwayを対応先とした。候補の幾何的曖昧性は、異なるway IDを持つ第1候補と第2候補の距離差 `match_margin_m` で表す。2m未満は188/379点（49.60%）で、既存フィルタを経て判定へ影響する曖昧点は150/379点（39.58%）、違反有無が反転しうる点は12/379点（3.17%）だった。

## 改訂履歴

- 2026-08-26：`match_margin_m`を第1・第2候補の有限線分距離差としてコード確認。
- 2026-08-26：幾何的曖昧、判定影響曖昧、違反反転の3指標を区別。
- 2026-08-26：現在の論文用文案へ統合。
