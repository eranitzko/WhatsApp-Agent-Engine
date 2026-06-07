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
  else if (hash === 'people') await renderPeople(app);
  else if (hash === 'blueprints') await renderBlueprints(app);
  else if (hash === 'tools') await renderTools(app);
  else if (hash === 'settings') await renderSettings(app);
  else await renderGroups(app);
}

window.addEventListener('hashchange', route);
window.addEventListener('DOMContentLoaded', route);

// ── Layout shell ──────────────────────────────────────────────────────────────

function layout(page, content) {
  const nav = [
    { hash: 'groups',     icon: '🏠', label: 'Groups' },
    { hash: 'people',     icon: '👥', label: 'People' },
    { hash: 'blueprints', icon: '📋', label: 'Blueprints' },
    { hash: 'tools',      icon: '🔧', label: 'Tools' },
    { hash: 'settings',   icon: '⚙️', label: 'Settings' },
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
          <td><button class="btn btn-danger" onclick="deleteGroup('${escAttr(g.group_jid)}')">Remove</button></td>
        </tr>`).join('')
    : '<tr><td colspan="4" class="empty">No groups registered yet.</td></tr>';

  app.innerHTML = layout('groups', `
    <div class="page-header">
      <h2>Groups</h2>
      <button class="btn btn-primary" onclick="openRegisterModal()">+ Register Group</button>
    </div>
    <div class="table-wrap"><table class="table">
      <thead><tr><th>Group</th><th>Blueprint</th><th></th></tr></thead>
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

// ── People ───────────────────────────────────────────────────────────────────

async function renderPeople(app) {
  app.innerHTML = layout('people', '<p style="color:var(--muted)">Loading...</p>');
  const res = await apiFetch('/people');
  if (!res) return;
  const people = await res.json();

  const rows = people.length
    ? people.map(p => `
        <tr>
          <td>${escHtml(p.phone)}</td>
          <td>
            <input id="pname-${escAttr(p.phone)}" value="${escAttr(p.display_name || '')}"
              placeholder="— add name —"
              onblur="savePersonName('${escAttr(p.phone)}')"
              style="width:120px;background:var(--bg);border:1px solid var(--border);color:var(--text);padding:4px 8px;border-radius:4px;font-size:13px">
          </td>
          <td style="font-size:0.8em;color:var(--muted)">${escHtml(p.group_jid || '—')}</td>
          <td>
            <label style="display:flex;align-items:center;gap:6px;cursor:pointer">
              <input type="checkbox" ${p.is_admin ? 'checked' : ''}
                onchange="togglePersonAdmin('${escAttr(p.phone)}', this.checked)">
              Admin
            </label>
          </td>
          <td>
            <button class="btn btn-danger" onclick="deletePerson('${escAttr(p.phone)}')">Remove</button>
          </td>
        </tr>`).join('')
    : '<tr><td colspan="5" class="empty">No people registered yet.</td></tr>';

  app.innerHTML = layout('people', `
    <div class="page-header"><h2>People</h2></div>
    <div class="table-wrap"><table class="table">
      <thead><tr><th>Phone</th><th>Display Name</th><th>Group JID</th><th>Admin</th><th></th></tr></thead>
      <tbody>${rows}</tbody>
    </table></div>
    <details style="margin-top:20px">
      <summary style="cursor:pointer;font-size:13px;color:var(--accent)">+ Add person</summary>
      <div style="padding:16px 0;display:flex;flex-wrap:wrap;gap:8px;align-items:flex-end">
        <input id="new-person-phone" type="text" placeholder="Phone e.g. 972501234567" style="width:180px;background:var(--bg);border:1px solid var(--border);color:var(--text);padding:8px 10px;border-radius:6px;font-size:13px">
        <input id="new-person-name" type="text" placeholder="Display name (optional)" style="width:160px;background:var(--bg);border:1px solid var(--border);color:var(--text);padding:8px 10px;border-radius:6px;font-size:13px">
        <input id="new-person-jid" type="text" placeholder="Group JID (optional)" style="width:200px;background:var(--bg);border:1px solid var(--border);color:var(--text);padding:8px 10px;border-radius:6px;font-size:13px">
        <label style="display:flex;align-items:center;gap:6px;font-size:13px;cursor:pointer">
          <input type="checkbox" id="new-person-admin"> Admin
        </label>
        <button class="btn btn-primary" onclick="addPerson()">Add</button>
      </div>
    </details>`);
}

async function savePersonName(phone) {
  const input = document.getElementById('pname-' + phone);
  if (!input) return;
  await apiFetch('/people/' + encodeURIComponent(phone), {
    method: 'PUT',
    body: JSON.stringify({ display_name: input.value.trim() }),
  });
}

async function togglePersonAdmin(phone, isAdmin) {
  await apiFetch('/people/' + encodeURIComponent(phone) + '/admin', {
    method: 'PUT',
    body: JSON.stringify({ is_admin: isAdmin }),
  });
}

async function addPerson() {
  const phone = document.getElementById('new-person-phone').value.trim();
  if (!phone) return;
  await apiFetch('/people', {
    method: 'POST',
    body: JSON.stringify({
      phone,
      display_name: document.getElementById('new-person-name').value.trim() || null,
      group_jid: document.getElementById('new-person-jid').value.trim() || null,
      is_admin: document.getElementById('new-person-admin').checked,
    }),
  });
  renderPeople(document.getElementById('app'));
}

async function deletePerson(phone) {
  if (!confirm(`Remove ${phone}? This removes their user account (admin status unchanged).`)) return;
  await apiFetch('/people/' + encodeURIComponent(phone), { method: 'DELETE' });
  renderPeople(document.getElementById('app'));
}

// ── Blueprints (enhanced with tool editor) ────────────────────────────────────

async function renderBlueprints(app) {
  app.innerHTML = layout('blueprints', '<p style="color:var(--muted)">Loading...</p>');
  const [bpRes, toolsRes] = await Promise.all([
    apiFetch('/blueprints'),
    apiFetch('/tools'),
  ]);
  if (!bpRes || !toolsRes) return;
  const blueprints = await bpRes.json();
  const allTools   = await toolsRes.json();

  const rows = blueprints.map((b, i) => {
    const enabledSet = new Set(JSON.parse(b.tools_list || '[]'));
    const chipsHtml = allTools.map(t => {
      const on = enabledSet.has(t.name);
      return `<div class="chip ${on ? 'on' : 'off'}" data-tool="${escAttr(t.name)}" onclick="toggleChip(this)">
        <span class="chip-dot"></span>${escHtml(t.name)}
      </div>`;
    }).join('');

    return `
      <tr>
        <td>
          <strong>${escHtml(b.display_name)}</strong><br>
          <span style="font-size:11px;color:var(--muted)">${escHtml(b.id)}</span>
        </td>
        <td><span class="badge">${b.tools_count} tools</span></td>
        <td>
          <div class="bp-expand-wrap" id="bp-wrap-${i}">
            <div class="bp-prompt bp-collapsed" id="bp-prompt-${i}"
                 onclick="expandPrompt(${i})" title="Click to expand">
              ${escHtml(b.system_prompt_preview)}…
            </div>
            <textarea class="bp-full" id="bp-full-${i}" readonly
              onblur="collapsePrompt(${i})" style="display:none">${escHtml(b.system_prompt)}</textarea>
          </div>
        </td>
        <td style="text-align:right">
          <button class="btn btn-primary" style="font-size:12px;padding:5px 12px"
            onclick="toggleToolEditor(${i})">🔧 Edit tools</button>
        </td>
      </tr>
      <tr class="expand-row" id="tool-editor-${i}" style="display:none">
        <td colspan="4">
          <div class="expand-inner">
            <h4>Enabled tools — ${escHtml(b.id)}</h4>
            <div class="tool-chips" id="chips-${i}">
              ${chipsHtml}
            </div>
            <div class="chip-save-row">
              <button class="btn btn-primary" style="font-size:12px;padding:5px 12px"
                onclick="saveBlueprintTools('${escAttr(b.id)}', ${i})">Save changes</button>
              <span class="hint">Click chips to toggle · Blue = enabled · Grey = disabled</span>
            </div>
          </div>
        </td>
      </tr>`;
  }).join('');

  app.innerHTML = layout('blueprints', `
    <div class="page-header"><h2>Blueprints</h2></div>
    <div class="table-wrap"><table class="table">
      <thead>
        <tr><th>Blueprint</th><th>Tools</th><th>System Prompt</th><th></th></tr>
      </thead>
      <tbody>${rows}</tbody>
    </table></div>`);
}

function toggleChip(el) {
  el.classList.toggle('on');
  el.classList.toggle('off');
}

function toggleToolEditor(i) {
  const row = document.getElementById('tool-editor-' + i);
  row.style.display = row.style.display === 'none' ? '' : 'none';
}

async function saveBlueprintTools(blueprintId, i) {
  const chips = document.querySelectorAll(`#chips-${i} .chip.on`);
  const tools = Array.from(chips).map(c => c.dataset.tool);
  const res = await apiFetch('/blueprints/' + encodeURIComponent(blueprintId) + '/tools', {
    method: 'PATCH',
    body: JSON.stringify({ tools_enabled: tools }),
  });
  if (res && res.ok) {
    renderBlueprints(document.getElementById('app'));
  } else {
    alert('Failed to save tools. Check that all tool names are valid.');
  }
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

// ── Tools Registry ─────────────────────────────────────────────────────────────

let _toolsData = [];
let _toolsSortCol = 0;
let _toolsSortDir = 1;
let _toolsFilter  = 'all';

async function renderTools(app) {
  app.innerHTML = layout('tools', '<p style="color:var(--muted)">Loading...</p>');
  const res = await apiFetch('/tools');
  if (!res) return;
  _toolsData = await res.json();
  _toolsSortCol = 0; _toolsSortDir = 1; _toolsFilter = 'all';

  const cats = [...new Set(_toolsData.map(t => t.category))].sort();
  const tabsHtml = `
    <div class="tab-bar">
      <div class="tab active" onclick="toolsFilterCat('all', this)">All (${_toolsData.length})</div>
      ${cats.map(c => {
        const n = _toolsData.filter(t => t.category === c).length;
        return `<div class="tab" onclick="toolsFilterCat('${escAttr(c)}', this)">${escHtml(c)} (${n})</div>`;
      }).join('')}
    </div>`;

  app.innerHTML = layout('tools', `
    <div class="page-header"><h2>Tools Registry</h2></div>
    <p style="font-size:13px;color:var(--muted);margin:0 0 16px">
      All tools registered in the engine. Toggle global availability or see which blueprints use each.
      Changes take effect immediately — no restart needed.
    </p>
    ${tabsHtml}
    <div id="tools-table-wrap"></div>`);

  _renderToolsTable();
}

function _renderToolsTable() {
  let rows = _toolsData.filter(t => _toolsFilter === 'all' || t.category === _toolsFilter);

  rows.sort((a, b) => {
    let av, bv;
    switch (_toolsSortCol) {
      case 0: av = a.name;                    bv = b.name; break;
      case 1: av = a.category;                bv = b.category; break;
      case 2: av = a.blueprints_using.join(); bv = b.blueprints_using.join(); break;
      case 3: av = a.globally_enabled ? 1 : 0; bv = b.globally_enabled ? 1 : 0; break;
      default: av = a.name; bv = b.name;
    }
    return (av > bv ? 1 : av < bv ? -1 : 0) * _toolsSortDir;
  });

  function thCls(col) {
    if (_toolsSortCol !== col) return 'sortable';
    return 'sortable ' + (_toolsSortDir === 1 ? 'sort-asc' : 'sort-desc');
  }

  const tableHtml = `
    <table class="table">
      <thead><tr>
        <th class="${thCls(0)}" onclick="toolsSort(0)"><span class="sort-icon">Tool name</span></th>
        <th class="${thCls(1)}" onclick="toolsSort(1)"><span class="sort-icon">Category</span></th>
        <th class="${thCls(2)}" onclick="toolsSort(2)"><span class="sort-icon">Used by</span></th>
        <th class="${thCls(3)}" style="text-align:center" onclick="toolsSort(3)"><span class="sort-icon">Enabled</span></th>
        <th></th>
      </tr></thead>
      <tbody>
        ${rows.map(t => `
          <tr style="${t.globally_enabled ? '' : 'opacity:0.55'}">
            <td>
              <strong>${escHtml(t.name)}</strong><br>
              <span style="font-size:11px;color:var(--muted)">${escHtml(t.description)}</span>
            </td>
            <td><span class="badge">${escHtml(t.category)}</span></td>
            <td><div class="group-pills">${t.blueprints_using.map(b =>
              `<span class="group-pill">${escHtml(b)}</span>`).join('')}</div></td>
            <td style="text-align:center">
              <label class="toggle">
                <input type="checkbox" ${t.globally_enabled ? 'checked' : ''}
                  onchange="toggleToolEnabled('${escAttr(t.name)}', this.checked)">
                <span class="slider"></span>
              </label>
            </td>
            <td style="text-align:right">
              <button class="btn btn-danger"
                onclick="removeToolFromAllBlueprints('${escAttr(t.name)}')">Remove</button>
            </td>
          </tr>`).join('')}
      </tbody>
    </table>
    <p style="font-size:11px;color:var(--muted);margin-top:12px">
      ${rows.length} tool${rows.length !== 1 ? 's' : ''} shown${_toolsFilter !== 'all' ? ` in "${_toolsFilter}"` : ''}.
    </p>`;

  document.getElementById('tools-table-wrap').innerHTML = tableHtml;
}

function toolsSort(col) {
  if (_toolsSortCol === col) _toolsSortDir *= -1;
  else { _toolsSortCol = col; _toolsSortDir = 1; }
  _renderToolsTable();
}

function toolsFilterCat(cat, tab) {
  _toolsFilter = cat;
  document.querySelectorAll('.tab-bar .tab').forEach(t => t.classList.remove('active'));
  tab.classList.add('active');
  _renderToolsTable();
}

async function toggleToolEnabled(toolName, enabled) {
  const res = await apiFetch('/tools/' + encodeURIComponent(toolName) + '/enabled', {
    method: 'PATCH',
    body: JSON.stringify({ enabled }),
  });
  if (res && res.ok) {
    const t = _toolsData.find(x => x.name === toolName);
    if (t) t.globally_enabled = enabled;
    _renderToolsTable();
  } else {
    alert('Failed to update tool status.');
    _renderToolsTable();
  }
}

async function removeToolFromAllBlueprints(toolName) {
  if (!confirm(`Remove "${toolName}" from all blueprints? This cannot be undone from the UI.`)) return;
  const res = await apiFetch('/tools/' + encodeURIComponent(toolName) + '/blueprints', {
    method: 'DELETE',
  });
  if (res && res.ok) {
    const data = await res.json();
    const updated = data.blueprints_updated;
    alert(`Removed from ${updated.length} blueprint${updated.length !== 1 ? 's' : ''}: ${updated.join(', ')}`);
    const fresh = await apiFetch('/tools');
    if (fresh) _toolsData = await fresh.json();
    _renderToolsTable();
  } else {
    alert('Failed to remove tool.');
  }
}

// ── Settings page ────────────────────────────────────────────────────────────
async function renderSettings(app) {
  const res = await apiFetch('/settings');
  if (!res) return;
  const settings = await res.json();
  const labels = {
    cross_group_confirmation_timeout_hours: 'Cross-group confirmation timeout (hours)',
    group_registration_timeout_hours: 'Group registration approval timeout (hours)',
  };
  app.innerHTML = layout('settings', `
    <h2>Settings</h2>
    <table class="data-table">
      <tbody>
        ${Object.entries(settings).map(([k, v]) => `
          <tr>
            <td>${labels[k] || k}</td>
            <td>
              <input id="setting-${k}" type="number" min="1" max="168" value="${v}"
                     style="width:80px">
              <button onclick="saveSetting('${k}')">Save</button>
            </td>
          </tr>`).join('')}
      </tbody>
    </table>`);
}

async function saveSetting(key) {
  const value = document.getElementById(`setting-${key}`).value;
  await apiFetch(`/settings/${key}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ value }),
  });
  alert('Saved.');
}

// ── Utils ─────────────────────────────────────────────────────────────────────

function escHtml(s) {
  return String(s ?? '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}
function escAttr(s) { return escHtml(s); }
