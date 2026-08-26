# 精読ノート：Whiteほか（2000）

**確認日：2026-08-26 ／ 確認方法：出版社ページのアブストラクト・Introductionのみ確認。本文PDFは入手不可。代替として直接の先行報告 Bernstein & Kornhauser (1996) 全17ページを精読**

---

## 1. 書誌情報（一次情報で確定）

Christopher E. White, David Bernstein, Alain L. Kornhauser, “Some map matching algorithms for personal navigation assistants,” *Transportation Research Part C: Emerging Technologies*, vol. 8, issues 1–6, pp. 91–108, 2000. DOI: https://doi.org/10.1016/S0968-090X(00)00026-7

- 出版社書誌・本文入口: https://www.sciencedirect.com/science/article/pii/S0968090X00000267
- Princeton University研究成果ページ: https://collaborate.princeton.edu/en/publications/some-map-matching-algorithms-for-personal-navigation-assistants/
- 査読：Princetonの一次書誌で “Article › peer-review” と明記。

**重要な確認限界：** ScienceDirectはPDF購入または機関アクセスを要求し、操作環境ではCAPTCHAにも遮られた。2000年論文の全18ページは読めていない。したがって、以下では2000年論文について確認できた範囲と、全文を確認できた1996年先行報告を明確に分ける。

代替・直接の先行報告：David Bernstein and Alain Kornhauser, *An Introduction to Map Matching for Personal Navigation Assistants*, New Jersey TIDE Center, August 1996, 17 pages. 全文URL: https://rosap.ntl.bts.gov/view/dot/38257/dot_38257_DS1.pdf

## 2. この論文が実際に主張していること

### 2.1 White et al. (2000) で本文確認できた範囲

出版社ページのIntroductionは、誤差を含む位置情報と誤差を含む地図ネットワークを対応付ける単純なアルゴリズムを論じること、point-to-point、point-to-curve、curve-to-curveを扱い、それぞれで幾何情報のみを用いる場合とトポロジーも用いる場合を検討すると明記する。

また、著者らは目的を「決定的な比較評価」ではなく、単純なアルゴリズムを記述し、限定的な試験と理論的考察から実務上の成否を検討することだと限定している。したがって、3分類を扱うこと自体は確認できるが、全実験結果・最終推奨は本文未取得のためWhite (2000)について断定しない。

### 2.2 Bernstein & Kornhauser (1996) 全文で確認した3分類

1996年報告は、同じ問題設定と3分類を明示的に扱う。

- **point-to-point**（pp.4–5）：推定位置とネットワーク上のノード・形状点とのユークリッド距離を求め、最も近い点に対応するアークを選ぶ。実装が容易で高速だが、形状点の配置・密度に結果が依存し、形状点の多いアークほど選ばれやすい。
- **point-to-curve**（pp.5–7）：推定位置から候補アークを構成する各線分までの最小距離を計算し、最小のアークを選ぶ。線分への垂線が線分外に落ちる場合は端点距離も比較する。
- **curve-to-curve**（pp.8–10）：複数の連続位置から構成した折れ線と候補アークの曲線を同時に比較する。距離尺度、外れ値、曲線長、端点順序に依存し、単純な実装でも望ましくない結果が生じうる。

### 2.3 point-to-curve の評価

1996年報告はpoint-to-curveを単純な改善策として推奨していない。過去の位置情報を使わないため曖昧さを解けず、並行する二つのアークからほぼ等距離の点列では、わずかな誤差で対応先が交互に切り替わる「不安定性」を図4・図5で示す（pp.7–8）。

> “point-to-curve matching does not make use of ‘historical’ information.”（p.7）

> “it can be quite ‘unstable’.”（p.7）

結論（pp.13–14）は、point-to-pointとpoint-to-curveは位置・ネットワークに誤差がある場合にうまく機能しにくく、curve-to-curveとトポロジー情報の組合せが重要だと述べる。ただしこれは1996年報告の結論であり、2000年論文の実験結果を直接確認したものではない。

### 2.4 本研究との問題設定の差

