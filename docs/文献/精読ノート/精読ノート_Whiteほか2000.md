# 精読ノート：Whiteほか（2000）

**確認日：2026-08-26 ／ 確認方法：出版社版PDF全18ページ（誌面 pp.91–108）を全文精読**

## 1. 書誌情報（一次情報で確定）

Christopher E. White, David Bernstein, Alain L. Kornhauser, “Some map matching algorithms for personal navigation assistants,” *Transportation Research Part C: Emerging Technologies*, vol.8, issues 1–6, pp.91–108, 2000. DOI: https://doi.org/10.1016/S0968-090X(00)00026-7

- 確認ファイル：`取得PDF/Some Map Matching Algorithms for Personal Navigation Assistants.pdf`
- SHA-256：`F29780B0FE164324DA8F9D01EBA8CC523D8547C823F80883C401629D6D921FCB`
- 出版社ページ：https://www.sciencedirect.com/science/article/pii/S0968090X00000267
- 論文種別：査読付きjournal article

## 2. この論文が実際に主張していること

### 2.1 問題設定

誤差を含む離散的な位置推定値列を、道路網を表すpiecewise linear arcsへ対応付け、各時点で走行中のstreetを同定する問題を扱う（pp.92–93）。入力はGPS等の走行軌跡であり、本研究の「既知ルート上の判定点をOSM wayへ割り当てる処理」とは目的・入力が異なる。

### 2.2 幾何方式

- **point-to-point**（pp.94–95）：推定点からネットワーク上のnode／shape pointまでの距離を比較する。デジタイズ方法とshape point密度に敏感である。
- **point-to-curve**（pp.95–98）：候補arcを構成する全ての有限線分について点からの最小距離を計算する。垂線の足が線分外なら端点距離を用いる。
- **curve-to-curve**（pp.99–101）：複数時点の推定点列から成る曲線と候補arcの曲線を比較する。

これらは同論文が検討する古典的な幾何方式である。Quddus et al.（2007）の包括的整理では、マップマッチング全体をgeometric / topological / probabilistic / advancedの4群に分け、上記3方式はgeometric群内に置かれる。

### 2.3 point-to-curveの評価

White et al.自身が、単純point-to-curveは履歴情報を使わず、近接する並行arc間で対応先が振動しうると述べる（p.96、Figs.5–6）。

> “it does not make use of ‘historical’ information” and “it can be quite ‘unstable’”（p.96）

したがって、同論文はpoint-to-curveへの単純移行を一般的解決策として推奨していない。4ルートの実験でも複雑な方式が常に単純方式を上回らず、著者らは強い結論を避け、追加データと交差点処理の研究を求めている（pp.102–108）。

### 2.4 実験結果

1997年TIGER/Line道路網と、ニュージャージー州内4ルートを1 Hzで記録したGPSデータで4アルゴリズムを比較した（pp.101–102）。Table 2の正解率は次のとおり。

| Route | Algorithm 1 | Algorithm 2 | Algorithm 3 | Algorithm 4 |
|---|---:|---:|---:|---:|
| 1 | .534 | .677 | .618 | .608 |
| 2 | .663 | .736 | .855 | .681 |
| 3 | .661 | .707 | .858 | .664 |
| 4 | .617 | .726 | .771 | .687 |

Algorithm 2はpoint-to-curveにheadingを加え、Algorithm 3はさらに接続性、Algorithm 4はcurve-to-curveを使う。誤りは交差点周辺に集中した（pp.102–105）。

## 3. ⚠️ 精読で発見した問題点

1. 3方式はマップマッチング全体の包括分類ではなく、古典的な幾何方式内の下位分類と書く。
2. point-to-curve固有の履歴欠如と並行道路での振動は、White et al.（2000）p.96へ直接帰属できる。
3. 本研究の処理は通常の走行軌跡マップマッチングと同一ではない。幾何演算の類比として引用し、Whiteの性能評価を直接外挿しない。
4. 本研究の実装は、射影係数を`[0,1]`へクランプした有限線分への最小距離を全セグメントについて計算するため、point-to-curveの幾何定義に整合する（詳細：`docs/分析/実装確認_最近傍way選択.md`）。

## 4. 現在の引用意図との照合

| 引用意図 | 判定 | 現在の扱い |
|---|---|---|
| 3方式の分類 | **条件付き一致** | 「古典的な幾何方式内の3方式」と限定し、包括分類はQuddusの4群を用いる。 |
| ノード最近傍はpoint-to-pointに相当 | **一致（類比）** | 本研究の旧実装の演算に対応する。 |
| 有限線分距離はpoint-to-curveに相当 | **一致（類比）** | 実装確認済み。 |
| point-to-curveなら一般に問題が解消する | **不一致** | White自身が不安定性を指摘する。 |
| 旧監査B群7事例の改善をWhiteが裏付ける | **不一致** | 本研究固有の追跡結果。現行方式では5事例が非検出化し、2事例が残存した。 |

## 5. 正確に引用するための文案

> Quddus et al.（2007）はマップマッチングをgeometric、topological、probabilistic、advancedの4群に整理しており、White et al.（2000）が扱うpoint-to-point、point-to-curve、curve-to-curveは、このうち古典的な幾何方式内の区分である。本研究の旧実装はway上のノードへの距離を用いる点でpoint-to-pointに、現行実装はwayを構成する有限線分への最小距離を用いる点でpoint-to-curveに相当する。ただし単純point-to-curveも近接する並行道路では対応先が振動しうる[White et al., 2000, p.96]。

> 旧方式で検出しストリートビュー画像を目視確認した18検出点の診断では、主たる原因として分離wayの誤対応を7事例で同定した。現行方式への変更後、5事例は非検出となったが2事例は残存した。この結果は本研究データ上の実測であり、White et al.（2000）の一般的結論には帰属させない。

## 6. 芋づる式に辿れる文献

1. Quddus, Ochieng & Noland（2007）— 4群分類とgeometric方式の限界を整理した査読付きサーベイ。
2. Bernstein & Kornhauser（1996）— White論文の直接の先行技術報告。p.7に同じ不安定性の記述がある。
3. Greenfeld（2002）— 幾何・トポロジーを組み合わせる古典的手法。
4. Newson & Krumm（2009）— 履歴を確率的に扱うHMM方式との対比候補。

## 7. 本研究への具体的な影響

- 三区分の位置づけを「全手法の大別」から「幾何方式内の区分」へ修正する。
- 有限線分距離への変更効果は5/7事例の非検出化、2/7事例の残存として報告し、一般化しない。
- 候補距離差が小さい地点を判定不能とする設計は、point-to-curveの既知の不安定性に対する本研究上の扱いとして説明できる。ただし閾値値そのものは本研究の実測に基づける。

## 8. 未解決の確認事項

- [ ] 本研究の処理をcross-dataset road-network matching / conflationとして位置づける場合は、その分野の原典を別途精読する。
- [ ] Whiteの4ルート実験を本論文で数値引用する必要が生じた場合、Table 2のアルゴリズム定義と条件を本文で併記する。

## 改訂履歴

- 2026-08-26：出版社版PDF全18ページを精読し、全文未取得という暫定状態を解消。
- 2026-08-26：不安定性の帰属をWhite et al.（2000）p.96へ確定し、Quddusの4群分類との関係を明記。
- 2026-08-26：本研究の追跡結果を「B群5事例非検出・2事例残存」に更新し、文献の一般論と分離。
