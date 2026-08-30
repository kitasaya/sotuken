# 検証記録：Layer 1閉ループ（way 28413951）

**検証日：2026-08-30 ／ バッチ20同定結果とバッチ21修正後再測定の統合記録**

## 結論

横浜→みなとみらいで検出されていたway 28413951は、実際には順方向に通過していた。閉ループwayの始点と終点が同じため、`_trim_geometry`が末尾でなく先頭index 0を終点候補に選び、走行した後半弧ではなく反対側の前半弧を`_check_direction`へ渡したことによる偽陽性である。

確認者のStreet View地点 `(35.4643064, 139.6233374)` はway 28413951上ではなく、隣接するway 28413949上だった。したがって目視した矢印だけで28413951の向きを直接確定することはできなかったが、OSM geometryと保存ルートの機械照合により28413951の順走を確定した。

## 修正前後

| 項目 | 修正前 | 修正後 |
|---|---|---|
| 閉ループの弧 | 始終点の最近傍indexを単純にmin/maxで切出し | リング上の2候補弧を作り、実ルート座標列への適合距離が小さい弧を選択 |
| way 28413951 | 7/7セグメントが逆向き | 走行した後半弧を選び`forward_travel` |
| 横浜→みなとみらいR1 | 一方通行違反1件 | 一方通行違反0件 |
| リルート距離差 | +419.9m（+18.4%） | 0.0m |

方向判定関数`check_oneway_violation`自体は変更していない。修正対象は判定へ渡すway部分geometryの選択だけである。

## 影響範囲

- GoogleルートR2の全379判定点を修正前後で比較し、判定差は0件だった。R2は12点、A/B/C各4点のままである。
- way 28413951にマッチしたR2判定点は0件だった。
- 非閉ループの合成16ケース、およびバッチ20の現行12点・旧18点の計30点は、修正前後の切出しgeometryと既存判定関数の結果が完全一致した。
- R1は固定GraphHopperで15/15ペアを再取得・再採点し、一方通行違反0件だった。

## Layer 1寄与の再評価

同じ15 ODについて、条件A（違反時にcustom_model areasを適用する現行2パス方式）と条件B（GraphHopper標準bike）を比較した。条件Bですでに一方通行違反は0/15ペアであり、way 28413948を逆走するルートも返らなかった。このため追加areasは全ペアで未発動し、A/Bのgeometryと距離は完全一致した。

よって、実装可能性は確認できるが、この15ペアにおけるLayer 1の違反排除実績は0件であり、有用性は実証されていない。

## 機械可読出典

- `bicycle-navi/backend/data/batch20_way28413951_validation.json`
- `bicycle-navi/backend/data/batch21_exclusion_comparison.csv`
- `bicycle-navi/backend/data/batch21_r2_remeasurement.csv`
- `bicycle-navi/backend/data/batch21_tagged_oneway_traversals.csv`
- `bicycle-navi/backend/scripts/smoke_test_trim_geometry.py`
- `bicycle-navi/backend/data/batch21_nonclosed_regression_30.csv`
- `docs/分析/分析_除外機構の実証.md`
