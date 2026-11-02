"""Google ストア 整備済 Pixel の在庫判定。

ページは読むだけ（カート追加・購入・フォーム送信はしない）。
使い方: python monitor.py [URL]   -> 判定結果を JSON で出力
"""
import datetime
import json
import os
import re
import sys

DEFAULT_URL = (
    "https://store.google.com/jp/config/refurbished_pixel_9a?hl=ja&selections="
    "eyJwcm9kdWN0RmFtaWx5IjoiY21WbWRYSmlhWE5vWldSZmNHbDRaV3hmT1dFPSJ9")
JST = datetime.timezone(datetime.timedelta(hours=9))
WAIT_MS = 2500


def parse_skus(html):
    out = []
    pat = re.compile(
        r'\["(_[a-z0-9_]+_rc)","([^"]+)",\[\[null,null,"[^"]+"\]\],'
        r'\[(\d+),null,\d+,\d+,\[\["JPY",(\d+)\].{0,120}?\]\],(\d+),"([^"]*)",(\d+),'
        r'\[[^\]]*\[[^\]]*\]\],(\d+),"[^"]*","[^"]*".{0,80}?\],'
        r'\[\[\[\d+,\d+\],"Capacity","([^"]+)"\],\[\[\d+,\d+\],"Color","([^"]+)"\]')
    for m in pat.finditer(html):
        out.append({
            "sku": m.group(1), "name": m.group(2).strip(),
            "state_code": int(m.group(3)), "price": int(m.group(4)),
            "flag_a": int(m.group(5)), "stock_label": m.group(6),
            "flag_b": int(m.group(8)),
            "storage": m.group(9).replace(" ", ""), "color": m.group(10),
        })
    sch = {k.strip(): v for k, v in re.findall(
        r'itemprop="description" content="([^"]*\d+GB ?)">.{0,400}?schema\.org/(\w+)"', html, re.S)}
    for s in out:
        s["schema"] = sch.get(s["name"])
        instock = (s["state_code"] == 1 and s["flag_a"] == 1 and s["flag_b"] == 1
                   and s["stock_label"] == "")
        oos = (s["state_code"] == 2 and s["flag_a"] == 0 and s["flag_b"] == 0
               and s["stock_label"] != "")
        v = "in_stock" if instock else ("out_of_stock" if oos else "unclear")
        if s["schema"] == "InStock" and v != "in_stock":
            v = "unclear"
        if s["schema"] == "OutOfStock" and v != "out_of_stock":
            v = "unclear"
        s["data_view"] = v
    return out


JS_STATE = r'''()=>{
  const colors=[...document.querySelectorAll('[data-test="color_selection_panel"]')].map(e=>({
     label:e.getAttribute('aria-label'), disabled:e.getAttribute('aria-disabled')==='true',
     selected:e.getAttribute('data-test-selected')==='true'}));
  const storages=[...document.querySelectorAll('[data-test="storage_selection_panel"]')].map(e=>({
     label:(e.getAttribute('data-tracking-label')||'').replace(/\s/g,''), text:e.innerText,
     disabled:e.getAttribute('aria-disabled')==='true', selected:e.getAttribute('data-test-selected')==='true'}));
  const atc=[...document.querySelectorAll('button[data-test="cta"]')].map(b=>({disabled:b.disabled, text:b.innerText.trim()}));
  return {colors, storages, atc};
}'''
# 色・容量の選択だけを行う（カートボタンは押さない）
JS_CLICK_COLOR = r'''l=>{const e=[...document.querySelectorAll('[data-test="color_selection_panel"]')].find(x=>x.getAttribute('aria-label')===l); if(!e) return false; e.click(); return true;}'''
JS_CLICK_STORAGE = r'''l=>{const e=[...document.querySelectorAll('[data-test="storage_selection_panel"]')].find(x=>(x.getAttribute('data-tracking-label')||'').replace(/\s/g,'')===l); if(!e) return false; e.click(); return true;}'''


def launch_browser(p):
    """CHROME_PATH → /usr/bin/google-chrome → インストール済み Chrome → 同梱 Chromium の順。"""
    path = os.environ.get("CHROME_PATH")
    if not path and os.path.exists("/usr/bin/google-chrome"):
        path = "/usr/bin/google-chrome"
    args = ["--no-sandbox"]
    if path:
        return p.chromium.launch(executable_path=path, args=args)
    try:
        return p.chromium.launch(channel="chrome", args=args)
    except Exception:
        return p.chromium.launch(args=args)


