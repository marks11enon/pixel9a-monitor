"""在庫判定 → state.json と比較 → 必要なときだけ ntfy で通知。

  python run.py                 通常実行
  python run.py --test-notify   【テスト通知】だけ送る（state は変えない）
  python run.py --dry-run       通知を送らず、送る予定の内容を表示
  python run.py --simulate FILE 判定の代わりに FILE（判定結果 JSON）を使う
  python run.py --state FILE    state.json の場所を変える（テスト用）
  python run.py --now ISO8601   現在時刻を指定する（テスト用）
  python run.py --heartbeat     heartbeat も更新する（ワークフローが50日無コミット時に付ける）

どんなエラーでも終了コード 0 で終える（GitHub の失敗メール連発を防ぐ）。
NTFY_TOPIC（環境変数）の値はログに出さない。
"""
import argparse
import datetime
import json
import os
import sys
import traceback
import urllib.request

JST = datetime.timezone(datetime.timedelta(hours=9))
STATE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "state.json")
UNKNOWN_ALERT_HOURS = 24

TITLE_STOCK = "【入荷】Pixel 9a 整備済品"
TITLE_PROBLEM = "【監視の問題】"
TEST_MESSAGE = "【テスト通知】Pixel 9a在庫監視の通知経路テストです（入荷ではありません）"

EMPTY_STATE = {
    "last_notified": [],
    "last_confirmed_status": None,
    "last_checked": None,
    "unknown_since": None,
    "unknown_alert_sent": False,
    "configs": [],
    "last_error": None,
    "heartbeat": None,
}


def load_state(path):
    try:
        with open(path, encoding="utf-8") as f:
            st = json.load(f)
    except (OSError, ValueError):
        st = {}
    return {**EMPTY_STATE, **st}


def save_state(path, st):
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(st, f, ensure_ascii=False, indent=2)
        f.write("\n")


def key(c):
    return f"{c['color']}|{c['storage']}"


def fmt_price(v):
    return f"￥{v:,}" if isinstance(v, int) else "価格不明"


