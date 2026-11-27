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
    set(key, value) { try { localStorage.setItem(key, JSON.stringify(value)); } catch { /* private mode: keep in memory */ } },
  };
  let notebooks = store.get("cited.notebooks", []);
  let activeId = store.get("cited.active", null);
  let history = [];
  let meta = null;
  let busy = false;
  const persist = () => { store.set("cited.notebooks", notebooks); store.set("cited.active", activeId); renderNotebooks(); };
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
          s.kind === "podcast" ? `▶ Watch around ${s.timestamp || "the start"}` : "Read this passage →") : null,
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
        body: JSON.stringify({ question, history: opts.fresh ? [] : history.slice(-8), fresh: !!opts.fresh }),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        card.replaceChildren(el("div", { class: "notice notice--warn" }, err.error || "Something went wrong. Please try again."));
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
    }

    function handle(ev) {
      switch (ev.type) {
        case "sources":
          state.sources = ev.sources; state.confidence = ev.confidence; state.more = ev.more || [];
          head.replaceChildren(confPill(ev.confidence), el("span", { class: "tag" }, `${ev.sources.length} passages from ${new Set(ev.sources.map((s) => s.doc_id)).size} sources`));
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
        case "not_covered":
          state.mode = "not_covered";
          notices.append(el("div", { class: "notice notice--warn" }, ev.message));
          break;
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
      el("h4", {}, "Want to explore related topics?"),
      el("div", { class: "chips" },
        rel.questions.map((q) => el("button", { class: "chip", onclick: () => ask(q) }, q)),
        rel.topics.map((t) => el("button", { class: "chip chip--topic", onclick: () => openTopic(t.id) }, `Topic: ${t.name}`))));
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
    box.replaceChildren(explore, save);
  }

  // ── Topics ─────────────────────────────────────────────────────────────────
  async function openTopic(id) {
    closeSidebar();
    const view = $("#topicView");
    $("#hero").hidden = true; $("#thread").hidden = true; view.hidden = false;
    view.replaceChildren(el("p", { class: "tag" }, el("span", { class: "spinner" }), "Loading topic…"));
    window.scrollTo({ top: 0 });
    const t = await fetch(`/api/topics/${encodeURIComponent(id)}`).then((r) => r.json());
    const points = (t.perspectives || []).map((p) => p.claim);
    const startNb = () => {
      const nb = createNotebook(t.name);
      nb.entries.push({
        kind: "topic", topic_name: t.name, overview: t.overview || t.description, points,
        sources: (t.passages || []).slice(0, 6).map(slimSource), saved_at: new Date().toISOString(),
      });
      persist(); openDrawer(); toast(`Started notebook “${t.name}”`);
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
    const nb = { id: uid(), title: title || "My notebook", created: new Date().toISOString(), entries: [] };
    notebooks.unshift(nb); activeId = nb.id; persist();
    return nb;
  }

  function findOrCreate(title) {
    return notebooks.find((n) => n.title.toLowerCase() === String(title).toLowerCase()) || createNotebook(title);
  }

  function saveEntry(entry, _topicId, notebookTitle) {
    const nb = notebookTitle ? findOrCreate(notebookTitle) : (active() || createNotebook("My notebook"));
    nb.entries.push({ ...entry, saved_at: new Date().toISOString() });
    activeId = nb.id; persist(); renderDrawer();
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
      el("button", { class: n.id === activeId ? "active" : "", onclick: () => { activeId = n.id; persist(); openDrawer(); } },
        el("span", {}, n.title), el("small", {}, String(n.entries.length))))));
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
    $("#nbMeta").textContent = `${nb.entries.length} saved · started ${nb.created.slice(0, 10)}`;
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
      return el("div", { class: "entry" },
        el("div", { class: "entry__kind" }, label),
        el("div", { class: "entry__title" }, title || ""),
        el("div", { class: "entry__body" }, (bodyText || "").replace(/\[(\d+)\]/g, "")),
        note,
        el("div", { class: "entry__actions" },
          el("button", { onclick: () => { nb.entries.splice(i, 1); persist(); renderDrawer(); } }, "Remove")));
    }));
  }

  function openDrawer() {
    $("#drawer").hidden = false; $(".layout").classList.add("with-drawer"); renderDrawer();
  }
  function closeDrawer() { $("#drawer").hidden = true; $(".layout").classList.remove("with-drawer"); }

  async function download(kind, nb) {
    if (!nb || !nb.entries?.length) return toast("Nothing to export yet");
    toast(kind === "pdf" ? "Preparing PDF…" : "Preparing Markdown…");
    const res = await fetch(`/api/export/${kind}`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ title: nb.title, created: nb.created, entries: nb.entries }),
    });
    if (!res.ok) return toast("Export failed. Please try again.");
    const blob = await res.blob();
    const name = (res.headers.get("Content-Disposition") || "").match(/filename="([^"]+)"/)?.[1] || `cited.${kind === "pdf" ? "pdf" : "md"}`;
    const a = el("a", { href: URL.createObjectURL(blob), download: name });
    document.body.append(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 4000);
  }

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
    try {
      meta = await fetch("/api/meta").then((r) => r.json());
      $("#statPods").textContent = meta.podcasts;
      $("#statPosts").textContent = meta.newsletters;
      $("#modeNote").textContent = meta.answers
        ? "Answers come only from the free archive and cite every source. Check a clip before quoting it."
        : "Preview: search-only mode. Answers will appear once answer writing is switched on.";
      renderTopics();
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

    const q = new URLSearchParams(location.search).get("q");
    if (q) ask(q);
  }
  init();
})();
