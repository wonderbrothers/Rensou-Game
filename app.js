const MARKS = ["A", "B", "C", "D", "E"];
const ic = (n, f) => `<span class="ms${f ? " fill" : ""}">${n}</span>`;
let sessions = [], cur = null, idx = 0, live = 0, answered = false;
let activeCat = null;
let order = [];            // 選択肢シャッフルの表示順（表示位置 → 元インデックス）
let sessionAnswers = [];   // 今回プレイの回答記録
let callsPromise = null;   // 遊びコールの株価: スタート直後に先読み

const $ = id => document.getElementById(id);
function show(id) {
  ["loader", "home", "stage"].forEach(v => $(v).classList.toggle("hidden", v !== id));
  // タイトルとHeroイメージはHOME（と読み込み画面）のみ表示する
  const hd = document.querySelector("header.hd");
  if (hd) hd.classList.toggle("hidden", id === "stage");
}

/* ---------- ローカル保存（成績・間違いノート・コール成績キャッシュ） ---------- */
const store = {
  get results() {
    try { return JSON.parse(localStorage.getItem("rensou_results") || "[]"); } catch (e) { return []; }
  },
  addResult(r) {
    const a = this.results; a.push(r);
    localStorage.setItem("rensou_results", JSON.stringify(a));
  },
  get callStats() {
    try { return JSON.parse(localStorage.getItem("rensou_callstats_v2") || "null"); } catch (e) { return null; }
  },
  setCallStats(v) { localStorage.setItem("rensou_callstats_v2", JSON.stringify(v)); },
  setResults(a) { localStorage.setItem("rensou_results", JSON.stringify(a)); },
  clearAll() {
    localStorage.removeItem("rensou_results");
    localStorage.removeItem("rensou_callstats");
  }
};

/* ---------- 成績のエクスポート / インポート ---------- */
function exportResults() {
  const data = { version: 1, exported_at: new Date().toISOString(), results: store.results };
  const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = `rensou_backup_${new Date().toISOString().slice(0, 10)}.json`;
  a.click();
  URL.revokeObjectURL(a.href);
}

function importResults(file) {
  const reader = new FileReader();
  reader.onload = () => {
    try {
      const d = JSON.parse(reader.result);
      const incoming = Array.isArray(d) ? d : (d.results || []);
      if (!incoming.length) { alert("読み込めるプレイ記録がありませんでした。"); return; }
      // 既存とマージ（id + 日時で重複排除）
      const seen = new Set(store.results.map(r => `${r.id}|${r.at}`));
      const merged = [...store.results];
      let added = 0;
      incoming.forEach(r => {
        const k = `${r.id}|${r.at}`;
        if (!seen.has(k)) { merged.push(r); seen.add(k); added++; }
      });
      merged.sort((a, b) => (a.at || "").localeCompare(b.at || ""));
      store.setResults(merged);
      alert(`${added} 件の記録を取り込みました（重複はスキップ）。`);
      showStats();
    } catch (e) {
      alert("ファイルを読み込めませんでした。バックアップJSONを選んでください。");
    }
  };
  reader.readAsText(file);
}

/* ---------- ストリーク・称号 ---------- */
function calcStreak(results) {
  const days = [...new Set(results.map(r => (r.at || "").slice(0, 10)))].sort();
  if (!days.length) return { current: 0, best: 0 };
  let best = 1, cur = 1;
  for (let i = 1; i < days.length; i++) {
    const diff = (new Date(days[i]) - new Date(days[i - 1])) / 86400000;
    cur = diff === 1 ? cur + 1 : 1;
    best = Math.max(best, cur);
  }
  // 現在のストリーク: 最終プレイ日が今日か昨日なら継続
  const last = days[days.length - 1];
  const today = new Date(); today.setHours(0, 0, 0, 0);
  const gap = (today - new Date(last)) / 86400000;
  return { current: gap <= 1 ? cur : 0, best };
}

const TITLES = [
  [0, "新人アナリスト"], [20, "アナリスト"], [60, "アソシエイト"],
  [120, "ヴァイスプレジデント"], [240, "エグゼクティブディレクター"], [400, "マネージングディレクター"]
];
function calcTitle(results) {
  const totalCorrect = results.reduce((a, r) => a + (r.score || 0), 0);
  let title = TITLES[0][1], next = null;
  for (const [th, name] of TITLES) {
    if (totalCorrect >= th) title = name;
    else { next = { need: th - totalCorrect, name }; break; }
  }
  return { title, totalCorrect, next };
}

/* ---------- 初回のみの保存告知バナー ---------- */
function showStorageNotice() {
  if (localStorage.getItem("rensou_notice_ok")) return;
  const bar = document.createElement("div");
  bar.className = "notice";
  bar.innerHTML = `<span>${ic("lock")} 成績・間違いノートは<b>この端末のブラウザ内にのみ</b>保存されます。サーバーや外部への送信はありません。削除はいつでも「成績表」から。</span>
    <button id="noticeOk">OK</button>`;
  document.body.appendChild(bar);
  document.getElementById("noticeOk").onclick = () => {
    localStorage.setItem("rensou_notice_ok", "1");
    bar.remove();
  };
}

/* ---------- loading ---------- */
/* Flask（動的API）でも GitHub Pages（静的ファイル）でも動くよう、
   ベースURLをドキュメント基準で解決し、拡張子ありもフォールバックで試す */
function apiUrl(p) {
  return new URL(p, document.baseURI).href;
}

async function apiGet(p) {
  let lastErr;
  for (const u of [apiUrl(p), apiUrl(p + ".json")]) {
    try {
      const r = await fetch(u, { cache: "no-cache" });
      if (r.ok) return await r.json();
      lastErr = new Error(`${r.status} ${u}`);
    } catch (e) { lastErr = e; }
  }
  throw lastErr || new Error("fetch failed");
}

async function boot() {
  try {
    // 一覧は軽量インデックスから（記事が増えても読み込みが重くならない）。
    // 旧構成（api/index が無い環境）では従来の全件JSONへフォールバック。
    try {
      sessions = await apiGet("api/index");
    } catch (_) {
      sessions = await apiGet("api/sessions");
    }
    renderHome();
    showStorageNotice();
  } catch (e) {
    console.error("[連想ゲーム] データ取得に失敗:", e);
    const note = document.querySelector("#loader .errnote");
    if (note) note.textContent = `（${e.message}）`;
    show("loader");
  }
}

/* 記事の完全データ（questions等）を必要時に取得して差し込む。
   インデックス由来のセッションは questions を持たないため、
   プレイ・解答表示の直前にこの関数で埋める。 */
async function ensureDetail(s) {
  if (!s || s.questions) return s;
  const d = await apiGet(`api/session/${s.id}`);
  Object.assign(s, d);
  return s;
}

function hasCalls(s) {
  return (s.calls && s.calls.length) || s.has_calls;
}

$("filePick").addEventListener("change", async ev => {
  const files = [...ev.target.files].filter(f => f.name !== "index.json");
  const loaded = [];
  for (const f of files) {
    try { loaded.push(JSON.parse(await f.text())); } catch (e) {}
  }
  if (loaded.length) { sessions = loaded; renderHome(); }
});

/* ---------- カレンダーモーダル（共通部品） ----------
   counts: { "YYYY-MM-DD": 件数 } / onPick(iso): 件数クリック時のコールバック */
