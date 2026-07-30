# ② 非車道除外フィルタ ドライラン分析

> **状態: 未実行。**
>
> 本分析は Overpass API への到達が必要だが、実装を行った実行環境からは
> 全4エンドポイント（overpass-api.de / overpass.kumi.systems / maps.mail.ru /
> overpass.private.coffee）がネットワークポリシーにより 403 で拒否された。
> `api.openstreetmap.org` / `download.geofabrik.de` も同様に到達不可。
>
> **ローカル環境で下記コマンドを実行すると、このファイルは実測値で自動的に上書きされる。**
>
> ```bash
> cd bicycle-navi/backend
> python3 scripts/dryrun_nonroad_filter.py                    # 15ペア全件（推奨）
> python3 scripts/dryrun_nonroad_filter.py --label 渋谷→新宿    # 部分実行（採否判断には使えない）
> ```
>
> 出力先: 本ファイル / `dryrun_nonroad_filter_points.csv`
> （いずれも新規ファイル。`google_comparison.csv` 等の既存CSVは書き換えない）
>
> **フィルタはコードに入っていない。** `scripts/dryrun_nonroad_filter.py` の中だけで
> シミュレートしており、`services/overpass.py` には①（垂線距離マッチング＋マージン記録）
> しか入っていない。この報告書を見てから採否を判断すること。

---

## 検討中のフィルタ仕様

- 除外対象 `highway`: `footway` / `path` / `steps` / `pedestrian` / `corridor` / `platform`
- 除外の例外: `bicycle=yes` または `bicycle=designated`（自転車が合法的に走行できるため）
- 除外の結果候補が0本になった場合は、除外前の候補にフォールバックする

**方向情報（travel_vector との角度）は候補の選択に使わない。** 測定対象が
「進行方向が逆か」であるため、方向で候補を選ぶと循環論法になる（RESEARCH.md 21.11節）。
角度は診断列 `diag_angle_baseline_deg` / `diag_angle_filtered_deg` に記録するのみ。

## 判定基準

**C群2件（3点）のいずれかが消えた場合、非車道除外フィルタは不採用。**

| 群 | way_id | 期待 |
|---|---|---|
| A群（自転車を除く・6件） | 138533178, 23690216, 142222817, 80835360, 350353685, 325347768 | 検出されたまま残るはず |
| C群（真の違反・2件） | 28413948, 853388885 | **検出されたまま残るはず。ここが消えたら不採用** |
| B群 | 22961575, 741785139, 667962675, 474601303, 271979254, 1429406683, 263457845 | 消滅したまま |
| D群（参考） | 151808609 | Googleルート外・原因未特定 |

**注意：** 既知18件は**垂線距離修正前（ノード距離ベース）**の採点で検出されたもの
（RESEARCH.md 29節）。本ドライランのベースラインは①適用後なので、B群および A群の一部は
この時点で既に消えている（RESEARCH.md 21.9節）。スクリプトはベースライン検出点数と
フィルタ後検出点数を両方出力するため、この差は表の上で区別できる。

---

## スクリプトが出力する内容

`scripts/dryrun_nonroad_filter.py` は `external_route_scorer.score_external_route` と
同一のパイプライン（40m 等間隔サンプリング → 半径20mのUnionクエリ → 最近傍way →
向き整合チェック → `check_oneway_violation`）を再現し、以下を生成する。

### 結論

C群の保持状況から自動的に採否の推奨を出す。以下の分岐がある。

- C群がフィルタで減少 → **不採用を推奨**
- 部分実行（15ペア未満） → 採否は判断できない
- C群がベースラインで0点 → 前提が崩れているため判断できない
- 除外が一度も作動しない → 実装する積極的理由がない
- 除外は作動するが判定変化0件 → 無害だが効果もない
- C群保持かつ判定変化あり → 群別表と判定変化一覧を見て判断

### 1. 集計

- rank1 の `highway` が除外対象リストに該当する判定点の件数
- うち `bicycle=yes/designated` が付いていて除外されない件数
- 実際に除外された件数 / rank2 以下へ切り替わった件数 / 候補0本でフォールバックした件数
- oneway 判定がフィルタ前後で変化した件数
- 参考: `match_ambiguous=True` の件数
- 除外対象 `highway` の値ごとの内訳

### 2. 除外された点の切り替わり先

除外された各判定点について、rank2 以下のどの way に切り替わるか。
その way の `highway` と `oneway`、垂線距離、マージン、判定変化の有無。

### 3. 18件の既知違反への影響

群別（A / C / B / D）に way_id ごとのベースライン検出点数とフィルタ後検出点数を並べ、
期待との突き合わせ判定を付ける。加えて、既知 way が rank1 / rank2 / ノード距離 rank1 の
いずれかに現れた判定点の明細（`node_rank1` 併記）を出力する。

### 4. oneway 判定が変化した判定点

「検出 → 消滅」「未検出 → 新規出現」の別、座標、ベース rank1 とフィルタ後 rank1、
マージン、既知18件の備考。

### 5. ルート別サマリ

15ペアそれぞれの判定点数・除外対象 highway 数・実除外数・曖昧数・
ベースライン違反数・フィルタ後違反数。

---

## 参照

- 分析スクリプト: `backend/scripts/dryrun_nonroad_filter.py`
- 共通基盤: `backend/scripts/route_match_probe.py`・`backend/scripts/known_violations.py`
- ①の実装と検証: `backend/data/fix_verification_v2.md`
- 背景: `RESEARCH.md` 21.10〜21.11節・24.3節・29節
