/* TSR Enterprises - shared frontend helpers */

/* ------------------------------------------------------------- API wrapper */
async function api(url, options = {}) {
  const opts = { headers: {}, ...options };
  if (opts.body && typeof opts.body === "object" && !(opts.body instanceof FormData)) {
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(opts.body);
  }
  const res = await fetch(url, opts);
  let data = null;
  try { data = await res.json(); } catch (e) { /* non-JSON response */ }
  if (!res.ok) {
    const message = (data && (data.detail || data.message)) || `Request failed (${res.status})`;
    throw new Error(message);
  }
  return data;
}

/* ------------------------------------------------------------------ toasts */
function toast(message, type = "success") {
  const holder = document.getElementById("toastHolder");
  if (!holder) return alert(message);
  const el = document.createElement("div");
  el.className = `toast-item ${type}`;
  const icon = type === "success" ? "bi-check-circle-fill"
    : type === "error" ? "bi-x-circle-fill" : "bi-info-circle-fill";
  el.innerHTML = `<i class="bi ${icon}"></i><span></span>`;
  el.querySelector("span").textContent = message;
  holder.appendChild(el);
  setTimeout(() => { el.classList.add("hide"); setTimeout(() => el.remove(), 350); }, 3800);
}

function toastError(err) { toast(err.message || String(err), "error"); }

