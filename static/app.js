/* Cited front end: chat, sources, related topics, notebooks, export. No build step. */
(() => {
  "use strict";
  const $ = (s, el = document) => el.querySelector(s);
  const el = (tag, attrs = {}, ...kids) => {
    const n = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs)) {
      if (k === "class") n.className = v;
      else if (k === "html") n.innerHTML = v;
      else if (k.startsWith("on")) n.addEventListener(k.slice(2), v);
      else if (v !== undefined && v !== null && v !== false) n.setAttribute(k, v);
    }
    for (const k of kids.flat()) if (k !== null && k !== undefined) n.append(k.nodeType ? k : document.createTextNode(k));
    return n;
  };
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  // ── State ──────────────────────────────────────────────────────────────────
  const store = {
    get(key, fallback) { try { const v = localStorage.getItem(key); return v ? JSON.parse(v) : fallback; } catch { return fallback; } },
    // Returns false when the browser refuses (storage full, or blocked in private mode).
    set(key, value) { try { localStorage.setItem(key, JSON.stringify(value)); return true; } catch { return false; } },
    bytes() { try { let n = 0; for (let i = 0; i < localStorage.length; i++) { const k = localStorage.key(i); n += (k.length + (localStorage.getItem(k) || "").length) * 2; } return n; } catch { return 0; } },
  };
  // Anonymous ids for usage counts (no names, emails or cookies). See cited/analytics.py.
  const rid = () => (crypto.randomUUID ? crypto.randomUUID() : Math.random().toString(36).slice(2) + Date.now().toString(36)).replace(/[^A-Za-z0-9_-]/g, "");
  const visitorId = store.get("cited.visitor", null) || rid();
  store.set("cited.visitor", visitorId);
  let sessionId; try { sessionId = sessionStorage.getItem("cited.session") || rid(); sessionStorage.setItem("cited.session", sessionId); } catch { sessionId = rid(); }
  function track(type, fields = {}) {
    try {
      fetch("/api/event", { method: "POST", keepalive: true, headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ type, visitor_id: visitorId, session_id: sessionId, ...fields }) }).catch(() => {});
    } catch { /* analytics must never break the app */ }
  }
  let notebooks = store.get("cited.notebooks", []);
  let activeId = store.get("cited.active", null);
  let history = [];
  let meta = null;
  let busy = false;
  let me = null;               // signed-in user, or null
  let previews = {};           // notebook id -> new items to view (free preview)
  let chatId = rid();          // one conversation; "New chat" starts another
  let chatTurns = [];          // this chat's turns (saved to the account when signed in)
  const synced = {};           // notebook id -> JSON last sent to the server
  const STORAGE_BUDGET = 4.5 * 1024 * 1024; // browsers allow ~5 MB per site
  let storageWarned = false;
  const persist = () => {
    const ok = store.set("cited.notebooks", notebooks) && store.set("cited.active", activeId);
    renderNotebooks();
    if (me) scheduleSync();
    if (!ok) {
      toast("Couldn't save: this browser's notebook storage is full or blocked. Export your notebook, then remove old items.");
      return false;
    }
    if (!storageWarned && store.bytes() > STORAGE_BUDGET * 0.8) {
      storageWarned = true;
      toast("Notebook storage is almost full in this browser. Export to keep a copy.");
    }
    return true;
  };
  const active = () => notebooks.find((n) => n.id === activeId) || null;
  const uid = () => Math.random().toString(36).slice(2, 10);

  function toast(msg) {
    const t = $("#toast");
    t.textContent = msg; t.hidden = false;
    clearTimeout(toast._t); toast._t = setTimeout(() => (t.hidden = true), 2600);
  }

  // ── Markdown (small, safe subset) ──────────────────────────────────────────
  function md(text, onCite) {
    const blocks = String(text || "").trim().split(/\n\s*\n/);
    const inline = (s) => esc(s)
      .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
      .replace(/(^|[^*])\*([^*\n]+)\*/g, "$1<em>$2</em>")
      .replace(/\[(\d{1,2})\]/g, '<button class="cite" data-n="$1" title="Show source $1">$1</button>');
    const html = blocks.map((b) => {
      const lines = b.split("\n");
      if (lines.every((l) => /^\s*([-*]|\d+\.)\s+/.test(l))) {
        return "<ul>" + lines.map((l) => `<li>${inline(l.replace(/^\s*([-*]|\d+\.)\s+/, ""))}</li>`).join("") + "</ul>";
      }
      if (/^#{1,4}\s/.test(b)) return `<p><strong>${inline(b.replace(/^#{1,4}\s/, ""))}</strong></p>`;
      return `<p>${inline(b).replace(/\n/g, "<br>")}</p>`;
    }).join("");
    const div = el("div", { class: "answer", html });
    if (onCite) div.querySelectorAll(".cite").forEach((b) => b.addEventListener("click", () => onCite(+b.dataset.n)));
    return div;
  }

  // ── Sources ────────────────────────────────────────────────────────────────
  const slimSource = (s) => ({
    title: s.title, kind: s.kind, speakers: s.speakers, timestamp: s.timestamp, date: s.date,
    url: s.url, excerpt: s.text.split(/\s+/).slice(0, 60).join(" "), chunk_id: s.chunk_id,
  });

  function sourceCard(s, n) {
    const who = s.speakers.length ? s.speakers.join(", ") : (s.kind === "newsletter" ? "Aakash Gupta" : "");
    const metaBits = [who, s.timestamp ? `around ${s.timestamp}` : (s.heading || ""), s.date].filter(Boolean).join(" · ");
    const text = el("div", { class: "src__text" }, s.text.replace(/\n/g, " "));
    const card = el("div", { class: "src", "data-n": n ?? "" },
      el("div", { class: "src__top" },
        n ? el("span", { class: "src__n" }, String(n)) : null,
        el("div", {},
          el("div", { class: "src__kind" }, s.kind === "podcast" ? "Podcast" : "Newsletter"),
          el("div", { class: "src__title" }, s.title),
          el("div", { class: "src__meta" }, metaBits))),
      text,
      el("div", { class: "src__actions" },
        s.url ? el("a", { href: s.url, target: "_blank", rel: "noopener" },
          s.kind === "podcast" ? (s.has_video === false ? "Listen on Substack →" : `▶ Watch around ${s.timestamp || "the start"}`) : "Read this passage →") : null,
        el("button", { onclick: () => text.classList.toggle("open") }, "Show more"),
        el("button", { onclick: () => saveEntry({ kind: "quote", source: slimSource(s) }, s.topics?.[0]) }, "＋ Save quote")));
    return card;
  }

  function confPill(c) {
    if (!c) return null;
    return el("span", { class: `conf conf--${c.level}`, title: `Top match score ${c.score}` }, c.label);
  }

  // ── Ask ────────────────────────────────────────────────────────────────────
  async function ask(question, opts = {}) {
    question = question.trim();
    if (!question || busy) return;
    busy = true; $(".send").disabled = true;
    $("#hero").hidden = true; $("#topicView").hidden = true; $("#thread").hidden = false;
    closeSidebar();

    const turn = el("article", { class: "turn" });
    const card = el("div", { class: "card" }, el("div", { class: "tag" }, el("span", { class: "spinner" }), "Searching 345 sources…"));
    turn.append(el("h2", { class: "q" }, question), card);
    $("#thread").append(turn);
    turn.scrollIntoView({ behavior: "smooth", block: "start" });

    const state = { question, sources: [], text: "", mode: null, related: null, more: [], confidence: null, savedFrom: null };
    const head = el("div", { class: "answer-head" });
    const body = el("div", {});
    const notices = el("div", {});
    const srcBox = el("div", {});
    const follow = el("div", { class: "followup" });
    const flashSource = (n) => {
      const t = srcBox.querySelector(`.src[data-n="${n}"]`);
      if (t) { t.scrollIntoView({ behavior: "smooth", block: "center" }); t.classList.add("flash"); setTimeout(() => t.classList.remove("flash"), 1400); }
    };
    let pending = false;
    const paint = () => {
      if (pending) return; pending = true;
      requestAnimationFrame(() => {
        pending = false;
        body.replaceChildren(md(state.text, flashSource));
        if (state.mode === "streaming") body.lastChild?.lastElementChild?.classList.add("cursor");
      });
    };

    try {
      const res = await fetch("/api/ask", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ question, history: opts.fresh ? [] : history.slice(-8), fresh: !!opts.fresh, visitor_id: visitorId, session_id: sessionId, chat_id: chatId }),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        const actions = el("div", { class: "limit-actions" });
        if (err.limit?.reason === "chat_full") actions.append(el("button", { class: "btn btn--primary", onclick: () => { newChat(); $("#input").value = question; $("#input").focus(); } }, "Start a new chat"));
        if (err.limit && !me) actions.append(el("button", { class: "btn", onclick: () => openSignin() }, "Sign in"));
        if (err.limit?.reason === "questions_full") actions.append(el("button", { class: "btn", onclick: () => openSupport("You've reached today's free questions.") }, "Keep Cited free"));
        card.replaceChildren(el("div", { class: "notice notice--warn" }, err.error || "Something went wrong. Please try again.", actions.children.length ? actions : null));
        return;
      }
      card.replaceChildren(head, notices, body, srcBox);
      turn.append(follow);
      const reader = res.body.getReader();
      const dec = new TextDecoder();
      let buf = "";
      for (;;) {
        const { value, done } = await reader.read();
        if (done) break;
        buf += dec.decode(value, { stream: true });
        let i;
        while ((i = buf.indexOf("\n\n")) !== -1) {
          const line = buf.slice(0, i).trim(); buf = buf.slice(i + 2);
          if (!line.startsWith("data:")) continue;
          handle(JSON.parse(line.slice(5)));
        }
      }
    } catch (e) {
      notices.append(el("div", { class: "notice notice--warn" }, "Connection lost. Please try again."));
    } finally {
      busy = false; $(".send").disabled = false;
      if (state.mode === "streaming") { state.mode = "answered"; paint(); }
      if (state.text) history.push({ role: "user", content: question }, { role: "assistant", content: state.text });
      history = history.slice(-8);
      if (state.sources.length || state.text) {
        chatTurns.push({ q: question, answer: state.text, sources: state.sources, confidence: state.confidence, mode: state.mode });
        if (me) saveChat();
      }
    }

    function handle(ev) {
      switch (ev.type) {
        case "allowance":
          renderUsage(ev);
          break;
        case "sources":
          state.sources = ev.sources; state.confidence = ev.confidence; state.more = ev.more || [];
          head.replaceChildren(confPill(ev.confidence), el("span", { class: "tag" }, `${ev.sources.length} passages · ${new Set(ev.sources.map((s) => s.doc_id)).size} episodes and posts`));
          srcBox.replaceChildren(
            el("div", { class: "sources-title" }, "Sources"),
            el("div", { class: "sources" }, ev.sources.map((s, i) => sourceCard(s, i + 1))));
          break;
        case "saved":
          state.mode = "saved"; state.text = ev.answer; state.savedFrom = ev.question;
          notices.append(el("div", { class: "notice notice--info" },
            `Saved answer to a similar question: “${ev.question}”. `,
            el("button", { onclick: () => ask(question, { fresh: true }) }, "Answer my exact question")));
          if (ev.sources?.length) {
            state.sources = ev.sources;
            srcBox.replaceChildren(el("div", { class: "sources-title" }, "Sources"),
              el("div", { class: "sources" }, ev.sources.map((s, i) => sourceCard(s, i + 1))));
          }
          paint();
          break;
        case "search_only":
          state.mode = "search_only";
          notices.append(el("div", { class: "notice notice--info" },
            "Preview mode: answer writing is off, so here are the passages that best answer your question. Save any of them to a notebook."));
          break;
        case "not_covered": {
          state.mode = "not_covered";
          head.replaceChildren(confPill(state.confidence));
          srcBox.hidden = true;
          const toggle = el("button", { onclick: () => { srcBox.hidden = !srcBox.hidden; toggle.textContent = srcBox.hidden ? "Show the closest passages anyway" : "Hide them"; } },
            "Show the closest passages anyway");
          notices.append(el("div", { class: "notice notice--warn" }, ev.message, " ", toggle));
          state.more = [];
          break;
        }
        case "token":
          state.mode = "streaming"; state.text += ev.text; paint();
          break;
        case "verification":
          if (ev.unverified?.length) {
            notices.append(el("div", { class: "notice notice--warn" },
              `${ev.unverified.length} quote${ev.unverified.length > 1 ? "s" : ""} couldn't be matched word for word to the sources. Treat ${ev.unverified.length > 1 ? "them" : "it"} as a paraphrase.`));
          }
          break;
        case "error":
          notices.append(el("div", { class: "notice notice--warn" }, ev.message));
          break;
        case "related":
          state.related = ev; renderFollowup(follow, state);
          break;
      }
    }
  }

  // The two questions after every answer: explore related? save this?
  function renderFollowup(box, state) {
    const rel = state.related || { topics: [], questions: [] };
    const explore = el("div", { class: "card" },
      el("h4", {}, state.mode === "not_covered" ? "Topics Cited does cover" : "Want to explore related topics?"),
      el("div", { class: "chips" },
        rel.questions.map((q) => el("button", { class: "chip", onclick: () => { track("related_click", { kind: "question" }); ask(q); } }, q)),
        rel.topics.map((t) => el("button", { class: "chip chip--topic", onclick: () => { track("related_click", { kind: "topic", topic: t.id }); openTopic(t.id); } }, `Topic: ${t.name}`))));
    if (state.more.length) {
      explore.append(el("h4", { style: "margin-top:12px" }, "More on this in the archive"),
        el("ul", { class: "more-list" }, state.more.slice(0, 4).map((m) =>
          el("li", {}, el("a", { href: m.url, target: "_blank", rel: "noopener" }, m.title),
            el("span", { class: "tag" }, ` · ${m.kind === "podcast" ? "Podcast" : "Newsletter"} · ${m.date}`)))));
    }
    const topic = rel.topics[0];
    const suggested = topic ? topic.name : "My notebook";
    const entry = () => ({
      kind: "answer", question: state.question,
      answer: state.text || "Top passages for this question (answer writing was off).",
      sources: state.sources.map(slimSource),
    });
    const save = el("div", { class: "card" },
      el("h4", {}, "Save this to your notebook?"),
      el("div", { class: "save-row" },
        el("button", { class: "btn btn--primary", onclick: () => saveEntry(entry(), topic?.id, suggested) }, `Save to “${suggested}”`),
        el("button", { class: "btn", onclick: () => chooseNotebook(entry()) }, "Choose notebook…"),
        el("button", { class: "btn btn--ghost", onclick: () => download("markdown", { title: state.question, entries: [entry()] }) }, "Markdown"),
        el("button", { class: "btn btn--ghost", onclick: () => download("pdf", { title: state.question, entries: [entry()] }) }, "PDF")));
    box.replaceChildren(explore, state.mode === "not_covered" ? null : save);
  }

  // ── Topics ─────────────────────────────────────────────────────────────────
  async function openTopic(id) {
    closeSidebar();
    const view = $("#topicView");
    $("#hero").hidden = true; $("#thread").hidden = true; view.hidden = false;
    view.replaceChildren(el("p", { class: "tag" }, el("span", { class: "spinner" }), "Loading topic…"));
    track("topic_open", { topic: id });
    window.scrollTo({ top: 0 });
    const t = await fetch(`/api/topics/${encodeURIComponent(id)}`).then((r) => r.json());
    const points = (t.perspectives || []).map((p) => p.claim);
    const startNb = () => {
      const nb = createNotebook(t.name);
      nb.entries.push({
        kind: "topic", topic_id: t.id, topic_name: t.name, overview: t.overview || t.description, points,
        sources: (t.passages || []).slice(0, 6).map(slimSource), saved_at: new Date().toISOString(),
      });
      persist(); openDrawer(); toast(`Started notebook “${t.name}”`);
      track("save", { kind: "topic", topic: id });
    };
    view.replaceChildren(
      el("button", { class: "link-btn", onclick: backToChat }, "← Back"),
      el("h1", {}, t.name),
      el("p", { class: "desc" }, t.description),
      el("div", { class: "topic-actions" },
        el("button", { class: "btn btn--primary", onclick: startNb }, "Start a notebook on this topic"),
        el("button", { class: "btn", onclick: () => ask(`What do guests say about ${t.name.toLowerCase()}?`) }, "Ask about this topic")),
      t.pregenerated
        ? el("div", { class: "card" },
            md(t.overview),
            (t.perspectives || []).length ? el("div", { class: "sources-title" }, "Perspectives") : null,
            (t.perspectives || []).map((p) => el("div", { class: "persp" },
              el("p", {}, p.claim),
              el("div", { class: "tag" }, (p.sources || []).map((s) => s.title).slice(0, 3).join(" · ")))))
        : el("div", { class: "notice notice--info" }, "A written overview of this topic is coming soon. Here are the passages most about it."),
      el("div", { class: "sources-title" }, "Explore next"),
      el("div", { class: "chips", style: "display:flex;flex-wrap:wrap;gap:6px" },
        (t.related?.questions || []).map((q) => el("button", { class: "chip", onclick: () => ask(q) }, q))),
      el("div", { class: "sources-title" }, "Top passages"),
      el("div", { class: "sources" }, (t.passages || []).map((s, i) => sourceCard(s, i + 1))));
  }

  function backToChat() {
    $("#topicView").hidden = true;
    const hasThread = $("#thread").children.length > 0;
    $("#thread").hidden = !hasThread; $("#hero").hidden = hasThread;
  }

  // ── Notebooks ──────────────────────────────────────────────────────────────
  function createNotebook(title) {
    const now = new Date().toISOString();
    const nb = { id: uid(), title: title || "My notebook", created: now, lastChecked: now, autoAdd: true, entries: [] };
    notebooks.unshift(nb); activeId = nb.id; persist();
    track("notebook_created");
    return nb;
  }

  function findOrCreate(title) {
    return notebooks.find((n) => n.title.toLowerCase() === String(title).toLowerCase()) || createNotebook(title);
  }

  function saveEntry(entry, _topicId, notebookTitle) {
    const nb = notebookTitle ? findOrCreate(notebookTitle) : (active() || createNotebook("My notebook"));
    nb.entries.push({ ...entry, saved_at: new Date().toISOString() });
    activeId = nb.id;
    if (!persist()) { nb.entries.pop(); renderDrawer(); return; }
    renderDrawer();
    track("save", { kind: entry.kind });
    maybePromptSignin();
    toast(`Saved to “${nb.title}”`);
  }

  function chooseNotebook(entry) {
    const dlg = $("#saveDialog");
    const box = $("#saveChoices");
    box.replaceChildren(...(notebooks.length ? notebooks : []).map((n, i) =>
      el("label", {}, el("input", { type: "radio", name: "nb", value: n.id, checked: (n.id === activeId || (!activeId && i === 0)) || undefined }),
        `${n.title}`, el("small", { class: "tag" }, ` · ${n.entries.length} saved`))));
    if (!notebooks.length) box.append(el("p", { class: "empty-note" }, "No notebooks yet. Name your first one below."));
    $("#newNbName").value = "";
    dlg.returnValue = "";
    dlg.showModal();
    dlg.onclose = () => {
      if (dlg.returnValue !== "ok") return;
      const name = $("#newNbName").value.trim();
      if (name) return saveEntry(entry, null, name);
      const picked = box.querySelector("input:checked");
      if (picked) { activeId = picked.value; saveEntry(entry); }
    };
  }

  function renderNotebooks() {
    $("#nbCount").textContent = active()?.entries.length ?? 0;
    const list = $("#nbList");
    list.replaceChildren(...notebooks.map((n) => el("li", {},
      el("button", { class: n.id === activeId ? "active" : "", onclick: () => { activeId = n.id; n.hasNew = false; persist(); openDrawer(); } },
        el("span", {}, n.title, n.hasNew ? el("span", { class: "new-dot", title: "New items added" }, " • new") : null),
        el("small", {}, String(n.entries.length))))));
    if (!notebooks.length) list.append(el("li", { class: "empty-note" }, "Save an answer to start one."));
    if (!$("#drawer").hidden) renderDrawer();
  }

  function renderDrawer() {
    const nb = active();
    const entries = $("#nbEntries");
    if (!nb) {
      $("#nbTitle").value = "No notebook yet"; $("#nbMeta").textContent = "";
      entries.replaceChildren(el("div", { class: "drawer__empty" }, "Ask a question, then choose “Save this to your notebook”."));
      return;
    }
    $("#nbTitle").value = nb.title;
    const used = store.bytes();
    $("#nbMeta").textContent = `${nb.entries.length} saved · started ${nb.created.slice(0, 10)}` +
      (used > STORAGE_BUDGET * 0.5 ? ` · browser storage ${Math.round((used / STORAGE_BUDGET) * 100)}% full` : "");
    if (!nb.entries.length) {
      entries.replaceChildren(el("div", { class: "drawer__empty" }, "Empty for now. Save answers, quotes or topics here."));
      return;
    }
    entries.replaceChildren(...nb.entries.map((e, i) => {
      const label = { answer: "Answer", quote: "Quote", topic: "Topic" }[e.kind] || "Note";
      const title = e.kind === "answer" ? e.question : e.kind === "topic" ? e.topic_name : e.source?.title;
      const bodyText = e.kind === "answer" ? e.answer : e.kind === "topic" ? e.overview : `“${e.source?.excerpt}”`;
      const note = el("textarea", { rows: 2, placeholder: "Add your note…" });
      note.value = e.note || "";
      note.addEventListener("change", () => { e.note = note.value; persist(); });
      return el("div", { class: "entry" + (e.auto ? " entry--new" : "") },
        el("div", { class: "entry__kind" }, e.auto ? `New ${e.source?.kind === "podcast" ? "episode" : "post"} · added for you` : label),
        e.auto && e.matched ? el("div", { class: "entry__why" }, `Matches “${e.matched}”`) : null,
        el("div", { class: "entry__title" }, title || ""),
        el("div", { class: "entry__body" }, (bodyText || "").replace(/\[(\d+)\]/g, "")),
        note,
        el("div", { class: "entry__actions" },
          el("button", { onclick: () => { if (e.auto) track("auto_removed"); nb.entries.splice(i, 1); persist(); renderDrawer(); } }, "Remove")));
    }));
    const toggle = el("input", { type: "checkbox", id: "autoAdd" });
    toggle.checked = nb.autoAdd !== false;
    toggle.addEventListener("change", () => {
      nb.autoAdd = toggle.checked; persist();
      track("auto_toggle", { value: toggle.checked ? "on" : "off" });
    });
    entries.prepend(el("label", { class: "auto-toggle", for: "autoAdd" }, toggle,
      me?.plan === "member" ? " Add new episodes and posts that match this notebook when I come back"
        : " Add matching new episodes and posts automatically (supporters)"));
    const pv = previews[nb.id] || [];
    if (pv.length && me?.plan !== "member") {
      entries.prepend(el("div", { class: "preview" },
        el("h4", {}, `New since your last visit · ${pv.length} match${pv.length === 1 ? "es" : ""} this notebook`),
        ...pv.map((s) => el("div", { class: "preview__item" },
          el("div", { class: "t" }, s.title),
          el("div", { class: "m" }, `${s.kind === "podcast" ? "New episode" : "New post"} · ${s.date}${s.matched ? ` · matches “${s.matched}”` : ""}`),
          el("div", { class: "preview__actions" },
            el("a", { href: s.url, target: "_blank", rel: "noopener" }, s.kind === "podcast" ? "▶ Watch" : "Read →"),
            el("button", { onclick: () => { track("paywall_shown"); openSupport(`“${s.title}” matches your notebook.`); } }, "＋ Add to notebook"))))));
    }
  }

  // ── New content for notebooks ──────────────────────────────────────────────
  function summarize(nb) {
    const questions = [], chunkIds = [], docIds = [], topics = [];
    for (const e of nb.entries) {
      if (e.kind === "answer" && e.question) questions.push(e.question);
      for (const s of e.sources || (e.source ? [e.source] : [])) {
        if (s.chunk_id) { chunkIds.push(s.chunk_id); docIds.push(s.chunk_id.split("#")[0]); }
      }
      if (e.kind === "topic" && e.topic_id) topics.push(e.topic_id);
    }
    const byName = Object.fromEntries((meta?.topics || []).map((t) => [t.name.toLowerCase(), t.id]));
    if (byName[nb.title.toLowerCase()]) topics.push(byName[nb.title.toLowerCase()]);
    return { id: nb.id, since: nb.lastChecked || nb.created, title: nb.title,
      questions: questions.slice(-20), chunk_ids: chunkIds.slice(-100), doc_ids: [...new Set(docIds)].slice(-100), topics: [...new Set(topics)] };
  }

  async function checkForNew() {
    const eligible = notebooks.filter((n) => n.autoAdd !== false && n.entries.length);
    if (!eligible.length) return;
    let res;
    try {
      res = await fetch("/api/updates", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ notebooks: eligible.slice(0, 20).map(summarize) }) }).then((r) => r.json());
    } catch { return; }
    let added = 0;
    if (res.preview) {
      // Returning signed-in free user: view what's new, adding is for supporters.
      previews = {};
      let nbWith = 0;
      for (const nb of eligible) {
        const items = res.notebooks?.[nb.id] || [];
        if (items.length) { previews[nb.id] = items; nb.hasNew = true; nbWith++; }
      }
      renderNotebooks();
      const topics = (res.topics || []).map(([t, n]) => `${t} (${n})`).join(", ");
      if (nbWith) toast(`New since your last visit${topics ? ` in ${topics}` : ""}. ${nbWith} of your notebooks ${nbWith === 1 ? "has" : "have"} updates: open ${nbWith === 1 ? "it" : "one"} to see them.`);
      return;
    }
    if (!res.member) return;
    for (const nb of eligible) {
      nb.teaser = 0;
      const items = res.notebooks?.[nb.id] || [];
      const have = new Set(nb.entries.flatMap((e) => (e.sources || (e.source ? [e.source] : [])).map((s) => (s.chunk_id || "").split("#")[0])));
      for (const s of items) {
        if (have.has(s.doc_id)) continue;
        nb.entries.push({ kind: "quote", source: slimSource(s), auto: true, matched: s.matched, saved_at: new Date().toISOString() });
        nb.hasNew = true; added++;
        track("auto_added", { kind: s.kind });
      }
      nb.lastChecked = res.checked_at;
    }
    persist();
    if (added) toast(`${added} new ${added === 1 ? "episode or post was" : "episodes and posts were"} added to your notebooks`);
  }

  function openDrawer() {
    $("#drawer").hidden = false; $(".layout").classList.add("with-drawer"); document.body.classList.add("drawer-open"); renderDrawer();
  }
  function closeDrawer() { $("#drawer").hidden = true; $(".layout").classList.remove("with-drawer"); document.body.classList.remove("drawer-open"); }

  async function download(kind, nb) {
    if (!nb || !nb.entries?.length) return toast("Nothing to export yet");
    track("export", { kind: kind === "pdf" ? "pdf" : "markdown" });
    toast(kind === "pdf" ? "Preparing PDF…" : "Preparing Markdown…");
    const res = await fetch(`/api/export/${kind}`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ title: nb.title, created: nb.created, entries: nb.entries, visitor_id: visitorId }),
    });
    if (res.status === 429) {
      const e = await res.json().catch(() => ({}));
      toast(e.error || "You've used this month's free exports.");
      if (e.signin && !me) setTimeout(openSignin, 1200);
      return;
    }
    if (!res.ok) return toast("Export failed. Please try again.");
    const blob = await res.blob();
    const name = (res.headers.get("Content-Disposition") || "").match(/filename="([^"]+)"/)?.[1] || `cited.${kind === "pdf" ? "pdf" : "md"}`;
    const a = el("a", { href: URL.createObjectURL(blob), download: name });
    document.body.append(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 4000);
  }

  // ── Chats ──────────────────────────────────────────────────────────────────
  function renderUsage(a) {
    const u = $("#usage");
    if (!a || a.member) { u.textContent = a?.member ? "Supporter: unlimited chats" : ""; return; }
    if (a.tier === "free") { u.textContent = `Question ${a.questions_today} of ${a.questions_max} today`; return; }
    u.textContent = `Question ${a.turns_used} of ${a.turns_max} in this chat · chat ${a.chats_today} of ${a.chats_max} today`;
  }

  function newChat() {
    chatId = rid(); chatTurns = []; history = [];
    $("#thread").replaceChildren(); $("#thread").hidden = true; $("#topicView").hidden = true; $("#hero").hidden = false;
    $("#usage").textContent = "";
    window.scrollTo({ top: 0 });
  }

  let chatSaveTimer;
  function saveChat() {
    clearTimeout(chatSaveTimer);
    const id = chatId, turns = chatTurns.slice(-50), title = turns[0]?.q || "Chat";
    chatSaveTimer = setTimeout(async () => {
      try {
        await fetch(`/api/me/chats/${id}`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ title, turns }) });
        loadChats();
      } catch { /* retried on the next turn */ }
    }, 300);
  }

  async function loadChats() {
    if (!me) { $("#chatsSection").hidden = true; return; }
    const res = await fetch("/api/me/chats").then((r) => r.ok ? r.json() : { chats: [] }).catch(() => ({ chats: [] }));
    $("#chatsSection").hidden = false;
    const list = $("#chatList");
    list.replaceChildren(...res.chats.slice(0, 15).map((c) => el("li", { class: "chat-item" },
      el("button", { class: c.id === chatId ? "active" : "", onclick: () => openChat(c.id) },
        el("span", {}, c.title.length > 40 ? c.title.slice(0, 40) + "…" : c.title), el("small", {}, String(c.turns))))));
    if (!res.chats.length) list.append(el("li", { class: "empty-note" }, "Your chats will appear here."));
  }

  async function openChat(id) {
    const chat = await fetch(`/api/me/chats/${id}`).then((r) => r.ok ? r.json() : null).catch(() => null);
    if (!chat) return toast("Couldn't open that chat.");
    newChat();
    chatId = id; chatTurns = chat.turns || [];
    history = chatTurns.flatMap((t) => t.answer ? [{ role: "user", content: t.q }, { role: "assistant", content: t.answer }] : []).slice(-8);
    $("#hero").hidden = true; $("#thread").hidden = false;
    for (const t of chatTurns) {
      const card = el("div", { class: "card" },
        el("div", { class: "answer-head" }, confPill(t.confidence), el("span", { class: "tag" }, "Saved chat")),
        t.answer ? md(t.answer) : el("div", { class: "notice notice--info" }, "Sources for this question:"),
        el("div", { class: "sources-title" }, "Sources"),
        el("div", { class: "sources" }, (t.sources || []).map((s, i) => sourceCard(s, i + 1))));
      $("#thread").append(el("article", { class: "turn" }, el("h2", { class: "q" }, t.q), card));
    }
    closeSidebar(); loadChats();
  }

  // ── Sign-in (email + 6-digit code) ─────────────────────────────────────────
  async function api(path, body) {
    const r = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) });
    const data = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(data.error || data.detail || "Something went wrong. Please try again.");
    return data;
  }

  function openSignin() {
    const d = $("#signinDialog");
    $("#signinEmailStep").hidden = false; $("#signinCodeStep").hidden = true;
    $("#signinErr").textContent = ""; $("#signinNext").textContent = "Email me a code"; $("#signinCode").value = "";
    d.showModal(); $("#signinEmail").focus();
  }

  async function signinNext(e) {
    if (e.submitter?.value === "cancel") return;
    e.preventDefault();
    const email = $("#signinEmail").value.trim();
    $("#signinErr").textContent = "";
    try {
      if (!$("#signinCodeStep").hidden) {
        await finishSignin(email, $("#signinCode").value);
        $("#signinDialog").close();
        return;
      }
      await api("/api/auth/request-code", { email });
      $("#signinEmailStep").hidden = true; $("#signinCodeStep").hidden = false;
      $("#signinSentTo").textContent = `Sent to ${email}. It works for 10 minutes.`;
      $("#signinNext").textContent = "Sign in"; $("#signinCode").focus();
    } catch (err) { $("#signinErr").textContent = err.message; }
  }

  async function finishSignin(email, code) {
    const res = await api("/api/auth/verify", { email, code, visitor_id: visitorId, session_id: sessionId });
    me = res.user;
    await syncAll();
    if (chatTurns.length) saveChat();
    renderAccount(); loadChats();
    toast(res.created ? "Welcome! Your notebooks and chats are now saved to your account." : "Signed in. Your notebooks are synced.");
  }

  async function signout() {
    await api("/api/auth/signout").catch(() => {});
    me = null; renderAccount(); loadChats(); toast("Signed out. Notebooks stay in this browser until you clear it.");
  }

  function renderAccount() {
    $("#heroNote").textContent = me
      ? (me.plan === "member" ? "You're a supporter: unlimited chats, and new episodes are added to your notebooks."
        : "Signed in: your chats and notebooks are saved to your account, on every device.")
      : "No sign-up. Chats aren't saved after you leave; notebooks stay in this browser until you download them.";
    $("#accountBtn").textContent = me ? (me.email.length > 22 ? me.email.slice(0, 20) + "…" : me.email) : "Sign in";
    $("#drawerHint").replaceChildren(...(me
      ? [el("strong", {}, "Synced to your account. "), `Your notebooks are saved for ${me.email} and appear on any device where you sign in.`]
      : [el("strong", {}, "No account needed. "), "Notebooks stay in this browser only. ",
         el("button", { class: "link-btn", onclick: () => openSignin() }, "Sign in"), " to keep them on every device, or download or email a copy."]));
    renderDrawer();
  }

  // Merge this browser's notebooks with the account's: newest edit wins, nothing is dropped.
  async function syncAll() {
    if (!me) return;
    const res = await fetch("/api/me/notebooks").then((r) => r.ok ? r.json() : null).catch(() => null);
    if (!res) return;
    const server = Object.fromEntries(res.notebooks.map((n) => [n.id, n]));
    const merged = [];
    for (const nb of notebooks) {
      const sv = server[nb.id];
      merged.push(sv && (sv.updated || "") > (nb.updated || "") ? sv : nb);
      delete server[nb.id];
    }
    merged.push(...Object.values(server));
    notebooks = merged;
    for (const nb of notebooks) synced[nb.id] = res.notebooks.some((n) => JSON.stringify(n) === JSON.stringify(nb)) ? JSON.stringify(nb) : null;
    if (!activeId && notebooks.length) activeId = notebooks[0].id;
    persist();
  }

  let syncTimer;
  function scheduleSync() {
    clearTimeout(syncTimer);
    syncTimer = setTimeout(async () => {
      for (const nb of notebooks) {
        const { updated, ...rest } = nb;
        const body = JSON.stringify(rest);
        if (synced[nb.id] === body) continue;
        nb.updated = new Date().toISOString();
        try {
          const r = await fetch(`/api/me/notebooks/${nb.id}`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(nb) });
          if (r.ok) synced[nb.id] = body;
          else { const e = await r.json().catch(() => ({})); toast(e.detail || "Couldn't sync a notebook."); }
        } catch { /* offline: tried again on the next change */ }
      }
      store.set("cited.notebooks", notebooks);
    }, 800);
  }

  function maybePromptSignin() {
    if (me) return;
    const saves = notebooks.reduce((a, n) => a + n.entries.length, 0);
    const snoozed = store.get("cited.promptSnooze", 0);
    if (saves < 3 || Date.now() < snoozed) return;
    $("#promptCard").hidden = false;
    track("signin_prompt");
  }

  // ── Email this notebook (and, for visitors, a sign-in code in the same email) ──
  function openEmailDialog() {
    const nb = active();
    if (!nb || !nb.entries.length) return toast("Save something to this notebook first.");
    $("#emailErr").textContent = ""; $("#emailCodeStep").hidden = true; $("#emailCode").value = "";
    $("#emailField").hidden = !!me; $("#emailNext").textContent = "Send PDF";
    const left = me ? me.emails_this_month : null;
    $("#emailLede").textContent = me
      ? `We'll email “${nb.title}” as a PDF to ${me.email}.` + (left ? ` ${left.max - left.used} of ${left.max} left this month.` : "")
      : `We'll email “${nb.title}” as a PDF. Without signing in you get 3 exports a month (downloads and emails together). Signed in, downloads are unlimited.`;
    $("#emailDialog").showModal();
  }

  async function emailNext(e) {
    if (e.submitter?.value === "cancel") return;
    e.preventDefault();
    $("#emailErr").textContent = "";
    const email = me ? me.email : $("#emailTo").value.trim();
    try {
      if (!$("#emailCodeStep").hidden) {
        if ($("#emailCode").value.trim()) await finishSignin(email, $("#emailCode").value);
        $("#emailDialog").close();
        return;
      }
      const nb = active();
      const res = await api("/api/notebooks/email", { email, notebook: { title: nb.title, created: nb.created, entries: nb.entries }, visitor_id: visitorId, session_id: sessionId });
      if (res.error) throw new Error(res.error);
      if (me) { me.emails_this_month = res.allowance; $("#emailDialog").close(); toast(`Sent to ${res.email}.`); return; }
      $("#emailField").hidden = true; $("#emailCodeStep").hidden = false;
      $("#emailNext").textContent = "Done"; $("#emailCode").focus();
    } catch (err) { $("#emailErr").textContent = err.message; }
  }

  // ── Support ("keep Cited free") ────────────────────────────────────────────
  function openSupport(why) {
    $("#supportWhy").textContent = why || "";
    $("#supportErr").textContent = "";
    $("#supportDialog").showModal();
  }

  async function supportGo() {
    track("support_click");
    if (!me) { $("#supportDialog").close(); toast("Sign in first, so your support is linked to your account."); return openSignin(); }
    const res = await fetch("/api/support/link").then((r) => r.json()).catch(() => ({}));
    if (!res.url) { $("#supportErr").textContent = "Payments open soon. Thank you for wanting to help!"; return; }
    window.open(res.url, "_blank", "noopener");
    $("#supportDialog").close();
    toast("Thank you! Supporter perks switch on as soon as the payment goes through.");
  }

  // ── Copying: keep the credit attached ──────────────────────────────────────
  document.addEventListener("copy", (e) => {
    const sel = window.getSelection();
    const text = sel ? sel.toString() : "";
    if (text.trim().length < 20) return;
    const node = sel.anchorNode && (sel.anchorNode.nodeType === 1 ? sel.anchorNode : sel.anchorNode.parentElement);
    const scope = node && node.closest(".src, .answer, .turn, .entry");
    if (!scope) return;
    const link = (scope.closest(".src") || scope.closest(".turn"))?.querySelector(".src__actions a")?.href || location.origin;
    e.clipboardData.setData("text/plain", `${text}\n\nvia Cited · ${link}\nSource: The Growth Podcast with Aakash Gupta (news.aakashg.com)`);
    e.preventDefault();
    track("copy");
  });

  // ── Sidebar / mobile ───────────────────────────────────────────────────────
  function closeSidebar() { $("#sidebar").classList.remove("open"); }

  function renderTopics() {
    const list = $("#topicList");
    list.replaceChildren(...(meta?.topics || []).filter((t) => t.passages > 0).map((t) =>
      el("li", {}, el("button", { onclick: () => openTopic(t.id) }, el("span", {}, t.name), el("small", {}, String(t.passages))))));
  }

  // ── Boot ───────────────────────────────────────────────────────────────────
  async function init() {
    renderNotebooks();
    track("visit");
    try {
      const auth = await fetch("/api/auth/me").then((r) => r.json());
      me = auth.user;
      if (me) await syncAll();
    } catch { /* stays signed out */ }
    renderAccount(); loadChats();
    document.querySelectorAll("[data-close]").forEach((b) => b.addEventListener("click", () => $("#" + b.dataset.close).close()));
    $("#signinForm").addEventListener("submit", signinNext);
    $("#emailForm").addEventListener("submit", emailNext);
    $("#emailNb").addEventListener("click", openEmailDialog);
    $("#newChatBtn").addEventListener("click", newChat);
    $("#accountBtn").addEventListener("click", () => {
      if (!me) return openSignin();
      const left = me.emails_this_month;
      $("#accountInfo").textContent = `Signed in as ${me.email} · ` +
        (me.plan === "member" ? `Supporter until ${me.member_until || "–"}` : "Free plan") +
        (left ? ` · ${left.max - left.used} of ${left.max} notebook emails left this month` : "");
      $("#accountSupport").hidden = me.plan === "member";
      $("#accountDialog").showModal();
    });
    $("#accountDialog").addEventListener("close", () => { if ($("#accountDialog").returnValue === "signout") signout(); });
    $("#promptSignin").addEventListener("click", () => { $("#promptCard").hidden = true; openSignin(); });
    $("#supportGo").addEventListener("click", supportGo);
    $("#accountSupport").addEventListener("click", () => { $("#accountDialog").close(); openSupport(""); });
    $("#promptLater").addEventListener("click", () => { $("#promptCard").hidden = true; store.set("cited.promptSnooze", Date.now() + 7 * 86400 * 1000); });
    try {
      meta = await fetch("/api/meta").then((r) => r.json());
      $("#statPods").textContent = meta.podcasts;
      $("#statPosts").textContent = meta.newsletters;
      $("#modeNote").textContent = meta.answers
        ? "Answers come only from the free archive and cite every source. Check a clip before quoting it."
        : "Preview: search-only mode. Answers will appear once answer writing is switched on.";
      renderTopics();
      checkForNew();
    } catch { $("#modeNote").textContent = "Couldn't reach the server."; }

    $("#composer").addEventListener("submit", (e) => { e.preventDefault(); const v = $("#input").value; $("#input").value = ""; autosize(); ask(v); });
    const autosize = () => { const t = $("#input"); t.style.height = "auto"; t.style.height = Math.min(t.scrollHeight, 160) + "px"; };
    $("#input").addEventListener("input", autosize);
    $("#input").addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); $("#composer").requestSubmit(); } });
    $("#starters").addEventListener("click", (e) => { if (e.target.matches(".chip")) ask(e.target.textContent); });
    $("#notebookBtn").addEventListener("click", () => ($("#drawer").hidden ? openDrawer() : closeDrawer()));
    $("#closeDrawer").addEventListener("click", closeDrawer);
    $("#topicsBtn").addEventListener("click", () => {
      if (window.matchMedia("(max-width: 760px)").matches) $("#sidebar").classList.toggle("open");
      else $("#topicList").scrollIntoView({ behavior: "smooth" });
    });
    $("#menuBtn").addEventListener("click", () => $("#sidebar").classList.toggle("open"));
    $("#newNotebookBtn").addEventListener("click", () => {
      const name = prompt("Name your notebook", "My notebook");
      if (name && name.trim()) { createNotebook(name.trim()); openDrawer(); }
    });
    $("#nbTitle").addEventListener("change", (e) => { const nb = active(); if (nb && e.target.value.trim()) { nb.title = e.target.value.trim(); persist(); } });
    $("#exportMd").addEventListener("click", () => download("markdown", active()));
    $("#exportPdf").addEventListener("click", () => download("pdf", active()));
    $("#homeLink").addEventListener("click", (e) => { e.preventDefault(); backToChat(); if (!$("#thread").children.length) window.scrollTo({ top: 0 }); });

    if (window.matchMedia("(max-width: 760px)").matches) $("#input").placeholder = "Ask a product question…";
    const q = new URLSearchParams(location.search).get("q");
    if (q) ask(q);
  }
  init();
})();