White (2000) と1996年報告が扱うのは、時間順に得られるGPS等の推定位置から、利用者が現に走行している道路を同定する**軌跡マップマッチング**である。本研究は、既に与えられたルート上の離散判定点に最も近いOSM wayを割り当て、法規違反判定へ渡す前処理である。

したがって、距離計算の幾何的分類を借りることはできるが、Whiteらの性能評価や最終推奨を本研究の修正効果へ直接外挿することはできない。特に、本研究の点列に時間履歴・進行方向・トポロジーを使っていないなら、Whiteらが問題視する不安定性は依然として残りうる。

## 3. ⚠️ 精読で発見した問題点

### 3.1 現在の引用意図は半分一致、半分ズレる

「point-to-point / point-to-curve / curve-to-curveという分類」は、White (2000) のIntroductionおよび1996年先行報告本文で確認できる。一方、「point-to-curveへ移行することで問題を解消した」という文をWhiteの結論で裏付けることはできない。直接の先行報告は、point-to-curve固有の履歴欠如と不安定性を明示している。

### 3.2 「垂直距離」と「point-to-curve」は完全な同義ではない

point-to-curveは、点から有限線分への最小距離であり、垂線の足が線分外なら端点距離を使う。実装が無限直線への垂線距離だけを使っている場合、White/Bernsteinの定義と一致しない。コード側の距離関数を確認する必要がある。

### 3.3 本研究は古典的マップマッチングそのものではない

GPS誤差を含む走行軌跡の道路同定と、ルート上のサンプル点をOSM wayへ対応付ける処理は目的・入力・評価基準が異なる。「相当する」という限定付きの類比なら妥当だが、「Whiteの手法を適用した」と書くのは過大である。

### 3.4 White (2000) 全文未取得

全文を読めていない以上、2000年論文が最終的にどのアルゴリズムを最高評価したか、実験表の数値、1996年報告からの変更点は未確認である。検索結果や二次文献から補って断定してはならない。

## 4. 現在の引用意図との照合

| 引用意図 | 判定 | 根拠 |
|---|---|---|
| 3手法に大別される | **概ね一致** | White (2000) Introductionと1996年全文で3語が一致。ただし2000年論文が「網羅的・排他的分類」と主張したかは全文未確認。 |
| ノードベース最近傍はpoint-to-pointに相当 | **一致（類比として）** | ノード・形状点への最近傍という定義と合う。 |
| 垂直距離ベースはpoint-to-curveに相当 | **条件付き一致** | 有限way形状への最小距離（線分外なら端点を含む）を計算している場合に限る。 |
| point-to-curve移行で問題が一般に改善する | **ズレあり** | 1996年報告は履歴欠如と不安定性を指摘し、単純point-to-curveを一般的解決策としていない。 |
| 本研究の7件解消をWhiteが裏付ける | **不一致** | 本研究固有の実測結果であり、異なる問題設定のWhiteから導けない。 |

## 5. 正確に引用するための文案

### 5.1 分類だけを引用する安全な文案

> 位置点と道路ネットワークを幾何的に対応付ける古典的手法として、point-to-point、point-to-curve、curve-to-curveが論じられている[W]。本研究の当初実装は判定点とway上のノードとの最近傍距離を用いる点でpoint-to-pointに相当し、修正後は判定点とwayを構成する有限線分との最小距離を用いる点でpoint-to-curveに相当する。

### 5.2 Whiteらの注意点も含める文案

> ただし、point-to-curveは過去の位置履歴を用いないため、近接する並行道路等では対応先が不安定になりうることが指摘されている[W]。本研究でも、候補way間の距離差が2 m未満の場合を判定不能として扱い、単純な最近傍選択の限界を明示した。

### 5.3 本研究の実測結果として分離する文案

> 本研究の評価対象では、ノードまでの距離を用いた最近傍way選択に起因する7件の誤対応が、way形状への最小距離へ変更することで解消した。これは本研究の対象データにおける実測結果であり、point-to-curveが一般に誤対応を解消することを意味しない。

### 5.4 2000年論文未取得の間の推奨引用

2000年論文の本文確認が終わるまでは、3分類とpoint-to-curveの弱点について、全文確認済みの Bernstein & Kornhauser (1996) を直接引用するのが最も安全である。White (2000) は書誌のみ参考文献候補として保持し、本文を入手後に差し替える。