/* ------------------------------------------------------------- formatters */
const INR = new Intl.NumberFormat("en-IN", { maximumFractionDigits: 2 });
function fmtMoney(v) { return "₹" + INR.format(Number(v) || 0); }
function fmtNum(v) { return INR.format(Number(v) || 0); }
function fmtDate(v) {
  if (!v) return "-";
  const d = new Date(String(v).slice(0, 10));
  if (isNaN(d)) return v;
  return d.toLocaleDateString("en-IN", { day: "2-digit", month: "short", year: "numeric" });
}
function esc(v) {
  return String(v == null ? "" : v).replace(/[&<>"']/g,
    c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
function thisMonth() { return new Date().toISOString().slice(0, 7); }
function today() { return new Date().toISOString().slice(0, 10); }

function statusBadge(status) {
  const s = String(status || "").toLowerCase();
  const cls = ["active", "paid", "yes"].includes(s) ? "success"
    : ["partial", "pending"].includes(s) ? "warning"
    : ["inactive", "closed", "completed", "exit"].includes(s) ? "muted" : "gold";
  return `<span class="badge-soft ${cls}">${esc(status || "-")}</span>`;
}

/* ------------------------------------------------------------------ forms */
function formData(formEl) {
  const data = {};
  formEl.querySelectorAll("input[name], select[name], textarea[name]").forEach(el => {
    data[el.name] = el.type === "checkbox" ? (el.checked ? "Yes" : "No") : el.value.trim();
  });
  return data;
}

function fillForm(formEl, record) {
  formEl.reset();
  if (!record) return;
  formEl.querySelectorAll("input[name], select[name], textarea[name]").forEach(el => {
    const value = record[el.name];
    if (value == null) return;
    if (el.type === "checkbox") el.checked = ["yes", "true", "1"].includes(String(value).toLowerCase());
    else el.value = value;
  });
}

function openModal(id) { bootstrap.Modal.getOrCreateInstance(document.getElementById(id)).show(); }
function closeModal(id) { bootstrap.Modal.getOrCreateInstance(document.getElementById(id)).hide(); }

/* -------------------------------------------------------------- rendering */
function renderRows(tbodyId, rows, rowHtml, emptyText = "No records found") {
  const tbody = document.getElementById(tbodyId);
  if (!tbody) return;
  if (!rows || !rows.length) {
    const cols = tbody.closest("table").querySelectorAll("thead th").length;
    tbody.innerHTML = `<tr><td colspan="${cols}">
      <div class="empty-state"><i class="bi bi-inbox"></i>${esc(emptyText)}</div></td></tr>`;
    return;
  }
  tbody.innerHTML = rows.map(rowHtml).join("");
}

function setLoading(tbodyId) {
  const tbody = document.getElementById(tbodyId);
  if (!tbody) return;
  const cols = tbody.closest("table").querySelectorAll("thead th").length;
  tbody.innerHTML = `<tr><td colspan="${cols}"><div class="spinner-gold"></div></td></tr>`;
}

function setText(id, value) {
  const el = document.getElementById(id);
  if (el) el.textContent = value;
}

/* actions cell (edit/delete buttons hidden for viewers via .admin-only) */
function actionButtons(id, extra = "") {
  return `<td class="text-nowrap">${extra}
    <button class="btn-icon-sm admin-only" onclick="editRecord('${id}')" title="Edit"><i class="bi bi-pencil"></i></button>
    <button class="btn-icon-sm danger admin-only" onclick="deleteRecord('${id}')" title="Delete"><i class="bi bi-trash"></i></button>
  </td>`;
}

function debounce(fn, ms = 350) {
  let t;
  return (...args) => { clearTimeout(t); t = setTimeout(() => fn(...args), ms); };
}

/* dropdown option helpers */
function fillSelect(selectEl, items, { value = v => v, label = v => v, keep = true } = {}) {
  if (typeof selectEl === "string") selectEl = document.getElementById(selectEl);
  if (!selectEl) return;
  const first = keep && selectEl.options.length ? selectEl.options[0].outerHTML : "";
  selectEl.innerHTML = first + items.map(i =>
    `<option value="${esc(value(i))}">${esc(label(i))}</option>`).join("");
}

async function loadVehicleOptions(...selectIds) {
  try {
    const vehicles = await api("/api/vehicles");
    selectIds.forEach(id => fillSelect(id, vehicles, {
      value: v => v.vehicle_number, label: v => v.vehicle_number
    }));
  } catch (e) { /* dropdowns stay empty */ }
}

async function loadDriverOptions(...selectIds) {
  try {
    const drivers = await api("/api/drivers");
    selectIds.forEach(id => fillSelect(id, drivers.filter(d => (d.status || "Active") === "Active"), {
      value: d => d.driver_name, label: d => `${d.driver_name} (${d.employee_type || "Driver"})`
    }));
  } catch (e) { /* dropdowns stay empty */ }
}

/* ------------------------------------------------------ layout behaviours */
document.addEventListener("DOMContentLoaded", () => {
  // sidebar toggle (collapse on desktop, drawer on mobile)
  const toggle = document.getElementById("sidebarToggle");
  if (toggle) {
    toggle.addEventListener("click", () => {
      if (window.innerWidth <= 991) document.body.classList.toggle("sidebar-open");
      else document.body.classList.toggle("sidebar-collapsed");
    });
  }
  document.addEventListener("click", e => {
    if (window.innerWidth <= 991 && document.body.classList.contains("sidebar-open")
      && !e.target.closest(".sidebar") && !e.target.closest("#sidebarToggle")) {
      document.body.classList.remove("sidebar-open");
    }
  });

  // animate cards into view as they scroll in
  const observer = new IntersectionObserver(entries => {
    entries.forEach(entry => {
      if (entry.isIntersecting) {
        entry.target.classList.add("slide-up");
        observer.unobserve(entry.target);
      }
    });
  }, { threshold: 0.08 });
  document.querySelectorAll(".animate-in").forEach(el => observer.observe(el));

  loadAlerts();
});

/* ------------------------------------------------------------- alert bell */
async function loadAlerts() {
  const menu = document.getElementById("alertMenu");
  const count = document.getElementById("alertCount");
  if (!menu || !count) return;
  try {
    const data = await api("/api/dashboard/alerts");
    count.textContent = data.count;
    count.style.display = data.count ? "grid" : "none";
    const wrap = document.getElementById("bellWrap");
    if (wrap) wrap.classList.toggle("has-alerts", data.count > 0);
    if (!data.alerts.length) {
      menu.innerHTML = `<div class="empty-state py-4"><i class="bi bi-bell-slash"></i>No alerts</div>`;
      return;
    }
    menu.innerHTML = data.alerts.map(a =>
      `<div class="alert-item ${a.level}"><i class="bi ${a.icon || "bi-exclamation-circle"}"></i>
       <span>${esc(a.text)}</span></div>`).join("");
  } catch (e) {
    count.style.display = "none";
    menu.innerHTML = `<div class="empty-state py-4"><i class="bi bi-bell-slash"></i>Alerts unavailable</div>`;
  }
}