function openCalendar(counts, onPick, note) {
  const keys = Object.keys(counts).filter(k => counts[k] > 0).sort();
  let ym = (keys.length ? keys[keys.length - 1] : new Date().toISOString().slice(0, 10)).slice(0, 7);
  const ov = document.createElement("div");
  ov.className = "calov";
  document.body.appendChild(ov);
  const close = () => ov.remove();
  const render = () => {
    const [Y, M] = ym.split("-").map(Number);
    const startDow = new Date(Y, M - 1, 1).getDay();
    const daysIn = new Date(Y, M, 0).getDate();
    let cells = "";
    for (let i = 0; i < startDow; i++) cells += `<div class="calc mute"></div>`;
    for (let dd = 1; dd <= daysIn; dd++) {
      const iso = `${Y}-${String(M).padStart(2, "0")}-${String(dd).padStart(2, "0")}`;
      const cnt = counts[iso] || 0;
      cells += `<div class="calc${cnt ? " has" : ""}"><span class="cd">${dd}</span>${cnt ? `<button class="cn" data-d="${iso}">${cnt}件</button>` : ""}</div>`;
    }
    ov.innerHTML = `<div class="calbox">
      <div class="calhd"><button class="calnav" data-nav="-1">‹</button><b>${Y}年${M}月</b><button class="calnav" data-nav="1">›</button><button class="calx">${ic("close")}</button></div>
      <div class="calgrid">${["日", "月", "火", "水", "木", "金", "土"].map(w => `<div class="calw">${w}</div>`).join("")}${cells}</div>
      <p class="cnote" style="margin:10px 2px 0;">${note || "件数をタップするとその日付で絞り込みます"}</p>
    </div>`;
    ov.onclick = e => { if (e.target === ov) close(); };
    ov.querySelector(".calx").onclick = close;
    ov.querySelectorAll(".calnav").forEach(b => {
      b.onclick = () => {
        const nd = new Date(Y, M - 1 + (+b.dataset.nav), 1);
        ym = `${nd.getFullYear()}-${String(nd.getMonth() + 1).padStart(2, "0")}`;
        render();
      };
    });
    ov.querySelectorAll(".cn").forEach(b => {
      b.onclick = () => { onPick(b.dataset.d); close(); };
    });
  };
  render();
}

/* ---------- home ---------- */
const PAGE = 12;                 // 1回に表示する件数
let shown = PAGE;                // 現在の表示件数
let keyword = "";                // キーワード検索
let unplayedOnly = false;        // 未プレイのみ
let sortDesc = true;             // true = 新しい順
let io = null;                   // 無限スクロール用オブザーバ
let activeDate = "";             // 日付フィルタ（カレンダーから設定）

function playedIds() {
  return new Set(store.results.map(r => r.id));
}

function filteredSessions() {
  const done = playedIds();
  const kw = keyword.trim().toLowerCase();
  let list = sessions.filter(s => {
    if (activeCat && !(s.categories || []).includes(activeCat)) return false;
    if (activeDate && s.date !== activeDate) return false;
    if (unplayedOnly && done.has(s.id)) return false;
    if (kw) {
      const hay = [
        s.news.headline, s.news.essence, s.news.source, s.date,
        ...(s.categories || []),
        ...(s.calls || []).map(c => `${c.name} ${c.ticker}`),
        ...(s.call_names || [])
      ].join(" ").toLowerCase();
      if (!hay.includes(kw)) return false;
    }
    return true;
  });
  list.sort((a, b) => sortDesc
    ? (b.date || "").localeCompare(a.date || "")
    : (a.date || "").localeCompare(b.date || ""));
  return list;
}

function cardHTML(s, i) {
  const tags = (s.categories || []).map(c => `<span class="cat">${c}</span>`).join("");
  const src = s.news.source
    ? (s.news.source_url
      ? `<a class="src link" href="${s.news.source_url}" target="_blank" rel="noopener noreferrer" title="出典を開く">${ic("newspaper")} ${s.news.source} ${ic("open_in_new")}</a>`
      : `<span class="src">${ic("newspaper")} ${s.news.source}</span>`)
    : "";
  const done = playedIds().has(s.id) ? `<span class="doneb">${ic("check_circle", 1)} 挑戦済み</span>` : "";
  return `
    <div class="badges"><button class="bd bdate" data-cal="1" title="カレンダーで絞り込む">${ic("calendar_month")} ${s.date || ""}</button>${src}${tags}${done}</div>
    <h3>${s.news.headline}</h3>
    <p class="es">${s.news.essence || ""}</p>
    <div class="row">
      <button class="btn primary" data-a="play" data-i="${i}">挑戦する ${ic("rocket_launch")}</button>
      <button class="btn ghost" data-a="ans" data-i="${i}">結果を見る ${ic("visibility")}</button>
    </div>`;
}

/* 投稿数付きカレンダーを開き、日付でHOME一覧を絞り込む */
function openHomeCalendar() {
  const counts = {};
  sessions.forEach(s => { if (s.date) counts[s.date] = (counts[s.date] || 0) + 1; });
  openCalendar(counts, iso => {
    activeDate = activeDate === iso ? "" : iso;
    shown = PAGE;
    renderHome(true);
  }, "件数をタップするとその日付のニュースだけを表示します");
}

function renderToolbar() {
  const cats = [...new Set(sessions.flatMap(s => s.categories || []))];
  const tb = $("toolbar");
  tb.innerHTML = `
    <div class="searchrow">
      <label class="searchbox">
        <span class="ms">search</span>
        <input type="search" id="kw" placeholder="見出し・銘柄名などで検索" value="${keyword.replace(/"/g, "&quot;")}">
      </label>
      <button class="chip tgl${unplayedOnly ? " active" : ""}" id="unplayed">${ic("radio_button_unchecked")} 未プレイのみ</button>
      <button class="chip tgl" id="sortBtn">${ic(sortDesc ? "arrow_downward" : "arrow_upward")} ${sortDesc ? "新しい順" : "古い順"}</button>
    </div>`;
  const kwInput = $("kw");
  kwInput.oninput = () => { keyword = kwInput.value; shown = PAGE; renderList(); };
  $("unplayed").onclick = () => { unplayedOnly = !unplayedOnly; shown = PAGE; renderHome(true); };
  $("sortBtn").onclick = () => { sortDesc = !sortDesc; shown = PAGE; renderHome(true); };

  const filt = $("filters");
  filt.innerHTML = "";
  if (activeDate) {
    const dc = document.createElement("button");
    dc.className = "chip active";
    dc.innerHTML = `${ic("calendar_month")} ${activeDate} ${ic("close")}`;
    dc.onclick = () => { activeDate = ""; shown = PAGE; renderHome(true); };
    filt.appendChild(dc);
  }
  if (cats.length) {
    const all = document.createElement("button");
    all.className = "chip" + (activeCat === null ? " active" : "");
    all.textContent = "すべて";
    all.onclick = () => { activeCat = null; shown = PAGE; renderHome(true); };
    filt.appendChild(all);
    cats.forEach(c => {
      const b = document.createElement("button");
      b.className = "chip" + (activeCat === c ? " active" : "");
      b.textContent = c;
      b.onclick = () => { activeCat = (activeCat === c ? null : c); shown = PAGE; renderHome(true); };
      filt.appendChild(b);
    });
  }
}

function renderList() {
  const list = $("list");
  const view = filteredSessions();
  const slice = view.slice(0, shown);

  list.innerHTML = "";
  slice.forEach(s => {
    const d = document.createElement("div");
    d.className = "scard";
    d.innerHTML = cardHTML(s, sessions.indexOf(s));
    list.appendChild(d);
  });

  if (!slice.length) {
    list.innerHTML = `<p class="empty">条件に合うニュースが見つかりませんでした ${ic("inbox")}</p>`;
  }

  // 件数表示と無限スクロールの番人
  const foot = $("listFoot");
  if (view.length > slice.length) {
    foot.innerHTML = `<div class="sentinel" id="sentinel"><span class="ms spin">progress_activity</span> 読み込み中…</div>`;
    observeSentinel();
  } else {
    foot.innerHTML = view.length
      ? `<p class="listcount">${view.length}件をすべて表示しました</p>`
      : "";
  }
}

