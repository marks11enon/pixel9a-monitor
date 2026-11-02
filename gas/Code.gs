/**
 * Pixel 9a 整備済品 在庫監視（Google Apps Script 版・試作）
 *
 * ページの HTML を取得して、ページ内の SKU データと schema.org の在庫表示で判定する。
 * ページは読むだけ（カート追加・購入・フォーム送信はしない）。
 *
 * 通知は GitHub の notify ワークフロー経由で ntfy に送る
 * （GAS から ntfy に直接送ると、Google の共用サーバーのため HTTP 429 で断られる）。
 *
 * 初回設定:
 *  0. スクリプト プロパティに GITHUB_TOKEN を保存（pixel9a-monitor 限定・Actions 読み書きのみのトークン）
 *  1. testNotify を実行 → iPhone の ntfy に【テスト通知】が届くか確認
 *  2. checkStock を実行 → 実行ログで判定結果を確認
 *  3. setupTrigger を実行 → 5分ごとの自動実行を登録
 * 止め方: removeTriggers を実行（または「トリガー」画面で削除）
 */

var URL = 'https://store.google.com/jp/config/refurbished_pixel_9a?hl=ja&selections=' +
  'eyJwcm9kdWN0RmFtaWx5IjoiY21WbWRYSmlhWE5vWldSZmNHbDRaV3hmT1dFPSJ9';
var TITLE_STOCK = '【入荷】Pixel 9a 整備済品';
var TITLE_PROBLEM = '【監視の問題】';
var TEST_MESSAGE = '【テスト通知】Pixel 9a在庫監視の通知経路テストです（入荷ではありません）';
var UNKNOWN_ALERT_HOURS = 24;
var NOTIFY_API = 'https://api.github.com/repos/marks11enon/pixel9a-monitor/actions/workflows/notify.yml/dispatches';

// ---------- 判定（GAS と Node のテストで共通） ----------

function parseSkus(html) {
  var out = [];
  var pat = new RegExp(
    '\\["(_[a-z0-9_]+_rc)","([^"]+)",\\[\\[null,null,"[^"]+"\\]\\],' +
    '\\[(\\d+),null,\\d+,\\d+,\\[\\["JPY",(\\d+)\\][\\s\\S]{0,120}?\\]\\],(\\d+),"([^"]*)",(\\d+),' +
    '\\[[^\\]]*\\[[^\\]]*\\]\\],(\\d+),"[^"]*","[^"]*"[\\s\\S]{0,80}?\\],' +
    '\\[\\[\\[\\d+,\\d+\\],"Capacity","([^"]+)"\\],\\[\\[\\d+,\\d+\\],"Color","([^"]+)"\\]', 'g');
  var m;
  while ((m = pat.exec(html)) !== null) {
    out.push({
      sku: m[1], name: m[2].trim(), stateCode: +m[3], price: +m[4],
      flagA: +m[5], stockLabel: m[6], flagB: +m[8],
      storage: m[9].replace(/ /g, ''), color: m[10]
    });
  }
  var sch = {};
  var sp = /itemprop="description" content="([^"]*\d+GB ?)">[\s\S]{0,400}?schema\.org\/(\w+)"/g;
  while ((m = sp.exec(html)) !== null) sch[m[1].trim()] = m[2];
  out.forEach(function (s) {
    s.schema = sch[s.name] || null;
    var inStock = s.stateCode === 1 && s.flagA === 1 && s.flagB === 1 && s.stockLabel === '';
    var oos = s.stateCode === 2 && s.flagA === 0 && s.flagB === 0 && s.stockLabel !== '';
    var v = inStock ? 'in_stock' : (oos ? 'out_of_stock' : 'unclear');
    // schema.org が無い・食い違う場合は判定しない
    if (!s.schema) v = 'unclear';
    if (s.schema === 'InStock' && v !== 'in_stock') v = 'unclear';
    if (s.schema === 'OutOfStock' && v !== 'out_of_stock') v = 'unclear';
    s.verdict = v;
  });
  return out;
}

function judge(httpStatus, html) {
  var res = { status: 'unknown', skus: [], notes: [] };
  if (httpStatus !== 200) { res.notes.push('HTTP ' + httpStatus); return res; }
  var skus = parseSkus(html);
  res.skus = skus;
  if (!skus.length) { res.notes.push('ページ内SKUデータを読めない（形式変更の可能性）'); return res; }
  var buyable = skus.filter(function (s) { return s.verdict === 'in_stock'; });
  if (buyable.length) {
    res.status = 'available';
    if (skus.some(function (s) { return s.verdict === 'unclear'; })) res.notes.push('一部構成は確認不可');
  } else if (skus.every(function (s) { return s.verdict === 'out_of_stock'; })) {
    res.status = 'out_of_stock';
  } else {
    res.notes.push('判定材料が矛盾または不足');
  }
  return res;
}

function key(s) { return s.color + '|' + s.storage; }

