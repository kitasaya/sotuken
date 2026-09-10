# J1：距離閾値の根拠づけと踏切のクラスタリング

作成日：2026-09-09（F・G を 2026-09-10 追記）
入力：`backend/data/r2_point_features_detail_j1.csv`（117行）と
`backend/data/google_routes_input.csv`。Overpass取得時点は全節 2026-08-01T20:21:21Z で固定。
再実行：`python scripts/analyze_j1_thresholds.py`
（A・Bのみなら `--offline`、G だけなら `--skip-f`、表の書き出しは `--emit-md PATH`）

A〜C・G は既存の明細CSVから集計しただけで、抽出はやり直していない。
F は経路と鉄道線の幾何交差を新たに計算している。いずれも判定器・経路探索には触れていない。

**結論の先出し：踏切の確定数は F-4 の7箇所である。** B の8箇所（距離基準）は
閾値依存であり、うち1箇所は経路が線路と並走しているだけの偽陽性だった。

---

## A. `dist_to_route_m` の分布と 3m 閾値の妥当性

### A-1. 累積件数

| 閾値 | 一時停止 (n=79) | 踏切_車道 (n=21) | 踏切_歩道自転車道 (n=17) |
|---:|---:|---:|---:|
| ≤ 2m | 6 (7.6%) | 10 (47.6%) | 5 (29.4%) |
| ≤ 3m | 6 (7.6%) | 11 (52.4%) | 5 (29.4%) |
| ≤ 5m | 11 (13.9%) | 16 (76.2%) | 10 (58.8%) |
| ≤ 8m | 28 (35.4%) | 19 (90.5%) | 13 (76.5%) |
| ≤ 10m | 48 (60.8%) | 19 (90.5%) | 13 (76.5%) |
| ≤ 15m | 69 (87.3%) | 20 (95.2%) | 15 (88.2%) |
| ≤ 20m | 79 (100%) | 21 (100%) | 17 (100%) |

### A-2. 一時停止：3m は分布から決まる。ただし「二峰」ではない

一時停止79件の距離を昇順に並べると、**1.34m と 3.55m の間に 2.21m の空白**がある。これは
この分布の中で最大の空白であり、2番目（1.31m・15.58〜16.89m の間）の1.7倍である。
すなわち **1.4m〜3.5m のどこに閾値を置いても、選ばれるノードは同じ6件**になる。
この意味で 3m は恣意的ではなく、分布から決まった線として書ける。

ただし **二峰ではない。** 近接群（0.51〜1.34m の6件）は孤立しているが、遠い側は
3.55m から 19.12m まで連続的に分布し、最頻は 8.0〜9.5m 帯（18件）である。谷が
あるのは「近接群とそれ以外の間」だけで、「経路上の群」と「交差道路側の群」が
それぞれ峰を作って谷で分かれているわけではない。**距離だけで両者を分離できる保証は
なく、3m より外にも経路上の標識が混じっている可能性は排除できない。**
現に 3.55m〜5m 帯に5件ある（うち residential 3件）。

正しい書き方：「近接群が 1.34m 以下に孤立しており、次の値まで 2.21m の空白がある。
この空白の内側に閾値を置いた」。「分布が二峰に分かれた」とは書かない。

### A-3. 親way種別との対応

| parent_way_highways | n | ≤3m | 3-10m | 10-20m |
|---|---:|---:|---:|---:|
| residential | 40 | 2 | 22 | 16 |
| unclassified | 21 | 3 | 13 | 5 |
| service | 9 | 0 | 6 | 3 |
| tertiary | 5 | 0 | 0 | 5 |
| cycleway | 2 | 1 | 1 | 0 |
| unclassified;unclassified | 2 | 0 | 0 | 2 |

`service`（駐車場・私道の出口）9件は全件 3m 超で、閾値によって正しく落ちている。
`tertiary` 5件も全件 10m 超で、幹線に面した側道側の標識である。
残る73件の大半は `residential` / `unclassified` の生活道路側に立つ標識で、
**経路が通る道ではなく、そこへ出てくる側の車両に向いている。**

### A-4. 踏切：3m の根拠は無い。閾値依存である

踏切_車道の距離を昇順に並べると空白の上位は 7.12m（11.06〜18.18m）・3.29m
（7.77〜11.06m）・2.90m（3.98〜6.88m）で、**3m 付近に空白は無い。** それどころか
3.04m・3.51m・3.83m に3件が並んでおり（立川→国分寺、いずれも経路が走る tertiary 上）、
3m はこの塊を割る位置にある。踏切_歩道自転車道も 1.43m と 4.24m の間に 2.81m の
空白があり、こちらの自然な線は 3m ではなく 1.5〜4.2m のどこかである。

**3m は一時停止の分布から決めた線であり、踏切に適用する根拠は無い。**
踏切に使う場合は閾値依存であることを明示する（B-3 の感度表）。

---

## B. 踏切の物理箇所単位への統合

### B-1. クラスタ閾値は 25m。ギャップ分布から決まる

on-route（3m以内）16ノードについて、同一ペア内の隣接ノード間 `route_position_m` 差を
昇順に並べると次のようになる。

```
1.1 / 1.2 / 2.6 / 3.5 / 3.6 / 3.7 / 4.3 / 5.3 / 109.3 / 326.4 / 410.2 / 560.3 / 1545.9 (m)
```

**5.3m と 109.3m の間で20倍に飛ぶ。** この間のどこに閾値を置いても結果は同一なので、
中間の 25m を採用した。閾値の選択が結果を動かさないことが確認できている。

### B-2. 統合結果（on-route 3m・クラスタ 25m）

16ノード → **8箇所**（検出ペア 3/15）。

| ペア | ノード | 箇所 | 内訳 |
|---|---:|---:|---|
| 千葉→幕張本郷 | 9 | 5 | 車道のみ3・歩道のみ2 |
| 自由が丘→等々力 | 6 | 2 | 車道＋歩道1・車道のみ1 |
| 立川→国分寺 | 1 | 1 | 車道のみ1 |

内訳合計：車道のみ 5箇所 / 歩道のみ 2箇所 / 車道＋歩道 1箇所。

**自由が丘→等々力の6ノードは2箇所にまとまる。**

- 箇所1：位置 85.2〜90.1m（幅4.9m）、4ノード、車道＋歩道
  （node 12987814290, 283343309, 12987814291, 2574536592）
- 箇所2：位置 500.3〜504.0m（幅3.7m）、2ノード、車道のみ
  （node 2574536563, 620534668）

いずれも幅5m以内に収まっており、1か所の踏切が線路本数と way 分割で複数ノードに
割れているという想定と一致する。ここが妥当なので他ペアの統合も同じ根拠で扱える。

### B-3. on-route 判定距離に対する感度（クラスタ閾値25m固定）

| on-route 閾値 | ノード数 | 箇所数 | 検出ペア数 |
|---:|---:|---:|---:|
| 2m | 15 | 7 | 2/15 |
| 3m | 16 | 8 | 3/15 |
| 5m | 26 | 10 | 3/15 |
| 8m | 32 | 12 | 4/15 |
| 10m | 32 | 12 | 4/15 |
| 20m | 38 | 13 | 5/15 |

**箇所数は 7〜13 の範囲で閾値に依存する。** 一時停止（1.4〜3.5m のどこでも6件で不変）と
違い、踏切の箇所数は単一の数字として確定しない。表に入れるなら閾値を明記し、
この感度表を併記する必要がある。

### B-4. 3m で落ちる踏切の実例

- **自由が丘→等々力・位置685〜690m**：`railway=level_crossing` 2ノード（3.53m・6.88m、
  いずれも経路が走る tertiary 上）。**3m 基準ではこの踏切が丸ごと落ちる。**
  5m 基準なら1箇所として拾える。
