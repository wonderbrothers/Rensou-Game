/*! wb-consent.js v1.1.0 — WONDER BROTHERS 共通 Cookie 同意 + Google Consent Mode v2（Basic）
 *
 * ■ 正本と配置
 *   正本は corporate-site/src/scripts/wb-consent.js（このファイル）。
 *   次の2か所は同一内容のコピーで、各サイトのビルドが正本と食い違っていれば自動で写し直す。
 *     64monsters/docs/assets/wb-consent.js   … tools/stamp-assets.js が同期
 *     Rensou-Game/wb-consent.js              … build_static.py が同期
 *   各サイトが自分のドメインから配信するので、どれか1サイトが落ちても他のサイトは影響を受けない。
 *   直すときは正本だけを編集し、各サイトでいつものビルドを回す（手でコピーしない）。
 *
 * ■ 読み込み方（<head> のできるだけ上、dataLayer を触る他のスクリプトより前に。async/defer は付けない）
 *   <script src="…/wb-consent.js" data-gtm="GTM-XXXXXXX" data-privacy="/privacy/"></script>
 *     data-gtm      GTM のコンテナID。空なら計測もバナーも出さない
 *     data-privacy  バナーと設定画面から開くプライバシーポリシーのURL（同じタブで開く）
 *     data-btn-primary / data-btn-secondary（任意）
 *                   ボタンに付けるサイト共通ボタンのクラス（例: "btn primary" / "btn ghost"）。
 *                   指定するとこのファイルのボタンの見た目は使わず、サイトのボタンそのものになる。
 *                   許可・保存＝primary、拒否＝secondary。
 *   「Cookie設定」を開く入口は、任意の要素に data-wb-consent-open を付けるだけ。
 *
 * ■ 挙動（Basic Consent Mode）
 *   ・未選択／拒否 … GTM も GA も読み込まない。Google への通信は一切発生しない。
 *                   残っている GA の Cookie（_ga / _ga_* など）は消す。
 *   ・許可        … analytics_storage だけ granted にしてから GTM を読み込む。
 *                   広告系の3項目（ad_storage / ad_user_data / ad_personalization）は常に denied。
 *   ・許可→拒否   … その場で analytics_storage を denied に戻し、GA の送信を止め（ga-disable）、
 *                   GA の Cookie を消す。次のページからは GTM 自体を読み込まない。
 *   ・拒否→許可   … その場で GTM を読み込む（1ページにつき1回だけ。再読み込みは不要）。
 *
 * ■ 同意の保存
 *   Cookie「wb_consent_v1」＝ "granted" または "denied" だけ。Domain=.wonder-bros.com なので
 *   wonder-bros.com / 64monsters / rensougame の3サイトで共有される。個人を識別する値は持たない。
 *   同意の内容を大きく変えたときは COOKIE_NAME を wb_consent_v2 に上げる → 全員に再度たずねる
 *   （古い版の Cookie は自動で消える）。
 *
 * ■ やらないこと
 *   localStorage / sessionStorage には触らない（診断データ・ゲームの記録は同意の対象外）。
 *   reCAPTCHA・Google Fonts など、GA/GTM 以外の Google のサービスは止めない。
 *   同意バナーの表示やクリックを dataLayer に送らない。
 */
