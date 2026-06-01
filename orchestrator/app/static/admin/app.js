// WhatsApp Agent Engine — Admin Panel
// Vanilla JS SPA with hash-based routing

const API = '/admin/api';

// ── Auth ──────────────────────────────────────────────────────────────────────

function getToken() { return localStorage.getItem('admin_token'); }
function setToken(t) { localStorage.setItem('admin_token', t); }
function clearToken() { localStorage.removeItem('admin_token'); }

async function apiFetch(path, opts = {}) {
  const token = getToken();
  const headers = { 'Content-Type': 'application/json', ...(opts.headers || {}) };
  if (token) headers['Authorization'] = `Bearer ${token}`;
  const res = await fetch(API + path, { ...opts, headers });
  if (res.status === 401) { clearToken(); route(); return null; }
  return res;
}

// ── Router ────────────────────────────────────────────────────────────────────

async function route() {
  const app = document.getElementById('app');
  const hash = location.hash.replace('#', '') || 'groups';
  if (!getToken()) { renderLogin(app); return; }
  if (hash === 'groups') await renderGroups(app);
  else if (hash === 'admins') await renderAdmins(app);
  else if (hash === 'blueprints') await renderBlueprints(app);
  else await renderGroups(app);
}

window.addEventListener('hashchange', route);
window.addEventListener('DOMContentLoaded', route);

// ── Layout shell ──────────────────────────────────────────────────────────────

function layout(page, content) {
  const nav = [
    { hash: 'groups',     icon: '🏠', label: 'Groups' },
    { hash: 'admins',     icon: '👥', label: 'Admins' },
    { hash: 'blueprints', icon: '📋', label: 'Blueprints' },
  ];
  return `
    <div class="layout">
      <nav class="sidebar">
        <div class="sidebar-title">Admin Panel</div>
        ${nav.map(n => `
          <div class="nav-item ${page === n.hash ? 'active' : ''}" onclick="location.hash='${n.hash}'">
            ${n.icon} ${n.label}
          </div>`).join('')}
        <div style="flex:1"></div>
        <div class="nav-item" onclick="clearToken();route()">🚪 Sign out</div>
      </nav>
      <main class="main">${content}</main>
    </div>
    <nav class="bottom-nav">
      ${nav.map(n => `
        <div class="bottom-nav-item ${page === n.hash ? 'active' : ''}" onclick="location.hash='${n.hash}'">
          <div class="bnav-icon">${n.icon}</div>
          <div class="bnav-label">${n.label}</div>
        </div>`).join('')}
      <div class="bottom-nav-item" onclick="clearToken();route()">
        <div class="bnav-icon">🚪</div>
        <div class="bnav-label">Sign out</div>
      </div>
    </nav>`;
}

// ── Login ─────────────────────────────────────────────────────────────────────

function renderLogin(app) {
  app.innerHTML = `
    <div class="login-wrap">
      <div class="login-box">
        <h1>Admin Panel</h1>
        <p>WhatsApp Agent Engine</p>
        <div class="form-group">
          <label>Password</label>
          <input id="pw" type="password" placeholder="Enter admin password" onkeydown="if(event.key==='Enter')doLogin()">
        </div>
        <button class="btn btn-primary" style="width:100%" onclick="doLogin()">Sign in</button>
        <div id="login-err" class="error"></div>
      </div>
    </div>`;
  document.getElementById('pw').focus();
}

async function doLogin() {
  const pw = document.getElementById('pw').value;
  const res = await fetch(API + '/login', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ password: pw }),
  });
  if (res.ok) {
    const { token } = await res.json();
    setToken(token);
    location.hash = 'groups';
    route();
  } else {
    document.getElementById('login-err').textContent = 'Incorrect password.';
  }
}

// ── Groups ────────────────────────────────────────────────────────────────────

