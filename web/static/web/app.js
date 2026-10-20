/* Pulse web console. Vanilla JS over the session-authenticated REST API.
   Every permission is enforced by the backend; the UI only hides what would fail. */
(() => {
  "use strict";

  const $ = (s, el = document) => el.querySelector(s);
  const app = $("#app");
  const state = { me: null, filter: "open", sev: "", incidents: [], engineers: null, timer: null };
  const STEPS = ["OPEN", "IN_PROGRESS", "RESOLVED", "CLOSED"];
  const LABEL = { OPEN: "Open", IN_PROGRESS: "In progress", RESOLVED: "Resolved", CLOSED: "Closed", CANCELLED: "Cancelled" };
  const SEV = { CRITICAL: "Critical", HIGH: "High", MEDIUM: "Medium", LOW: "Low" };
  const NEXT = { OPEN: ["IN_PROGRESS", "CANCELLED"], IN_PROGRESS: ["RESOLVED"], RESOLVED: ["CLOSED"] };
  const VERB = { IN_PROGRESS: "Start work", RESOLVED: "Mark resolved", CLOSED: "Close incident", CANCELLED: "Cancel incident" };

  const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const initials = (n) => esc(n.split(/[._\s-]/).filter(Boolean).slice(0, 2).map((p) => p[0].toUpperCase()).join(""));
  const role = () => state.me?.role;

  function cookie(name) {
    const m = document.cookie.match(new RegExp("(?:^|; )" + name + "=([^;]*)"));
    return m ? decodeURIComponent(m[1]) : "";
  }

  async function api(path, { method = "GET", body } = {}) {
    const opts = { method, credentials: "same-origin", headers: { Accept: "application/json" } };
    if (method !== "GET") opts.headers["X-CSRFToken"] = cookie("csrftoken");
    if (body !== undefined) { opts.headers["Content-Type"] = "application/json"; opts.body = JSON.stringify(body); }
    const res = await fetch("/api" + path, opts);
    const data = res.status === 204 ? null : await res.json().catch(() => null);
    if (!res.ok) {
      const err = new Error(errorText(data) || `Request failed (${res.status})`);
      err.status = res.status;
      throw err;
    }
    return data;
  }

  function errorText(d) {
    if (!d) return "";
    if (typeof d === "string") return d;
    if (d.detail) return d.detail;
    return Object.entries(d).map(([k, v]) => `${k === "non_field_errors" ? "" : k + ": "}${[].concat(v).join(" ")}`).join(" · ");
  }

  function toast(msg, bad = false) {
    const t = $("#toast");
    t.textContent = msg;
    t.className = "toast" + (bad ? " bad" : "");
    t.hidden = false;
    clearTimeout(toast.h);
    toast.h = setTimeout(() => (t.hidden = true), 4000);
  }

  // --- time helpers ------------------------------------------------------
  function dur(ms) {
    const a = Math.abs(ms), m = Math.round(a / 60000);
    if (m < 60) return `${m}m`;
    const h = Math.floor(m / 60), r = m % 60;
    if (h < 48) return r ? `${h}h ${r}m` : `${h}h`;
    return `${Math.round(h / 24)}d`;
  }
  const when = (iso) => new Date(iso).toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });

  function sla(inc) {
    const start = new Date(inc.created_at).getTime(), end = new Date(inc.sla_deadline).getTime();
    const now = Date.now();
    const closedAt = inc.resolved_at ? new Date(inc.resolved_at).getTime() : null;
    if (inc.status === "CANCELLED") return { cls: "done", pct: 0, text: "Cancelled" };
    if (closedAt) {
      const met = closedAt <= end;
      return { cls: met ? "done" : "burnt", pct: 100, text: met ? `Resolved with ${dur(end - closedAt)} to spare` : `Resolved ${dur(closedAt - end)} after deadline` };
    }
    const left = end - now;
    if (left <= 0 || inc.sla_breached) return { cls: "burnt", pct: 100, text: `Breached ${dur(now - end)} ago`, breached: true };
    const pct = Math.max(0, Math.min(100, (left / (end - start)) * 100));
    return { cls: pct < 20 ? "hot" : pct < 50 ? "warn" : "", pct, text: `${dur(left)} left` };
  }
  const fuse = (s, big = "") => `<div class="fuse ${s.cls} ${big}" role="img" aria-label="SLA: ${esc(s.text)}"><i style="width:${s.cls === "burnt" ? 0 : s.pct}%"></i></div>`;

  // --- auth ----------------------------------------------------------------
  async function boot() {
    try {
      state.me = await api("/auth/me/");
    } catch {
      return renderLogin();
    }
    $("#topbar").hidden = false;
    $("#who").textContent = `${state.me.username} · ${state.me.role[0] + state.me.role.slice(1).toLowerCase()}`;
    $("#teamBtn").hidden = role() !== "ADMIN";
    pollNotes();
    clearInterval(state.timer);
    state.timer = setInterval(pollNotes, 30000);
    window.addEventListener("hashchange", route);
    route();
  }

  function renderLogin() {
    $("#topbar").hidden = true;
    app.innerHTML = `
      <div class="login">
        <div class="login-art">
          <svg viewBox="0 0 680 120" aria-hidden="true"><path d="M0 70h180l18-40 26 78 24-96 22 72 14-14h396"/></svg>
          <h1>Who is on it, and how long do we have?</h1>
          <p>Pulse tracks production incidents from first report to close, against the SLA for their severity.</p>
        </div>
        <form id="loginForm" novalidate>
          <h2>Sign in</h2>
          <label for="u">Username</label>
          <input id="u" type="text" autocomplete="username" required autofocus>
          <label for="p">Password</label>
          <input id="p" type="password" autocomplete="current-password" required>
          <button class="btn" type="submit">Sign in</button>
          <p class="error" id="loginErr" role="alert"></p>
        </form>
      </div>`;
    $("#loginForm").addEventListener("submit", async (e) => {
      e.preventDefault();
      $("#loginErr").textContent = "";
      try {
        await api("/auth/csrf/");
        await api("/auth/login/", { method: "POST", body: { username: $("#u").value.trim(), password: $("#p").value } });
        location.hash = "#/";
        boot();
      } catch (err) {
        $("#loginErr").textContent = err.message;
      }
    });
  }

  $("#logout").addEventListener("click", async () => {
    await api("/auth/logout/", { method: "POST" }).catch(() => {});
    clearInterval(state.timer);
    state.me = null;
    renderLogin();
  });

  // --- routing ---------------------------------------------------------------
  function route() {
    if (!state.me) return;
    const h = location.hash.replace(/^#\/?/, "");
    if (h === "team") return renderTeam();
    const m = h.match(/^incident\/(\d+)/);
    renderConsole(m ? Number(m[1]) : null);
  }

  const FILTERS = [
    ["open", "Active", "status=OPEN,IN_PROGRESS"],
    ["mine", "Assigned to me", "mine=true&status=OPEN,IN_PROGRESS,RESOLVED"],
    ["unassigned", "Unassigned", "unassigned=true&status=OPEN"],
    ["breached", "Breached", "breached=true&status=OPEN,IN_PROGRESS"],
    ["resolved", "Resolved", "status=RESOLVED"],
    ["all", "Everything", ""],
  ];

  async function renderConsole(selectedId) {
    if (!$(".console")) {
      app.innerHTML = `
        <div class="console">
          <section class="queue" aria-label="Incident queue">
            <div class="queue-head">
              <h1>Incidents</h1>
              ${role() === "ENGINEER" ? `<button class="btn" id="newBtn">Report incident</button>` : ""}
            </div>
            <div class="filters" role="group" aria-label="Filter incidents">
              ${FILTERS.filter(([k]) => k !== "mine" || role() === "ENGINEER").map(([k, l]) => `<button class="chip" data-f="${k}" aria-pressed="false">${l}</button>`).join("")}
              <select id="sevSel" aria-label="Severity" style="width:auto;padding:.15rem .4rem;font-size:.85rem">
                <option value="">Any severity</option>${Object.entries(SEV).map(([k, v]) => `<option value="${k}">${v}</option>`).join("")}
              </select>
            </div>
            <ol class="rows" id="rows"></ol>
          </section>
          <div class="detail-wrap" id="detail"></div>
        </div>`;
      app.querySelectorAll("[data-f]").forEach((b) => b.addEventListener("click", () => { state.filter = b.dataset.f; loadQueue(); }));
      $("#sevSel").addEventListener("change", (e) => { state.sev = e.target.value; loadQueue(); });
      $("#newBtn")?.addEventListener("click", openNewIncident);
      await loadQueue();
    }
    $(".console").classList.toggle("has-detail", !!selectedId);
    markSelected(selectedId);
    if (selectedId) renderDetail(selectedId);
    else $("#detail").innerHTML = `<div class="detail-empty"><div><h2>Pick an incident from the queue</h2><p class="hint">The bar under each one is its SLA: it shortens as the resolution deadline gets closer.</p></div></div>`;
  }

  async function loadQueue() {
    app.querySelectorAll("[data-f]").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.f === state.filter)));
    const q = [FILTERS.find(([k]) => k === state.filter)[2], state.sev && `severity=${state.sev}`].filter(Boolean).join("&");
    const rows = $("#rows");
    try {
      const data = await api(`/incidents/?page_size=100${q ? "&" + q : ""}`);
      state.incidents = data.results;
    } catch (e) {
      rows.innerHTML = `<li class="empty">${esc(e.message)}</li>`;
      return;
    }
    if (!state.incidents.length) {
      rows.innerHTML = `<li class="empty">Nothing here. ${role() === "ENGINEER" && state.filter !== "all" ? "Use “Report incident” when something breaks." : "Try another filter."}</li>`;
      return;
    }
    rows.innerHTML = state.incidents.map((i) => {
      const s = sla(i);
      const people = i.assignments.map((a) => a.engineer.username);
      return `<li class="row"><a href="#/incident/${i.id}" data-id="${i.id}">
        <span class="id num">#${i.id}</span>
        <span class="title">${esc(i.title)}</span>
        <span class="left num">${esc(s.text)}</span>
        <span class="meta"><span class="sev sev-${i.severity}">${SEV[i.severity]}</span><span>${LABEL[i.status]}</span><span>${esc(i.service.name)}</span><span>${people.length ? esc(people.join(", ")) : "Nobody assigned"}</span></span>
        <span class="fuse-wrap" style="grid-column:1/4">${fuse(s)}</span>
      </a></li>`;
    }).join("");
    const m = location.hash.match(/incident\/(\d+)/);
    markSelected(m ? Number(m[1]) : null);
  }

  function markSelected(id) {
    app.querySelectorAll(".row a").forEach((a) => {
      if (Number(a.dataset.id) === id) a.setAttribute("aria-current", "page");
      else a.removeAttribute("aria-current");
    });
  }

  // --- detail -----------------------------------------------------------------
  async function renderDetail(id) {
    const host = $("#detail");
    let inc, comments, history;
    try {
      [inc, comments, history] = await Promise.all([api(`/incidents/${id}/`), api(`/incidents/${id}/comments/`), api(`/incidents/${id}/history/`)]);
    } catch (e) {
      host.innerHTML = `<div class="detail"><a class="link back" href="#/">Back to queue</a><h2>${e.status === 404 ? "This incident doesn't exist or isn't visible to you." : esc(e.message)}</h2></div>`;
      return;
    }
    const s = sla(inc);
    const me = state.me;
    const assigned = inc.assignments.some((a) => a.engineer.id === me.id);
    const can = (to) => (role() === "ENGINEER" && assigned) || (role() === "MANAGER" && (to === "CANCELLED" || to === "CLOSED"));
    const moves = (NEXT[inc.status] || []).filter(can);
    const assignable = role() === "MANAGER" && ["OPEN", "IN_PROGRESS"].includes(inc.status);
    const idx = STEPS.indexOf(inc.status);

    host.innerHTML = `
      <article class="detail">
        <a class="link back" href="#/">Back to queue</a>
        <div class="d-head"><span class="num">#${inc.id}</span><h1>${esc(inc.title)}</h1></div>
        <div class="d-meta">
          <span class="sev sev-${inc.severity}">${SEV[inc.severity]}</span>
          <span>${esc(inc.service.name)}</span>
          <span>Reported by ${esc(inc.created_by.username)}, ${when(inc.created_at)}</span>
        </div>

        <div class="sla">
          <div class="sla-line ${s.breached ? "breached" : ""}"><span>Resolution deadline ${when(inc.sla_deadline)}</span><strong>${esc(s.text)}</strong></div>
          ${fuse(s, "big")}
        </div>

        ${inc.status === "CANCELLED"
          ? `<p class="cancelled-note">This incident was cancelled and is closed to further changes.</p>`
          : `<ol class="stepper" aria-label="Lifecycle">${STEPS.map((st, i) => `<li class="${i < idx ? "done" : i === idx ? "now" : ""}" ${i === idx ? 'aria-current="step"' : ""}>${LABEL[st]}</li>`).join("")}</ol>`}

        ${moves.length ? `<div class="actions">${moves.map((to) => `<button class="btn ${to === "CANCELLED" ? "danger" : ""}" data-move="${to}">${VERB[to]}</button>`).join("")}</div>` : ""}

        <div class="grid2">
          <div>
            <section class="block"><h2>What's happening</h2><p class="desc">${esc(inc.description) || '<span class="hint">No description yet.</span>'}</p></section>

            <section class="block">
              <h2>Discussion</h2>
              <ol class="comments">${comments.map((c) => `
                <li><span class="avatar" aria-hidden="true">${initials(c.user.username)}</span>
                  <span class="by"><b>${esc(c.user.username)}</b> · ${when(c.created_at)}</span>
                  <p>${esc(c.content)}</p></li>`).join("") || '<li class="hint" style="display:block">No comments yet.</li>'}</ol>
              <form class="composer" id="commentForm">
                <label for="cbody" class="hint" style="font-weight:400;margin:0">Add an update for the team</label>
                <textarea id="cbody" required placeholder="Rollback completed. Monitoring the payment API."></textarea>
                <button class="btn" type="submit">Post update</button>
              </form>
            </section>

            <section class="block">
              <h2>AI analysis</h2>
              <p class="hint">Sends this incident's description, timeline and comments to the configured model and returns likely causes and next steps.</p>
              <button class="btn ghost" id="aiBtn">Analyse incident</button>
              <div id="aiOut" style="margin-top:1rem"></div>
            </section>
          </div>

          <aside>
            <section class="block">
              <h2>Engineers ${assignable ? `<button class="link" id="assignBtn">Change</button>` : ""}</h2>
              <ul class="people">${inc.assignments.map((a) => `
                <li><span class="avatar" aria-hidden="true">${initials(a.engineer.username)}</span>
                <span>${esc(a.engineer.username)}${a.engineer.id === me.id ? " (you)" : ""}<small>Added by ${esc(a.assigned_by.username)}, ${when(a.assigned_at)}</small></span></li>`).join("")
                || `<li class="hint">Nobody assigned yet.${role() === "MANAGER" ? "" : " A manager will pick engineers."}</li>`}</ul>
            </section>
            <section class="block">
              <h2>Timeline</h2>
              <ol class="timeline">${history.map((h) => `<li class="${h.action === "SLA_BREACHED" ? "breach" : ""}">${historyText(h)}<time>${when(h.created_at)}</time></li>`).join("")}</ol>
            </section>
          </aside>
        </div>
      </article>`;

    host.querySelectorAll("[data-move]").forEach((b) => b.addEventListener("click", async () => {
      const to = b.dataset.move;
      if (to === "CANCELLED" && !confirm("Cancel this incident? This can't be undone.")) return;
      b.disabled = true;
      try {
        await api(`/incidents/${id}/`, { method: "PATCH", body: { status: to } });
        toast(`Incident #${id} is now ${LABEL[to].toLowerCase()}.`);
        refresh(id);
      } catch (e) { toast(e.message, true); b.disabled = false; }
    }));
    $("#commentForm").addEventListener("submit", async (e) => {
      e.preventDefault();
      const content = $("#cbody").value.trim();
      if (!content) return;
      try {
        await api(`/incidents/${id}/comments/`, { method: "POST", body: { content } });
        toast("Update posted.");
        renderDetail(id);
      } catch (err) { toast(err.message, true); }
    });
    $("#assignBtn")?.addEventListener("click", () => openAssign(inc));
    $("#aiBtn").addEventListener("click", async (e) => {
      const out = $("#aiOut");
      e.target.disabled = true;
      out.innerHTML = `<p class="hint">Analysing…</p>`;
      try {
        const r = await api(`/incidents/${id}/ai-analysis/`, { method: "POST" });
        out.innerHTML = `<div class="ai-out">${esc(r.analysis)}</div><p class="hint">Generated by ${esc(r.model)}. Check it against what you're seeing.</p>`;
      } catch (err) {
        out.innerHTML = `<p class="error">${esc(err.message)}</p>`;
      }
      e.target.disabled = false;
    });
  }

  function historyText(h) {
    const who = h.user ? `<b>${esc(h.user.username)}</b>` : "<b>Pulse</b>";
    switch (h.action) {
      case "CREATED": return `${who} reported the incident`;
      case "ASSIGNED": return `${who} assigned ${esc(h.new_value)}`;
      case "UNASSIGNED": return `${who} removed ${esc(h.old_value)}`;
      case "STATUS_CHANGED": return `${who} moved it from ${LABEL[h.old_value] || esc(h.old_value)} to ${LABEL[h.new_value] || esc(h.new_value)}`;
      case "SLA_BREACHED": return `SLA deadline passed with the incident unresolved`;
      default: return `${who} ${esc(h.action)}`;
    }
  }

  async function refresh(id) {
    await loadQueue();
    renderDetail(id);
  }

  // --- dialogs ----------------------------------------------------------------
  const dlg = $("#dlg");
  function openDialog(html, onSubmit) {
    dlg.innerHTML = html;
    dlg.showModal();
    dlg.querySelector("[data-close]")?.addEventListener("click", () => dlg.close());
    dlg.querySelector("form").addEventListener("submit", async (e) => {
      e.preventDefault();
      const err = dlg.querySelector(".error");
      err.textContent = "";
      try { await onSubmit(new FormData(e.target)); dlg.close(); }
      catch (x) { err.textContent = x.message; }
    });
  }

  async function openNewIncident() {
    const [services, policies] = await Promise.all([api("/services/"), api("/sla-policies/").catch(() => [])]);
    if (!services.length) return toast("No services exist yet. Ask an admin to add one.", true);
    openDialog(`
      <form>
        <h2>Report an incident</h2>
        <label for="t">What's broken?</label>
        <input id="t" name="title" type="text" maxlength="200" required placeholder="Payment API returning HTTP 500">
        <label for="svc">Affected service</label>
        <select id="svc" name="service">${services.map((s) => `<option value="${s.id}">${esc(s.name)}</option>`).join("")}</select>
        <label for="sv">Severity</label>
        <select id="sv" name="severity">${Object.entries(SEV).map(([k, v]) => `<option value="${k}" ${k === "HIGH" ? "selected" : ""}>${v}</option>`).join("")}</select>
        <p class="hint" id="slaHint"></p>
        <label for="d">Details</label>
        <textarea id="d" name="description" placeholder="When it started, what users see, recent deploys…"></textarea>
        <p class="error" role="alert"></p>
        <div class="row-btns"><button type="button" class="btn ghost" data-close>Discard</button><button class="btn" type="submit">Report incident</button></div>
      </form>`, async (f) => {
      const inc = await api("/incidents/", { method: "POST", body: Object.fromEntries(f) });
      toast(`Incident #${inc.id} reported. Managers have been notified.`);
      state.filter = "open";
      await loadQueue();
      location.hash = `#/incident/${inc.id}`;
    });
    const hint = () => {
      const p = policies.find((x) => x.severity === $("#sv").value);
      $("#slaHint").textContent = p ? `Must be resolved within ${dur(p.resolution_minutes * 60000)}.` : "";
    };
    $("#sv").addEventListener("change", hint);
    hint();
  }

  async function openAssign(inc) {
    state.engineers ??= await api("/engineers/");
    const current = new Set(inc.assignments.map((a) => a.engineer.id));
    openDialog(`
      <form>
        <h2>Engineers on #${inc.id}</h2>
        <p class="hint">Everyone checked can work the incident and move it through its lifecycle. Newly added engineers get a notification.</p>
        <ul class="picklist">${state.engineers.map((u) => `<li><label><input type="checkbox" name="e" value="${u.id}" ${current.has(u.id) ? "checked" : ""}> ${esc(u.username)}</label></li>`).join("")}</ul>
        <p class="error" role="alert"></p>
        <div class="row-btns"><button type="button" class="btn ghost" data-close>Discard</button><button class="btn" type="submit">Save engineers</button></div>
      </form>`, async (f) => {
      const ids = f.getAll("e").map(Number);
      const r = await api(`/incidents/${inc.id}/assign/`, { method: "PATCH", body: { engineer_ids: ids } });
      const parts = [r.added.length && `added ${r.added.join(", ")}`, r.removed.length && `removed ${r.removed.join(", ")}`].filter(Boolean);
      toast(parts.length ? `Engineers saved: ${parts.join("; ")}.` : "No changes to engineers.");
      refresh(inc.id);
    });
  }

  // --- notifications ---------------------------------------------------------
  async function pollNotes() {
    try {
      const { unread } = await api("/notifications/unread-count/");
      const c = $("#unread");
      c.hidden = !unread;
      c.textContent = unread > 99 ? "99+" : unread;
      $("#bell").setAttribute("aria-label", `Notifications, ${unread} unread`);
    } catch (e) { if (e.status === 403) renderLogin(); }
  }

  async function openDrawer() {
    const d = $("#drawer");
    d.hidden = false;
    const data = await api("/notifications/?page_size=50");
    $("#notes").innerHTML = data.results.map((n) => `
      <li class="${n.is_read ? "" : "unread"} t-${n.type}"><a href="#/incident/${n.incident}" data-n="${n.id}" data-read="${n.is_read}">
        ${esc(n.message)}<time>${when(n.created_at)}</time></a></li>`).join("") || `<li class="empty">You're all caught up.</li>`;
    $("#closeDrawer").focus();
  }
  $("#bell").addEventListener("click", openDrawer);
  $("#closeDrawer").addEventListener("click", () => ($("#drawer").hidden = true));
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") $("#drawer").hidden = true; });
  $("#notes").addEventListener("click", async (e) => {
    const a = e.target.closest("a[data-n]");
    if (!a) return;
    $("#drawer").hidden = true;
    if (a.dataset.read === "false") {
      await api(`/notifications/${a.dataset.n}/`, { method: "PATCH", body: { is_read: true } }).catch(() => {});
      pollNotes();
    }
  });
  $("#markAll").addEventListener("click", async () => {
    await api("/notifications/mark-all-read/", { method: "POST" });
    pollNotes();
    openDrawer();
  });

  // --- admin: team & services --------------------------------------------------
  $("#teamBtn").addEventListener("click", () => (location.hash = "#/team"));
  async function renderTeam() {
    const [users, services] = await Promise.all([api("/users/?page_size=200"), api("/services/")]);
    app.innerHTML = `
      <div class="team">
        <section>
          <a class="link" href="#/">Back to incidents</a>
          <h1 style="margin:.5rem 0 1rem">People</h1>
          <table class="plain"><thead><tr><th>Username</th><th>Role</th><th>Status</th><th></th></tr></thead><tbody>
          ${users.results.map((u) => `<tr><td>${esc(u.username)}<br><span class="hint">${esc(u.email)}</span></td><td>${u.role[0] + u.role.slice(1).toLowerCase()}</td>
            <td>${u.is_active ? "Active" : "Deactivated"}</td>
            <td>${u.id !== state.me.id ? `<button class="link" data-toggle="${u.id}" data-active="${u.is_active}">${u.is_active ? "Deactivate" : "Reactivate"}</button>` : ""}</td></tr>`).join("")}
          </tbody></table>
          <form class="inline-form" id="userForm">
            <div><label for="nu">Username</label><input id="nu" name="username" type="text" required></div>
            <div><label for="ne">Email</label><input id="ne" name="email" type="email" required></div>
            <div><label for="nr">Role</label><select id="nr" name="role"><option value="ENGINEER">Engineer</option><option value="MANAGER">Manager</option><option value="ADMIN">Admin</option></select></div>
            <div><label for="np">Temporary password</label><input id="np" name="password" type="password" required autocomplete="new-password"></div>
            <button class="btn" type="submit">Add person</button>
          </form>
        </section>
        <section>
          <h1 style="margin:2rem 0 1rem">Services</h1>
          <table class="plain"><thead><tr><th>Name</th><th>Description</th></tr></thead><tbody>
          ${services.map((s) => `<tr><td>${esc(s.name)}</td><td>${esc(s.description)}</td></tr>`).join("")}</tbody></table>
          <form class="inline-form" id="svcForm">
            <div><label for="sn">Name</label><input id="sn" name="name" type="text" required></div>
            <div><label for="sd">Description</label><input id="sd" name="description" type="text"></div>
            <button class="btn" type="submit">Add service</button>
          </form>
        </section>
      </div>`;
    $("#userForm").addEventListener("submit", async (e) => {
      e.preventDefault();
      try { await api("/users/", { method: "POST", body: Object.fromEntries(new FormData(e.target)) }); toast("Person added."); state.engineers = null; renderTeam(); }
      catch (x) { toast(x.message, true); }
    });
    $("#svcForm").addEventListener("submit", async (e) => {
      e.preventDefault();
      try { await api("/services/", { method: "POST", body: Object.fromEntries(new FormData(e.target)) }); toast("Service added."); renderTeam(); }
      catch (x) { toast(x.message, true); }
    });
    app.querySelectorAll("[data-toggle]").forEach((b) => b.addEventListener("click", async () => {
      const active = b.dataset.active === "true";
      await api(`/users/${b.dataset.toggle}/`, { method: "PATCH", body: { is_active: !active } });
      toast(active ? "Person deactivated." : "Person reactivated.");
      state.engineers = null;
      renderTeam();
    }));
  }

  // keep SLA countdowns fresh without a full reload
  setInterval(() => { if (state.me && $("#rows")) loadQueue(); }, 60000);

  boot();
})();