(function (w, d) {
  "use strict";
  if (w.WBConsent) return; /* 二重読み込みの保険 */

  /* ---------------- 設定 ---------------- */
  var VERSION = "1.1.0";
  var COOKIE_NAME = "wb_consent_v1";
  var OLD_COOKIES = [];              /* 版を上げたら、ここに古い名前を足す（例: "wb_consent_v1"） */
  var MAX_AGE = 180 * 24 * 60 * 60;  /* 180日 = 15552000 秒 */
  var ROOT_DOMAIN = "wonder-bros.com";
  /* 消す対象の GA Cookie。名前の決め打ちではなく、この形に当てはまるものを実際の Cookie から探して消す */
  var GA_COOKIE = /^(_ga|_ga_[A-Za-z0-9]+|_gid|_gat(_[A-Za-z0-9_-]+)?|_dc_gtm_[A-Za-z0-9_-]+)$/;

  var script = d.currentScript;
  function attr(n) { return (script && script.getAttribute(n)) || ""; }
  var GTM_ID = attr("data-gtm").trim();
  var PRIVACY_URL = attr("data-privacy") || "/privacy/";
  if (!/^GTM-[A-Z0-9]+$/.test(GTM_ID)) GTM_ID = ""; /* 未設定・書き損じは「計測なし」として扱う */
  var BTN = { primary: attr("data-btn-primary").trim(), secondary: attr("data-btn-secondary").trim() };

  /* ---------------- Consent Mode v2 ---------------- */
  var dl = (w.dataLayer = w.dataLayer || []);
  function gtag() { dl.push(arguments); }
  if (typeof w.gtag !== "function") w.gtag = gtag;
  function cmd() { return arguments; } /* dataLayer に積む gtag コマンド（Arguments）を作る */

  var DENIED = { ad_storage: "denied", ad_user_data: "denied", ad_personalization: "denied", analytics_storage: "denied" };
  var GRANTED = { ad_storage: "denied", ad_user_data: "denied", ad_personalization: "denied", analytics_storage: "granted" };

  /* 既定値は必ず最初に積む（公式仕様: default は GTM の読み込みより前） */
  gtag("consent", "default", DENIED);

  /* ---------------- Cookie ---------------- */
  var host = location.hostname;
  var onRoot = host === ROOT_DOMAIN || host.slice(-(ROOT_DOMAIN.length + 1)) === "." + ROOT_DOMAIN;
  var secure = location.protocol === "https:";

  function readCookie(name) {
    var parts = d.cookie ? d.cookie.split(/;\s*/) : [];
    for (var i = 0; i < parts.length; i++) {
      var eq = parts[i].indexOf("=");
      if (eq > 0 && parts[i].slice(0, eq) === name) return parts[i].slice(eq + 1);
    }
    return null;
  }
  function writeConsent(v) {
    d.cookie = COOKIE_NAME + "=" + v + "; Max-Age=" + MAX_AGE + "; Path=/" +
      (onRoot ? "; Domain=." + ROOT_DOMAIN : "") + "; SameSite=Lax" + (secure ? "; Secure" : "");
  }
  function readConsent() {
    var v = readCookie(COOKIE_NAME);
    return v === "granted" || v === "denied" ? v : null;
  }

  /* 名前・Domain・Path の組み合わせを総当たりで失効させる。
     GA は既定で「登録可能な最上位ドメイン」（.wonder-bros.com）・Path=/ に書くが、
     設定次第でサブドメインや下位パスに書かれることもあるので、候補を全部試す。 */
  function expire(name) {
    var labels = host.split(".");
    var domains = [""];
    for (var i = 0; i <= labels.length - 2; i++) domains.push("; Domain=." + labels.slice(i).join("."));
    var paths = ["/"], segs = location.pathname.split("/"), p = "";
    for (var j = 1; j < segs.length - 1; j++) { p += "/" + segs[j]; paths.push(p, p + "/"); }
    for (var a = 0; a < domains.length; a++) {
      for (var b = 0; b < paths.length; b++) {
        d.cookie = name + "=; Max-Age=0; Expires=Thu, 01 Jan 1970 00:00:00 GMT; Path=" + paths[b] + domains[a];
      }
    }
  }
  function gaCookieNames() {
    var out = [], parts = d.cookie ? d.cookie.split(/;\s*/) : [];
    for (var i = 0; i < parts.length; i++) {
      var n = parts[i].split("=")[0];
      if (GA_COOKIE.test(n) && out.indexOf(n) < 0) out.push(n);
    }
    return out;
  }
  function clearGaCookies() {
    var names = gaCookieNames();
    for (var i = 0; i < names.length; i++) expire(names[i]);
    return names;
  }
  for (var o = 0; o < OLD_COOKIES.length; o++) expire(OLD_COOKIES[o]);

  /* ---------------- GTM の読み込み（1ページにつき1回） ---------------- */
  function isCmd(x) { return Object.prototype.toString.call(x) === "[object Arguments]"; }

  /* 同意より前に積まれたアプリのイベントは、GTM が読み込まれたときに順に処理される。
     通常の読み込みと同じ順序（同意 → gtm.js → 各イベント）になるよう、
     同意の更新と gtm.js を「最初のイベントの手前」に差し込む。 */
  function insertBeforeQueuedEvents(items) {
    var at = dl.length;
    for (var i = 0; i < dl.length; i++) { if (!isCmd(dl[i])) { at = i; break; } }
    dl.splice.apply(dl, [at, 0].concat(items));
  }

  function gtmPresent() {
    return !!d.querySelector('script[src*="googletagmanager.com/gtm.js"]');
  }

  function startAnalytics() {
    if (!GTM_ID) return;
    enableGa();
    if (w.__wbAnalyticsLoaded || gtmPresent()) {
      /* 既に読み込み済み（拒否→許可をその場で切り替えた場合）。同意の更新だけ伝える */
      w.__wbAnalyticsLoaded = true;
      gtag("consent", "update", GRANTED);
      return;
    }
    w.__wbAnalyticsLoaded = true;
    insertBeforeQueuedEvents([cmd("consent", "update", GRANTED), { "gtm.start": new Date().getTime(), event: "gtm.js" }]);
    var s = d.createElement("script");
    s.async = true;
    s.src = "https://www.googletagmanager.com/gtm.js?id=" + encodeURIComponent(GTM_ID);
    var first = d.getElementsByTagName("script")[0];
    if (first && first.parentNode) first.parentNode.insertBefore(s, first);
    else (d.head || d.documentElement).appendChild(s);
  }

  /* 許可→拒否をその場で切り替えたときの送信停止。
     analytics_storage を denied にしただけだと、読み込み済みの GA は Cookie なしの計測を続けるため、
     GA 公式のオプトアウト（window['ga-disable-G-XXXX'] = true）も併用する。
     測定IDはコードに持たず、読み込まれた GTM から拾う（GTM 側でIDを変えても追従する）。 */
  var disabledIds = [];
  var watchTimer = null;
  function gaIds() {
    var ids = [], gtm = w.google_tag_manager;
    if (gtm) for (var k in gtm) if (/^G-[A-Z0-9]+$/.test(k)) ids.push(k);
    return ids;
  }
  function disableGa() {
    var ids = gaIds();
    for (var i = 0; i < ids.length; i++) {
      w["ga-disable-" + ids[i]] = true;
      if (disabledIds.indexOf(ids[i]) < 0) disabledIds.push(ids[i]);
    }
  }
  function enableGa() {
    if (watchTimer) { clearInterval(watchTimer); watchTimer = null; }
    for (var i = 0; i < disabledIds.length; i++) w["ga-disable-" + disabledIds[i]] = false;
    disabledIds = [];
  }
  function stopAnalytics() {
    if (!w.__wbAnalyticsLoaded) return;
    gtag("consent", "update", DENIED);
    disableGa();
    /* GTM の読み込み途中で拒否された場合に備え、GA が現れるまで少しのあいだ見張る */
    var n = 0;
    if (watchTimer) clearInterval(watchTimer);
    watchTimer = setInterval(function () {
      disableGa(); clearGaCookies();
      if (++n >= 20) { clearInterval(watchTimer); watchTimer = null; }
    }, 500);
  }

  /* ---------------- 状態の変更 ---------------- */
  var listeners = [];
  function notify(v) {
    for (var i = 0; i < listeners.length; i++) { try { listeners[i](v); } catch (e) {} }
    try { d.dispatchEvent(new CustomEvent("wbconsent:change", { detail: { analytics: v } })); } catch (e) {}
  }
  function set(v) {
    writeConsent(v);
    if (v === "granted") startAnalytics();
    else { stopAnalytics(); clearGaCookies(); }
    hideBanner();
    notify(v);
  }

  /* ---------------- ページ読み込み時 ---------------- */
  var initial = readConsent();
  if (initial) writeConsent(initial); /* 有効期限を延ばす（理由は README「Cookie同意」参照） */
  if (initial === "granted") startAnalytics();
  else clearGaCookies(); /* 未選択・拒否のとき、以前の実装で作られた GA Cookie が残っていれば消す */

  /* ---------------- UI ---------------- */
  var TEXT = {
    banner: "このサイトでは、利用状況の分析とサービス改善のために Google Analytics を使用しています。" +
      "「許可する」を選ぶまで、アクセス解析は行いません。",
    privacy: "プライバシーポリシー",
    deny: "拒否する",
    grant: "許可する",
    title: "Cookie設定",
    close: "閉じる",
    needH: "必要な機能",
    needNote: "ページの表示、セキュリティ、お問い合わせの送信、診断やゲームの記録・設定の保存など、サービスの提供に必要な機能です。",
    always: "常に有効",
    anH: "アクセス解析",
    anNote: "Google Analytics を利用して、サービス改善のための利用状況を計測します。",
    shared: "この設定は、株式会社ワンダーブラザースが運営する wonder-bros.com・64モンスターズ・連想ゲームで共通です。",
    save: "設定を保存"
  };

  var CSS =
    ":where(.wbc-banner){position:fixed;left:0;right:0;bottom:0;z-index:var(--wbc-z,2147483000);" +
      "padding:0 var(--wbc-gutter,16px) calc(var(--wbc-gutter,16px) + env(safe-area-inset-bottom,0px));pointer-events:none;}" +
    ":where(.wbc-banner) :where(.wbc-card){pointer-events:auto;box-sizing:border-box;max-width:var(--wbc-max,760px);margin:0 auto;" +
      "display:flex;align-items:center;gap:14px 20px;padding:var(--wbc-pad,16px 18px);" +
      "background:var(--wbc-bg,#fff);color:var(--wbc-fg,#1a1a1a);border:var(--wbc-border,1px solid rgba(0,0,0,.14));" +
      "border-radius:var(--wbc-radius,14px);box-shadow:var(--wbc-shadow,0 10px 30px rgba(0,0,0,.16));" +
      "font-family:var(--wbc-font,inherit);font-size:var(--wbc-fs,14px);line-height:1.7;" +
      "animation:wbc-in .28s ease both;}" +
    "@keyframes wbc-in{from{opacity:0;transform:translateY(12px)}to{opacity:1;transform:none}}" +
    ":where(.wbc-text){margin:0;flex:1 1 auto;min-width:0;}" +
    ":where(.wbc-link){color:inherit;font-weight:700;text-decoration:underline;text-underline-offset:3px;white-space:nowrap;}" +
    ":where(.wbc-actions){display:grid;grid-auto-flow:column;grid-auto-columns:1fr;gap:10px;flex:none;}" + /* 2つのボタンは常に同じ幅 */
    ":where(.wbc) .wbc-btn{-webkit-appearance:none;appearance:none;box-sizing:border-box;min-width:112px;min-height:44px;margin:0;" +
      "padding:0 20px;border-radius:var(--wbc-btn-radius,999px);font:inherit;font-weight:700;line-height:1.2;cursor:pointer;" +
      "border:var(--wbc-btn-border-width,1.5px) solid var(--wbc-accent,#1a1a1a);}" +
    ":where(.wbc) .wbc-btn--secondary{background:transparent;color:var(--wbc-fg,#1a1a1a);}" +
    ":where(.wbc) .wbc-btn--primary{background:var(--wbc-accent,#1a1a1a);color:var(--wbc-accent-fg,#fff);}" +
    ":where(.wbc) .wbc-btn:hover{opacity:.86;}" +
    ":where(.wbc-btn:focus-visible,.wbc-switch:focus-visible,.wbc-x:focus-visible,.wbc-link:focus-visible){outline:2px solid var(--wbc-focus,var(--wbc-accent,#1a1a1a));outline-offset:2px;}" +
    "@media (max-width:640px){:where(.wbc-banner) :where(.wbc-card){flex-direction:column;align-items:stretch;}" +
      ":where(.wbc-actions){width:100%;} :where(.wbc-actions) :where(.wbc-act){min-width:0;}}" +
    ":where(.wbc-dialog){box-sizing:border-box;width:min(480px,calc(100vw - 2 * var(--wbc-gutter,16px)));max-width:none;" +
      "max-height:calc(100vh - 2 * var(--wbc-gutter,16px));overflow:auto;margin:auto;padding:0;" +
      "background:var(--wbc-bg,#fff);color:var(--wbc-fg,#1a1a1a);border:var(--wbc-border,1px solid rgba(0,0,0,.14));" +
      "border-radius:var(--wbc-radius,14px);box-shadow:var(--wbc-shadow,0 10px 30px rgba(0,0,0,.16));" +
      "font-family:var(--wbc-font,inherit);font-size:var(--wbc-fs,14px);line-height:1.7;}" +
    ":where(.wbc-dialog:not([open])){display:none;}" +
    ":where(.wbc-dialog.wbc-fallback){position:fixed;inset:0;z-index:var(--wbc-z,2147483000);}" +
    ":where(.wbc-dialog)::backdrop{background:var(--wbc-backdrop,rgba(0,0,0,.45));}" +
    ":where(.wbc-panel){margin:0;padding:var(--wbc-dialog-pad,22px 22px 20px);}" +
    ":where(.wbc-head){display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:6px;}" +
    ":where(.wbc-title){margin:0;font-size:1.2em;font-weight:700;line-height:1.4;}" +
    ":where(.wbc) .wbc-x{-webkit-appearance:none;appearance:none;width:40px;height:40px;margin:-8px -8px -8px 0;padding:0;border:0;" +
      "background:transparent;color:inherit;font:inherit;font-size:22px;line-height:1;cursor:pointer;border-radius:999px;}" +
    ":where(.wbc-row){display:flex;align-items:center;gap:16px;padding:14px 0;border-bottom:1px solid var(--wbc-line,rgba(0,0,0,.12));}" +
    ":where(.wbc-row-text){flex:1 1 auto;min-width:0;}" +
    ":where(.wbc-h){margin:0;font-weight:700;}" +
    ":where(.wbc-note){margin:2px 0 0;color:var(--wbc-muted,#555);font-size:.92em;line-height:1.65;}" +
    ":where(.wbc-always){flex:none;color:var(--wbc-muted,#555);font-size:.92em;font-weight:700;white-space:nowrap;}" +
    ":where(.wbc) .wbc-switch{-webkit-appearance:none;appearance:none;flex:none;display:inline-flex;align-items:center;gap:8px;margin:0;padding:0;" +
      "border:0;background:transparent;color:inherit;font:inherit;font-size:.86em;font-weight:700;cursor:pointer;}" +
    ":where(.wbc-track){position:relative;display:inline-block;width:46px;height:28px;border-radius:999px;box-sizing:border-box;" +
      "background:var(--wbc-switch-off,rgba(0,0,0,.18));transition:background .15s;}" +
    ":where(.wbc-knob){position:absolute;top:3px;left:3px;width:22px;height:22px;border-radius:50%;background:#fff;" +
      "box-shadow:0 1px 3px rgba(0,0,0,.25);transition:transform .15s;}" +
    ":where(.wbc-switch[aria-checked=\"true\"]) :where(.wbc-track){background:var(--wbc-accent,#1a1a1a);}" +
    ":where(.wbc-switch[aria-checked=\"true\"]) :where(.wbc-knob){transform:translateX(18px);}" +
    ":where(.wbc-state){min-width:2.2em;text-align:left;}" +
    ":where(.wbc-foot){margin:14px 0 0;color:var(--wbc-muted,#555);font-size:.88em;line-height:1.65;}" +
    ":where(.wbc-panel) :where(.wbc-actions){justify-content:end;margin-top:18px;}" +
    "@media (prefers-reduced-motion:reduce){:where(.wbc-banner) :where(.wbc-card){animation:none;}" +
      ":where(.wbc-track),:where(.wbc-knob){transition:none;}}" +
    "@media print{:where(.wbc-banner){display:none;}}";

  var banner = null, dialog = null, sw = null, lastFocus = null, ro = null;

  /* ボタンのクラス。サイトのボタンクラスが指定されていればそれを使い、無ければこのファイルの見た目を使う */
  function btnClass(kind) {
    return "wbc-act " + (BTN[kind] || "wbc-btn wbc-btn--" + kind);
  }

  function injectCss() {
    if (d.getElementById("wbc-style")) return;
    var st = d.createElement("style");
    st.id = "wbc-style";
    st.textContent = CSS;
    /* サイトのスタイルシートより前に置く。ほぼ全体を :where() で詳細度0にしてあるので、サイト側の指定が勝つ。
       ボタン類だけは詳細度を (0,1,0) にしてある。サイトの button{background:none;border:none} のような
       要素リセットに負けて、ボタンの枠や塗りが消えないようにするため（上書きは .wbc .wbc-btn で行う） */
    var h = d.head || d.documentElement;
    h.insertBefore(st, h.firstChild);
  }
  function el(tag, cls, html) {
    var e = d.createElement(tag);
    if (cls) e.className = cls;
    if (html != null) e.innerHTML = html;
    return e;
  }
  function esc(s) { return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/"/g, "&quot;"); }

  function setBannerHeight() {
    var h = banner ? banner.getBoundingClientRect().height : 0;
    d.documentElement.style.setProperty("--wbc-banner-h", Math.ceil(h) + "px");
  }

  function showBanner() {
    if (banner || !GTM_ID) return;
    injectCss();
    banner = el("div", "wbc wbc-banner");
    banner.setAttribute("role", "region");
    banner.setAttribute("aria-label", "Cookieの利用について");
    banner.innerHTML =
      '<div class="wbc-card">' +
        '<p class="wbc-text">' + esc(TEXT.banner) +
          ' <a class="wbc-link" href="' + esc(PRIVACY_URL) + '">' + esc(TEXT.privacy) + "</a></p>" +
        '<div class="wbc-actions">' +
          '<button type="button" class="' + btnClass("secondary") + '" data-wbc="deny">' + esc(TEXT.deny) + "</button>" +
          '<button type="button" class="' + btnClass("primary") + '" data-wbc="grant">' + esc(TEXT.grant) + "</button>" +
        "</div>" +
      "</div>";
    banner.addEventListener("click", function (e) {
      var b = e.target.closest ? e.target.closest("[data-wbc]") : null;
      if (!b) return;
      set(b.getAttribute("data-wbc") === "grant" ? "granted" : "denied");
    });
    d.body.appendChild(banner);
    d.documentElement.classList.add("wbc-banner-open");
    setBannerHeight();
    if (w.ResizeObserver) { ro = new ResizeObserver(setBannerHeight); ro.observe(banner); }
    else w.addEventListener("resize", setBannerHeight);
  }
  function hideBanner() {
    if (!banner) return;
    if (ro) { ro.disconnect(); ro = null; }
    w.removeEventListener("resize", setBannerHeight);
    banner.parentNode && banner.parentNode.removeChild(banner);
    banner = null;
    d.documentElement.classList.remove("wbc-banner-open");
    setBannerHeight();
  }

  function syncSwitch(on) {
    sw.setAttribute("aria-checked", on ? "true" : "false");
    sw.querySelector(".wbc-state").textContent = on ? "ON" : "OFF";
  }

  function buildDialog() {
    injectCss();
    dialog = el("dialog", "wbc wbc-dialog");
    dialog.setAttribute("aria-labelledby", "wbc-title");
    dialog.innerHTML =
      '<div class="wbc-panel">' +
        '<div class="wbc-head"><h2 class="wbc-title" id="wbc-title">' + esc(TEXT.title) + "</h2>" +
          '<button type="button" class="wbc-x" data-wbc="close" aria-label="' + esc(TEXT.close) + '">×</button></div>' +
        '<div class="wbc-row"><div class="wbc-row-text"><p class="wbc-h">' + esc(TEXT.needH) + "</p>" +
          '<p class="wbc-note">' + esc(TEXT.needNote) + "</p></div>" +
          '<span class="wbc-always">' + esc(TEXT.always) + "</span></div>" +
        '<div class="wbc-row"><div class="wbc-row-text"><p class="wbc-h" id="wbc-an-h">' + esc(TEXT.anH) + "</p>" +
          '<p class="wbc-note" id="wbc-an-note">' + esc(TEXT.anNote) + "</p></div>" +
          '<button type="button" class="wbc-switch" role="switch" aria-checked="false" aria-labelledby="wbc-an-h" aria-describedby="wbc-an-note">' +
            '<span class="wbc-track" aria-hidden="true"><span class="wbc-knob"></span></span>' +
            '<span class="wbc-state" aria-hidden="true">OFF</span></button></div>' +
        '<p class="wbc-foot">' + esc(TEXT.shared) +
          ' <a class="wbc-link" href="' + esc(PRIVACY_URL) + '">' + esc(TEXT.privacy) + "</a></p>" +
        '<div class="wbc-actions"><button type="button" class="' + btnClass("primary") + '" data-wbc="save">' + esc(TEXT.save) + "</button></div>" +
      "</div>";
    sw = dialog.querySelector(".wbc-switch");
    dialog.addEventListener("click", function (e) {
      if (e.target === dialog) { close(); return; } /* 背景（::backdrop）のクリック */
      if (e.target.closest(".wbc-switch")) { syncSwitch(sw.getAttribute("aria-checked") !== "true"); return; }
      var b = e.target.closest("[data-wbc]");
      if (!b) return;
      var act = b.getAttribute("data-wbc");
      if (act === "close") close();
      else if (act === "save") { set(sw.getAttribute("aria-checked") === "true" ? "granted" : "denied"); close(); }
    });
    dialog.addEventListener("cancel", function (e) { e.preventDefault(); close(); }); /* Esc */
    d.body.appendChild(dialog);
  }

  function open() {
    if (!d.body) return;
    if (!dialog) buildDialog();
    syncSwitch(readConsent() === "granted");
    lastFocus = d.activeElement;
    if (dialog.open) return;
    if (typeof dialog.showModal === "function") dialog.showModal();
    else { dialog.classList.add("wbc-fallback"); dialog.setAttribute("open", ""); }
    sw.focus();
  }
  function close() {
    if (!dialog || !dialog.open) return;
    if (typeof dialog.close === "function") dialog.close();
    else { dialog.removeAttribute("open"); dialog.classList.remove("wbc-fallback"); }
    if (lastFocus && lastFocus.focus && d.contains(lastFocus)) lastFocus.focus();
  }

  /* 「Cookie設定」の入口：data-wb-consent-open を付けた要素（後から描画されたものも含む） */
  d.addEventListener("click", function (e) {
    var t = e.target && e.target.closest ? e.target.closest("[data-wb-consent-open]") : null;
    if (!t) return;
    e.preventDefault();
    open();
  });

  function boot() { if (!readConsent()) showBanner(); }
  if (d.body) boot();
  else d.addEventListener("DOMContentLoaded", boot);

  /* ---------------- 公開API ---------------- */
  w.WBConsent = {
    version: VERSION,
    cookieName: COOKIE_NAME,
    /* "granted" | "denied" | null（未選択） */
    get: readConsent,
    grant: function () { set("granted"); },
    deny: function () { set("denied"); },
    open: open,
    close: close,
    onChange: function (fn) { if (typeof fn === "function") listeners.push(fn); },
    /* 動作確認用 */
    state: function () {
      return { analytics: readConsent(), gtmLoaded: !!w.__wbAnalyticsLoaded, gaCookies: gaCookieNames(), gaDisabled: disabledIds.slice() };
    }
  };
})(window, document);