async function renderGroups(app) {
  app.innerHTML = layout('groups', '<p style="color:var(--muted)">Loading...</p>');
  const res = await apiFetch('/groups');
  if (!res) return;
  const groups = await res.json();

  const rows = groups.length
    ? groups.map(g => `
        <tr>
          <td>${escHtml(g.group_name)}<br><span style="font-size:11px;color:var(--muted)">${escHtml(g.group_jid)}</span></td>
          <td><span class="badge">${escHtml(g.blueprint_name)}</span></td>
          <td><span class="badge">${escHtml(g.status)}</span></td>
          <td><button class="btn btn-danger" onclick="deleteGroup('${escAttr(g.group_jid)}')">Remove</button></td>
        </tr>`).join('')
    : '<tr><td colspan="4" class="empty">No groups registered yet.</td></tr>';

  app.innerHTML = layout('groups', `
    <div class="page-header">
      <h2>Groups</h2>
      <button class="btn btn-primary" onclick="openRegisterModal()">+ Register Group</button>
    </div>
    <div class="table-wrap"><table class="table">
      <thead><tr><th>Group</th><th>Blueprint</th><th>Status</th><th></th></tr></thead>
      <tbody>${rows}</tbody>
    </table></div>
    <div id="modal-container"></div>`);
}

async function deleteGroup(jid) {
  if (!confirm(`Remove group ${jid}?`)) return;
  await apiFetch('/groups/' + encodeURIComponent(jid), { method: 'DELETE' });
  renderGroups(document.getElementById('app'));
}

async function openRegisterModal() {
  const [bpRes, grpRes] = await Promise.all([
    apiFetch('/blueprints'),
    apiFetch('/bridge-groups'),
  ]);
  if (!bpRes || !grpRes) return;
  const blueprints = await bpRes.json();
  const bridgeGroups = await grpRes.json();

  const groupOpts = bridgeGroups.length
    ? bridgeGroups.map(g => `<option value="${escAttr(g.jid)}">${escHtml(g.name)}</option>`).join('')
    : '<option value="" disabled>No unregistered groups found</option>';

  const bpOpts = blueprints.map(b =>
    `<option value="${escAttr(b.id)}">${escHtml(b.display_name)}</option>`).join('');

  document.getElementById('modal-container').innerHTML = `
    <div class="modal-overlay" onclick="if(event.target===this)closeModal()">
      <div class="modal">
        <h3>Register Group</h3>
        <p class="subtitle">Select a group the bot is in and assign a blueprint</p>
        <div class="form-group">
          <label>Group</label>
          <select id="modal-group">${groupOpts}</select>
          <div style="font-size:11px;color:var(--muted);margin-top:4px">Only unregistered groups shown</div>
        </div>
        <div class="form-group">
          <label>Blueprint</label>
          <select id="modal-bp">${bpOpts}</select>
        </div>
        <div class="modal-footer">
          <button class="btn" style="background:transparent;color:var(--muted)" onclick="closeModal()">Cancel</button>
          <button class="btn btn-primary" onclick="submitRegisterGroup()">Register</button>
        </div>
      </div>
    </div>`;
}

function closeModal() {
  const c = document.getElementById('modal-container');
  if (c) c.innerHTML = '';
}

async function submitRegisterGroup() {
  const jid = document.getElementById('modal-group').value;
  const bp  = document.getElementById('modal-bp').value;
  if (!jid) return;
  await apiFetch('/groups', { method: 'POST', body: JSON.stringify({ group_jid: jid, blueprint_id: bp }) });
  closeModal();
  renderGroups(document.getElementById('app'));
}

// ── Admins ────────────────────────────────────────────────────────────────────

async function renderAdmins(app) {
  app.innerHTML = layout('admins', '<p style="color:var(--muted)">Loading...</p>');
  const res = await apiFetch('/admins');
  if (!res) return;
  const admins = await res.json();

  const rows = admins.length
    ? admins.map(a => `
        <tr id="admin-row-${escAttr(a.phone_number)}">
          <td>${escHtml(a.phone_number)}</td>
          <td>
            <span id="admin-label-${escAttr(a.phone_number)}" style="cursor:pointer;color:var(--accent)" onclick="editAdminLabel('${escAttr(a.phone_number)}','${escAttr(a.label||'')}')">
              ${escHtml(a.label || '+ add name')}
            </span>
          </td>
          <td><button class="btn btn-danger" onclick="deleteAdmin('${escAttr(a.phone_number)}')">Remove</button></td>
        </tr>`).join('')
    : '<tr><td colspan="3" class="empty">No admins configured.</td></tr>';

  app.innerHTML = layout('admins', `
    <div class="page-header"><h2>Admins</h2></div>
    <div class="table-wrap"><table class="table">
      <thead><tr><th>Phone Number</th><th>Name</th><th></th></tr></thead>
      <tbody>${rows}</tbody>
    </table></div>
    <div class="add-row">
      <input id="new-phone" type="text" placeholder="Phone e.g. 972501234567" onkeydown="if(event.key==='Enter')document.getElementById('new-name').focus()">
      <input id="new-name" type="text" placeholder="Name (optional)" onkeydown="if(event.key==='Enter')addAdmin()">
      <button class="btn btn-primary" onclick="addAdmin()">+ Add Admin</button>
    </div>`);
}