def send_ntfy(title, message, priority, click, dry_run):
    """ntfy の JSON 公開 API で送る。成功で True。"""
    if dry_run:
        print(f"[dry-run] 送信予定: title={title} priority={priority}\n{message}")
        return True
    topic = os.environ.get("NTFY_TOPIC", "").strip()
    if not topic:
        print("NTFY_TOPIC が未設定のため送信できません")
        return False
    body = {"topic": topic, "title": title, "message": message,
            "priority": {"default": 3, "high": 4}.get(priority, 3)}
    if click:
        body["click"] = click
    req = urllib.request.Request(
        "https://ntfy.sh/", data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            ok = 200 <= r.status < 300
            print(f"ntfy 送信: {title} -> HTTP {r.status}")
            return ok
    except Exception as e:
        # 例外メッセージに URL・トピックが入らないよう型名だけ出す
        print(f"ntfy 送信失敗: {title} ({type(e).__name__})")
        return False


def decide(state, res, now):
    """state と判定結果から (新しい state, 送る通知のリスト) を返す。副作用なし。"""
    st = dict(state)
    msgs = []
    status = res.get("status", "unknown")
    url = res.get("url")
    st["last_checked"] = now.strftime("%Y-%m-%d %H:%M JST")
    st["configs"] = [{k: c.get(k) for k in ("color", "storage", "verdict", "shown_price",
                                             "data_price", "data_view")}
                     for c in res.get("configs", [])]
    st["last_error"] = "; ".join(res.get("notes", [])) or None

    if status in ("available", "out_of_stock"):
        st["last_confirmed_status"] = status
        st["unknown_since"] = None
        st["unknown_alert_sent"] = False

    if status == "available":
        buyable = [c for c in res.get("configs", []) if c.get("verdict") == "purchasable"]
        known = set(state.get("last_notified") or [])
        if any(key(c) not in known for c in buyable):
            lines = [f"・{c['color']} {c['storage']} 税込{fmt_price(c.get('shown_price'))}"
                     for c in buyable]
            msg = ("購入可能な構成:\n" + "\n".join(lines)
                   + f"\n\n{url}\n確認: {st['last_checked']}")
            msgs.append({"title": TITLE_STOCK, "message": msg, "priority": "high", "click": url})
        st["last_notified"] = sorted(key(c) for c in buyable)
    elif status == "out_of_stock":
        st["last_notified"] = []
    else:  # unknown: last_notified は変えない
        if not st.get("unknown_since"):
            st["unknown_since"] = now.isoformat(timespec="seconds")
        since = datetime.datetime.fromisoformat(st["unknown_since"])
        hours = (now - since).total_seconds() / 3600
        if hours >= UNKNOWN_ALERT_HOURS and not st.get("unknown_alert_sent"):
            msg = (f"在庫を{int(hours)}時間確認できていません（{since.strftime('%m/%d %H:%M')}から）。\n"
                   f"直近のエラー: {st['last_error'] or '不明'}\n"
                   "ページ構造の変更やアクセス制限の可能性があります。GitHub Actions のログを確認してください。")
            msgs.append({"title": TITLE_PROBLEM, "message": msg, "priority": "default",
                         "click": url, "marks_alert": True})
    return st, msgs


def parse_args():
    ap = argparse.ArgumentParser()
    ap.add_argument("--test-notify", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--simulate")
    ap.add_argument("--state", default=STATE_PATH)
    ap.add_argument("--now")
    ap.add_argument("--url")
    ap.add_argument("--heartbeat", action="store_true", help="heartbeat を更新する（60日停止対策）")
    return ap.parse_args()


def main(a):

    if a.test_notify:
        import monitor
        send_ntfy("【テスト通知】", TEST_MESSAGE, "default", monitor.DEFAULT_URL, a.dry_run)
        return

    now = (datetime.datetime.fromisoformat(a.now) if a.now
           else datetime.datetime.now(JST).replace(microsecond=0))
    if now.tzinfo is None:
        now = now.replace(tzinfo=JST)
    state = load_state(a.state)

    if a.simulate:
        with open(a.simulate, encoding="utf-8") as f:
            res = json.load(f)
    else:
        import monitor
        res = monitor.check(a.url or monitor.DEFAULT_URL)

    print(f"判定: {res.get('status')} / 構成: "
          + ", ".join(f"{c.get('color')} {c.get('storage')}={c.get('verdict')}"
                      for c in res.get("configs", []))
          + (f" / メモ: {'; '.join(res.get('notes', []))}" if res.get("notes") else ""))

    new_state, msgs = decide(state, res, now)
    for m in msgs:
        ok = send_ntfy(m["title"], m["message"], m["priority"], m["click"], a.dry_run)
        if m.get("marks_alert") and ok:
            new_state["unknown_alert_sent"] = True
        if not ok:
            new_state["last_error"] = ((new_state.get("last_error") or "") + "; 通知送信失敗").lstrip("; ")
            if m["title"] == TITLE_STOCK:
                # 送れなかった入荷は次回また送れるよう据え置く
                new_state["last_notified"] = state.get("last_notified") or []
    if not msgs:
        print("通知なし")
    if a.heartbeat:
        new_state["heartbeat"] = now.strftime("%Y-%m-%d %H:%M JST")
    # 確認時刻だけの変化ではファイルを書き換えない（毎回コミットされるのを防ぐ）
    strip = lambda s: {k: v for k, v in s.items() if k != "last_checked"}
    if strip(new_state) == strip(state) and os.path.exists(a.state):
        print("state 変化なし")
        return
    save_state(a.state, new_state)
    print("state 更新")


if __name__ == "__main__":
    args = parse_args()
    try:
        main(args)
    except Exception:
        # どんな失敗でも exit 0。state に記録できればする。
        print("想定外のエラー（unknown 扱い）:")
        print(traceback.format_exc(limit=3))
        try:
            err = traceback.format_exc(limit=1).strip().splitlines()[-1][:200]
            st = load_state(args.state)
            now = datetime.datetime.now(JST).replace(microsecond=0)
            res = {"status": "unknown", "url": None, "configs": [],
                   "notes": [f"run.py 想定外エラー: {err}"]}
            new_st, msgs = decide(st, res, now)
            new_st["configs"] = st.get("configs", [])
            for m in msgs:  # 24時間続いた場合の【監視の問題】
                if send_ntfy(m["title"], m["message"], m["priority"], None, False):
                    new_st["unknown_alert_sent"] = True
            save_state(args.state, new_st)
        except Exception:
            pass
    sys.exit(0)
