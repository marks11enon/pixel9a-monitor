# pixel9a-monitor

Google ストアの「Google Pixel 9a［認定整備済品］」の在庫を GitHub Actions で約5分ごとに確認し、
購入できる構成が出たら ntfy でスマホに通知します。PC の常時起動は不要です。

ページは読むだけで、カート追加・購入・フォーム送信は一切しません。

## 仕組み

1. `.github/workflows/monitor.yml` が5分ごと（GitHub の都合で遅延・抜けあり）に起動
2. `monitor.py` が Playwright でページを毎回新しく開き、構成（色×容量）ごとに次をすべて満たすか確認
   - 色と容量が選択状態になる / その状態でカートボタンが有効
   - ページ内の SKU データと schema.org がともに在庫あり / 表示価格とデータ上の価格が一致
3. `run.py` が `state.json` と比べ、新しく買えるようになった構成があるときだけ通知
4. `state.json` は内容が変わったときだけ github-actions[bot] がコミット（確認時刻だけの変化ではコミットしない）

## 通知の種類

| タイトル | 送る条件 |
|---|---|
| 【入荷】Pixel 9a 整備済品（優先度 high） | 前回通知に無い構成が購入可能になった（初入荷・再入荷・構成追加） |
| 【監視の問題】 | 確認不可（unknown）が24時間以上続いた（1回だけ） |
| 【テスト通知】 | 手動実行で `test_notify` を true にしたとき |

在庫なし・確認不可・同じ構成の継続では通知しません。取得失敗などでもワークフローは「成功」で終わります
（GitHub の失敗メールが5分ごとに届くのを防ぐため）。異常は【監視の問題】で分かります。

## ntfy の購読

トピック名は GitHub Secret `NTFY_TOPIC` にだけ保存しています（このリポジトリには書いていません）。
iPhone の ntfy アプリ → 「+」→ トピック名を入力（サーバーは既定の ntfy.sh）→ Subscribe。

### トピック名を控え損ねたとき

Secret の値は後から読み出せません。新しいトピック名を作って登録し直してください。

```
gh secret set NTFY_TOPIC -R marks11enon/pixel9a-monitor
```

（実行すると値の入力を求められます。）iPhone では新しいトピックを購読し、古いトピックは削除します。

## 手動実行

```
gh workflow run monitor.yml -R marks11enon/pixel9a-monitor -f test_notify=true   # テスト通知
gh workflow run monitor.yml -R marks11enon/pixel9a-monitor                       # 通常の確認
gh run list -R marks11enon/pixel9a-monitor --workflow monitor.yml               # 実行履歴
```

## 止め方

GitHub のリポジトリ → Actions → monitor → 右上「…」→ Disable workflow。
またはコマンド: `gh workflow disable monitor.yml -R marks11enon/pixel9a-monitor`
（再開は `gh workflow enable`）

## ローカルでの確認（Windows）

```
$env:PYTHONUTF8='1'
py monitor.py                       # 9a の判定（JSON）
py monitor.py <別の整備済品URL>     # 他商品で陽性テスト
py tests/test_transitions.py        # 通知の状態遷移テスト（送信なし）
py run.py --dry-run                 # 実際に判定し、通知は送らず内容を表示
```

## 制約

- GitHub の定期実行は混雑時に数分〜数十分遅れたり、抜けたりすることがあります。
- 公開リポジトリは60日間活動が無いと定期実行が止まるため、最後のコミットから50日経つと `heartbeat` を更新してコミットします。
- ページ構造が変わると判定できなくなります（その場合は unknown になり、24時間後に【監視の問題】が届きます）。
- 海外の GitHub サーバーからのアクセスは、日本の回線と応答が異なる可能性があります。

## state.json の項目

`last_notified`（通知済みの構成）/ `last_confirmed_status` / `last_checked` / `unknown_since` /
`unknown_alert_sent` / `configs`（最新の構成別状態）/ `last_error`（直近のエラー概要）/ `heartbeat`