function editAdminLabel(phone, currentLabel) {
  const span = document.getElementById('admin-label-' + phone);
  if (!span) return;
  span.innerHTML = `
    <input id="edit-label-${escAttr(phone)}" type="text" value="${escAttr(currentLabel)}"
      style="width:140px;background:var(--bg);border:1px solid var(--border);color:var(--text);padding:4px 8px;border-radius:4px;font-size:13px"
      onkeydown="if(event.key==='Enter')saveAdminLabel('${escAttr(phone)}');if(event.key==='Escape')renderAdmins(document.getElementById('app'))">
    <button class="btn btn-primary" style="padding:4px 10px;font-size:12px;margin-left:4px" onclick="saveAdminLabel('${escAttr(phone)}')">Save</button>`;
  document.getElementById('edit-label-' + phone).focus();
}

async function saveAdminLabel(phone) {
  const input = document.getElementById('edit-label-' + phone);
  if (!input) return;
  await apiFetch('/admins/' + encodeURIComponent(phone), {
    method: 'PATCH',
    body: JSON.stringify({ label: input.value.trim() || null }),
  });
  renderAdmins(document.getElementById('app'));
}

async function addAdmin() {
  const phone = document.getElementById('new-phone').value.trim();
  const name  = document.getElementById('new-name').value.trim();
  if (!phone) return;
  await apiFetch('/admins', { method: 'POST', body: JSON.stringify({ phone_number: phone, label: name || null }) });
  renderAdmins(document.getElementById('app'));
}

async function deleteAdmin(phone) {
  if (!confirm(`Remove admin ${phone}?`)) return;
  await apiFetch('/admins/' + encodeURIComponent(phone), { method: 'DELETE' });
  renderAdmins(document.getElementById('app'));
}

// ── Blueprints ────────────────────────────────────────────────────────────────

async function renderBlueprints(app) {
  app.innerHTML = layout('blueprints', '<p style="color:var(--muted)">Loading...</p>');
  const res = await apiFetch('/blueprints');
  if (!res) return;
  const blueprints = await res.json();

  const rows = blueprints.map((b, i) => `
    <tr>
      <td><strong>${escHtml(b.display_name)}</strong><br><span style="font-size:11px;color:var(--muted)">${escHtml(b.id)}</span></td>
      <td><span class="badge">${b.tools_count} tools</span></td>
      <td>
        <div class="bp-expand-wrap" id="bp-wrap-${i}">
          <div class="bp-prompt bp-collapsed" id="bp-prompt-${i}"
               onclick="expandPrompt(${i})"
               title="Click to expand">
            ${escHtml(b.system_prompt_preview)}…
          </div>
          <textarea class="bp-full" id="bp-full-${i}"
            readonly
            onblur="collapsePrompt(${i})"
            style="display:none"
          >${escHtml(b.system_prompt)}</textarea>
        </div>
      </td>
    </tr>`).join('');

  app.innerHTML = layout('blueprints', `
    <div class="page-header"><h2>Blueprints</h2></div>
    <div class="table-wrap"><table class="table">
      <thead><tr><th>Blueprint</th><th>Tools</th><th>System Prompt</th></tr></thead>
      <tbody>${rows}</tbody>
    </table></div>`);
}

function expandPrompt(i) {
  document.getElementById('bp-prompt-' + i).style.display = 'none';
  const ta = document.getElementById('bp-full-' + i);
  ta.style.display = 'block';
  ta.focus();
}

function collapsePrompt(i) {
  document.getElementById('bp-full-' + i).style.display = 'none';
  document.getElementById('bp-prompt-' + i).style.display = '';
}

// ── Utils ─────────────────────────────────────────────────────────────────────

function escHtml(s) {
  return String(s ?? '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}
function escAttr(s) { return escHtml(s); }
