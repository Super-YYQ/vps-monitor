"use strict";
const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
const escapeHTML = (value) =>
  String(value ?? "").replace(
    /[&<>"']/g,
    (char) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        char
      ],
  );
const names = {
  in_stock: "有货",
  out_of_stock: "缺货",
  unknown: "未知",
  error: "检测失败",
  paused: "已暂停",
  confirming: "确认中",
};
const views = {
  overview: "监控总览",
  catalog: "商家与机型",
  notifications: "通知记录",
  mail: "邮件通知",
  settings: "系统设置",
};
const state = {
  csrf: "",
  dashboard: null,
  catalog: null,
  view: "overview",
  filter: "",
  editing: null,
  discovered: [],
  deleting: null,
  timer: null,
  mail: null,
  editingMail: null,
  deletingMail: null,
};
const defaults = {
  provider: "",
  name: "",
  url: "",
  purchase_url: "",
  adapter: "html",
  selector: "",
  product_match: "",
  expected_text: "",
  mirror_aliases: [],
  in_stock: ["In Stock", "有货"],
  out_of_stock: ["Out of Stock", "Sold Out", "缺货", "售罄"],
  json_path: "",
  interval: 120,
  confirmations: 2,
  enabled: false,
  notify: true,
  notify_initial: false,
  region: "Los Angeles",
  specs: "",
  price_note: "",
  notes: "",
};
let toastTimer;
function toast(message, error = false) {
  const node = $("#toast");
  node.textContent = message;
  node.classList.toggle("error-toast", error);
  node.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (node.hidden = true), 5500);
}
function time(value, date = false) {
  if (!value) return "尚未检测";
  return new Intl.DateTimeFormat("zh-CN", {
    timeZone: "Asia/Shanghai",
    ...(date ? { month: "2-digit", day: "2-digit" } : {}),
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  }).format(new Date(value * 1000));
}
function relative(value) {
  if (!value) return "等待首次检测";
  const seconds = Math.max(0, Math.floor(Date.now() / 1000 - value));
  return seconds < 60
    ? "刚刚"
    : seconds < 3600
      ? `${Math.floor(seconds / 60)} 分钟前`
      : `${Math.floor(seconds / 3600)} 小时前`;
}
function badge(status) {
  return `<span class="badge ${escapeHTML(status)}">${escapeHTML(names[status] || status)}</span>`;
}
function safeLink(url) {
  try {
    const u = new URL(url);
    return ["http:", "https:"].includes(u.protocol) ? escapeHTML(u.href) : "#";
  } catch {
    return "#";
  }
}
function initials(provider) {
  return (
    {
      VMISS: "VM",
      VMRack: "VR",
      DMIT: "DM",
      BandwagonHost: "BW",
      ZgoCloud: "ZG",
      VIRCS: "VI",
      DigitalFyre: "DF",
    }[provider] || provider.slice(0, 2).toUpperCase()
  );
}
function logo(provider) {
  return `<span class="provider-logo" data-provider="${escapeHTML(provider)}">${escapeHTML(initials(provider))}</span>`;
}
function uiStatus(row) {
  if (!row.config.enabled) return "paused";
  if (
    ["in_stock", "out_of_stock"].includes(row.status) &&
    row.status !== row.stable
  )
    return "confirming";
  return row.status;
}
function showLogin() {
  state.csrf = "";
  clearInterval(state.timer);
  $("#app").hidden = true;
  $("#login-screen").hidden = false;
  $$("dialog[open]").forEach((d) => d.close());
}
async function api(path, options = {}) {
  const headers = {
    "Content-Type": "application/json",
    ...(state.csrf ? { "X-CSRF-Token": state.csrf } : {}),
    ...options.headers,
  };
  const response = await fetch(`/api${path}`, {
    ...options,
    headers,
    credentials: "same-origin",
  });
  let data;
  try {
    data = await response.json();
  } catch {
    throw new Error("服务响应异常，请检查连接");
  }
  if (!response.ok) {
    if (response.status === 401 && path !== "/login") showLogin();
    throw new Error(
      typeof data.detail === "string" ? data.detail : "请求失败，请检查输入",
    );
  }
  return data;
}
async function busy(button, action) {
  if (button.disabled) return;
  button.disabled = true;
  try {
    await action();
  } catch (error) {
    toast(error.message, true);
  } finally {
    button.disabled = false;
  }
}
async function enterApp() {
  $("#login-screen").hidden = true;
  $("#app").hidden = false;
  $("#login-form").reset();
  await Promise.all([loadDashboard(), loadCatalog()]);
  await setView(location.hash.slice(1) || "overview");
  clearInterval(state.timer);
  state.timer = setInterval(async () => {
    if (document.hidden) return;
    await loadDashboard();
    if (state.view === "notifications")
      await loadNotifications().catch((e) => toast(e.message, true));
  }, 15000);
}
async function loadDashboard() {
  try {
    state.dashboard = await api("/dashboard");
    $("#connection-error").hidden = true;
    renderDashboard();
  } catch (error) {
    $("#connection-error").textContent =
      `连接中断：${error.message}。当前显示的是最后一次取得的数据。`;
    $("#connection-error").hidden = false;
    $("#service-text").textContent = "连接中断";
    $("#service-dot").classList.add("off");
  }
}
function renderDashboard() {
  const data = state.dashboard,
    rows = data.monitors;
  const enabled = rows.filter((x) => x.config.enabled),
    stock = rows.filter((x) => uiStatus(x) === "in_stock");
  const problem = enabled.filter((x) =>
    ["unknown", "error"].includes(x.status),
  );
  const total = data.stats.checks || 0,
    percent = total
      ? Math.round(((data.stats.success || 0) / total) * 100)
      : null;
  $("#total-label").textContent = `${rows.length} 台`;
  const stats = [
    [
      "正在监控",
      enabled.length,
      "台",
      `${rows.length - enabled.length} 台已暂停`,
      "◎",
    ],
    ["当前有货", stock.length, "台", "已通过连续确认", "↗"],
    ["需要关注", problem.length, "台", "未知状态或检测异常", "◷"],
    ["今日检查", total.toLocaleString(), "次", "最近 24 小时累计", "⌁"],
  ];
  $("#summary").innerHTML = stats
    .map(
      ([label, value, unit, foot, icon], i) =>
        `<article class="stat"><div class="stat-label"><span>${label}</span><span>${icon}</span></div><div class="stat-value ${i === 1 ? "green" : ""}">${value}<em>${unit}</em></div><div class="stat-foot">${foot}</div></article>`,
    )
    .join("");
  $("#service-text").textContent = data.scheduler.running
    ? "监控服务运行中"
    : "监控服务未运行";
  $("#service-dot").classList.toggle("off", !data.scheduler.running);
  $("#mail-banner").hidden = data.mail_enabled;
  $("#last-updated").textContent = `同步于 ${time(data.now)}`;
  const selected = $("#provider-filter").value;
  $("#provider-filter").innerHTML =
    '<option value="">全部商家</option>' +
    [...new Set(rows.map((x) => x.config.provider))]
      .sort()
      .map((p) => `<option>${escapeHTML(p)}</option>`)
      .join("");
  $("#provider-filter").value = selected;
  $("#health-value").textContent = percent === null ? "—" : `${percent}%`;
  const end = Math.floor(data.now / 3600) * 3600;
  const bars = Array.from({ length: 24 }, (_, i) => {
    const h = end - (23 - i) * 3600;
    const b = data.buckets.find((x) => x.hour === h);
    const ratio = b ? b.success / b.total : 0;
    const height = b ? Math.max(5, ratio * 45) : 5;
    return `<rect x="${i * 9}" y="${48 - height}" width="6" height="${height}" rx="1" class="chart-bar ${b ? (ratio < 0.8 ? "fail" : "") : "empty"}"><title>${time(h)}：${b ? `${b.success}/${b.total} 次成功` : "无检查"}</title></rect>`;
  });
  $("#health-chart").innerHTML =
    `<svg viewBox="0 0 216 50" role="img" aria-label="24 小时检测记录">${bars.join("")}</svg>`;
  $("#events").innerHTML = data.events.length
    ? data.events
        .slice(0, 8)
        .map(
          (event) =>
            `<div class="event ${escapeHTML(event.status)}"><div class="event-name">${escapeHTML(event.name)}</div><div class="event-description">${escapeHTML(names[event.previous])} → ${escapeHTML(names[event.status])}</div><time>${time(event.created, true)}</time></div>`,
        )
        .join("")
    : '<div class="empty-small">还没有库存变更。<br>启用监控后，确认的变化会出现在这里。</div>';
  renderMonitors();
}
function renderMonitors() {
  if (!state.dashboard) return;
  const search = $("#search").value.trim().toLowerCase(),
    provider = $("#provider-filter").value,
    status = $("#status-filter").value;
  const rows = state.dashboard.monitors.filter(
    (row) =>
      (!provider || row.config.provider === provider) &&
      (!status || uiStatus(row) === status) &&
      (!search ||
        JSON.stringify([
          row.config.name,
          row.config.provider,
          row.config.specs,
          row.config.notes,
        ])
          .toLowerCase()
          .includes(search)),
  );
  $("#result-count").textContent = `${rows.length} 个监控`;
  if (!rows.length) {
    const empty = !state.dashboard.monitors.length;
    $("#monitor-list").innerHTML =
      `<div class="empty-state"><div class="empty-icon">◎</div><h3>${empty ? "你的下一台 VPS，从这里开始" : "没有符合条件的监控"}</h3><p>${empty ? "从 DMIT、VMISS、VMRack 等商家的预设中挑选，或添加你自己的机型。" : "试着更换商家、状态或搜索关键词。"}</p>${empty ? '<button class="primary" data-view="catalog">浏览商家与机型 →</button>' : ""}</div>`;
    return;
  }
  $("#monitor-list").innerHTML =
    `<div class="table-scroll"><table class="monitor-table"><thead><tr><th>商家 / 机型</th><th>库存与检查</th><th>管理</th></tr></thead><tbody>${rows
      .map((row) => {
        const c = row.config,
          s = uiStatus(row),
          id = escapeHTML(row.id),
          next = row.next_check ? time(row.next_check) : "排队中";
        return `<tr><td><div class="product-heading">${logo(c.provider)}<div><div class="product-name">${escapeHTML(c.name)}</div><div class="product-meta">${escapeHTML(c.provider)} <span>· ${escapeHTML(c.region)}</span></div></div></div>${c.specs ? `<div class="product-specs">${escapeHTML(c.specs)}</div>` : ""}${c.price_note ? `<div class="product-price">${escapeHTML(c.price_note)}</div>` : ""}<div class="row-tools"><button data-row="history" data-id="${id}">检测历史</button>${c.purchase_url || c.url ? `<a href="${safeLink(c.purchase_url || c.url)}" target="_blank" rel="noopener noreferrer">商品页 ↗</a>` : ""}<button data-row="edit" data-id="${id}">编辑规则</button></div></td><td>${badge(s)}${s === "confirming" ? `<div class="check-time">${escapeHTML(names[row.status])} · ${row.streak}/${c.confirmations} 次</div>` : ""}<div class="check-time" title="${time(row.last_check, true)}">${relative(row.last_check)}</div><div class="reason">${escapeHTML(c.enabled ? row.reason : !c.url ? "等待填写检测地址" : "监控已暂停")}</div><div class="check-time">${c.enabled ? `下次 ${next}` : `间隔 ${c.interval} 秒`}</div></td><td><div class="row-tools"><button data-row="toggle" data-id="${id}">${c.enabled ? "暂停" : "启用"}</button>${c.enabled ? `<button data-row="check" data-id="${id}">检查</button>` : ""}<button class="delete-link" data-row="delete" data-id="${id}">删除</button></div></td></tr>`;
      })
      .join("")}</tbody></table></div>`;
}
async function loadCatalog() {
  state.catalog = await api("/catalog");
  $("#catalog-notice").textContent = state.catalog.notice;
  $("#provider-options").innerHTML = state.catalog.providers
    .map((p) => `<option value="${escapeHTML(p.name)}">`)
    .join("");
  renderCatalog();
}
function renderCatalog() {
  if (!state.catalog) return;
  $("#catalog-filters").innerHTML = [
    "",
    ...state.catalog.providers.map((p) => p.name),
  ]
    .map(
      (p) =>
        `<button class="chip ${state.filter === p ? "active" : ""}" data-provider="${escapeHTML(p)}">${escapeHTML(p || "全部商家")}</button>`,
    )
    .join("");
  $("#catalog-grid").innerHTML = state.catalog.presets
    .filter((p) => !state.filter || p.config.provider === state.filter)
    .map((p) => {
      const c = p.config;
      return `<article class="panel catalog-card"><div class="card-top">${logo(c.provider)}<div><div class="card-provider">${escapeHTML(c.provider)}</div><div class="card-label">${escapeHTML(p.label)}</div></div></div><h2>${escapeHTML(c.name)}</h2><div class="price">${escapeHTML(c.price_note)}</div><div class="specs">${escapeHTML(c.specs || "详细配置待本轮活动页确认")}</div><p class="card-note">${escapeHTML(c.notes)}</p><div class="card-bottom"><span class="readiness">○ ${escapeHTML(p.readiness)}</span><button class="secondary" data-preset="${escapeHTML(p.id)}">＋ 添加监控</button></div></article>`;
    })
    .join("");
}
async function setView(view) {
  if (!views[view]) view = "overview";
  state.view = view;
  for (const [key, name] of Object.entries(views)) {
    $(`#view-${key}`).hidden = key !== view;
    $$(".nav-item")
      .filter((x) => x.dataset.view === key)
      .forEach((x) => x.classList.toggle("active", key === view));
  }
  $("#breadcrumb").textContent = `工作空间 / ${views[view]}`;
  if (location.hash !== `#${view}`) history.replaceState(null, "", `#${view}`);
  if (view === "notifications") await loadNotifications();
  if (view === "mail") await loadMail();
  if (view === "settings") await loadSettings();
}
async function loadSettings() {
  const mirror = await api("/settings/mirror");
  $("#mirror-toggle").checked = mirror.enabled;
}
function openMonitor(config = {}, id = null) {
  state.editing = id;
  const c = { ...defaults, ...config };
  const form = $("#monitor-form");
  form.reset();
  Object.entries(c).forEach(([key, value]) => {
    const input = form.elements.namedItem(key);
    if (!input) return;
    if (input.type === "checkbox") input.checked = value;
    else input.value = Array.isArray(value) ? value.join("\n") : value;
  });
  $("#monitor-dialog-title").textContent = id ? "编辑监控" : "添加监控";
  $("#monitor-error").textContent = "";
  $("#probe-result").hidden = true;
  adapterFields();
  $("#monitor-dialog").showModal();
}
function adapterFields() {
  const mode = $("#monitor-form").elements.adapter.value;
  $$("[data-field]", $("#monitor-form")).forEach(
    (el) => (el.hidden = !el.dataset.field.split(" ").includes(mode)),
  );
  $("#adapter-help").textContent = {
    html: "只读取一个产品区域。先用 CSS 选择器选卡片，再用产品名筛选；缺货标记优先。需确认有货标记真正代表该机型可购买。",
    whmcs:
      "使用带 pid 的 cart.php 商品链接，预期文字填写产品名。缺货文字优先；只有出现对应的产品配置表单才认定有货。有货标记字段不参与此模式。",
    json: "使用点分路径定位库存标量；有货、缺货值采用完整匹配，例如 true / false 或 1 / 0。",
  }[mode];
}
function readMonitor() {
  const form = $("#monitor-form"),
    c = {};
  Object.keys(defaults).forEach((key) => {
    const input = form.elements.namedItem(key);
    if (["in_stock", "out_of_stock", "mirror_aliases"].includes(key))
      c[key] = input.value
        .split("\n")
        .map((x) => x.trim())
        .filter(Boolean);
    else if (input.type === "checkbox") c[key] = input.checked;
    else if (input.type === "number") c[key] = Number(input.value);
    else c[key] = input.value.trim();
  });
  return c;
}
async function loadNotifications() {
  const rows = await api("/notifications");
  $("#notification-list").innerHTML = rows.length
    ? `<table class="log-table"><thead><tr><th>邮件主题</th><th>投递状态</th><th>尝试次数</th><th>时间</th></tr></thead><tbody>${rows.map((row) => `<tr><td>${escapeHTML(row.subject)}${row.error ? `<div class="error">${escapeHTML(row.error)}</div>` : ""}</td><td><span class="delivery ${escapeHTML(row.status)}">${{ sent: "已发送", pending: "待发送 / 重试中", failed: "发送失败", cancelled: "已取消" }[row.status] || escapeHTML(row.status)}</span></td><td>${row.attempts} / 5</td><td>${time(row.sent || row.created, true)}</td></tr>`).join("")}</tbody></table>`
    : '<div class="empty-state"><div class="empty-icon">✉</div><h3>还没有通知记录</h3><p>设置邮件后，可以先发送一封测试邮件。</p><button class="secondary" data-view="mail">配置邮件通知 →</button></div>';
}
async function loadMail() {
  state.mail = await api("/settings/mail/profiles");
  $("#mail-profiles").innerHTML = state.mail.profiles.length
    ? state.mail.profiles.map((c) => {
        const active = c.id === state.mail.active_id, id = escapeHTML(c.id);
        return `<article class="panel settings-card mail-card ${active ? "mail-active" : ""}">
          <div class="card-bottom"><h2>${escapeHTML(c.name)}</h2><span class="readiness">${active ? (c.enabled ? "● 当前使用" : "○ 当前已停用") : "备用配置"}</span></div>
          <p>${escapeHTML(c.sender || "尚未填写发件邮箱")}</p>
          <p class="muted small">${escapeHTML(c.host || "尚未填写服务器")} · ${c.port} · ${escapeHTML(c.security.toUpperCase())}</p>
          <p class="muted small">收件人：${escapeHTML(c.recipients.join("、") || "尚未填写")}</p>
          <div class="actions"><button class="secondary" data-mail="edit" data-id="${id}">编辑</button>
          ${!active ? `<button class="primary" data-mail="activate" data-id="${id}">设为当前</button>` : `<button class="secondary" data-mail="test" data-id="${id}" ${c.enabled ? "" : "disabled"}>发送测试邮件</button>`}
          <button class="text-button delete-link" data-mail="delete" data-id="${id}">删除</button></div></article>`;
      }).join("")
    : '<div class="panel empty-state"><div class="empty-icon">✉</div><h3>添加你的第一组邮箱</h3><p>支持保存多组 SMTP 配置，第一组会自动设为当前配置。</p></div>';
}
function openMail(c = null) {
  const form = $("#mail-form");
  form.reset();
  state.editingMail = c?.id || null;
  c = c || { enabled: true, port: 587, security: "starttls" };
  for (const [key, value] of Object.entries(c)) {
    const input = form.elements.namedItem(key);
    if (!input) continue;
    if (input.type === "checkbox") input.checked = value;
    else input.value = Array.isArray(value) ? value.join("\n") : value;
  }
  form.elements.password.value = "";
  form.elements.password.placeholder = c.has_password
    ? "已保存授权码，留空保持不变"
    : "填写 SMTP 密码或邮箱授权码";
  $("#mail-dialog-title").textContent = state.editingMail ? "编辑邮件配置" : "新增邮件配置";
  $("#mail-error").textContent = "";
  $("#mail-dialog").showModal();
}
$("#new-mail").addEventListener("click", () => openMail());