/** state と判定結果から { state, msgs } を返す（副作用なし） */
function decide(state, res, now) {
  var st = JSON.parse(JSON.stringify(state));
  var msgs = [];
  st.last_checked = fmt(now);
  st.last_error = res.notes.join('; ') || null;
  st.skus = res.skus.map(function (s) { return { color: s.color, storage: s.storage, price: s.price, verdict: s.verdict }; });
  if (res.status !== 'unknown') {
    st.last_confirmed_status = res.status;
    st.unknown_since = null;
    st.unknown_alert_sent = false;
  }
  if (res.status === 'available') {
    var buyable = res.skus.filter(function (s) { return s.verdict === 'in_stock'; });
    var known = state.last_notified || [];
    if (buyable.some(function (s) { return known.indexOf(key(s)) < 0; })) {
      var lines = buyable.map(function (s) { return '・' + s.color + ' ' + s.storage + ' 税込￥' + s.price.toLocaleString('ja-JP'); });
      msgs.push({ title: TITLE_STOCK, priority: 4,
        message: '購入可能な構成:\n' + lines.join('\n') + '\n\n' + URL + '\n確認: ' + st.last_checked });
    }
    st.last_notified = buyable.map(key).sort();
  } else if (res.status === 'out_of_stock') {
    st.last_notified = [];
  } else {
    if (!st.unknown_since) st.unknown_since = now.toISOString();
    var hours = (now - new Date(st.unknown_since)) / 3600000;
    if (hours >= UNKNOWN_ALERT_HOURS && !st.unknown_alert_sent) {
      msgs.push({ title: TITLE_PROBLEM, priority: 3, marksAlert: true,
        message: '在庫を' + Math.floor(hours) + '時間確認できていません。\n直近のエラー: ' + (st.last_error || '不明') +
          '\nページ構造の変更やアクセス制限の可能性があります。' });
    }
  }
  return { state: st, msgs: msgs };
}

function fmt(d) {
  var j = new Date(d.getTime() + 9 * 3600000);
  return j.toISOString().slice(0, 16).replace('T', ' ') + ' JST';
}

var EMPTY_STATE = { last_notified: [], last_confirmed_status: null, last_checked: null,
  unknown_since: null, unknown_alert_sent: false, skus: [], last_error: null };

// ---------- GAS 専用 ----------

function checkStock() {
  var props = PropertiesService.getScriptProperties();
  var lock = LockService.getScriptLock();
  if (!lock.tryLock(1000)) return;  // 前回がまだ動いていれば重ねない
  try {
    var state = JSON.parse(props.getProperty('STATE') || 'null') || EMPTY_STATE;
    var res;
    try {
      var r = UrlFetchApp.fetch(URL + '&_=' + Date.now(), {
        muteHttpExceptions: true, followRedirects: true,
        headers: { 'Accept-Language': 'ja-JP', 'Cache-Control': 'no-cache' } });
      res = judge(r.getResponseCode(), r.getContentText('UTF-8'));
    } catch (e) {
      res = { status: 'unknown', skus: [], notes: ['取得エラー: ' + e.name] };
    }
    var d = decide(state, res, new Date());
    d.msgs.forEach(function (m) {
      var ok = notify(m.title, m.message, m.priority);
      if (ok && m.marksAlert) d.state.unknown_alert_sent = true;
      if (!ok && m.title === TITLE_STOCK) d.state.last_notified = state.last_notified || [];  // 次回また送る
    });
    props.setProperty('STATE', JSON.stringify(d.state));
    console.log('判定: ' + res.status + ' / ' + res.skus.map(function (s) { return key(s) + '=' + s.verdict; }).join(', ') +
      (res.notes.length ? ' / ' + res.notes.join('; ') : '') + ' / 通知 ' + d.msgs.length + '件');
  } finally {
    lock.releaseLock();
  }
}

/**
 * GitHub の notify ワークフローを起動し、そこから ntfy に送ってもらう。成功で true。
 * トークンはスクリプト プロパティ GITHUB_TOKEN に保存する（コードに書かない）。
 * トークンは pixel9a-monitor だけ・Actions の読み書きだけに絞った Fine-grained token。
 */
function notify(title, message, priority) {
  var token = (PropertiesService.getScriptProperties().getProperty('GITHUB_TOKEN') || '').trim();
  if (!token) { console.log('GITHUB_TOKEN が未設定'); return false; }
  try {
    var r = UrlFetchApp.fetch(NOTIFY_API, {
      method: 'post', contentType: 'application/json', muteHttpExceptions: true,
      headers: { Authorization: 'Bearer ' + token, Accept: 'application/vnd.github+json',
        'X-GitHub-Api-Version': '2022-11-28' },
      payload: JSON.stringify({ ref: 'main',
        inputs: { title: title, message: message, priority: String(priority || 3) } })
    });
    var code = r.getResponseCode();
    console.log('GitHub へ通知依頼: ' + title + ' -> HTTP ' + code +
      (code === 204 ? '' : ' ' + r.getContentText().slice(0, 150)));
    return code === 204;
  } catch (e) {
    console.log('GitHub へ通知依頼失敗: ' + title + ' (' + e.name + ')');
    return false;
  }
}

function testNotify() { notify('【テスト通知】', TEST_MESSAGE, 3); }

function setupTrigger() {
  removeTriggers();
  ScriptApp.newTrigger('checkStock').timeBased().everyMinutes(5).create();
  console.log('5分ごとのトリガーを登録しました');
}

function removeTriggers() {
  ScriptApp.getProjectTriggers().forEach(function (t) {
    if (t.getHandlerFunction() === 'checkStock') ScriptApp.deleteTrigger(t);
  });
}

function showState() { console.log(PropertiesService.getScriptProperties().getProperty('STATE')); }

if (typeof module !== 'undefined') module.exports = { parseSkus: parseSkus, judge: judge, decide: decide, EMPTY_STATE: EMPTY_STATE };
