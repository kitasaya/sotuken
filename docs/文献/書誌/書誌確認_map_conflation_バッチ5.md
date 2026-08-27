# 書誌確認：map conflation / road-network matching（バッチ5）

**確認日：2026-08-26**

## 1. 本文を確認できた文献

### Hackeloeer et al.（2013）

A. Hackeloeer, K. Klasing, J. M. Krisp, and L. Meng, “Comparison of Point Matching Techniques for Road Network Matching,” *The International Archives of the Photogrammetry, Remote Sensing and Spatial Information Sciences*, XL-2/W1, pp.87–92, 2013. DOI: 10.5194/isprsarchives-XL-2-W1-87-2013.

- 確認経路：著者公開のCC BY全文
- 個別ノート：`docs/文献/精読ノート/精読ノート_Hackeloeerほか2013.md`

### Volz（2006）

Steffen Volz, “An Iterative Approach for Matching Multiple Representations of Street Data,” *The International Archives of the Photogrammetry, Remote Sensing and Spatial Information Sciences*, XXXVI-2/W40, pp.101–110, 2006.

- 確認経路：ResearchGateの著者公開全文テキスト、ISPRSアーカイブの一次書誌
- 個別ノート：`docs/文献/精読ノート/精読ノート_Volz2006.md`

### Yang et al.（2013）

Bisheng Yang, Yunfei Zhang, and Xuechen Luan, “A Probabilistic Relaxation Approach for Matching Road Networks,” *International Journal of Geographical Information Science*, 27(2), pp.319–338, 2013. DOI: 10.1080/13658816.2012.683486.

- 確認経路：ResearchGate公開全文、武漢大学の著者所属ページ
- 個別ノート：`docs/文献/精読ノート/精読ノート_Yangほか2013.md`
- 出版社サイトへの自動アクセス・出版社PDF取得は行っていない。

## 2. 書誌・要旨のみ確認し、本文未取得の文献

### Ruiz et al.（2011）

Juan J. Ruiz, F. Javier Ariza, Manuel A. Ureña, and Elidia B. Blázquez, “Digital Map Conflation: A Review of the Process and a Proposal for Classification,” *International Journal of Geographical Information Science*, 25(9), pp.1439–1466, 2011. DOI: 10.1080/13658816.2010.519707.

- 試した経路：題名・DOIによる一般検索、著者名による公開版検索、大学・機関リポジトリ検索
- 結果：書誌と要旨のみ。合法的な公開全文を発見できず、内容の精読・引用はしていない。

### Xavier et al.（2016）

Emerson M. A. Xavier, Francisco J. Ariza-López, and Manuel A. Ureña-Cámara, “A Survey of Measures and Methods for Matching Geospatial Vector Datasets,” *ACM Computing Surveys*, 49(2), Article 39, pp.1–34, 2016. DOI: 10.1145/2963147.

- 試した経路：題名・DOIによる一般検索、著者・機関リポジトリ検索、公開プレビュー検索
- 結果：書誌、要旨、序論の公開プレビューのみ。全文未取得のため個別精読ノートは作成しない。

### relaxation labeling 系の題名整理

指定例の “Relaxation Labeling Matching for Large Vector Road Network Datasets” と完全一致する一次書誌は発見できなかった。一方、公開全文を取得できたYang et al.（2013）は probabilistic relaxation によりOSMと専門道路網を対応づける直接関連文献であり、上記のとおり個別精読した。また、“Relaxation-Based Point Feature Matching for Vector Map Conflation” は書誌・要旨のみで全文未取得のため、内容は生成しない。

## 3. 暫定的な分野位置づけ

本文を確認できた2文献に限れば、道路ネットワーク対応づけは、単一の最近傍距離ではなく、幾何、接続関係、方向・角度、意味属性を組み合わせる問題として扱われる。対応品質・類似度を数値で保存する発想も存在する。

ただし本研究は、2つの完全な道路ネットワークを統合するのではなく、外部ルートの離散点をOSM wayへ局所対応づける。このため、**road-network matching / conflation と問題構造を共有するが、その標準問題の一部を簡略化した特殊ケース**と記述するのが妥当である。

## 改訂履歴

- 2026-08-26：全文確認済み文献と書誌・要旨のみの文献を分離。
- 2026-08-26：本研究をconflationそのものと断定せず、問題構造を共有する特殊ケースと整理。
- 2026-08-26：未取得文献の内容を生成しない方針を維持。