## 6. 芋づる式に辿れる文献

1. **最優先：Bernstein & Kornhauser (1996)** — 3分類、定義、point-to-curveの不安定性を全文で直接確認済み。
2. **高：Quddus, Ochieng, Noland (2007), “Current map-matching algorithms for transport applications: State-of-the art and future research directions”** — 分類の後年サーベイ候補。本文入手後に定義と評価を確認する。
3. **高：Greenfeld (2002), “Matching GPS observations to locations on a digital map”** — 幾何・トポロジーを組み合わせる古典的手法。
4. **中：Newson & Krumm (2009), “Hidden Markov map matching through noise and sparseness”** — 軌跡・履歴を使う現代的手法との対比。

## 7. 本研究への具体的な影響

1. 「3分類」は維持できるが、**Whiteがpoint-to-curveを推奨した**という書き方は避ける。
2. 7件解消はWhiteの外部知見ではなく、本研究の実験結果として独立に報告する。
3. 2 m未満を判定不能とする設計は、point-to-curveの不安定性に対する本研究の安全策として位置づけられる。ただし2 mという閾値自体の妥当性は別途実験で根拠づける。
4. 実装が有限線分への最小距離か、無限直線への垂線距離かを確認する。前者でなければ用語を修正する。
5. 「マップマッチング手法を適用」ではなく、「古典的マップマッチングの幾何分類に照らすと～に相当」と限定する。

## 8. 未解決の確認事項

- [ ] 青山学院大学のScienceDirect機関認証からWhite et al. (2000) PDF全18ページを取得し、§2以降・実験表・結論を精読する。
- [ ] 2000年論文と1996年報告の差分（追加アルゴリズム、実験結果、最終推奨）を確認する。
- [ ] 本研究の距離関数が有限線分への最小距離を実装しているかコードで確認する。
- [ ] Quddus et al. (2007) 本文を取得し、3分類の後年整理と用語の定着を確認する。

### 入手を試した経路

- ScienceDirect DOI・PIIページ：書誌、Abstract、Introductionは表示。PDFは購入または機関認証が必要。
- Codex内ブラウザ：ScienceDirectでCAPTCHAが表示され、回避は行っていない。
- Princeton University著者・研究成果ページ：書誌とAbstractのみで本文ファイルなし。
- 著者名＋題名＋DOIによる公開PDF検索：合法的な公開版を発見できず。
- ResearchGate：Request PDF表示のみで、公開本文なし。
- CiteSeerX / Semantic Scholar相当の検索：本文PDFへの到達なし。
- ROSA P：2000年論文ではなく、直接の先行報告 Bernstein & Kornhauser (1996) は全文閲覧可能。

## 9. 改訂履歴（2026-08-26・バッチ3追記）

### 9.1 B-1：不安定性記述の出典帰属を確定

バッチ2で「全文確認できた直接の先行報告」とした文書は、次の文献である。

> David Bernstein and Alain Kornhauser, *An Introduction to Map Matching for Personal Navigation Assistants*, New Jersey TIDE Center, August 1996, 17 pages.

- 確認ファイル：`dot_38257_DS1.pdf`
- URL：https://rosap.ntl.bts.gov/view/dot/38257/dot_38257_DS1.pdf
- 該当ページ：**p.7**（不安定性の本文）、pp.7–8（Figure 4・5の並行arc例）
- 帰属：White et al. (2000)を引用する二次文献ではない。White論文の共著者2名による1996年の直接の先行報告であり、当該箇所はBernstein & Kornhauser自身の説明である。

したがって、p.7の “does not make use of ‘historical’ information” および “quite ‘unstable’” を**White et al. (2000)に帰属させてはならない**。正しい帰属先はBernstein & Kornhauser (1996)である。

### 9.2 B-2：White本体の取得状況

今回もWhite et al. (2000)の出版社版全文は取得できなかった。

- ScienceDirect：Abstract・Introductionは閲覧可。PDFは `Access through your organization` / purchase表示。
- アプリ内ブラウザ：大学機関認証セッションなし。利用可能ブラウザ一覧にも認証済みChrome等はなかった。
- 題名・DOI・PIIによる公開PDF検索：合法的な公開全文を発見できず。
- Princeton著者書誌：書誌・Abstractのみ。