/* スケルトンカード（無限スクロールの読み込み表示。カードの形のまま光が走る） */
let loadingMore = false;
function skeletonCardHTML() {
  return `
    <span class="ms spin sksp">progress_activity</span>
    <div class="badges"><span class="skl" style="width:86px;height:18px;"></span><span class="skl" style="width:72px;height:18px;"></span><span class="skl" style="width:58px;height:18px;"></span></div>
    <div class="skl" style="height:16px;margin:6px 0 7px;"></div>
    <div class="skl" style="height:16px;width:68%;margin-bottom:13px;"></div>
    <div class="skl" style="height:11px;margin-bottom:7px;"></div>
    <div class="skl" style="height:11px;margin-bottom:7px;"></div>
    <div class="skl" style="height:11px;width:55%;margin-bottom:16px;"></div>
    <div class="row"><span class="skl" style="width:118px;height:40px;border-radius:999px;"></span><span class="skl" style="width:118px;height:40px;border-radius:999px;"></span></div>`;
}

function observeSentinel() {
  const el = $("sentinel");
  if (!el) return;
  if (io) io.disconnect();
  io = new IntersectionObserver(entries => {
    if (entries.some(e => e.isIntersecting) && !loadingMore) {
      loadingMore = true;
      // 次に読み込まれる位置へスケルトンカードを差し込む
      const list = $("list");
      const remaining = filteredSessions().length - shown;
      const n = Math.max(1, Math.min(PAGE, remaining));
      for (let i = 0; i < n; i++) {
        const d = document.createElement("div");
        d.className = "scard skcard";
        d.innerHTML = skeletonCardHTML();
        list.appendChild(d);
      }
      $("listFoot").innerHTML = "";   // 番人を消して二重発火を防ぐ
      setTimeout(() => {
        shown += PAGE;
        renderList();                  // 実カードで置き換え
        loadingMore = false;
      }, 600);
    }
  }, { rootMargin: "0px" });   // 画面内に入ってから発火（スケルトンが見える位置で出す）
  io.observe(el);
}

function renderHome(keepScroll) {
  const y = keepScroll ? window.scrollY : 0;
  renderToolbar();
  renderList();

  $("list").onclick = async ev => {
    const b = ev.target.closest("button");
    if (!b) return;
    if (b.dataset.cal) { openHomeCalendar(); return; }   // 日付バッヂ → カレンダーモーダル
    const s = sessions[+b.dataset.i];
    // 記事データの取得中はボタンにスピナーを表示
    const orig = b.innerHTML;
    b.disabled = true;
    b.innerHTML = `<span class="ms spin">progress_activity</span> 読み込み中…`;
    try {
      await ensureDetail(s);
    } catch (e) {
      b.disabled = false;
      b.innerHTML = orig;
      alert("記事の読み込みに失敗しました。通信環境をご確認ください。");
      return;
    }
    b.disabled = false;
    b.innerHTML = orig;
    if (b.dataset.a === "ans") showAnswers(s); else startPlay(s);
  };
  show("home");
  setNav("home");
  if (keepScroll) window.scrollTo(0, y);
}
function setNav(name) {
  document.querySelectorAll(".gnav-item").forEach(b =>
    b.classList.toggle("active", b.dataset.nav === name));
}
$("backBtn").onclick = () => renderHome();
$("homeLink").onclick = () => renderHome();
$("homeBtn").onclick = () => renderHome();
$("statsBtn").onclick = () => { setNav("stats"); showStats(); };
$("notesBtn").onclick = () => { setNav("notes"); showNotes(); };
$("patternsBtn").onclick = () => { setNav("patterns"); showPatterns(); };
$("calNavBtn").onclick = () => { renderHome(); openHomeCalendar(); };   // HOMEに戻してカレンダーを開く

/* ---------- 連想パターン図鑑 ---------- */
let patternsCache = null;
async function showPatterns() {
  show("stage");
  $("scoreBox").innerHTML = "";
  // learning を持つ記事だけの軽量API。無ければ手元のセッション（ファイル読み込み時など）から
  let items = sessions.filter(s => s.learning);
  if (!items.length) {
    if (!patternsCache) {
      try { patternsCache = await apiGet("api/patterns"); } catch (_) { patternsCache = []; }
    }
    items = patternsCache.map(p => {
      const s = sessions.find(x => x.id === p.id);
      return { ...p, news: s ? s.news : { headline: "" } };
    });
  }
  let h = `<div class="stepno">${ic("menu_book")} 連想パターン図鑑</div>
    <p class="cnote" style="margin-bottom:14px;">これまでのニュースで学んだ汎用パターン ${items.length} 件。ニュースを見たらまずここの「型」に当てはまるか考える。</p>`;
  if (!items.length) {
    h += `<p class="empty">まだパターンがありません。ニュースを追加していこう！</p>`;
  } else {
    items.forEach(s => {
      const tags = (s.categories || []).map(c => `<span class="cat">${c}</span>`).join("");
      h += `<div class="anscard">
        <div class="badges"><span class="bd">${ic("calendar_month")} ${s.date || ""}</span>${tags}</div>
        <p class="cnote" style="margin:2px 0 8px;">${(s.news && s.news.headline) || ""}</p>
        <div class="areason" style="background:var(--yellow-soft);"><b style="color:#a9861f;">${ic("lightbulb", 1)} パターン</b>${s.learning}</div>
      </div>`;
    });
  }
  $("stageBody").innerHTML = h;
  window.scrollTo({ top: 0, behavior: "smooth" });
}

/* ---------- play ---------- */
function prefetchCalls(s) {
  return (s.calls && s.calls.length)
    ? apiGet(`api/calls/${s.id}`).catch(() => null)
    : null;
}

function shuffle(arr) {
  const a = [...arr];
  for (let i = a.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1));
    [a[i], a[j]] = [a[j], a[i]];
  }
  return a;
}

/* 解説内の {A}{B}{C}{D} を表示順のラベルに変換する。
   order[disp] = 元の選択肢index。元index → 表示ラベルの対応で置換する。
   order 省略時は元の並び順（A=0,B=1…）でそのまま表示。 */
const IDENT = [0, 1, 2, 3, 4];
function subMarks(reason, order) {
  if (!reason) return reason || "";
  const map = {};
  (order || IDENT).forEach((orig, disp) => { map[orig] = MARKS[disp]; });
  return reason.replace(/\{([A-E])\}/g, (m, L) => {
    const v = map[L.charCodeAt(0) - 65];
    return v !== undefined ? v : L;
  });
}

function startPlay(s) {
  cur = s; idx = 0; live = 0; sessionAnswers = [];
  callsPromise = prefetchCalls(s);
  show("stage");
  setNav(null);
  renderQ();
}

function glossaryHTML(q) {
  const g = q.glossary || [];
  if (!g.length) return "";
  const items = g.map(t => `<div class="gterm"><b>${t.t}</b><span>${t.d}</span></div>`).join("");
  return `<details class="glossary"><summary>${ic("menu_book")} 用語解説（${g.length}）</summary><div class="gbody">${items}</div></details>`;
}

function renderQ() {
  answered = false;
  const q = cur.questions[idx];
  order = shuffle(q.options.map((_, i) => i));   // 選択肢をシャッフル
  $("scoreBox").innerHTML = `${ic("star", 1)} <b>${live}</b> / ${cur.questions.length}`;
  $("stageBody").innerHTML = `
    <div class="bar"><i style="width:${idx / cur.questions.length * 100}%"></i></div>
    <div class="stepno">${q.step}</div>
    <div class="evt">${ic("newspaper")} ${cur.news.source ? cur.news.source + "｜" : ""}${cur.news.headline}</div>
    <div class="qq">${q.q}</div><div class="opts" id="opts"></div>
    ${glossaryHTML(q)}
    <div class="analyst" id="fb"><div class="who"><img class="av" src="images/analyst.png" alt="" width="38" height="38"><b>シニアアナリストより</b></div><p id="fbTxt"></p></div>
    <button class="next" id="nx">${idx === cur.questions.length - 1 ? `結果を見る ${ic("celebration")}` : `次へ ${ic("arrow_forward")}`}</button>`;
  const opts = $("opts");
  order.forEach((orig, disp) => {
    const b = document.createElement("button");
    b.className = "opt";
    b.innerHTML = `<span class="mk">${MARKS[disp]}</span><span>${q.options[orig]}</span>`;
    b.onclick = () => choose(disp);
    opts.appendChild(b);
  });
  $("nx").onclick = () => { idx++; if (idx >= cur.questions.length) playResult(); else renderQ(); };
  window.scrollTo({ top: 0, behavior: "smooth" });
}

