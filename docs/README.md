# 卒業論文執筆用ドキュメント入口

測定は2026-08-30に再凍結した。測定値の正は `docs/測定結果_凍結版.md` だけであり、数値台帳やRESEARCHへ同じ測定値を重複記載しない。現行再測定はGit tag `measurement-freeze-2`、旧凍結はtag `measurement-freeze-20260828`から復元できる。

## 論文執筆時に参照するファイルはこれだけ

1. `卒論構成.md` — 研究全体の現在地と論旨
2. `docs/測定仕様_凍結版.md` — 実験環境、固定入力、Google取得時点、OD座標
3. `docs/測定結果_凍結版.md` — 8指標の唯一の正
4. `docs/検証記録/検証記録_現行12点.md` — 現行システムの12点の違反を人力で確認したもの
5. `docs/文献/文献リスト_統合版.md` — 採用文献一覧
6. `docs/文献/精読ノート/` — 本文を読めた文献の確認記録

アンケート執筆時だけ `docs/アンケート/` を追加参照する。


## 測定データの読み方

- 人が読む正：`docs/測定結果_凍結版.md`
- 機械可読の正：`bicycle-navi/backend/data/measurement_freeze_20260828.csv`
- OD座標：`bicycle-navi/backend/data/measurement_freeze_od_20260828.csv`
- 固定入力：`google_routes_input.csv`、`od_pairs.csv`、`experiment_settings.json`、`kanto-260801.osm.pbf`
- 削減前の再測定中間出力：tag `measurement-freeze-20260828`
- 閉ループ修正後の再測定・A/B比較：tag `measurement-freeze-2`

## 配置ルール

- 現在値は上記の正へ集約し、同じ数値を別文書へ手入力で複製しない。
- 人による標識・画像確認は `docs/検証記録/` に残す。
- 文献本文を確認した記録は `docs/文献/精読ノート/` に残す。
- 取得したポリライン、PBF、PDF、実行ログは一次データとして削除しない。
- 氏名・メール・学籍番号等を含むアンケート元データはGit管理しない。匿名化済みデータだけを `bicycle-navi/backend/data/survey/` に置く。
- 判定ロジックを測定整理のために変更しない。

## PDFの例外

取得PDF 14件は `.gitignore` の `*.pdf` によりGit管理外である。既定方針どおり、元の場所から移動・改名せず、Git管理下へ追加しない。

## 履歴と復元

削除対象・理由は `docs/整理_削減記録.md` に1ファイル1行で記録する。削除はすべて `git rm` で行い、履歴とtagから復元できる。迷ったファイルは削除せず `docs/台帳/要判断リスト.md` に記録する。ただし、ユーザーが削除した場合にはその限りではない。