よって、White (2000)について確認済みなのはAbstractとIntroductionだけであり、実験・結論は引き続き未確認である。

### 9.3 B-3：Quddus et al. (2007)全文による補強

Quddus et al. (2007)の著者機関リポジトリ由来PDF全30ページを確認した。

1. 論文全体はgeometric / topological / probabilistic / advancedの4群に分類する。point-to-point / point-to-curve / curve-to-curveはgeometric群内の3方式である（PDF pp.5–7）。
2. point-to-curveはpoint-to-pointより良い結果を与える一方、道路密度が高い都市部では非常に不安定で、最近傍リンクが正しいとは限らないとする（PDF p.6）。
3. Y字分岐で候補への垂直距離がほぼ等しい場合の誤選択を指摘する（PDF p.15）。
4. 複数中心線で車道を表す地図が誤リンク同定を生むというMeng (2006)の初期結果を紹介する（PDF p.17）。

詳細は `精読ノート_Quddusほか2007.md` に分離した。

### 9.4 B-4：最終的な帰属方針

| 記述 | 帰属先 | 方針 |
|---|---|---|
| point-to-point / point-to-curve / curve-to-curve | White (2000) Introduction、Bernstein & Kornhauser (1996)、Quddus et al. (2007) | 「マップマッチング全体の3分類」ではなく「古典的な幾何方式の3分類」と限定する。 |
| point-to-curveの履歴欠如・並行arc間の振動 | Bernstein & Kornhauser (1996), p.7 | Whiteに帰属させない。 |
| 道路密度下の不安定性・最近傍リンク≠正リンク | Quddus et al. (2007), PDF p.6 | 全文確認済みサーベイ自身の記述として引用可能。 |
| 本研究の7件解消 | 本研究 | 文献に帰属させず、自データでの実測結果とする。 |

### 9.5 実装確認により解決した事項

`backend/services/overpass.py` の実装は無限直線への垂線距離ではなく、射影係数を`[0,1]`へクランプした**有限線分への最小距離**である。複数セグメントwayでは全セグメントの最小値を採用する。よって§3.2・§8に残した実装定義の確認事項は解決した。詳細は `実装確認_最近傍way選択.md` を参照。

### 9.6 改訂後の推奨文案

> 幾何情報に基づく古典的なマップマッチングとして、point-to-point、point-to-curve、curve-to-curveが論じられている[BK96; W00; Q07]。本研究の当初実装は判定点とway上のノードとの距離を用いる点でpoint-to-pointに相当し、修正後はwayを構成する有限線分への最小距離を用いる点でpoint-to-curveに相当する。ただし単純point-to-curveは近接道路で不安定になりうるため[BK96; Q07]、候補距離差が2m未満の地点を判定不能として区別した。7件の誤対応解消は、本研究対象データにおける実測結果である。

## 10. 改訂履歴（2026-08-26・バッチ4：White本体全文取得）

### 10.1 確認方法の訂正

ワークスペース内で出版社版PDFを確認できたため、§1の「全文未取得」および§9.2を更新する。旧記述は調査履歴として残すが、**本節を最新の判定とする**。

- 確認ファイル：`取得PDF/Some Map Matching Algorithms for Personal Navigation Assistants.pdf`
- SHA-256：`F29780B0FE164324DA8F9D01EBA8CC523D8547C823F80883C401629D6D921FCB`
- 確認範囲：PDF全18ページ（誌面 pp.91–108）
- 論文種別：査読付きjournal article

### 10.2 問題設定と三区分

White et al.は、誤差を含む離散的な位置推定値列を、道路網を表すpiecewise linear arcsへ対応付ける問題を扱う（pp.92–93）。道路網と現実の街路の一対一対応を仮定し、各時点で走行中のstreetを同定する。point-to-point、point-to-curve、curve-to-curveを実際に本文で論じるが、Quddus et al.（2007）のような包括的分類ではなく、この論文が検討する古典的幾何方式である。

