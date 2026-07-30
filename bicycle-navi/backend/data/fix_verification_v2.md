# ① match_margin_m / match_ambiguous 実装の検証（v2）

> **状態: A・B・C は未実行。D のみ完了。**
>
> A・B は Overpass API、C は GraphHopper（`localhost:8989`）への到達が必要だが、
> 実装を行った実行環境からはどちらにも到達できなかった（Overpass の全4エンドポイントが
> ネットワークポリシーにより 403、GraphHopper は未起動）。
>
> **ローカル環境で下記コマンドを実行すると、このファイルは実測値で自動的に上書きされる。**
>
> ```bash
> cd bicycle-navi/backend
> python3 scripts/verify_match_margin.py --skip-analyze-route   # A・B（Overpass のみ）
> python3 scripts/verify_match_margin.py                        # A・B・C（GraphHopper も必要）
> python3 scripts/verify_match_margin.py --only-analyze-route    # C だけ再実行
> ```
>
> 出力先: 本ファイル / `verify_match_margin_points.csv` / `verify_v2_analyze_route.csv`
> （いずれも新規ファイル。`google_comparison.csv` 等の既存CSVは書き換えない）

---

## 実装内容（完了）

対象は `services/overpass.py` のみ。`external_route_scorer.py` / `law_checker.py` /
`route_analyzer.py` / `rerouter.py` は変更していない。

### 追加した定数

```python
MATCH_AMBIGUOUS_MARGIN_M = 2.0
```

根拠は RESEARCH.md 21.10 / 24.3節の実測（想定外4地点は 0.50〜1.74m、正常10地点は
2.75m 以上という明確な断絶）。モジュール定数なので後から差し替えできる。

### `get_bulk_way_data` の戻り値に追加したキー

| キー | 内容 |
|---|---|
| `match_dist_m` | rank1 の垂線距離（m・小数3桁）。候補0本なら `None` |
| `match_margin_m` | rank1 と rank2 の垂線距離差（m・小数3桁）。候補が1本以下なら `None` |
| `match_ambiguous` | `bool`。マージンが `MATCH_AMBIGUOUS_MARGIN_M` 未満なら `True`。`None` のときは `False` |
| `match_way_id` | rank1 の way id。群の同定・後段の分析に必要なため併せて追加 |

既存キー `tags` / `geometry` は形式・意味とも不変。公開シグネチャも不変。

### 前提として復元した point-to-curve マッチング

RESEARCH.md 21.8節は「2026-07-25 に `get_bulk_way_data` を垂線距離ベースへ修正済み」と
記録しているが、**その変更はリポジトリに未コミット**であり、コード上はノード距離ベース
（`(node["lat"]-lat)**2 + (node["lon"]-lng)**2`）のままだった。`_point_to_segment_dist_m` も
`get_bulk_way_data_with_id` も存在せず、`fix_verification.md` /
`google_comparison_after_fix.csv` / `investigation_perpendicular_side_effects.md` /
`scripts/rerun_r2_after_fix.py` / `ground_truth.csv` / `調査結果/` も同様に未コミット。

マージンは垂線距離の差として定義されるため、①の前提として `overpass.py` 内に
point-to-curve マッチングを復元した。

- `_point_to_segment_dist_m(lat, lng, a, b)` — 局所平面近似（経度差に `111_320·cos(lat)`、
  緯度差に `110_540`）で線分への垂線距離。射影が線分外なら t を `[0, 1]` にクランプ
- `_point_to_way_dist_m(lat, lng, geometry)` — way の全セグメントに対する最小値
- `_rank_way_candidates(lat, lng, elements)` — 候補を垂線距離の昇順で返す。
  **way id で重複除去する**（同一 way が Union 結果に複数現れると rank2 が rank1 と
  同じ way になり、マージン 0 の偽の「曖昧」判定を生むため）

**⚠ ローカル版とは別実装である。** RESEARCH.md 21.10節の報告値（想定外 0.50〜1.74m /
正常 2.75m 以上）との完全一致は保証されない。A項はまさにその一致・乖離を確認する工程であり、
乖離した場合も実測値をそのまま提示すること（数値を合わせにいかない）。

### 方向タイブレークは実装していない

測定対象が「進行方向が逆か（＝逆走）」であるため、方向で候補を選ぶと答えを使って
答えを決める循環論法になる（RESEARCH.md 21.11節）。travel_vector との角度は
分析スクリプトの診断列（`diag_angle_*_deg`）に記録するだけで、候補の選択には
一切使っていない。

---

## A. マージン分布（14地点）

**未実行。** `python3 scripts/verify_match_margin.py --skip-analyze-route` で生成される。

14地点 = 想定外4 + B群4（正しく消えた対照）+ C群3（真の違反）+ 残存A群3。

| 群 | 同定に使う way_id |
|---|---|
| 想定外4 | 23690216（品川→東京）・80835360（荻窪→阿佐ヶ谷）・325347768（浦和→さいたま新都心）・新宿→池袋 idx=22 |
| B群4 | 271979254（外苑東通り）・667962675（中央通り）・263457845（南武沿線道路）・22961575（国道126号） |
| C群3点 | 28413948 ×2点・853388885 |
| 残存A群3 | 138533178（渋谷→新宿）・350353685（自由が丘→等々力）・142222817（千葉→幕張本郷） |