function choose(disp) {
  if (answered) return;
  answered = true;
  const q = cur.questions[idx];
  const orig = order[disp];
  const ok = orig === q.correct;
  if (ok) live++;
  $("scoreBox").innerHTML = `${ic("star", 1)} <b>${live}</b> / ${cur.questions.length}`;
  [...document.querySelectorAll(".opt")].forEach((b, i) => {
    b.disabled = true;
    const o = order[i];
    if (o === q.correct) b.classList.add("correct");
    else if (i === disp) b.classList.add("wrong");
    else b.classList.add("faded");
  });
  // 間違いノート用に記録
  sessionAnswers.push({
    st: q.step, q: q.q, ok,
    chosen: q.options[orig], corr: q.options[q.correct], reason: q.reason
  });
  $("fbTxt").innerHTML = `<b>${ok ? `正解 ${ic("check_circle", 1)}` : `不正解 ${ic("cancel", 1)}`}</b> ${subMarks(q.reason, order)}`;
  $("fb").classList.add("show");
  $("nx").classList.add("show");
}

function playResult() {
  const n = cur.questions.length;
  // 成績をローカル保存（復習モードは保存しない）
  if (!cur.__review) {
    store.addResult({
      id: cur.id, headline: cur.news.headline, cats: cur.categories || [],
      at: new Date().toISOString(), score: live, total: n, answers: sessionAnswers
    });
  }

  const stars = Array(live).fill(ic("star", 1)).join("") + Array(n - live).fill(ic("star")).join("");
  let t;
  if (live === n) { t = "パーフェクト。文句なし " + ic("trophy", 1); burstConfetti(); }
  else if (live >= n - 1) { t = "惜しい。あと一歩 " + ic("auto_awesome", 1); burstConfetti(); }
  else if (live >= 2) { t = "良い調子。もう一段掘ろう " + ic("fitness_center"); }
  else { t = "難問揃い。もう一度挑戦を " + ic("local_fire_department", 1); }
  let h = `<div class="result"><div class="stars">${stars}</div><div class="big">${live}<small> / ${n}</small></div><h2>${t}</h2></div>`;
  if (cur.review) h += `<div class="band sec"><b>${ic("school", 1)} シニアアナリストの講評</b>${cur.review}</div>`;
  if (cur.learning) h += `<div class="band chk"><b>${ic("lightbulb", 1)} 今回の学び</b>${cur.learning}</div>`;
  if (cur.calls && cur.calls.length) h += `<div class="calls" id="callsBox"></div>`;
  h += `<div class="row" style="justify-content:center;margin-top:6px;">
    <button class="btn primary" id="again">もう一度 ${ic("replay")}</button></div>`;
  $("scoreBox").innerHTML = "";
  $("stageBody").innerHTML = h;
  $("again").onclick = () => startPlay(cur);
  if (cur.calls && cur.calls.length) loadCalls();
  window.scrollTo({ top: 0, behavior: "smooth" });
}

/* ---------- 解答一覧 ---------- */
function showAnswers(s) {
  cur = s;
  callsPromise = prefetchCalls(s);
  show("stage");
  setNav(null);
  $("scoreBox").innerHTML = "";
  let h = `<div class="stepno">解答一覧</div>
    <div class="evt">${ic("newspaper")} ${s.news.source ? s.news.source + "｜" : ""}${s.news.headline}</div>`;
  s.questions.forEach(q => {
    h += `<div class="anscard"><div class="ast">${q.step}</div><div class="aq">${q.q}</div>`;
    q.options.forEach((o, k) => {
      h += `<div class="aopt${k === q.correct ? " ok" : ""}"><span class="mk">${MARKS[k]}</span><span>${o}</span>${k === q.correct ? '<span class="abadge">正解</span>' : ""}</div>`;
    });
    h += glossaryHTML(q);
    h += `<div class="areason"><b>解説</b>${subMarks(q.reason)}</div></div>`;
  });
  if (s.learning) h += `<div class="band chk"><b>${ic("lightbulb", 1)} 今回の学び</b>${s.learning}</div>`;
  if (s.calls && s.calls.length) h += `<div class="calls" id="callsBox"></div>`;
  h += `<div class="row" style="justify-content:center;margin-top:6px;">
    <button class="btn primary" id="goPlay">挑戦する ${ic("rocket_launch")}</button></div>`;
  $("stageBody").innerHTML = h;
  $("goPlay").onclick = () => startPlay(s);
  if (s.calls && s.calls.length) loadCalls();
  window.scrollTo({ top: 0, behavior: "smooth" });
}

/* ---------- 遊びコール（T+N固定・市場相対で答え合わせ） ---------- */
const CALL_INTRO = "「もしこのニュースで一つ賭けるなら」というシニアアナリスト役の予想です。＋は「上がるかも」、−は「下がるかも」の向きだけを当てにいく遊びで、投資助言ではありません。";

function judgeLegendHTML() {
  return `<details class="glossary"><summary>${ic("help")} 判定の見方（的中・外れ・横ばい）</summary><div class="gbody">
    <div class="gterm"><b>${ic("target")} 的中</b><span>予想した向きに、しかも市場平均より強く動いた。＋予想なら市場超え、−予想なら市場割れ。</span></div>
    <div class="gterm"><b>${ic("cancel")} 外れ</b><span>予想と逆に動いた。株価が上がっても「−（下がる）」予想なら外れになる——上がった＝的中ではない。</span></div>
    <div class="gterm"><b>${ic("trending_flat")} 横ばい</b><span>市場平均との差が±1%以内で、勝ち負けがはっきりしない状態。</span></div>
    <div class="gterm"><b>なぜ市場平均と比べる？</b><span>地合いで全体が上がっただけでは実力とは言えないため、市場平均（ベンチマーク）を引いた相対リターンで判定します。</span></div>
  </div></details>`;
}

function judgeBadge(rel, dir) {
  if (Math.abs(rel) <= 1) return `<span class="vb flat">${ic("trending_flat")} 横ばい</span>`;
  const hit = (dir === "+" && rel > 0) || (dir === "-" && rel < 0);
  return hit ? `<span class="vb hit">${ic("target")} 的中</span>` : `<span class="vb miss">${ic("cancel")} 外れ</span>`;
}

function fmtWin(w, label, bench, dir) {
  if (!w) return "";
  if (w.status !== "done") return `<span class="ewin pend">${label}: 経過待ち（あと${w.remaining}営業日）</span>`;
  const s1 = w.chg > 0 ? "+" : "", s2 = w.rel > 0 ? "+" : "";
  return `<span class="ewin">${label}: ${s1}${w.chg}%（${bench}比 ${s2}${w.rel}%）${judgeBadge(w.rel, dir)}</span>`;
}