- **point-to-point**（pp.94–95）：推定点からネットワークのノード／shape pointまでの距離を比較する。デジタイズ方法とshape point密度に敏感である。
- **point-to-curve**（pp.95–98）：候補arcを構成する全有限線分について点からの最小距離を計算し、その最小値をarcの距離とする。垂線の足が線分外なら端点距離を使う。
- **curve-to-curve**（pp.99–101）：複数時点の推定点列から成る曲線と候補arcの曲線を比較する。

### 10.3 point-to-curveの弱点と帰属訂正

White et al.自身がAlgorithm 1について、履歴情報を使わず、近接する並行arc間で対応先が振動しうると説明する（p.96、Figs.5–6）。

> “it does not make use of ‘historical’ information” and “it can be quite ‘unstable’” (p.96)

したがって、§9.1の「この記述をWhiteに帰属させてはならない」は**誤りであり撤回する**。Bernstein & Kornhauser（1996, p.7）にも同趣旨の先行記述があるが、White et al.（2000, p.96）へ直接帰属させても孫引きにはならない。

### 10.4 実験結果と推奨の有無

1997年TIGER/Line道路網と、ニュージャージー州内4ルートを1Hzで記録したGPSデータで4アルゴリズムを比較する（pp.101–102）。Table 2の正解率は次のとおりである。

| Route | Algorithm 1 | Algorithm 2 | Algorithm 3 | Algorithm 4 |
|---|---:|---:|---:|---:|
| 1 | .534 | .677 | .618 | .608 |
| 2 | .663 | .736 | .855 | .681 |
| 3 | .661 | .707 | .858 | .664 |
| 4 | .617 | .726 | .771 | .687 |

Algorithm 2はpoint-to-curveにheadingを加え、Algorithm 3はさらに接続性、Algorithm 4はcurve-to-curveを使う。複雑な3・4が単純な2を一貫して上回らず、著者らは4ルートだけでは強い結論を出せないと明記する（pp.102–105）。誤りは交差点周辺に集中し、結論は追加データ・追加アルゴリズム・交差点処理の研究を求める（pp.105–108）。**point-to-curveへの単純移行を一般的解決策として推奨する論文ではない。**

### 10.5 本研究との問題設定の差：map matchingかconflationか

White et al.の入力はGPS等による実世界の位置推定点列、出力は単一道路網内のarcである。地図同士の道路オブジェクト対応付けは実施しない。ロボティクスのlocal mapとglobal mapの照合に似るという脚注はあるが、graph-to-graphのconflationを扱う根拠にはならない。

本研究は、別ルータが生成した経路上の判定点／形状をOSM wayへ対応付けるため、通常の走行軌跡マップマッチングと完全には同じでない。**幾何距離方式の類推としてWhite/Quddusを引用するのは妥当だが、問題設定そのものはcross-dataset road-network matching / conflationに近い**。conflationと位置付ける場合は、その分野の原典を別途精読する必要がある。

### 10.6 改訂後の帰属方針

| 記述 | 主な帰属先 | 理由 |
|---|---|---|
| 幾何方式内の三区分 | Quddus et al. (2007)、補助的にWhite et al. (2000) | Quddusは査読付きサーベイ、Whiteは原型の実験論文。 |
| 履歴を使わないpoint-to-curveの振動 | White et al. (2000), p.96 | 本文で直接確認済み。Bernstein & Kornhauser (1996), p.7は先行技術報告として補助。 |
| 高密度道路で最近傍linkが不安定 | Quddus et al. (2007), PDF p.6 | サーベイ本文で直接確認済み。 |
| 本研究の7件解消 | 本研究 | 対象データ上の固有の実測結果であり、外部文献に帰属させない。 |

### 10.7 改訂後の引用文案

> 古典的な幾何ベースのマップマッチングには、point-to-point、point-to-curve、curve-to-curveがある[White et al., 2000; Quddus et al., 2007]。本研究の当初実装はway上のノードとの最近傍距離を用いる点でpoint-to-pointに相当し、修正後はwayを構成する有限線分への最小距離を用いる点でpoint-to-curveに相当する。ただしpoint-to-curveも近接する並行道路では対応先が振動しうるため[White et al., 2000, p.96]、候補距離差が2m未満の地点を判定不能として区別した。7件の誤対応解消は本研究データ上の実測結果である。
