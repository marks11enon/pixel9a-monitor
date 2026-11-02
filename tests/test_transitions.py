"""架空の状態遷移で通知の有無を確認する（通知は送らない）。
実行: python tests/test_transitions.py
"""
import json, os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import datetime
import run

JST = run.JST
F = lambda n: json.load(open(os.path.join(os.path.dirname(__file__), "fixtures", n + ".json"), encoding="utf-8"))
T = lambda h: datetime.datetime(2026, 11, 2, 0, 0, tzinfo=JST) + datetime.timedelta(hours=h)

steps = [  # (説明, fixture, 経過時間h, 期待する通知タイトル or None)
    ("在庫なし", "oos", 0, None),
    ("在庫なし→入荷（Iris）", "avail_iris", 1, run.TITLE_STOCK),
    ("入荷継続（同じ構成）", "avail_iris", 2, None),
    ("構成追加（Obsidian）", "avail_both", 3, run.TITLE_STOCK),
    ("確認不可（開始）", "unknown", 4, None),
    ("確認不可 23時間", "unknown", 27, None),
    ("確認不可 24時間", "unknown", 28, run.TITLE_PROBLEM),
    ("確認不可 継続（再送しない）", "unknown", 30, None),
    ("確認不可中も入荷構成は保持→同構成で復帰", "avail_both", 31, None),
    ("在庫なし（リセット）", "oos", 32, None),
    ("再入荷（Iris）", "avail_iris", 33, run.TITLE_STOCK),
]
st = dict(run.EMPTY_STATE)
ok_all = True
for desc, fx, h, expect in steps:
    st, msgs = run.decide(st, F(fx), T(h))
    for m in msgs:
        if m.get("marks_alert"):
            st["unknown_alert_sent"] = True
    got = msgs[0]["title"] if msgs else None
    ok = got == expect and len(msgs) <= 1
    ok_all &= ok
    print(f"{'OK ' if ok else 'NG '} {desc:28} 通知={got} last_notified={st['last_notified']} unknown_since={st['unknown_since']}")
    if msgs and fx.startswith("avail"):
        print("     本文: " + msgs[0]["message"].replace("\n", " / "))
print("\n全項目合格" if ok_all else "\n不合格あり")
sys.exit(0 if ok_all else 1)