async function loadCalls() {
  const box = $("callsBox");
  box.innerHTML = `<b class="ct">${ic("casino")} シニアアナリストの遊びコール</b><p class="cnote">株価を取得中…</p>`;
  try {
    const d = await callsPromise;
    if (!d) throw 0;
    let h = `<b class="ct">${ic("casino")} シニアアナリストの遊びコール</b>
      <p class="cnote callintro">${CALL_INTRO}</p>`;
    d.calls.forEach(c => {
      const yen = c.ticker.endsWith(".T") ? "¥"
        : (c.ticker.endsWith(".KS") || c.ticker.endsWith(".KQ")) ? "₩" : "$";
      const dir = c.direction === "+"
        ? `<span class="dir up">${ic("trending_up")} 上がるかも</span>`
        : `<span class="dir dn">${ic("trending_down")} 下がるかも</span>`;
      h += `<div class="call"><div class="chd"><b>${c.name}</b><span class="tik">${c.ticker}</span>${dir}</div>
        <p class="basis">${c.basis}</p>`;
      if (c.status === "recorded") {
        h += `<p class="verdict rec">${ic("push_pin", 1)} ニュース当日（${c.called_at}）の株価 ${yen}${c.price_at_call.toLocaleString()} を記録。答え合わせは数日後にもう一度！</p>`;
      } else if (c.status === "checked") {
        const chg = c.change_pct;
        const sign = chg > 0 ? "+" : "";
        let nowRel = "";
        if (c.eval && c.eval.now) {
          const r = c.eval.now.rel, s = r > 0 ? "+" : "";
          nowRel = `（${c.bench || "市場"}比 ${s}${r}%）`;
        }
        h += `<p class="verdict">ニュース時（${c.called_at}）${yen}${c.price_at_call.toLocaleString()} → 現在 ${yen}${c.current.toLocaleString()}（${sign}${chg}%）${nowRel}</p>`;
        if (c.eval) {
          h += `<p class="verdict evalline">${fmtWin(c.eval.t5, "T+5", c.bench, c.direction)}<br>${fmtWin(c.eval.t20, "T+20", c.bench, c.direction)}</p>`;
        }
      } else {
        h += `<p class="verdict err">${ic("warning")} 株価を取得できませんでした（${c.ticker}）</p>`;
      }
      if (c.history && c.history.length > 1 && c.price_at_call) h += buildOneChart(c);
      h += `</div>`;
    });
    h += judgeLegendHTML();
    h += `<p class="cnote">※ T+5／T+20は、ニュースから5・20営業日後の固定の答え合わせタイミング。あくまで遊びで、投資助言ではありません。${d.frozen ? "<br>❄ このニュースはT+20の判定が確定済みです（株価は判定時点のもの）。" : ""}</p>`;
    box.innerHTML = h;
  } catch (e) {
    box.querySelector(".cnote").textContent = "株価を取得できませんでした（サーバー未起動またはオフライン）";
  }
}