- **立川→国分寺・位置1333〜1339m**：tertiary 上の4ノード（3.04・3.51・3.83m と 3m以内1件）。
  1件が閾値内に残るため箇所数は変わらないが、ノード数は 4→1 になる。
- **千葉→幕張本郷・位置7934.8m**：residential 上の2ノード（7.24m・11.06m）。3m でも
  5m でも落ちる。Google polyline の位置ずれか、経路に接しない並行道路の踏切かは
  この距離だけでは決められない。

### B-5. タグの付け方の乱れ

千葉→幕張本郷・位置10331〜10333m では、`railway=level_crossing`（車道用）が
`highway=footway` の way 上のノードに付いている（node 6925822213, 6925822212）。
逆に同じ場所の `railway=crossing`（歩道用）は `residential;footway` に付いている
（node 2046049354）。**タグ値と親wayの種別が対応していない。** 車道／歩道の区別を
`railway` のタグ値だけで行うと、この地点では誤る。B-2 で「歩道のみ」と分類された
2箇所には、この乱れの影響が入っている可能性がある。

---

## C. 一時停止 on-route 6件の方向適用判定

判定方法：親way の当該ノードにおける forward ベクトル（前後ノードの変位）と、
経路の `travel_vector`（前後30mの変位）の偏差角を取り、90度未満なら経路は way の
forward 方向に進行しているとみなす。これを `direction` タグと突き合わせる。
角度は way の選択には使っていない（診断情報としてのみ使用。`RESEARCH.md` 21.11節の制約に整合）。

| node_id | ペア | direction | stop | 親way (highway) | 偏差角 | 経路の進行 | 判定 |
|---|---|---|---|---|---:|---|---|
| 11162524128 | 新宿→池袋 | forward | none | 155224464 (unclassified) | 0.1° | forward | 適用 |
| 8784582360 | 高円寺→中野 | backward | none | 49175544 (unclassified) | 144.6° | backward | 適用 |
| 12438301581 | 浦和→さいたま新都心 | forward | none | 100400218 (residential) | 5.4° | forward | 適用 |
| 12438301580 | 浦和→さいたま新都心 | backward | none | 100400218 (residential) | 4.4° | forward | **適用外** |
| 12293389477 | 千葉→幕張本郷 | forward | none | 1328740627 (cycleway) | 61.6° | forward | 適用 |
| 12623149769 | 千葉→幕張本郷 | forward | none | 140120618 (unclassified) | 33.9° | forward | 適用 |

ストリートビューURLは `r2_point_features_detail_j1.csv` の `street_view_url` 列を参照。

**判定内訳：適用 5件 / 適用外 1件。** 適用5件が属するペアは 新宿→池袋・高円寺→中野・
浦和→さいたま新都心・千葉→幕張本郷 の**4ペア**（検出ゼロ 11/15）。

適用外の1件（node 12438301580）は、同じ way 100400218 上で 12438301581 と約10m 離れて
向かい合う対の標識である。経路は forward 方向に進むため、backward 側の標識は
背にして通過する。**同一交差点の対向側の標識を二重に数えないことが、この判定の実効。**

### C-1. `direction` タグ無しの扱い

**該当0件。** on-route 6件だけでなく、**コリドー20m内の一時停止79件すべてに
`direction` タグが付いていた。** したがって「タグ無しを両方向として数えると過大評価に
なる」という懸念は、本データでは発生していない。他地域・他時点のデータに広げる際は
再確認が必要である（本結果はこのデータでそうだったという観測であり、
OSM 全般の性質ではない）。

`stop` タグ（`all` / `minor`）は6件すべてで未指定だった。優先関係が
タグから読めないため、全方向一時停止か従道路側のみかはこのデータからは判別できない。

### C-2. 偏差角が大きい2件

node 12293389477（61.6°）と node 12623149769（33.9°）は偏差角が大きい。
これは way が当該ノード付近で曲がっているか、`travel_vector` の参照範囲（前後30m）に
カーブが含まれるためである。いずれも90度未満で forward 判定に揺らぎは無いが、
90度付近の値が出た場合は判定不能として扱う必要がある（本データでは該当なし）。

---

## D. 限界

1. **3m という on-route 閾値は一時停止の分布からしか根拠づけられていない。** 踏切に
   適用した場合の箇所数は 7〜13 で閾値に依存する（B-3）。
2. **距離だけでは「経路上の標識」と「交差道路側の標識」を完全には分離できない。**
   一時停止の距離分布は近接群の外側で連続しており、谷は近接群の直後にしか無い（A-2）。
3. **`railway` のタグ値と親wayの種別が対応しない地点がある**（B-5）。車道／歩道の
   区別をタグ値だけで行うと誤る。
4. **方向適用判定は OSM の `direction` タグを正としている。** タグ自体の正しさは
   検証していない。ストリートビューでの確認は人手作業として残る。
5. 本節の数値はすべて Overpass 取得時点 2026-08-01T20:21:21Z の OSM に対するものであり、
   現地の設置状況ではない。収録率そのものの検証は `stop_survey_segments_j1.csv` を
   使った人手確認に依存する。
6. **踏切については F で幾何交差による確定を行った。** B の8箇所は距離閾値に依存し
   （7〜13箇所）、うち1箇所は経路が線路と並走しているだけの偽陽性だった（F-3 ②）。
   論文表には B の8箇所ではなく **F-4 の7箇所** を使う。ただし道路側wayの
   bridge/layer タグ欠落による平面誤判定は残る（F-3 ①）。
7. G の方向適用判定は、親wayが経路と直交する場合ほとんど情報を持たない（G-1）。
   3m 超の73件に対する「適用45/適用外34」を取りこぼし件数として読んではいけない。

---

## F. 経路と鉄道線の幾何交差

B までの「踏切ノードが経路の近くにあるか」という距離基準をやめ、**経路ポリラインと
鉄道線の幾何交差**で踏切を確定させる。手順は次のとおり。

1. コリドー20m内の `way["railway"~"^(rail|light_rail|narrow_gauge)$"]` を取得する。
   `tram`（路面電車）は踏切ではないため交差判定の対象にしないが、件数は記録する。
2. 経路の各セグメントと鉄道wayの各セグメントの交点を局所平面近似で求める。
3. 道路側way（点-曲線垂直距離マッチで同定）と鉄道wayの `bridge` / `tunnel` / `layer`
   を見て、平面交差・立体交差・不明に分ける。判断がつかない組み合わせは不明に置き、
   平面には寄せない。
4. 各平面交差について、半径30m以内の `railway=level_crossing` / `crossing` ノードを
   探し、実距離を記録する。

道路側wayの同定に進行方向は使っていない（`RESEARCH.md` 21.11節の制約）。

### F-0. ペア別の交差数

| ペア | 鉄道way | tram way | 交差点 | 平面 | 立体 | 不明 |
|---|---:|---:|---:|---:|---:|---:|
| 渋谷→新宿 | 8 | 0 | 4 | 0 | 4 | 0 |
| 新宿→池袋 | 0 | 5 | 0 | 0 | 0 | 0 |
| 東京→渋谷 | 4 | 0 | 2 | 0 | 2 | 0 |
| 品川→東京 | 61 | 0 | 26 | 0 | 26 | 0 |
| 渋谷→六本木 | 2 | 0 | 0 | 0 | 0 | 0 |
| 下北沢→三軒茶屋 | 16 | 0 | 2 | 0 | 2 | 0 |
| 高円寺→中野 | 36 | 0 | 11 | 0 | 11 | 0 |
| 荻窪→阿佐ヶ谷 | 9 | 0 | 0 | 0 | 0 | 0 |
| 自由が丘→等々力 | 5 | 0 | 4 | 4 | 0 | 0 |
| 浦和→さいたま新都心 | 7 | 0 | 0 | 0 | 0 | 0 |
| 吉祥寺→三鷹 | 4 | 0 | 4 | 0 | 4 | 0 |
| 立川→国分寺 | 4 | 0 | 4 | 2 | 2 | 0 |
| 横浜→みなとみらい | 1 | 0 | 1 | 0 | 1 | 0 |
| 川崎→武蔵小杉 | 22 | 0 | 8 | 0 | 8 | 0 |
| 千葉→幕張本郷 | 23 | 0 | 22 | 14 | 8 | 0 |
| **合計** | | 5 | 88 | 20 | 68 | 0 |