def _crawl(url, res):
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        b = launch_browser(p)
        try:
            ctx = b.new_context(locale="ja-JP", viewport={"width": 1400, "height": 1000})
            pg = ctx.new_page()

            def fresh():
                r = pg.goto(url, wait_until="networkidle", timeout=60000)
                pg.wait_for_timeout(WAIT_MS)
                return r

            resp = fresh()
            if not resp or resp.status != 200:
                res["notes"].append(f"HTTP {resp.status if resp else 'なし'}")
                return False
            skus = parse_skus(resp.text())
            res["skus"] = skus
            st = pg.evaluate(JS_STATE)
            res["initial"] = st
            if not skus:
                res["notes"].append("ページ内SKUデータを読めない（形式変更の可能性）")
            if not st["colors"]:
                res["notes"].append("色選択欄が見つからない")
            if len(st["atc"]) != 1:
                res["notes"].append(f"カートボタン数が想定外: {len(st['atc'])}")
            if res["notes"]:
                return False

            pairs = []
            for c in st["colors"]:
                if c["disabled"]:
                    continue
                fresh()
                pg.evaluate(JS_CLICK_COLOR, c["label"])
                pg.wait_for_timeout(WAIT_MS)
                for sto in pg.evaluate(JS_STATE)["storages"]:
                    if not sto["disabled"] and sto["label"]:
                        pairs.append((c["label"], sto["label"]))

            for cl, sl in pairs:
                fresh()
                pg.evaluate(JS_CLICK_COLOR, cl)
                pg.wait_for_timeout(WAIT_MS)
                pg.evaluate(JS_CLICK_STORAGE, sl)
                pg.wait_for_timeout(WAIT_MS)
                s2 = pg.evaluate(JS_STATE)
                csel = [x for x in s2["colors"] if x["selected"]]
                ssel = [x for x in s2["storages"] if x["selected"]]
                shown_price = None
                m = re.search(r"￥([\d,]+)",
                              next((x["text"] for x in s2["storages"] if x["label"] == sl), ""))
                if m:
                    shown_price = int(m.group(1).replace(",", ""))
                sku = next((k for k in skus if k["color"] == cl and k["storage"] == sl), None)
                cfg = {
                    "color": cl, "storage": sl,
                    "color_selected": bool(csel) and csel[0]["label"] == cl,
                    "storage_selected": bool(ssel) and ssel[0]["label"] == sl,
                    "atc_enabled": len(s2["atc"]) == 1 and not s2["atc"][0]["disabled"],
                    "shown_price": shown_price,
                    "data_price": sku["price"] if sku else None,
                    "data_view": sku["data_view"] if sku else "no_sku_data",
                }
                ok = (cfg["color_selected"] and cfg["storage_selected"] and cfg["atc_enabled"]
                      and cfg["data_view"] == "in_stock" and shown_price is not None
                      and shown_price == cfg["data_price"])
                contradiction = ((cfg["atc_enabled"] and cfg["data_view"] != "in_stock")
                                 or (not cfg["atc_enabled"] and cfg["data_view"] == "in_stock")
                                 or (shown_price and cfg["data_price"]
                                     and shown_price != cfg["data_price"]))
                if ok:
                    v = "purchasable"
                elif contradiction or not cfg["color_selected"] or not cfg["storage_selected"]:
                    v = "unclear"
                else:
                    v = "not_purchasable"
                cfg["verdict"] = v
                res["configs"].append(cfg)
        finally:
            b.close()
    return True


def check(url=DEFAULT_URL):
    """在庫を判定して dict を返す。例外は投げず、失敗時は status=unknown。"""
    now = datetime.datetime.now(JST).strftime("%Y-%m-%d %H:%M JST")
    res = {"checked_at": now, "url": url, "status": "unknown", "configs": [], "notes": []}
    try:
        if not _crawl(url, res):
            return res
    except Exception as e:
        res["notes"].append(f"取得・描画エラー: {type(e).__name__}: {str(e)[:200]}")
        return res

    data_all_oos = all(k["data_view"] == "out_of_stock" for k in res["skus"])
    colors_all_disabled = all(c["disabled"] for c in res["initial"]["colors"])
    atc_initial_disabled = res["initial"]["atc"][0]["disabled"]
    cfgs = res["configs"]
    if any(c["verdict"] == "purchasable" for c in cfgs):
        res["status"] = "available"
        if any(c["verdict"] == "unclear" for c in cfgs):
            res["notes"].append("一部構成は確認不可")
    elif (any(c["verdict"] == "unclear" for c in cfgs)
          or any(k["data_view"] == "unclear" for k in res["skus"])):
        res["status"] = "unknown"
        res["notes"].append("判定材料が矛盾または不足")
    elif data_all_oos and colors_all_disabled and atc_initial_disabled and not cfgs:
        res["status"] = "out_of_stock"
    elif data_all_oos and cfgs and all(c["verdict"] == "not_purchasable" for c in cfgs):
        res["status"] = "out_of_stock"
    else:
        res["status"] = "unknown"
        res["notes"].append("データと画面の状態が一致しない")
    return res


if __name__ == "__main__":
    print(json.dumps(check(sys.argv[1] if len(sys.argv) > 1 else DEFAULT_URL),
                     ensure_ascii=False, indent=2))