/* ---------- 騰落率チャート ---------- */
function buildOneChart(c) {
  const vals = c.history.map(hh => (hh.p / c.price_at_call - 1) * 100);
  const dates = c.history.map(hh => hh.d);
  const isos = c.history.map(hh => hh.iso || "");
  const n = vals.length;
  let min = 0, max = 0;
  vals.forEach(v => { min = Math.min(min, v); max = Math.max(max, v); });
  const pad = (max - min) * 0.18 || 1;
  min -= pad; max += pad;
  const W = 560, H = 140, L = 50, R = 14, T = 14, B = 24;
  const x = i => L + (W - L - R) * (n === 1 ? 0 : i / (n - 1));
  const y = v => T + (H - T - B) * (1 - (v - min) / (max - min));
  const chg = vals[n - 1];
  const lineColor = "#2e3a67";
  const dotFill = chg >= 0 ? "#5cb85c" : "#f4693c";

  let svg = `<svg viewBox="0 0 ${W} ${H}" class="plchart" role="img">`;
  svg += `<line x1="${L}" x2="${W - R}" y1="${y(0)}" y2="${y(0)}" stroke="#b4b0a4" stroke-width="1.5" stroke-dasharray="5 5"/>`;
  svg += `<text x="${L - 7}" y="${y(0) + 4}" text-anchor="end" font-size="10.5" fill="#9aa0ab">0%</text>`;
  svg += `<text x="${L - 7}" y="${T + 8}" text-anchor="end" font-size="10.5" fill="#9aa0ab">${max.toFixed(1)}%</text>`;
  svg += `<text x="${L - 7}" y="${H - B}" text-anchor="end" font-size="10.5" fill="#9aa0ab">${min.toFixed(1)}%</text>`;
  let newsIdx = isos.findIndex(iso => iso >= (c.called_at || ""));
  if (newsIdx < 0) newsIdx = n - 1;   // ニュース日が履歴の後（休場中など）→ 右端に置く
  const nx = x(newsIdx);
  svg += `<line x1="${nx.toFixed(1)}" x2="${nx.toFixed(1)}" y1="${T}" y2="${H - B}" stroke="#f4693c" stroke-width="1.5" stroke-dasharray="3 4"/>`;

  // ベンチマーク線（ニュース日=0%基準）を薄く重ねる
  let hasBench = false;
  if (c.bench_history && c.bench_history.length > 1) {
    const bmap = c.bench_history.map(b => ({ iso: b.iso, p: b.p }));
    const benchAt = iso => {
      let prev = null;
      for (const b of bmap) { if (b.iso <= iso) prev = b.p; else break; }
      return prev;
    };
    const b0 = benchAt(isos[newsIdx]);
    if (b0) {
      const bvals = isos.map(iso => {
        const bp = benchAt(iso);
        return bp ? (bp / b0 - 1) * 100 : null;
      });
      const bd = bvals.map((v, i) => v === null ? "" : `${i && bvals[i - 1] !== null ? "L" : "M"}${x(i).toFixed(1)},${y(Math.max(min, Math.min(max, v))).toFixed(1)}`).join("");
      if (bd) {
        svg += `<path d="${bd}" fill="none" stroke="#56b7e6" stroke-width="2" stroke-dasharray="6 4" stroke-linecap="round" opacity=".9"/>`;
        hasBench = true;
      }
    }
  }

  // T+5 / T+20 の判定日マーカー（到達済みの時だけ描く）
  const drawnMarkers = [];
  [[5, "#a58bd8", "T+5"], [20, "#5cb85c", "T+20"]].forEach(([nDays, col, label]) => {
    const j = newsIdx + nDays;
    if (j < n) {
      const tx = x(j);
      svg += `<line x1="${tx.toFixed(1)}" x2="${tx.toFixed(1)}" y1="${T}" y2="${H - B}" stroke="${col}" stroke-width="1.5" stroke-dasharray="2 4"/>`;
      svg += `<text x="${tx.toFixed(1)}" y="${T + 9}" text-anchor="middle" font-size="9.5" font-weight="bold" fill="${col}">${label}</text>`;
      drawnMarkers.push([col, label]);
    }
  });

  const d = vals.map((v, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join("");
  svg += `<path d="${d}" fill="none" stroke="${lineColor}" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"/>`;
  svg += `<circle cx="${x(n - 1).toFixed(1)}" cy="${y(chg).toFixed(1)}" r="4.5" fill="${dotFill}" stroke="#fff" stroke-width="2"/>`;
  // 日付ラベル: ニュースラベルが左端・右端に寄る時は通常の日付と重ならないよう出し分ける
  const newsLabel = `${dates[newsIdx]} ニュース`;
  const nearLeft = nx < L + 80;          // 左端の日付ラベルと重なる位置
  const nearRight = nx > W - R - 80;     // 右端の日付ラベルと重なる位置
  if (!nearLeft) {
    svg += `<text x="${L}" y="${H - 6}" font-size="10.5" fill="#9aa0ab">${dates[0]}</text>`;
  }
  if (nearRight) {
    svg += `<text x="${W - R}" y="${H - 6}" text-anchor="end" font-size="10.5" fill="#f4693c" font-weight="bold">${newsLabel}</text>`;
  } else if (nearLeft) {
    svg += `<text x="${L}" y="${H - 6}" font-size="10.5" fill="#f4693c" font-weight="bold">${newsLabel}</text>`;
    svg += `<text x="${W - R}" y="${H - 6}" text-anchor="end" font-size="10.5" fill="#9aa0ab">${dates[n - 1]}</text>`;
  } else {
    svg += `<text x="${nx.toFixed(1)}" y="${H - 6}" text-anchor="middle" font-size="10.5" fill="#f4693c" font-weight="bold">${newsLabel}</text>`;
    svg += `<text x="${W - R}" y="${H - 6}" text-anchor="end" font-size="10.5" fill="#9aa0ab">${dates[n - 1]}</text>`;
  }
  svg += `</svg>`;

  const sign = chg > 0 ? "+" : "";
  const chgBadge = `<span class="chg ${chg >= 0 ? "cpos" : "cneg"}">${sign}${chg.toFixed(2)}%</span>`;
  const markerLegend = drawnMarkers
    .map(([col, label]) => `<span class="lg"><i style="background:${col}"></i>${label}</span>`).join("");
  const legend = hasBench
    ? `<div class="legend"><span class="lg"><i style="background:#2e3a67"></i>${c.name}</span><span class="lg"><i style="background:#56b7e6"></i>${c.bench || "ベンチマーク"}</span>${markerLegend}</div>`
    : "";
  return `<div class="chartwrap">
    <div class="chead2"><b class="ct2">${ic("show_chart")} 騰落率（ニュース日の終値 = 0%）</b>${chgBadge}</div>
    ${svg}${legend}
  </div>`;
}

/* ---------- 成績表 ---------- */
function stepNum(st) {
  const m = (st || "").match(/[①②③④⑤⑥]/);
  return m ? m[0] : "?";
}

function showStats() {
  show("stage");
  $("scoreBox").innerHTML = "";
  const rs = store.results;
  let h = `<div class="stepno">${ic("leaderboard")} 成績表</div>`;

  if (!rs.length) {
    h += `<p class="empty">まだプレイ記録がありません。まずは1問挑戦を！</p>`;
  } else {
    const plays = rs.length;
    const avg = (rs.reduce((a, r) => a + r.score / r.total, 0) / plays * 100).toFixed(0);
    const perfect = rs.filter(r => r.score === r.total).length;
    const streak = calcStreak(rs);
    const rank = calcTitle(rs);
    h += `<div class="titlecard">
      <span class="ms fill" style="font-size:30px;">workspace_premium</span>
      <div><b>${rank.title}</b>
      <small>通算正解 ${rank.totalCorrect} 問${rank.next ? `｜あと ${rank.next.need} 問で ${rank.next.name} に昇進` : "｜最高位です"}</small></div>
    </div>`;
    h += `<div class="statgrid">
      <div class="statcard"><b>${plays}</b><span>プレイ回数</span></div>
      <div class="statcard"><b>${avg}%</b><span>平均正答率</span></div>
      <div class="statcard"><b>${perfect}</b><span>パーフェクト</span></div>
      <div class="statcard"><b>${ic("local_fire_department", 1)} ${streak.current}</b><span>連続日数（最高${streak.best}）</span></div>
    </div>`;

    // カテゴリ別正答率（このゲームの弱点マップ）
    const cats = {};
    rs.forEach(r => (r.cats || []).forEach(c => {
      cats[c] = cats[c] || { ok: 0, all: 0 };
      cats[c].ok += r.score; cats[c].all += r.total;
    }));
    const catKeys = Object.keys(cats);
    if (catKeys.length) {
      h += `<div class="anscard"><div class="aq">カテゴリ別の正答率（苦手な経路はどこ？）</div>`;
      catKeys.sort((a, b) => cats[a].ok / cats[a].all - cats[b].ok / cats[b].all).forEach(k => {
        const pct = Math.round(cats[k].ok / cats[k].all * 100);
        h += `<div class="sbarrow"><span class="sblabel">${k}</span>
          <div class="sbar"><i style="width:${pct}%"></i></div><span class="sbpct">${pct}%（${cats[k].ok}/${cats[k].all}）</span></div>`;
      });
      h += `</div>`;
    }

    // ステップ別正答率
    const steps = {};
    rs.forEach(r => (r.answers || []).forEach(a => {
      const k = stepNum(a.st);
      steps[k] = steps[k] || { ok: 0, all: 0 };
      steps[k].all++; if (a.ok) steps[k].ok++;
    }));
    const labels = { "①": "本質", "②": "一次+", "③": "一次−", "④": "二次影響", "⑤": "逆シナリオ", "⑥": "検証" };
    h += `<div class="anscard"><div class="aq">ステップ別の正答率（弱点はどこ？）</div>`;
    ["①", "②", "③", "④", "⑤", "⑥"].forEach(k => {
      if (!steps[k]) return;
      const pct = Math.round(steps[k].ok / steps[k].all * 100);
      h += `<div class="sbarrow"><span class="sblabel">${k} ${labels[k] || ""}</span>
        <div class="sbar"><i style="width:${pct}%"></i></div><span class="sbpct">${pct}%（${steps[k].ok}/${steps[k].all}）</span></div>`;
    });
    h += `</div>`;

    // 直近のプレイ履歴（日付クリックでカレンダーフィルタ）
    h += `<div class="anscard"><div class="aq">直近のプレイ${statsDate
      ? ` <button class="chip active sm" id="pdClear" style="font-size:11px;">${ic("calendar_month")} ${statsDate} ${ic("close")}</button>` : ""}</div>`;
    const hist = statsDate
      ? rs.filter(r => (r.at || "").slice(0, 10) === statsDate).reverse()
      : rs.slice(-8).reverse();
    if (!hist.length) h += `<p class="cnote">この日付のプレイはありません。</p>`;
    hist.forEach(r => {
      const pd = (r.at || "").slice(0, 10);
      h += `<div class="hrow"><button class="hdate hdbtn" data-pd="${pd}" title="カレンダーで絞り込む">${pd}</button><span class="hname">${r.headline}</span><span class="hscore">${r.score}/${r.total}</span></div>`;
    });
    h += `</div>`;
  }

  // アナリストの遊びコール成績
  h += `<div class="anscard" id="callStatsBox"><div class="aq">${ic("casino")} 遊びコールの通算成績（市場相対・T+20優先）</div>
    <p class="cnote" id="csNote">株価データから集計します。</p>
    <div class="row"><button class="btn ghost" id="csBtn">集計する ${ic("sync")}</button></div>
    <div id="csBody"></div></div>`;

  h += `<div class="row" style="margin-top:16px;">
    <button class="btn ghost sm" id="expBtn">${ic("download")} バックアップを書き出す</button>
    <button class="btn ghost sm" id="impBtn">${ic("upload")} 読み込む</button>
    <input type="file" id="impFile" accept=".json" style="display:none;">
  </div>
  <p class="cnote" style="margin-top:10px;">${ic("lock")} 成績はこの端末のブラウザ内にのみ保存されています（サーバー送信なし）。書き出したJSONで別端末への引っ越しができます。
    <button class="linkbtn" id="clearBtn">保存データをすべて削除</button></p>`;

  $("stageBody").innerHTML = h;
  // 直近のプレイ: 日付→カレンダー
  $("stageBody").querySelectorAll("[data-pd]").forEach(b => {
    b.onclick = () => {
      const counts = {};
      store.results.forEach(r => {
        const pd = (r.at || "").slice(0, 10);
        if (pd) counts[pd] = (counts[pd] || 0) + 1;
      });
      openCalendar(counts, iso => {
        statsDate = statsDate === iso ? "" : iso;
        showStats();
      }, "件数をタップするとその日付のプレイだけを表示します");
    };
  });
  const pdc = $("pdClear");
  if (pdc) pdc.onclick = () => { statsDate = ""; showStats(); };
  $("csBtn").onclick = () => loadCallStats(true);
  $("expBtn").onclick = () => exportResults();
  $("impBtn").onclick = () => $("impFile").click();
  $("impFile").onchange = ev => { if (ev.target.files[0]) importResults(ev.target.files[0]); };
  $("clearBtn").onclick = () => {
    if (confirm("成績・間違いノート・コール成績キャッシュをこの端末から削除します。よろしいですか？")) {
      store.clearAll();
      showStats();
    }
  };

  // キャッシュがあれば即表示（6時間以内）
  const cache = store.callStats;
  if (cache && Date.now() - cache.ts < 6 * 3600 * 1000) renderCallStats(cache.rows, new Date(cache.ts));
  window.scrollTo({ top: 0, behavior: "smooth" });
}

/* 集計中のスケルトン（統計カード4枚＋銘柄行の形で光が走る） */
function csSkeletonHTML() {
  let cards = "";
  for (let i = 0; i < 4; i++) {
    cards += `<div class="statcard">
      <div class="skl" style="height:24px;width:56%;margin:0 auto 7px;"></div>
      <div class="skl" style="height:11px;width:74%;margin:0 auto;"></div></div>`;
  }
  let rows = "";
  for (let i = 0; i < 4; i++) {
    rows += `<div class="hrow"><span style="flex:1;min-width:0;">
      <div class="skl" style="height:13px;width:38%;margin-bottom:5px;"></div>
      <div class="skl" style="height:10px;width:72%;"></div></span>
      <span class="skl" style="width:78px;height:20px;border-radius:99px;flex:none;"></span>
      <span class="skl" style="width:112px;height:16px;flex:none;"></span></div>`;
  }
  return `<div class="statgrid" style="margin-top:12px;">${cards}</div>${rows}`;
}

async function loadCallStats(force) {
  const btn = $("csBtn");
  if (btn) {
    btn.disabled = true;
    btn.innerHTML = `<span class="ms spin">progress_activity</span> 集計中…`;
  }
  $("csNote").textContent = "集計中…（全ニュースの株価を取得しています）";
  $("csBody").innerHTML = csSkeletonHTML();
  const rows = [];
  for (const s of sessions) {
    if (!hasCalls(s)) continue;
    try {
      const d = await apiGet(`api/calls/${s.id}`);
      d.calls.forEach(c => {
        if (!c.eval) return;
        const e = c.eval;
        const w = (e.t20 && e.t20.status === "done") ? { ...e.t20, win: "T+20" }
          : (e.t5 && e.t5.status === "done") ? { ...e.t5, win: "T+5" }
          : { ...e.now, win: "経過中" };
        rows.push({ sid: s.id, news: s.news.headline, name: c.name, dir: c.direction, win: w.win, rel: w.rel, bench: c.bench || "", ticker: c.ticker, market: marketOf(c.ticker) });
      });
    } catch (err) { /* skip */ }
  }
  store.setCallStats({ ts: Date.now(), rows });
  renderCallStats(rows, new Date());
  if (btn) {
    btn.disabled = false;
    btn.innerHTML = `集計する ${ic("sync")}`;
  }
}

function marketOf(t) {
  if (!t) return "その他";
  if (t.endsWith(".T")) return "日本";
  if (t.endsWith(".KS") || t.endsWith(".KQ")) return "韓国";
  return "米国";
}

let csRows = [], csAsof = null, csMarket = "all", csStatus = "all";
let csSort = "all";        // "all" | "+" | "-" … コールの向きで並び替え
let csOpen = null;         // 展開中の行キー "sid|ticker"
const csDetail = {};       // sid → api/calls payload（詳細展開用キャッシュ）
let statsDate = "";        // 直近のプレイの日付フィルタ

function callStatus(r) {
  if (Math.abs(r.rel) <= 1) return "flat";
  if ((r.dir === "+" && r.rel > 0) || (r.dir === "-" && r.rel < 0)) return "hit";
  return "miss";
}

function renderCallStats(rows, asof) {
  csRows = rows; csAsof = asof; csMarket = "all"; csStatus = "all"; csSort = "all"; csOpen = null;
  renderCSView();
}

/* 展開行の詳細（根拠・価格・T+5/T+20・チャート） */
function csDetailHTML(r) {
  const p = csDetail[r.sid];
  if (!p) return "";
  const c = (p.calls || []).find(x => x.ticker === r.ticker);
  if (!c) return "";
  const yen = c.ticker.endsWith(".T") ? "¥"
    : (c.ticker.endsWith(".KS") || c.ticker.endsWith(".KQ")) ? "₩" : "$";
  let h = `<div class="csdetail">`;
  if (c.basis) h += `<p class="basis">${ic("psychology")} ${c.basis}</p>`;
  if (c.price_at_call && c.current != null) {
    const sign = c.change_pct > 0 ? "+" : "";
    h += `<p class="verdict">ニュース時（${c.called_at}）${yen}${c.price_at_call.toLocaleString()} → ${yen}${c.current.toLocaleString()}（${sign}${c.change_pct}%）</p>`;
  }
  if (c.eval) {
    h += `<p class="verdict evalline">${fmtWin(c.eval.t5, "T+5", c.bench, c.direction)}<br>${fmtWin(c.eval.t20, "T+20", c.bench, c.direction)}</p>`;
  }
  if (c.history && c.history.length > 1 && c.price_at_call) h += buildOneChart(c);
  h += `</div>`;
  return h;
}

function renderCSView() {
  if (!csRows.length) {
    $("csNote").textContent = "集計できるコールがまだありません。";
    $("csBody").innerHTML = "";
    return;
  }
  const all = csRows;
  const markets = ["日本", "韓国", "米国", "その他"].filter(m => all.some(r => (r.market || "その他") === m));
  const rows = csMarket === "all" ? all : all.filter(r => (r.market || "その他") === csMarket);

  // 市場フィルタチップ
  let h = `<div class="filters" style="justify-content:flex-start;margin:12px 0;">
    <button class="chip${csMarket === "all" ? " active" : ""}" data-m="all">すべて（${all.length}）</button>`;
  markets.forEach(m => {
    const n = all.filter(r => (r.market || "その他") === m).length;
    h += `<button class="chip${csMarket === m ? " active" : ""}" data-m="${m}">${m}（${n}）</button>`;
  });
  h += `</div>`;

  let hit = 0, miss = 0, flat = 0;
  rows.forEach(r => {
    if (Math.abs(r.rel) <= 1) flat++;
    else if ((r.dir === "+" && r.rel > 0) || (r.dir === "-" && r.rel < 0)) hit++;
    else miss++;
  });
  const decided = hit + miss;
  const rate = decided ? Math.round(hit / decided * 100) : 0;
  const on = k => csStatus === k ? " on" : "";
  h += `<div class="statgrid">
    <div class="statcard cstat${on("hit")}" data-cs="hit"><b>${ic("target")} ${hit}</b><span>的中</span></div>
    <div class="statcard cstat${on("miss")}" data-cs="miss"><b>${ic("cancel")} ${miss}</b><span>外れ</span></div>
    <div class="statcard cstat${on("flat")}" data-cs="flat"><b>${ic("trending_flat")} ${flat}</b><span>横ばい</span></div>
    <div class="statcard cstat${on("decided")}" data-cs="decided"><b>${rate}%</b><span>的中率</span></div>
  </div>`;
  h += judgeLegendHTML();
  if (csStatus !== "all") {
    const label = { hit: "的中", miss: "外れ", flat: "横ばい", decided: "判定済み（的中＋外れ）" }[csStatus];
    h += `<p class="cnote" style="margin-bottom:10px;">${ic("filter_alt")} 「${label}」で絞り込み中。<button class="linkbtn" id="csClear">解除</button></p>`;
  } else if (decided < 30) {
    h += `<p class="cnote" style="margin-bottom:10px;">${ic("info")} 判定済み ${decided} 件。サンプルが30件に満たない的中率は偶然と区別がつきません——数字よりも「なぜ外れたか（織り込み済み？逆シナリオ発動？）」を読むのが本番です。</p>`;
  }
  let listRows = csStatus === "all" ? rows
    : csStatus === "decided" ? rows.filter(r => callStatus(r) !== "flat")
    : rows.filter(r => callStatus(r) === csStatus);

  // コールの向きで絞り込み（＋/−の全件を表示。的中も外れも含み、0%だけ除外）
  h += `<div class="filters" style="justify-content:flex-start;margin:0 0 10px;">
    <span class="cnote" style="margin:4px 6px 0 0;">絞り込み:</span>
    <button class="chip${csSort === "all" ? " active" : ""}" data-sort="all">すべて</button>
    <button class="chip${csSort === "+" ? " active" : ""}" data-sort="+">＋コール（0%除く）</button>
    <button class="chip${csSort === "-" ? " active" : ""}" data-sort="-">−コール（0%除く）</button>
  </div>`;
  if (csSort !== "all") listRows = listRows.filter(r => r.dir === csSort && r.rel !== 0);

  if (!listRows.length) {
    h += `<p class="empty">該当する銘柄がありません。</p>`;
  }
  listRows.forEach((r, k) => {
    const s = r.rel > 0 ? "+" : "";
    const relCls = r.rel > 0 ? "relpos" : r.rel < 0 ? "relneg" : "";
    const key = `${r.sid || ""}|${r.ticker}`;
    const isOpen = r.sid && csOpen === key;
    h += `<div class="hrow csrow${isOpen ? " open" : ""}" data-k="${k}" title="タップで詳細を表示">
      <span class="hname">${r.name}<small class="hnews">${r.news}</small></span>
      <span class="hwin">${r.market || ""}・${r.win}</span>
      <span class="hscore"><span class="${relCls}">${r.dir}コール ${s}${r.rel}%</span> ${judgeBadge(r.rel, r.dir)} <span class="ms csarrow">${isOpen ? "expand_less" : "expand_more"}</span></span></div>`;
    if (isOpen) h += csDetailHTML(r);
  });
  $("csBody").innerHTML = h;
  $("csBody").querySelectorAll("[data-m]").forEach(b => {
    b.onclick = () => { csMarket = b.dataset.m; csStatus = "all"; csOpen = null; renderCSView(); };
  });
  $("csBody").querySelectorAll("[data-cs]").forEach(b => {
    b.onclick = () => { csStatus = csStatus === b.dataset.cs ? "all" : b.dataset.cs; csOpen = null; renderCSView(); };
  });
  $("csBody").querySelectorAll("[data-sort]").forEach(b => {
    b.onclick = () => { csSort = csSort === b.dataset.sort ? "all" : b.dataset.sort; renderCSView(); };
  });
  // 銘柄行クリック → チャートと詳細を展開
  $("csBody").querySelectorAll(".csrow").forEach(el => {
    el.onclick = async () => {
      const r = listRows[+el.dataset.k];
      if (!r || !r.sid) return;   // 旧キャッシュ（sidなし）は「集計する」で更新後に対応
      const key = `${r.sid}|${r.ticker}`;
      if (csOpen === key) { csOpen = null; renderCSView(); return; }
      if (!csDetail[r.sid]) {
        el.style.opacity = ".6";
        try { csDetail[r.sid] = await apiGet(`api/calls/${r.sid}`); }
        catch (e) { el.style.opacity = ""; return; }
      }
      csOpen = key;
      renderCSView();
    };
  });
  const clr = $("csClear");
  if (clr) clr.onclick = () => { csStatus = "all"; csOpen = null; renderCSView(); };
  if (csAsof) $("csNote").textContent = `最終集計: ${csAsof.toLocaleString("ja-JP")}（結果はこの端末に保存されます）`;
}

/* ---------- 間違いノート ---------- */
function getWrongs() {
  const wrongs = [];
  store.results.forEach(r => (r.answers || []).forEach(a => {
    if (!a.ok) wrongs.push({ ...a, id: r.id, headline: r.headline, at: (r.at || "").slice(0, 10) });
  }));
  return wrongs.reverse();
}

function showNotes() {
  show("stage");
  $("scoreBox").innerHTML = "";
  const wrongs = getWrongs();

  let h = `<div class="stepno">${ic("edit_note")} 間違いノート</div>`;
  if (!wrongs.length) {
    h += `<p class="empty">間違いはまだありません。素晴らしい！ ${ic("celebration", 1)}</p>`;
  } else {
    h += `<p class="cnote" style="margin-bottom:12px;">間違えた問題 ${wrongs.length} 件（新しい順・この端末に保存）</p>
      <div class="row" style="margin-bottom:16px;">
        <button class="btn primary" id="reviewBtn">${ic("replay")} 間違えた問題だけ解き直す</button>
      </div>`;
    wrongs.slice(0, 50).forEach((w, i) => {
      const hasSession = sessions.some(s => s.id === w.id);
      h += `<div class="anscard">
        <div class="badges"><span class="bd">${w.at}</span><span class="ast" style="margin:0;">${w.st}</span></div>
        <p class="cnote" style="margin:4px 0 6px;">${w.headline}</p>
        <div class="aq">${w.q}</div>
        <div class="aopt"><span class="mk"><span class="ms">close</span></span><span>${w.chosen}</span><span class="abadge" style="background:#f4693c;">あなた</span></div>
        <div class="aopt ok"><span class="mk"><span class="ms">check</span></span><span>${w.corr}</span><span class="abadge">正解</span></div>
        <div class="areason"><b>解説</b>${subMarks(w.reason)}</div>
        ${hasSession ? `<div class="row" style="margin-top:10px;"><button class="btn ghost sm" data-retry="${w.id}">${ic("rocket_launch")} このニュースを解き直す</button></div>` : ""}
      </div>`;
    });
  }
  $("stageBody").innerHTML = h;
  const rb = $("reviewBtn");
  if (rb) rb.onclick = () => startWrongReview();
  $("stageBody").querySelectorAll("[data-retry]").forEach(b => {
    b.onclick = async () => {
      const s = sessions.find(x => x.id === b.dataset.retry);
      if (!s) return;
      const orig = b.innerHTML;
      b.disabled = true;
      b.innerHTML = `<span class="ms spin">progress_activity</span> 読み込み中…`;
      try { await ensureDetail(s); } catch (e) { b.disabled = false; b.innerHTML = orig; return; }
      b.disabled = false;
      b.innerHTML = orig;
      startPlay(s);
    };
  });
  window.scrollTo({ top: 0, behavior: "smooth" });
}

/* ---------- 復習モード（間違えた問題だけを横断出題） ---------- */
async function startWrongReview() {
  const wrongs = getWrongs();
  // 間違いに関係する記事の詳細をまとめて取得（未取得分のみ）
  const ids = [...new Set(wrongs.map(w => w.id))];
  await Promise.all(ids.map(id => {
    const s = sessions.find(x => x.id === id);
    return s ? ensureDetail(s).catch(() => null) : null;
  }));
  const qs = [];
  const seen = new Set();
  wrongs.forEach(w => {
    const k = `${w.id}|${w.q}`;
    if (seen.has(k)) return;
    seen.add(k);
    const s = sessions.find(x => x.id === w.id);
    if (!s || !s.questions) return;
    const q = s.questions.find(qq => qq.q === w.q);
    if (q) qs.push(q);
  });
  if (!qs.length) { alert("復習できる問題が見つかりませんでした。"); return; }
  cur = {
    id: "__review", __review: true, categories: [],
    news: { source: "復習モード", headline: `間違えた問題だけを解き直す（${Math.min(qs.length, 12)}問）` },
    questions: shuffle(qs).slice(0, 12)
  };
  callsPromise = null;
  idx = 0; live = 0; sessionAnswers = [];
  show("stage");
  renderQ();
}

/* ---------- confetti ---------- */
function burstConfetti() {
  const emo = ["celebration", "star", "auto_awesome", "favorite", "cake", "music_note"];
  const cols = ["#f4693c", "#ffc93c", "#56b7e6", "#a58bd8", "#5cb85c", "#f7a6b9"];
  for (let i = 0; i < 26; i++) {
    const s = document.createElement("span");
    s.className = "confetti";
    s.innerHTML = ic(emo[Math.floor(Math.random() * emo.length)], 1);
    s.style.color = cols[Math.floor(Math.random() * cols.length)];
    s.style.left = Math.random() * 100 + "vw";
    s.style.animationDuration = (2.2 + Math.random() * 2) + "s";
    s.style.animationDelay = (Math.random() * .7) + "s";
    document.body.appendChild(s);
    setTimeout(() => s.remove(), 5000);
  }
}

boot();