**同定方法：** 想定外4地点は垂線距離マッチング後の rank1 が歩道・交差道路に変わっている
ため、修正後の way_id では引けない。スクリプトは各判定点について
**ノード距離ベース（修正前）の rank1 way_id** を別途算出し、それで群を同定する。
新宿→池袋 は way_id ではなく判定点 idx で特定されているため、サンプル番号で照合する。

スクリプトは以下を出力する。

- 14地点それぞれの `match_margin_m` / `match_dist_m` / `match_ambiguous` /
  ノード距離 rank1 / 垂線距離 rank1 / rank2
- 群ごとの最小・中央値・最大
- 21.10節の報告値との一致確認表（想定外群の最大 < 2.00m か、正常群の最小 >= 2.75m か、
  両群に断絶があるか）
- 同定された地点数が 4/4/3/3 と一致しない場合の警告

## B. 15ペア全体での曖昧地点の割合

**未実行。** A と同じコマンドで生成される。

`google_routes_input.csv` の全判定点のうち `match_ambiguous=True` の件数と割合を、
ルート別内訳と全体合計の両方で出力する。論文で
「N件について評価した。M件はマッチング曖昧のため判定不能」と報告する際の母数になる。

候補が1本のみ（`match_margin_m=None`）の点と候補0本の点は曖昧扱いしないが、
母数の解釈に必要なため件数を別途集計する。

## C. 自システムへの影響（od_pairs.csv 15ペア）

**未実行。** GraphHopper（`http://localhost:8989`）の起動が必要。

`analyze_route(algo_version="v3")` を15ペアで実行し、`google_comparison.csv` の
`system_distance_m` / `system_time_s` / `system_violation_count` /
`system_violation_count_high_conf` と突き合わせる。

**変化しないことが期待される理由：** v3 は `osm_way_id` details が取れる限り
`get_way_tags_by_ids` を使い、`get_bulk_way_data` は Overpass の by-ID 取得が失敗した
ときのフォールバック経路（`route_analyzer.py:166`）でしか呼ばれない。①はキー追加のみ。

**ただし point-to-curve の復元はそのフォールバック経路の挙動を変えうる。**
差分が出た場合は出力表の `using_edge_ids` 列を確認すること。`False` なら by-ID 取得が
失敗してフォールバックに落ちている。`True` のまま差分が出た場合は①とは無関係の要因
（GraphHopper のグラフ更新等）を疑う。

---

## D. キー追加の非破壊性 — ✅ 完了

`get_bulk_way_data` の呼び出し側は以下の4箇所で、いずれも `tags` と `geometry` しか読まない。

| 箇所 | 読むキー |
|---|---|
| `services/external_route_scorer.py:175` | `tags`, `geometry` |
| `services/external_route_scorer.py:202` | `tags` |
| `services/route_analyzer.py:166` | `tags`, `geometry` |
| `services/overpass.py`（`get_bulk_way_tags`） | `tags` |

`route_analyzer.py:170` の Overpass 失敗時フォールバックは `{"tags": {}, "geometry": []}` を
生成するが、新キーを読む消費側が無いため問題ない。

実行時の確認は Overpass をスタブした smoke テストで行った。

```
$ python3 scripts/smoke_test_match_keys.py
合計 50 件 / 成功 50 件 / 失敗 0 件
全ケース成功。match_* キーの追加は既存の呼び出し側を壊さない。
```

確認した内容:

| # | 項目 | 結果 |
|---|---|---|
| 1 | 既存キー `tags` / `geometry` の形式が不変（`[[lon, lat], ...]` の座標順を含む） | PASS |
| 2 | 新キー4つが存在し、rank1 の way_id・垂線距離・rank1/rank2 の差を正しく持つ | PASS |
| 3 | 候補0本 / 1本 / 僅差2本（margin 1.5m→曖昧）/ 離れた2本（margin 3.0m→非曖昧）| PASS |
| 4 | 同一 way が Union 結果に重複しても偽の `match_ambiguous=True` を生まない | PASS |
| 5 | point-to-curve 選択（ノードが疎で線が近い way を、ノードが密で線が遠い way より優先） | PASS |
| 6 | `get_bulk_way_tags` の戻り値はタグ dict のままで `match_*` が混入しない | PASS |
| 7 | `score_external_route` が例外なく完走・`route_analyzer` が import できる | PASS |
| 8 | 垂線距離を独立実装（線分を20,000分割した大円距離のブルートフォース）と照合 | PASS |

**#5 の内容（B群の再現）：** 線が 0.8m 先だがノードが両端（±100m）にしかない way と、
線が 8m 先だが 5m 間隔でノードが密な way を用意すると、ノード距離ベースは後者
（対向車線相当）を選び、垂線距離ベースは前者（正しい車線）を選ぶ。修正の意図どおり。

**#8 の結果：** 4つの線分配置（射影が線分内・線分外・斜め・退化）で相対差は
0.105〜0.589% であり、RESEARCH.md 21.10節の仮説Z棄却で報告された「差分0.6%未満」と
同水準。局所平面近似の誤差範囲として妥当。

---

## 参照

- 実装: `backend/services/overpass.py`
- smoke テスト: `backend/scripts/smoke_test_match_keys.py`（Overpass 不要）
- 検証スクリプト: `backend/scripts/verify_match_margin.py`
- 共通基盤: `backend/scripts/route_match_probe.py`・`backend/scripts/known_violations.py`
- 背景: `RESEARCH.md` 21.5〜21.11節・24.3節・29節