**tram は新宿→池袋にのみ5way存在し、rail系は0だった。** B で 新宿→池袋 に1件あった
`railway=level_crossing`（18.18m）は、都電の軌道に付いたノードで、rail系の踏切ではない。
距離基準ではこれを区別できなかった。

立体交差68件のうち26件は品川→東京、11件は高円寺→中野で、いずれも線路が高架または
掘割の区間を経路が横切っている。**不明は0件**で、bridge/tunnel/layer のいずれかで
全件が判定できた。

### F-1. 平面交差の明細

| ペア | 位置(m) | 鉄道way | 道路way (highway) | 最寄り踏切ノード | 距離(m) | railway | Street View |
|---|---:|---:|---|---|---:|---|---|
| 自由が丘→等々力 | 85.9 | 114207283 | 25533292 (unclassified) | 283343309 | 0.88 | level_crossing | [SV](https://www.google.com/maps/@?api=1&map_action=pano&viewpoint=35.6071907,139.6681885) |
| 自由が丘→等々力 | 89.8 | 251287359 | 25533292 (unclassified) | 2574536592 | 0.61 | level_crossing | [SV](https://www.google.com/maps/@?api=1&map_action=pano&viewpoint=35.6071559,139.6681836) |
| 自由が丘→等々力 | 500.2 | 251287359 | 48903694 (residential) | 2574536563 | 0.41 | level_crossing | [SV](https://www.google.com/maps/@?api=1&map_action=pano&viewpoint=35.6060388,139.6649541) |
| 自由が丘→等々力 | 504.0 | 114207283 | 48903694 (residential) | 620534668 | 0.09 | level_crossing | [SV](https://www.google.com/maps/@?api=1&map_action=pano&viewpoint=35.6060664,139.6649292) |
| 立川→国分寺 | 1334.3 | 24398923 | 138427587 (tertiary) | 1517858200 | 3.15 | level_crossing | [SV](https://www.google.com/maps/@?api=1&map_action=pano&viewpoint=35.6915395,139.4246884) |
| 立川→国分寺 | 1338.0 | 399827726 | 138427587 (tertiary) | 4032122672 | 2.53 | level_crossing | [SV](https://www.google.com/maps/@?api=1&map_action=pano&viewpoint=35.6915419,139.4247296) |
| 千葉→幕張本郷 | 5977.7 | 194646016 | 142225531 (footway) | **なし** | - | - | [SV](https://www.google.com/maps/@?api=1&map_action=pano&viewpoint=35.6474233,140.0853732) |
| 千葉→幕張本郷 | 5981.4 | 194468876 | 142225531 (footway) | **なし** | - | - | [SV](https://www.google.com/maps/@?api=1&map_action=pano&viewpoint=35.6474004,140.0853435) |
| 千葉→幕張本郷 | 5986.5 | 194468871 | 142225531 (footway) | **なし** | - | - | [SV](https://www.google.com/maps/@?api=1&map_action=pano&viewpoint=35.6473678,140.0853030) |
| 千葉→幕張本郷 | 5990.4 | 22064363 | 142225531 (footway) | **なし** | - | - | [SV](https://www.google.com/maps/@?api=1&map_action=pano&viewpoint=35.6473433,140.0852731) |
| 千葉→幕張本郷 | 6140.7 | 22728981 | 142225531 (footway) | **なし** | - | - | [SV](https://www.google.com/maps/@?api=1&map_action=pano&viewpoint=35.6463814,140.0841034) |
| 千葉→幕張本郷 | 6144.8 | 22728993 | 142225531 (footway) | **なし** | - | - | [SV](https://www.google.com/maps/@?api=1&map_action=pano&viewpoint=35.6463553,140.0840716) |
| 千葉→幕張本郷 | 7772.6 | 22728993 | 139653293 (unclassified) | 2697832127 | 0.39 | level_crossing | [SV](https://www.google.com/maps/@?api=1&map_action=pano&viewpoint=35.6516032,140.0694287) |
| 千葉→幕張本郷 | 7776.8 | 22728981 | 139653293 (unclassified) | 2697832130 | 0.69 | level_crossing | [SV](https://www.google.com/maps/@?api=1&map_action=pano&viewpoint=35.6516364,140.0694522) |
| 千葉→幕張本郷 | 8103.3 | 1456766562 | 312441870 (footway) | 2686744906 | 0.23 | crossing | [SV](https://www.google.com/maps/@?api=1&map_action=pano&viewpoint=35.6526863,140.0662608) |
| 千葉→幕張本郷 | 8106.9 | 1456766561 | 312441870 (footway) | 2686744905 | 0.14 | crossing | [SV](https://www.google.com/maps/@?api=1&map_action=pano&viewpoint=35.6526575,140.0662431) |
| 千葉→幕張本郷 | 8216.2 | 1456766561 | 1410024945 (tertiary) | 2697832135 | 0.15 | level_crossing | [SV](https://www.google.com/maps/@?api=1&map_action=pano&viewpoint=35.6528973,140.0655160) |
| 千葉→幕張本郷 | 8219.7 | 1456766562 | 1410024945 (tertiary) | 2697832139 | 0.33 | level_crossing | [SV](https://www.google.com/maps/@?api=1&map_action=pano&viewpoint=35.6529269,140.0655295) |
| 千葉→幕張本郷 | 9765.4 | 22823373 | 142222817 (residential) | 2672501042 | 0.41 | level_crossing | [SV](https://www.google.com/maps/@?api=1&map_action=pano&viewpoint=35.6594269,140.0564790) |
| 千葉→幕張本郷 | 9770.9 | 22728983 | 142222817 (residential) | 2672501045 | 0.02 | level_crossing | [SV](https://www.google.com/maps/@?api=1&map_action=pano&viewpoint=35.6594493,140.0564246) |

### F-2. 道路側 highway による分類（診断用）

| 道路側 highway | 平面交差数 |
|---|---:|
| footway | 8 |
| unclassified | 4 |
| residential | 4 |
| tertiary | 4 |

分類は `railway` のタグ値ではなく、**交差した道路側wayの `highway`** で行っている。
根拠は B-5 に記録した実例である。千葉→幕張本郷・位置10331m では
`railway=level_crossing`（本来は車道用）が `highway=footway` の way 上のノードに
付いていた。`railway` のタグ値は車道／歩道の区別として信頼できない。
なお本分類は診断用であり、論文表では踏切の総箇所数のみを使う想定である。

| ペア | F の平面交差(箇所) | B の箇所 | 対応 |
|---|---:|---:|---|
| 自由が丘→等々力 | 2 | 2 | 一致2 |
| 立川→国分寺 | 1 | 1 | 一致1 |
| 千葉→幕張本郷 | 6 | 5 | 一致4 |
- F にあって B に無い: 2件
  - 千葉→幕張本郷 位置5984.0m
  - 千葉→幕張本郷 位置6142.8m
- B にあって F に無い: 1件
  - 千葉→幕張本郷 位置10331.2m

差分3件はいずれも原因を特定した。

**① F にあって B に無い：千葉→幕張本郷 位置5977〜5990m・6140〜6144m（計6交差）**

経路は鉄道線を6本横切っているが、**30m以内に踏切ノードが1件も無い**。道路側wayは
いずれも `142225531`（`highway=footway`、bridge/tunnel/layer すべて未指定）で、
周囲25m以内には `1545996530`（tertiary・タグ無し、6.73m）と
`62062786`（東関東自動車道・`bridge=yes` `layer=1`、21.8m）がある。千葉駅構内の
線路群を横切る地点で、踏切ノードが1件も無いことと合わせると、**実体は立体交差だが
道路側wayに bridge/layer が付いていないため平面と判定された**とみるのが妥当である。
本節の判定規則「両側とも layer 未指定かつ bridge/tunnel 無しなら平面」が、
タグ欠落をそのまま平面に化けさせる例になっている。**踏切の確定数からは外す。**

**② B にあって F に無い：千葉→幕張本郷 位置10331.2m**

経路と鉄道線の最短距離を全鉄道wayについて計算すると、京成千葉線 way 1456766575 まで
**3.59m**、総武緩行線 way 194646032 まで 9.07m で、いずれも交差していない。
**経路は線路と並走しているだけである。** それにもかかわらず B が踏切ノードを拾ったのは、
線路を渡る歩道の踏切ノード（node 2046049354、`railway=crossing`）が経路から 0.19m の
位置にあったためである。**距離基準は「横切る」と「並走する」を区別できない。**
これは B の偽陽性であり、F が正しい。

### F-4. 確定した踏切数

平面交差20件のうち、30m以内に踏切ノードがあるのは14件で、対応距離は最大3.15m
（立川→国分寺）である。**幾何交差と踏切ノードの両方が一致した14交差を25mでまとめると
7箇所**になる。

| ペア | 確定した踏切 |
|---|---:|
| 自由が丘→等々力 | 2 |
| 立川→国分寺 | 1 |
| 千葉→幕張本郷 | 4 |
| **合計** | **7箇所（検出ペア 3/15・検出ゼロ 12/15）** |

B の8箇所から、②の偽陽性1箇所を除いた数と一致する。

**この7という数字は on-route 距離閾値に依存しない。** B の8箇所は閾値2〜20mで
7〜13箇所と揺れたが、F は幾何交差そのものを見ているため 3m / 5m といった線引きを
必要としない。踏切ノードの対応づけに使った30mも、実測が最大3.15mなので
半径の取り方で結果は変わらない。**論文表に入れるなら B の8箇所ではなく、
この7箇所を使うべきである。**

ただし①のとおり、**タグ欠落による平面誤判定は残る**。7箇所は「幾何交差と踏切ノードの
両方が揃った数」であり、OSMに踏切ノードが無い実在の踏切があれば取りこぼす。

---

## G. 一時停止コリドー全件の方向適用判定（診断）

C の方向適用判定を、on-route 6件ではなくコリドー20m内の79件全件に広げた。
目的は 3m 閾値の外に、経路に適用される向きの標識が残っていないかの確認である。

**この判定は診断にのみ使う。** way の選択・候補の絞り込みには一切用いていない
（測定対象が進行方向そのものであるため、方向で候補を選ぶと循環論法になる。
`RESEARCH.md` 21.11節）。最終判断は目視で行う前提で、コードは候補を出すところまでとする。

### G-1. 判定結果と、その解釈の限界

判定内訳は **適用45件・適用外34件**、うち 3m 超で「適用」と判定されたのは40件
（3-5m 3件 / 5-10m 22件 / 10-20m 15件）だった。

**しかしこの40件をそのまま取りこぼし候補として扱ってはいけない。** 方向適用判定は
「経路がその標識の親way上を走っている」ことを前提にしている。親wayが経路と直交する
交差道路である場合、親wayの forward ベクトルと `travel_vector` の偏差角は90度付近に
なり、forward か backward かは実質的に偶然で決まる。

| 群 | n | 偏差角の中央値 | 90度±15度 | 90度±15〜45度 | 90度から45度超 |
|---|---:|---:|---:|---:|---:|
| 3m以内 | 6 | 33.9度 | 0 | 1 | 5 |
| 3m超 | 73 | 80.4度 | 34 | 29 | 10 |

3m以内の6件は偏差角が90度から大きく離れており（5件が45度超）、親wayが経路と
おおむね平行＝経路がその道を走っていることを示す。判定は意味を持つ。
3m超の73件は中央値80.4度で、34件が90度±15度に入る。**親wayは交差道路であり、
この群の「適用45/適用外34」という分割にはほとんど情報が無い。**

### G-2. 目視確認すべき候補

3m超のうち、親wayが経路とおおむね平行（偏差角が90度から45度超離れている）ものは
10件で、そのうち「適用」は **7件** である。取りこぼし候補として目視確認する価値が
あるのはこの7件に限られる。

| dist(m) | ペア | node_id | direction | 偏差角 | 親way (highway) |
|---:|---|---|---|---:|---|
| 5.63 | 下北沢→三軒茶屋 | 12728441319 | forward | 39.5 | 241993539 (service) |
| 6.52 | 東京→渋谷 | 2138385693 | forward | 32.2 | 93185600 (unclassified) |
| 7.96 | 東京→渋谷 | 8663938876 | forward | 21.7 | 372005557 (residential) |
| 8.47 | 渋谷→六本木 | 8667731925 | forward | 36.7 | 265033759 (residential) |
| 9.34 | 新宿→池袋 | 8329351985 | forward | 13.8 | 635337704 (unclassified) |
| 12.60 | 吉祥寺→三鷹 | 13366744340 | forward | 41.8 | 205040880 (service) |
| 15.50 | 下北沢→三軒茶屋 | 12695787374 | forward | 19.8 | 1370782200 (residential) |

うち2件は親wayが `service`（駐車場・私道）で、経路がその上を走っているとは考えにくい。
実質の候補は5件である。Street View URL は下の全件表を参照。

**この7件が仮にすべて経路上の標識だったとしても、6件が7件増えて13件になるだけで、
79件との差は埋まらない。** A-2 で述べたとおり、3m 閾値は近接群を取るための線であり、
残る73件の大半は交差道路側の標識である、という結論は変わらない。

### G-3. 全79件（`dist_to_route_m` 昇順）

| dist(m) | ペア | node_id | direction | 偏差角 | 経路の進行 | 判定 | 親way (highway) | Street View |
|---:|---|---|---|---:|---|---|---|---|
| 0.51 | 高円寺→中野 | 8784582360 | backward | 144.6 | backward | 適用 | 49175544 (unclassified) | [SV](https://www.google.com/maps/@?api=1&map_action=pano&viewpoint=35.7054323,139.6652607) |
| 0.72 | 千葉→幕張本郷 | 12293389477 | forward | 61.6 | forward | 適用 | 1328740627 (cycleway) | [SV](https://www.google.com/maps/@?api=1&map_action=pano&viewpoint=35.6562671,140.0639761) |
| 0.91 | 新宿→池袋 | 11162524128 | forward | 0.1 | forward | 適用 | 155224464 (unclassified) | [SV](https://www.google.com/maps/@?api=1&map_action=pano&viewpoint=35.6912512,139.7023123) |
| 1.04 | 浦和→さいたま新都心 | 12438301580 | backward | 4.4 | forward | 適用外 | 100400218 (residential) | [SV](https://www.google.com/maps/@?api=1&map_action=pano&viewpoint=35.8691237,139.6501731) |
| 1.12 | 浦和→さいたま新都心 | 12438301581 | forward | 5.4 | forward | 適用 | 100400218 (residential) | [SV](https://www.google.com/maps/@?api=1&map_action=pano&viewpoint=35.8690468,139.6502325) |
| 1.34 | 千葉→幕張本郷 | 12623149769 | forward | 33.9 | forward | 適用 | 140120618 (unclassified) | [SV](https://www.google.com/maps/@?api=1&map_action=pano&viewpoint=35.6566086,140.0630387) |
| 3.55 | 千葉→幕張本郷 | 12293389478 | backward | 67.7 | forward | 適用外 | 780205366 (cycleway) | [SV](https://www.google.com/maps/@?api=1&map_action=pano&viewpoint=35.6563531,140.0640086) |
| 4.11 | 自由が丘→等々力 | 11759381736 | forward | 80.0 | forward | 適用 | 49730487 (residential) | [SV](https://www.google.com/maps/@?api=1&map_action=pano&viewpoint=35.6069361,139.6568612) |
| 4.27 | 自由が丘→等々力 | 12514350338 | forward | 156.6 | backward | 適用外 | 1352547328 (service) | [SV](https://www.google.com/maps/@?api=1&map_action=pano&viewpoint=35.6085265,139.6490737) |
| 4.44 | 渋谷→六本木 | 12323845982 | backward | 118.7 | backward | 適用 | 109231692 (residential) | [SV](https://www.google.com/maps/@?api=1&map_action=pano&viewpoint=35.6572863,139.7060461) |
| 4.98 | 浦和→さいたま新都心 | 7196414489 | forward | 54.6 | forward | 適用 | 95385874 (unclassified) | [SV](https://www.google.com/maps/@?api=1&map_action=pano&viewpoint=35.8852337,139.6400533) |
| 5.42 | 自由が丘→等々力 | 12273548008 | backward | 97.4 | backward | 適用 | 48959133 (residential) | [SV](https://www.google.com/maps/@?api=1&map_action=pano&viewpoint=35.6079531,139.6525426) |
| 5.63 | 下北沢→三軒茶屋 | 12728441319 | forward | 39.5 | forward | 適用 | 241993539 (service) | [SV](https://www.google.com/maps/@?api=1&map_action=pano&viewpoint=35.6610189,139.6692735) |
| 5.81 | 渋谷→新宿 | 1522968060 | forward | 148.9 | backward | 適用外 | 138917006 (residential) | [SV](https://www.google.com/maps/@?api=1&map_action=pano&viewpoint=35.6671974,139.7042934) |
| 5.85 | 下北沢→三軒茶屋 | 12695846666 | backward | 78.9 | forward | 適用外 | 88519913 (residential) | [SV](https://www.google.com/maps/@?api=1&map_action=pano&viewpoint=35.6546179,139.6672941) |
| 6.04 | 下北沢→三軒茶屋 | 12692217222 | backward | 93.6 | backward | 適用 | 116433279 (unclassified) | [SV](https://www.google.com/maps/@?api=1&map_action=pano&viewpoint=35.6503911,139.6673971) |
| 6.17 | 東京→渋谷 | 11612834886 | forward | 88.8 | forward | 適用 | 23273635 (unclassified) | [SV](https://www.google.com/maps/@?api=1&map_action=pano&viewpoint=35.6613671,139.7369892) |
| 6.27 | 立川→国分寺 | 13888216707 | forward | 85.1 | forward | 適用 | 161953549 (residential) | [SV](https://www.google.com/maps/@?api=1&map_action=pano&viewpoint=35.6965933,139.4747373) |
| 6.38 | 下北沢→三軒茶屋 | 12695787378 | forward | 107.8 | backward | 適用外 | 217262443 (service) | [SV](https://www.google.com/maps/@?api=1&map_action=pano&viewpoint=35.6453275,139.6704836) |
| 6.52 | 東京→渋谷 | 2138385693 | forward | 32.2 | forward | 適用 | 93185600 (unclassified) | [SV](https://www.google.com/maps/@?api=1&map_action=pano&viewpoint=35.6583492,139.7102518) |
| 6.65 | 下北沢→三軒茶屋 | 12692218768 | forward | 87.0 | forward | 適用 | 48728743 (residential) | [SV](https://www.google.com/maps/@?api=1&map_action=pano&viewpoint=35.6530397,139.6671170) |
| 6.88 | 下北沢→三軒茶屋 | 12695787380 | backward | 80.7 | forward | 適用外 | 27263501 (service) | [SV](https://www.google.com/maps/@?api=1&map_action=pano&viewpoint=35.6440526,139.6709767) |
| 7.22 | 下北沢→三軒茶屋 | 12692218767 | forward | 93.0 | backward | 適用外 | 88527664 (residential) | [SV](https://www.google.com/maps/@?api=1&map_action=pano&viewpoint=35.6530366,139.6672706) |
| 7.39 | 渋谷→新宿 | 8254068911 | forward | 81.0 | forward | 適用 | 170232029 (unclassified) | [SV](https://www.google.com/maps/@?api=1&map_action=pano&viewpoint=35.6893287,139.7022679) |
| 7.67 | 下北沢→三軒茶屋 | 12692218773 | backward | 86.3 | forward | 適用外 | 48728752 (residential) | [SV](https://www.google.com/maps/@?api=1&map_action=pano&viewpoint=35.6522979,139.6673161) |
| 7.77 | 下北沢→三軒茶屋 | 12695787376 | forward | 78.7 | forward | 適用 | 158815335 (service) | [SV](https://www.google.com/maps/@?api=1&map_action=pano&viewpoint=35.6457422,139.6700955) |
| 7.96 | 東京→渋谷 | 8663938876 | forward | 21.7 | forward | 適用 | 372005557 (residential) | [SV](https://www.google.com/maps/@?api=1&map_action=pano&viewpoint=35.6591415,139.7186637) |
| 7.97 | 渋谷→新宿 | 5619189103 | forward | 84.1 | forward | 適用 | 963385012 (residential) | [SV](https://www.google.com/maps/@?api=1&map_action=pano&viewpoint=35.6679387,139.7048237) |
| 8.05 | 渋谷→新宿 | 12285513211 | backward | 102.0 | backward | 適用 | 531362280 (unclassified) | [SV](https://www.google.com/maps/@?api=1&map_action=pano&viewpoint=35.6756503,139.7068022) |
| 8.15 | 下北沢→三軒茶屋 | 12695846667 | forward | 91.6 | backward | 適用外 | 220704411 (residential) | [SV](https://www.google.com/maps/@?api=1&map_action=pano&viewpoint=35.6546366,139.6671407) |
| 8.28 | 川崎→武蔵小杉 | 10270731845 | forward | 113.2 | backward | 適用外 | 159367744 (residential) | [SV](https://www.google.com/maps/@?api=1&map_action=pano&viewpoint=35.5550088,139.6782253) |
| 8.33 | 渋谷→新宿 | 1522967767 | forward | 67.2 | forward | 適用 | 41857281 (residential) | [SV](https://www.google.com/maps/@?api=1&map_action=pano&viewpoint=35.6653396,139.7028611) |
| 8.47 | 渋谷→六本木 | 8667731925 | forward | 36.7 | forward | 適用 | 265033759 (residential) | [SV](https://www.google.com/maps/@?api=1&map_action=pano&viewpoint=35.6591762,139.7148882) |
| 8.47 | 下北沢→三軒茶屋 | 12695787377 | backward | 78.7 | forward | 適用外 | 158815335 (service) | [SV](https://www.google.com/maps/@?api=1&map_action=pano&viewpoint=35.6457960,139.6702637) |
| 8.59 | 下北沢→三軒茶屋 | 12692218775 | backward | 80.6 | forward | 適用外 | 48728749 (residential) | [SV](https://www.google.com/maps/@?api=1&map_action=pano&viewpoint=35.6517222,139.6673578) |
| 8.59 | 川崎→武蔵小杉 | 10297920069 | forward | 78.1 | forward | 適用 | 1058854790 (unclassified) | [SV](https://www.google.com/maps/@?api=1&map_action=pano&viewpoint=35.5448020,139.6890285) |
| 8.61 | 下北沢→三軒茶屋 | 12728441312 | forward | 88.5 | forward | 適用 | 33614319 (unclassified) | [SV](https://www.google.com/maps/@?api=1&map_action=pano&viewpoint=35.6602088,139.6687415) |
| 8.67 | 下北沢→三軒茶屋 | 12692218774 | forward | 80.0 | forward | 適用 | 55114345 (residential) | [SV](https://www.google.com/maps/@?api=1&map_action=pano&viewpoint=35.6516671,139.6671698) |
| 8.78 | 渋谷→六本木 | 8667731924 | forward | 124.7 | backward | 適用外 | 243264888 (residential) | [SV](https://www.google.com/maps/@?api=1&map_action=pano&viewpoint=35.6591855,139.7149565) |
| 8.95 | 渋谷→六本木 | 8594646448 | forward | 68.9 | forward | 適用 | 967220470 (unclassified) | [SV](https://www.google.com/maps/@?api=1&map_action=pano&viewpoint=35.6598627,139.7229005) |
| 9.06 | 下北沢→三軒茶屋 | 12695787372 | backward | 78.1 | forward | 適用外 | 48681453 (residential) | [SV](https://www.google.com/maps/@?api=1&map_action=pano&viewpoint=35.6490164,139.6682231) |
| 9.15 | 川崎→武蔵小杉 | 9269777761 | backward | 77.9 | forward | 適用外 | 221309639 (unclassified) | [SV](https://www.google.com/maps/@?api=1&map_action=pano&viewpoint=35.5578991,139.6761437) |
| 9.18 | 下北沢→三軒茶屋 | 12692218772 | forward | 86.9 | forward | 適用 | 51155716 (residential) | [SV](https://www.google.com/maps/@?api=1&map_action=pano&viewpoint=35.6523028,139.6671293) |
| 9.20 | 新宿→池袋 | 11286893487 | forward | 63.9 | forward | 適用 | 72457420 (unclassified) | [SV](https://www.google.com/maps/@?api=1&map_action=pano&viewpoint=35.6919347,139.7053279) |
| 9.34 | 新宿→池袋 | 8329351985 | forward | 13.8 | forward | 適用 | 635337704 (unclassified) | [SV](https://www.google.com/maps/@?api=1&map_action=pano&viewpoint=35.6913782,139.7015391) |
| 9.37 | 渋谷→六本木 | 1533906714 | forward | 70.8 | forward | 適用 | 139978182 (residential) | [SV](https://www.google.com/maps/@?api=1&map_action=pano&viewpoint=35.6591545,139.7145704) |
| 9.59 | 渋谷→六本木 | 8667731918 | backward | 70.5 | forward | 適用外 | 137630409 (residential) | [SV](https://www.google.com/maps/@?api=1&map_action=pano&viewpoint=35.6593591,139.7167214) |
| 9.96 | 高円寺→中野 | 10035139362 | forward | 94.7 | backward | 適用外 | 1330235066 (unclassified) | [SV](https://www.google.com/maps/@?api=1&map_action=pano&viewpoint=35.7053433,139.6642794) |
| 10.23 | 東京→渋谷 | 13042424432 | backward | 89.4 | forward | 適用外 | 141293465 (residential) | [SV](https://www.google.com/maps/@?api=1&map_action=pano&viewpoint=35.6580156,139.7061248) |
| 10.28 | 渋谷→六本木 | 8667731917 | forward | 125.1 | backward | 適用外 | 935330870 (residential) | [SV](https://www.google.com/maps/@?api=1&map_action=pano&viewpoint=35.6592801,139.7158162) |
| 10.42 | 下北沢→三軒茶屋 | 12692231840 | forward | 74.9 | forward | 適用 | 48671202 (unclassified) | [SV](https://www.google.com/maps/@?api=1&map_action=pano&viewpoint=35.6444306,139.6706319) |
| 10.54 | 川崎→武蔵小杉 | 12229680648 | backward | 55.8 | forward | 適用外 | 1321613838 (service) | [SV](https://www.google.com/maps/@?api=1&map_action=pano&viewpoint=35.5423172,139.6943228) |
| 10.79 | 東京→渋谷 | 12334423109 | forward | 87.2 | forward | 適用 | 176209641 (service) | [SV](https://www.google.com/maps/@?api=1&map_action=pano&viewpoint=35.6608618,139.7276580) |
| 11.11 | 品川→東京 | 1074585156 | forward | 45.4 | forward | 適用 | 60544302 (residential) | [SV](https://www.google.com/maps/@?api=1&map_action=pano&viewpoint=35.6481411,139.7547212) |
| 11.37 | 東京→渋谷 | 13042424433 | forward | 90.6 | backward | 適用外 | 211286456 (unclassified) | [SV](https://www.google.com/maps/@?api=1&map_action=pano&viewpoint=35.6579591,139.7047102) |
| 11.50 | 東京→渋谷 | 12334378300 | backward | 97.2 | backward | 適用 | 122928198 (residential) | [SV](https://www.google.com/maps/@?api=1&map_action=pano&viewpoint=35.6600519,139.7253558) |
| 11.52 | 東京→渋谷 | 8663938894 | backward | 42.7 | forward | 適用外 | 103751727 (residential) | [SV](https://www.google.com/maps/@?api=1&map_action=pano&viewpoint=35.6590311,139.7177425) |
| 11.54 | 品川→東京 | 11295266877 | backward | 96.5 | backward | 適用 | 111594585 (residential) | [SV](https://www.google.com/maps/@?api=1&map_action=pano&viewpoint=35.6511178,139.7536184) |
| 11.81 | 渋谷→六本木 | 8563870271 | backward | 116.0 | backward | 適用 | 141293462 (residential) | [SV](https://www.google.com/maps/@?api=1&map_action=pano&viewpoint=35.6574614,139.7077601) |
| 12.60 | 吉祥寺→三鷹 | 13366744340 | forward | 41.8 | forward | 適用 | 205040880 (service) | [SV](https://www.google.com/maps/@?api=1&map_action=pano&viewpoint=35.7034799,139.5607731) |
| 12.83 | 東京→渋谷 | 8563870267 | forward | 85.0 | forward | 適用 | 141293467 (tertiary) | [SV](https://www.google.com/maps/@?api=1&map_action=pano&viewpoint=35.6580440,139.7070990) |
| 13.33 | 渋谷→六本木 | 8563870269 | backward | 71.1 | forward | 適用外 | 141293467 (tertiary) | [SV](https://www.google.com/maps/@?api=1&map_action=pano&viewpoint=35.6572931,139.7069811) |
| 13.62 | 品川→東京 | 13109512797 | forward | 132.1 | backward | 適用外 | 462907587 (residential) | [SV](https://www.google.com/maps/@?api=1&map_action=pano&viewpoint=35.6486498,139.7541064) |
| 13.82 | 品川→東京 | 10073004133 | forward | 79.2 | forward | 適用 | 600239764 (unclassified) | [SV](https://www.google.com/maps/@?api=1&map_action=pano&viewpoint=35.6609555,139.7554056) |
| 14.01 | 川崎→武蔵小杉 | 9043732236 | forward | 116.5 | backward | 適用外 | 1071867337 (residential) | [SV](https://www.google.com/maps/@?api=1&map_action=pano&viewpoint=35.5527404,139.6801910) |
| 14.02 | 川崎→武蔵小杉 | 9328887768 | forward | 107.9 | backward | 適用外 | 124965707 (tertiary) | [SV](https://www.google.com/maps/@?api=1&map_action=pano&viewpoint=35.5498719,139.6816852) |
| 14.40 | 渋谷→新宿 | 12285513210 | forward | 80.4 | forward | 適用 | 220544944 (residential) | [SV](https://www.google.com/maps/@?api=1&map_action=pano&viewpoint=35.6762327,139.7066619) |
| 14.52 | 渋谷→六本木 | 8350070983 | forward | 53.7 | forward | 適用 | 898573318 (unclassified) | [SV](https://www.google.com/maps/@?api=1&map_action=pano&viewpoint=35.6617015,139.7284790) |
| 14.99 | 渋谷→六本木 | 1509501699 | backward | 68.7 | forward | 適用外 | 137630411 (unclassified) | [SV](https://www.google.com/maps/@?api=1&map_action=pano&viewpoint=35.6591106,139.7135619) |
| 15.27 | 渋谷→六本木 | 8660985202 | forward | 105.5 | backward | 適用外 | 967220472 (residential) | [SV](https://www.google.com/maps/@?api=1&map_action=pano&viewpoint=35.6598965,139.7227656) |
| 15.50 | 下北沢→三軒茶屋 | 12695787374 | forward | 19.8 | forward | 適用 | 1370782200 (residential) | [SV](https://www.google.com/maps/@?api=1&map_action=pano&viewpoint=35.6474931,139.6692720) |
| 15.51 | 東京→渋谷 | 1075233588 | forward | 71.5 | forward | 適用 | 92702343 (unclassified) | [SV](https://www.google.com/maps/@?api=1&map_action=pano&viewpoint=35.6609890,139.7280779) |
| 15.58 | 品川→東京 | 9991674504 | forward | 74.9 | forward | 適用 | 87510459 (unclassified) | [SV](https://www.google.com/maps/@?api=1&map_action=pano&viewpoint=35.6488030,139.7535131) |
| 16.89 | 渋谷→新宿 | 5619189102 | backward | 77.6 | forward | 適用外 | 963385012 (residential) | [SV](https://www.google.com/maps/@?api=1&map_action=pano&viewpoint=35.6679878,139.7047449) |
| 17.10 | 東京→渋谷 | 13288902293 | forward | 47.2 | forward | 適用 | 216895901 (tertiary) | [SV](https://www.google.com/maps/@?api=1&map_action=pano&viewpoint=35.6773607,139.7608360) |
| 17.41 | 新宿→池袋 | 1685099979 | forward | 108.4 | backward | 適用外 | 1128023034 (residential) | [SV](https://www.google.com/maps/@?api=1&map_action=pano&viewpoint=35.7268822,139.7115929) |
| 18.02 | 渋谷→新宿 | 12285513212 | forward | 102.3 | backward | 適用外 | 531362279 (residential) | [SV](https://www.google.com/maps/@?api=1&map_action=pano&viewpoint=35.6756002,139.7065195) |
| 18.03 | 新宿→池袋 | 10313170970 | backward | 72.3 | forward | 適用外 | 192620023 (residential) | [SV](https://www.google.com/maps/@?api=1&map_action=pano&viewpoint=35.7267127,139.7115426) |
| 19.12 | 品川→東京 | 9523456780 | backward | 48.5 | forward | 適用外 | 33847389 (tertiary) | [SV](https://www.google.com/maps/@?api=1&map_action=pano&viewpoint=35.6484018,139.7545873) |

- 判定内訳: 適用 45件 / 適用外 34件
- 3m 超で「適用」: 40件（3-5m 3件 / 5-10m 22件 / 10-20m 15件）
- そのうち親wayが経路とおおむね平行なもの: 7件（G-2）

---

## H. 自システム経路との対照

Google経路に対して行った抽出を、自システムが生成した同一15 O-D の経路にも同一条件で
実行した。スクリプトは `scripts/extract_system_point_features.py`、出力は
`backend/data/j1_system_point_features.csv`（ペア別30行＋合計2行）。

経路は `services.graphhopper.get_route`（profile=bike の素の呼び出し）で取得しており、
`custom_model` / `areas` は発生させていない。バッチ21で確認したとおり現行15 ODでは
条件A/Bの経路は同一なので、これが自システムの提示経路である。抽出結果は
`violations` 配列には入れていない。経路探索・`rerouter.py` には触れていない。

### H-1. 対照表

| 対象法規 | Google経路 | 自システム経路 |
|---|---:|---:|
| 一時停止（3m以内・方向適用） | 5点 ※1 | 20点 ※2 |
| 踏切（幾何交差・クラスタ25m） | 7箇所 | 2箇所 |
| 検出ゼロのペア（一時停止） | 11/15 | 5/15 ※2 |
| 検出ゼロのペア（踏切） | 12/15 | 13/15 |

※2 H-8 の検算により、20点のうち1点は経路が通過していない区間のノードだった。
**区間基準で数え直すと19点・検出ゼロ6/15** になる。

※1 目視確認の結果、5点のうち法的な一時停止義務があるのは4点。残る1点
（node `12293389477`・cycleway上）は標識がなく路面の法定外表示のみだった（H-4）。

**この差を法規遵守の優劣として読んではいけない。** 差の要因は次の2つで、どちらも
経路そのものが異なることによる副次的な差である。

### H-2. 差の要因①：経路が違う

自システムのbikeプロファイルは生活道路を選ぶ傾向があり、Google経路より一時停止標識の
多い道を通る。極端な例が 川崎→武蔵小杉 で、コリドー20m内の一時停止ノードが
自システム36件に対しGoogle 6件、確定した検出も自システム8点でこのペアだけで
合計20点の4割を占める。逆に踏切では 千葉→幕張本郷 が対照的で、Google経路は鉄道線を
22回横切って4箇所の踏切を通るのに対し、自システム経路は別の経路を取り交差2回・踏切0箇所である。
**同じ O-D でも通る道が違えば、経路上の点的規制の数は当然変わる。**

### H-3. 差の要因②：3m 基準の効きが両者で違う

自システム側は経路ジオメトリが OSM のジオメトリそのものなので、経路上のノードは
垂線距離がほぼ0になる（検出20点の実測は 0.01〜2.76m、大半が 0.1m 未満）。
Google の polyline は独立に作られた別のジオメトリなので、同じ「経路上の標識」でも
0.5〜1.3m ずれる（A-2 の近接群6件）。**同じ 3m でも、両者で捕捉率が違う。**

自システム側は GraphHopper の `osm_way_id` detail があるため、この捕捉率を実測できる。

| 段階 | 件数 |
|---|---:|
| コリドー20m内 | 138 |
| うち経路が実際に通る way 上のノード（way_id 一致） | 40 |
| うち垂線距離3m以内 | 33 |
| うち方向適用 | 20 |

**⚠ この 33/40 = 82.5% は誤りである。分母 40 が過大だった。H-8 を参照。**
way_id 一致は「経路がその way を使った」を意味するだけで、「経路がそのノードを
通過した」を意味しない。正しい分母（経路が実際に通過した区間上のノード数）は32で、
**再現率は 32/32 = 100%、取りこぼしは0件**である。Google 側には way_id が無いため
この検算自体ができない点は変わらない。

### H-4. 検出条件の非対称（記録のみ）

自システム側は GraphHopper の `osm_way_id` detail で親wayを直接特定できるのに対し、
Google側は座標逆引き（点-曲線垂直距離マッチ）に依存する。**これは二段階右折における
instruction 有無の非対称と同じ構造である。** way_id を使うのは親wayの特定だけに留め、
判定基準（コリドー20m・垂線距離3m・`direction` との照合・幾何交差・bridge/tunnel/layer）は
両者で同一にしてある。**この非対称を理由に自システム側だけ精度を上げる実装はしていない。**

### H-5. 目視確認で確定した事項（Google側）

- 一時停止5点のうち **4点は標識により法的義務を確認**した。残る1点
  （node `12293389477`、cycleway上）は**標識がなく路面の法定外表示のみ**で、
  法的な一時停止義務は生じない地点だった。**OSM の `highway=stop` が法定外表示に
  付与されている実例**であり、タグの存在が法的義務の存在を意味しないことを示す。
- 踏切7箇所は**全件実在を確認、偽陽性ゼロ**。
- F-3 ① の千葉駅構内 way `142225531` は**跨線橋であることを航空写真で確認**した。
  `bridge` タグ欠落による平面誤判定だったと確定している。

### H-6. 既知の限界：タグ欠落が平面交差に化ける

F の判定規則「両側とも layer 未指定かつ bridge/tunnel 無し → 平面交差」は、
**タグ欠落をそのまま平面に化けさせる。** 自システム側でも同じ現象が3件出ている
（渋谷→新宿・東京→渋谷・渋谷→六本木で各1件、いずれも平面と判定されたが30m以内に
踏切ノードが無い）。Google側の千葉駅構内2箇所は航空写真で跨線橋と確定済みである。

**規則自体は変更しない。** 「layer 未指定を不明扱いにする」と判定不能が大量に増える
だけで精度は上がらない。踏切ノードの有無を併用して確定する現在の運用
（平面かつ踏切ノードありのみを箇所として数える）で、この誤判定は実質的に排除できている。

### H-7. この対照から言えること

**「両者が同程度の数になる」ことは確認できなかった。** 一時停止は自システムが4倍、
踏切はGoogleが3.5倍で、いずれも経路が違うことの帰結である。両者の数字を並べて
どちらが法規に適合しているかを論じることはできない。

言えるのは次の一点である。**自システムが自ら生成した経路上にも、経路選択では
除去できない一時停止19点（H-8 の区間基準）・踏切2箇所が残っている。** 事前排除の対象にできる
一方通行と違い、これらは経路を選び直しても消えない。卒論2-2②「経路が決まっても
違反の有無が決まらない」の裏付けとしては、Google との比較ではなく
**自システム自身の経路に残った数** を使うのが筋が通る。

### H-8. 再現率 33/40 の検算：分母が過大だった

H-3 で「way_id 一致40件」を分母に使ったが、**これは誤りである。** way_id 一致は
「経路がその way を使った」を意味するだけで、「経路がそのノードを通過した」を
意味しない。正しい分母は**経路が実際に通過した区間上のノード数**である。

検算スクリプト：`scripts/verify_j1_stop_recall.py`。GraphHopper の `osm_way_id`
detail（`[from, to, way_id]` の半開区間）に対応する経路座標を way のノード列へ
突き合わせ、経路が触れたノード index の範囲を求めて、当該ノードがその内側かを見る。

#### クロス集計（way_id 一致40件）

| | 区間内 | 区間外 | 計 |
|---|---:|---:|---:|
| 垂線距離3m以内 | **32** | 1 | 33 |
| 垂線距離3m超 | **0** | 7 | 7 |
| 計 | 32 | 8 | 40 |

**3m を超えた7件はすべて区間外だった。** すなわち3m基準による取りこぼしは0件で、
正しい分母32に対して **再現率 32/32 = 100%**。分母40は8件ぶん過大だった。

一方、3m以内でありながら区間外のものが1件ある（後述）。**適合率は 32/33 = 97.0%。**

#### 区間外8件の明細

| node_id | ペア | 垂線距離 | 親way (highway) | way全長 | 走行index範囲 | ノードindex |
|---|---|---:|---|---:|---|---:|
| 9828229739 | 荻窪→阿佐ヶ谷 | 2.76m | 1334040961 (unclassified) | 402.7m | 3〜21 | 2 |
| 8254068912 | 新宿→池袋 | 3.65m | 39436592 (unclassified) | 139.1m | 0〜4 | 5 |
| 11759381665 | 自由が丘→等々力 | 4.55m | 49730356 (residential) | 325.9m | 5〜8 | 4 |
| 11759381667 | 自由が丘→等々力 | 4.95m | 48584923 (residential) | 383.5m | 0〜1 | 2 |
| 12754084338 | 川崎→武蔵小杉 | 5.56m | 31887481 (unclassified) | 481.2m | 0〜4 | 5 |
| 11759381738 | 自由が丘→等々力 | 5.77m | 48584922 (residential) | 1006.8m | 6〜20 | 21 |
| 12273548013 | 自由が丘→等々力 | 6.64m | 37707136 (residential) | 1553.5m | 18〜21 | 17 |
| 8254068912 | 渋谷→新宿 | 13.33m | 39436592 (unclassified) | 139.1m | 0〜2 | 5 |

8行だが node は7個（`8254068912` が2ペアに現れる）。**8件すべて、走行区間の境界の
すぐ外側にある**（7件は境界から1ノード、1件は3ノード）。経路がその way を使いながら、
当該ノードまで走らずに手前で曲がった、という形である。Street View URL は
実行ログに出力される。

#### 垂線距離による裏づけ

- 区間内32件の垂線距離：**0.01〜0.60m**
- 区間外8件の垂線距離：**2.76〜13.33m**

**両群は重ならず、0.60m と 2.76m の間に空白がある。** 経路ジオメトリが OSM の
ジオメトリそのものである以上、通過したノードは経路線上（ほぼ0m）に乗る、という
想定と一致する。

#### 検算方法の限界

走行 index 範囲は、GraphHopper が返した座標と way のノードを 1m 以内で突き合わせて
求めている。GraphHopper は返却ジオメトリを簡略化するため、照合できたノード数は
範囲の幅より少ない（例：範囲6〜20に対し照合5ノード）。したがって**この範囲は
走行区間の下限であり、境界から1ノード外側という判定は下限に依存する。**
ただし上記のとおり垂線距離が 0.60m と 2.76m で分離しているため、8件が区間外という
結論は距離側からも独立に裏づけられている。

#### 集計への影響

3m以内かつ区間外だった1件（node `9828229739`・荻窪→阿佐ヶ谷・2.76m）は、H-1 の
自システム一時停止20点に含まれていた。**区間基準で数え直すと19点**になり、
荻窪→阿佐ヶ谷は検出0となるため**検出ゼロのペアは 5/15 → 6/15** になる。
踏切は幾何交差で確定しており、この検算の影響を受けない。

| 対象法規 | Google経路 | 自システム経路（way_id一致基準） | 自システム経路（区間基準） |
|---|---:|---:|---:|
| 一時停止 | 5点（法的義務4点） | 20点 | **19点** |
| 検出ゼロのペア（一時停止） | 11/15 | 5/15 | **6/15** |

#### Google側について言えること

自システム側では、3m基準の取りこぼしは0件だった。これは**経路ジオメトリが OSM の
ジオメトリそのものである場合**の結果である。Google の polyline は独立に作られた
別のジオメトリであり、同じ保証は成り立たない。**Google側の取りこぼしは依然として
測定不能である**（way_id が無く、経路が通過した区間を確定できないため）。
Google側について書けるのは「ジオメトリ誤差の分だけ取りこぼしうる」という
限定的な言い方までで、その量は本データからは求められない。