$("#login-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = $("button", event.currentTarget);
  await busy(button, async () => {
    try {
      const r = await api("/login", {
        method: "POST",
        body: JSON.stringify({
          password: event.currentTarget.elements.password.value,
        }),
      });
      state.csrf = r.csrf;
      $("#login-error").textContent = "";
      await enterApp();
    } catch (error) {
      $("#login-error").textContent = error.message;
    }
  });
});
$("#logout").addEventListener("click", () =>
  busy($("#logout"), async () => {
    await api("/logout", { method: "POST" });
    showLogin();
  }),
);
$("#refresh").addEventListener("click", () =>
  busy($("#refresh"), loadDashboard),
);
$("#refresh-notifications").addEventListener("click", () =>
  busy($("#refresh-notifications"), loadNotifications),
);
for (const id of ["search", "provider-filter", "status-filter"])
  $(`#${id}`).addEventListener(
    id === "search" ? "input" : "change",
    renderMonitors,
  );
$("#monitor-form").elements.adapter.addEventListener("change", adapterFields);
$("#monitor-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = $("button[type=submit]", event.currentTarget);
  await busy(button, async () => {
    try {
      const result = await api(
        state.editing ? `/monitors/${state.editing}` : "/monitors",
        {
          method: state.editing ? "PUT" : "POST",
          body: JSON.stringify(readMonitor()),
        },
      );
      $("#monitor-dialog").close();
      toast(result.ids?.length === 0 ? "该监控已存在" : "监控已保存");
      await loadDashboard();
      await setView("overview");
    } catch (error) {
      $("#monitor-error").textContent = error.message;
    }
  });
});
$("#probe").addEventListener("click", () =>
  busy($("#probe"), async () => {
    const node = $("#probe-result");
    node.hidden = false;
    node.textContent = "正在读取商家页面并验证规则…";
    const r = await api("/probe", {
      method: "POST",
      body: JSON.stringify(readMonitor()),
    });
    node.textContent = `${names[r.status]}：${r.reason}${r.latency ? `（${r.latency} ms）` : ""}`;
  }),
);
$("#mail-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  await busy($("button[type=submit]", form), async () => {
    const data = Object.fromEntries(new FormData(form));
    data.enabled = form.elements.enabled.checked;
    data.port = Number(data.port);
    data.recipients = data.recipients
      .split(/[\n,;]+/)
      .map((x) => x.trim())
      .filter(Boolean);
    try {
      await api(state.editingMail ? `/settings/mail/profiles/${state.editingMail}` : "/settings/mail/profiles", {
        method: state.editingMail ? "PUT" : "POST", body: JSON.stringify(data),
      });
      $("#mail-dialog").close();
      toast("邮件配置已保存");
      await loadMail();
      await loadDashboard();
    } catch (error) {
      $("#mail-error").textContent = error.message;
    }
  });
});
$("#password-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  await busy($("button", form), async () => {
    await api("/password", {
      method: "POST",
      body: JSON.stringify(Object.fromEntries(new FormData(form))),
    });
    form.reset();
    showLogin();
    toast("密码已更新，请重新登录");
  });
});
$("#mirror-toggle").addEventListener("change", async (event) => {
  await api("/settings/mirror", {
    method: "PUT",
    body: JSON.stringify({ enabled: event.target.checked }),
  });
  toast(event.target.checked ? "镜像源已启用" : "镜像源已关闭");
});
$("#export").addEventListener("click", () =>
  busy($("#export"), async () => {
    const config = await api("/export");
    const url = URL.createObjectURL(
      new Blob([JSON.stringify(config, null, 2)], { type: "application/json" }),
    );
    const a = document.createElement("a");
    a.href = url;
    a.download = `vps-radar-${new Date().toISOString().slice(0, 10)}.json`;
    a.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }),
);
$("#import").addEventListener("change", async (event) => {
  const input = event.currentTarget,
    file = input.files[0];
  if (!file) return;
  try {
    if (file.size > 128 * 1024) throw new Error("文件不能超过 128 KB");
    const parsed = JSON.parse(await file.text());
    const r = await api("/import", {
      method: "POST",
      body: JSON.stringify({ monitors: parsed.monitors }),
    });
    toast(`已导入 ${r.ids.length} 个监控，默认暂停`);
    await loadDashboard();
  } catch (error) {
    toast(error.message, true);
  } finally {
    input.value = "";
  }
});
$("#discover-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  await busy($("button", form), async () => {
    const r = await api("/discover", {
      method: "POST",
      body: JSON.stringify({ url: form.elements.url.value }),
    });
    state.discovered = r.products;
    $("#discovered").innerHTML = r.products.length
      ? r.products
          .map(
            (p, i) =>
              `<div class="discovered-item"><div><strong>${escapeHTML(p.name)}</strong><p class="muted small">PID ${escapeHTML(p.pid)}</p></div><button class="secondary" data-discovered="${i}">添加</button></div>`,
          )
          .join("")
      : '<p class="empty-small">未找到可确认的 WHMCS 商品卡片。该页面可能需要 JavaScript 或登录；可以手动填写产品链接。</p>';
  });
});
document.addEventListener("click", async (event) => {
  const close = event.target.closest("[data-close]");
  if (close) {
    $(`#${close.dataset.close}`).close();
    return;
  }
  const nav = event.target.closest("[data-view]");
  if (nav) {
    await setView(nav.dataset.view).catch((e) => toast(e.message, true));
    return;
  }
  if (event.target.closest('[data-action="new-monitor"]')) {
    openMonitor();
    return;
  }
  const provider = event.target.closest("#catalog-filters [data-provider]");
  if (provider) {
    state.filter = provider.dataset.provider;
    renderCatalog();
    return;
  }
  const preset = event.target.closest("[data-preset]");
  if (preset) {
    openMonitor(
      state.catalog.presets.find((p) => p.id === preset.dataset.preset).config,
    );
    return;
  }
  const discovered = event.target.closest("[data-discovered]");
  if (discovered) {
    const p = state.discovered[Number(discovered.dataset.discovered)];
    let provider = "";
    try {
      provider =
        state.catalog.providers.find(
          (x) =>
            new URL(x.directory || x.url).hostname === new URL(p.url).hostname,
        )?.name || new URL(p.url).hostname;
    } catch {}
    openMonitor({
      provider,
      name: p.name,
      url: p.url,
      adapter: "whmcs",
      expected_text: p.name,
      specs: p.specs,
    });
    return;
  }
  const mailButton = event.target.closest("[data-mail]");
  if (mailButton) {
    const c = state.mail?.profiles.find((p) => p.id === mailButton.dataset.id);
    if (!c) return;
    await busy(mailButton, async () => {
      const action = mailButton.dataset.mail;
      if (action === "edit") openMail(c);
      if (action === "activate") {
        await api(`/settings/mail/profiles/${c.id}/activate`, { method: "POST" });
        toast(c.enabled ? "已切换当前邮件配置" : "已选用此配置，通知处于停用状态");
        await loadMail();
        await loadDashboard();
      }
      if (action === "test") {
        const result = await api("/settings/mail/test", { method: "POST" });
        toast(result.message);
        await setView("notifications");
      }
      if (action === "delete") {
        state.deletingMail = c.id;
        state.deleting = null;
        $("#confirm-message").textContent = `确定删除邮件配置「${c.name}」？${c.id === state.mail.active_id ? "删除后邮件通知将停用，需另选当前配置。" : ""}`;
        $("#confirm-dialog").showModal();
      }
    });
    return;
  }
  const rowButton = event.target.closest("[data-row]");
  if (!rowButton) return;
  const row = state.dashboard.monitors.find(
    (r) => r.id === rowButton.dataset.id,
  );
  if (!row) return;
  await busy(rowButton, async () => {
    const action = rowButton.dataset.row;
    if (action === "edit") openMonitor(row.config, row.id);
    if (action === "toggle") {
      await api(`/monitors/${row.id}`, {
        method: "PUT",
        body: JSON.stringify({ ...row.config, enabled: !row.config.enabled }),
      });
      toast(row.config.enabled ? "监控已暂停" : "监控已启用");
      await loadDashboard();
    }
    if (action === "check") {
      const r = await api(`/monitors/${row.id}/check`, { method: "POST" });
      toast(r.message);
    }
    if (action === "history") {
      const rows = await api(`/monitors/${row.id}/history`);
      $("#history-title").textContent = row.config.name;
      $("#history-list").innerHTML = rows.length
        ? rows
            .map(
              (h) =>
                `<div class="history-item">${badge(h.status)}<div><p>${escapeHTML(h.reason)}</p><time>${time(h.created, true)} · ${h.latency || 0} ms</time></div></div>`,
            )
            .join("")
        : '<p class="empty-small">尚无检测记录。</p>';
      $("#history-dialog").showModal();
    }
    if (action === "delete") {
      state.deleting = row.id;
      state.deletingMail = null;
      $("#confirm-message").textContent =
        `确定删除「${row.config.provider} · ${row.config.name}」？`;
      $("#confirm-dialog").showModal();
    }
  });
});
$("#confirm-delete").addEventListener("click", () =>
  busy($("#confirm-delete"), async () => {
    if (state.deletingMail) {
      await api(`/settings/mail/profiles/${state.deletingMail}`, { method: "DELETE" });
      state.deletingMail = null;
      $("#confirm-dialog").close();
      toast("邮件配置已删除");
      await loadMail();
      await loadDashboard();
      return;
    }
    if (!state.deleting) return;
    await api(`/monitors/${state.deleting}`, { method: "DELETE" });
    state.deleting = null;
    $("#confirm-dialog").close();
    toast("监控已删除");
    await loadDashboard();
  }),
);
window.addEventListener("hashchange", () => {
  if (state.csrf)
    setView(location.hash.slice(1)).catch((e) => toast(e.message, true));
});
document.addEventListener("visibilitychange", () => {
  if (!document.hidden && state.csrf) loadDashboard();
});
(async () => {
  try {
    state.csrf = (await api("/session")).csrf;
    await enterApp();
  } catch {
    showLogin();
  }
})();
