# 分析：新宿→池袋のOD座標不整合（バッチ15）

**確認日：2026-08-28／ジオコーダ：アプリと同じ Nominatim**

## 0. 結論

- `google_comparison.csv` には `rerouted` 列自体がない。バッチ14のリルート数の出典は `verify_v2_analyze_route.csv` であり、その新宿→池袋は `rerouted=False` である（`bicycle-navi/backend/data/od_geocode_mismatch.json:7-12`）。
- CSVのOD座標と、アプリで「新宿駅」「池袋駅」を入力したときの取得座標は、それぞれ **315.326m** と **205.715m** 離れていた（同JSON `:14-24`, `:27-38`）。
- したがって、実動作確認の「新宿駅→池袋駅」と実験CSVの「新宿→池袋」は**別のODペアとして扱う**。よって、「CSVはFalseだがアプリはTrue」という差は数値台帳の自己矛盾ではなく、ODが同一でないことで説明できる。
- ただし、実動作時のAPIレスポンスまたは完全なルート形状は保存されていないため、「駅名ODなら必ずリルートする」ことまでを本記録から再現確認したわけではない。

## 1. `rerouted` 値の所在

`bicycle-navi/backend/data/google_comparison.csv:1` のヘッダには `rerouted` がない。新宿→池袋の比較値自体は同CSV `:4` にあるが、リルート真偽は記録していない。

実際の出典は `bicycle-navi/backend/data/verify_v2_analyze_route.csv:4` で、値は `False`。出典の取り違えを防ぐため、分析生データにも「`google_comparison.csv` に列なし」と実値の所在を分けて保存した（`bicycle-navi/backend/data/od_geocode_mismatch.json:7-12`）。

## 2. 座標比較

| 地点 | 実験CSV座標 | 「駅名」のジオコード結果 | 大円距離差 |
|---|---|---|---:|
| 出発地 | 35.6895000, 139.7006000 | 35.6922179, 139.6996037 | **315.326m** |
| 目的地 | 35.7295000, 139.7109000 | 35.7285540, 139.7128585 | **205.715m** |

実験CSV座標の出典は `bicycle-navi/backend/data/od_pairs.csv:4`。ジオコード座標、応答名、距離差は `bicycle-navi/backend/data/od_geocode_mismatch.json:14-24`, `:27-38`。取得時刻と設定は同JSON `:2-6`。

## 3. 方法と判断

- `bicycle-navi/backend/scripts/analyze_od_geocode_mismatch.py` から、アプリ本番の `services.geocoder.geocode` を import して「新宿駅」「池袋駅」を検索した。分析側で別のジオコーダは実装していない。
- 対応先は Nominatim の日本限定、第1候補である（`bicycle-navi/backend/data/od_geocode_mismatch.json:3-6`）。
- 200〜315mの端点差は、異なる道路接続・駅出入口・初期エッジを選びうる大きさである。ルート探索とリルート真偽を比較する場合、ODの双方を一致させなければ同一実験にはならない。

## 4. 証拠ファイル

- 再現スクリプト：`bicycle-navi/backend/scripts/analyze_od_geocode_mismatch.py`
- 取得結果：`bicycle-navi/backend/data/od_geocode_mismatch.json`
