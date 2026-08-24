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
  else if (hash === 'households') await renderHouseholds(app);
  else if (hash === 'blueprints') await renderBlueprints(app);
  else if (hash === 'tools') await renderTools(app);
  else if (hash === 'settings') await renderSettings(app);
  else if (hash === 'logs') await renderLogs(app);
  else await renderGroups(app);
}

window.addEventListener('hashchange', route);
window.addEventListener('DOMContentLoaded', route);

// ── Layout shell ──────────────────────────────────────────────────────────────

function layout(page, content) {
  const nav = [
    { hash: 'groups',     icon: '🏠', label: 'Groups' },
    { hash: 'people',     icon: '👥', label: 'People' },
    { hash: 'households', icon: '🏡', label: 'Households' },
    { hash: 'blueprints', icon: '📋', label: 'Blueprints' },
    { hash: 'tools',      icon: '🔧', label: 'Tools' },
    { hash: 'settings',   icon: '⚙️', label: 'Settings' },
    { hash: 'logs',       icon: '📋', label: 'Logs' },
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

let _groupsData = [];

async function renderGroups(app) {
  app.innerHTML = layout('groups', '<p style="color:var(--muted)">Loading...</p>');
  const res = await apiFetch('/groups');
  if (!res) return;
  _groupsData = await res.json();

  const rows = _groupsData.length
    ? _groupsData.map((g, i) => `
        <tr class="group-row" onclick="toggleGroupDetail(${i})" style="cursor:pointer" title="Click to see members">
          <td>
            <span style="font-weight:500">${escHtml(g.group_name)}</span>
            <br><span style="font-size:11px;color:var(--muted)">${escHtml(g.group_jid)}</span>
            ${g.notes ? `<br><span style="font-size:11px;color:var(--muted);font-style:italic">📝 ${escHtml(g.notes.length > 60 ? g.notes.slice(0, 60) + '…' : g.notes)}</span>` : ''}
          </td>
          <td><span class="badge">${escHtml(g.blueprint_name)}</span></td>
          <td style="font-size:13px;color:var(--muted);white-space:nowrap">
            ${g.member_count} member${g.member_count !== 1 ? 's' : ''}
          </td>
          <td>
            <button class="btn btn-danger" onclick="event.stopPropagation();deleteGroup('${escAttr(g.group_jid)}')">Remove</button>
          </td>
        </tr>
        <tr id="group-detail-${i}" style="display:none">
          <td colspan="4" style="padding:0">
            <div style="padding:10px 16px 14px;background:var(--surface);border-top:1px solid var(--border)">
              ${g.member_count > 0
                ? `<div style="font-size:12px;color:var(--muted);margin-bottom:6px">Members (${g.member_count})</div>
                   <div style="display:flex;flex-wrap:wrap;gap:6px">
                     ${g.members.map(m => m.name
                       ? `<div style="background:var(--bg);border:1px solid var(--border);border-radius:6px;padding:4px 10px;font-size:12px">
                            <span style="font-weight:500">${escHtml(m.name)}</span>
                            ${m.phone ? `<span style="color:var(--muted);margin-left:6px;font-size:11px">${escHtml(m.phone)}</span>` : ''}
                          </div>`
                       : `<div style="background:var(--bg);border:1px dashed var(--border);border-radius:6px;padding:4px 10px;font-size:12px;color:var(--muted);cursor:pointer;display:flex;align-items:center;gap:5px"
                              onclick="event.stopPropagation();openAddPersonFromGroup('${escAttr(g.group_jid)}')"
                              title="Click to add this person">
                            <span style="color:var(--accent);font-size:14px;line-height:1">+</span>Unknown member
                          </div>`
                     ).join('')}
                   </div>`
                : '<span style="font-size:12px;color:var(--muted)">No members recorded yet.</span>'}
              <div style="margin-top:12px" onclick="event.stopPropagation()">
                <div style="font-size:12px;color:var(--muted);margin-bottom:6px">Notes</div>
                <textarea id="group-notes-${i}" rows="2" placeholder="What is this group for? (admin reference only — not seen by the bot)"
                  style="width:100%;max-width:420px;background:var(--bg);border:1px solid var(--border);color:var(--text);padding:8px 10px;border-radius:6px;font-size:13px;box-sizing:border-box;font-family:inherit;resize:vertical">${escHtml(g.notes || '')}</textarea>
                <div style="margin-top:6px">
                  <button class="btn" style="padding:4px 10px;font-size:12px" onclick="saveGroupNotes('${escAttr(g.group_jid)}', ${i})">Save notes</button>
                </div>
              </div>
            </div>
          </td>
        </tr>`).join('')
    : '<tr><td colspan="4" class="empty">No groups registered yet.</td></tr>';

  app.innerHTML = layout('groups', `
    <div class="page-header">
      <h2>Groups</h2>
      <button class="btn btn-primary" onclick="openRegisterModal()">+ Register Group</button>
    </div>
    <div class="table-wrap"><table class="table">
      <thead><tr><th>Group</th><th>Blueprint</th><th>Members</th><th></th></tr></thead>
      <tbody>${rows}</tbody>
    </table></div>
    <div id="modal-container"></div>`);
}

function toggleGroupDetail(i) {
  const row = document.getElementById('group-detail-' + i);
  if (!row) return;
  row.style.display = row.style.display === 'none' ? '' : 'none';
}

async function deleteGroup(jid) {
  if (!confirm(`Remove group ${jid}?\n\nThis also removes all participants and automations for this group.`)) return;
  const res = await apiFetch('/groups/' + encodeURIComponent(jid), { method: 'DELETE' });
  if (!res || !res.ok) {
    const body = await res?.json().catch(() => ({}));
    alert('Failed to remove group: ' + (body?.detail || 'Unknown error'));
    return;
  }
  renderGroups(document.getElementById('app'));
}

async function saveGroupNotes(groupJid, i) {
  const notes = document.getElementById('group-notes-' + i).value.trim();
  const res = await apiFetch('/groups/' + encodeURIComponent(groupJid), {
    method: 'PATCH',
    body: JSON.stringify({ notes: notes || null }),
  });
  if (!res || !res.ok) {
    const body = await res?.json().catch(() => ({}));
    alert('Failed to save notes: ' + (body?.detail || 'Unknown error'));
    return;
  }
  renderGroups(document.getElementById('app'));
}

function openAddPersonFromGroup(groupJid) {
  document.getElementById('modal-container').innerHTML = `
    <div class="modal-overlay" onclick="if(event.target===this)closeModal()">
      <div class="modal">
        <h3>Add Person</h3>
        <div style="background:var(--surface);border:1px solid var(--border);border-radius:6px;padding:10px 12px;font-size:12px;color:var(--muted);margin-bottom:16px;line-height:1.5">
          ⚠️ WhatsApp no longer exposes phone numbers for group members — it uses
          opaque internal IDs instead. Enter the person's actual phone number below.
        </div>
        <div class="form-group">
          <label>Phone number</label>
          <input id="ap-phone" type="text" placeholder="e.g. 972501234567">
        </div>
        <div class="form-group">
          <label>Display name (optional)</label>
          <input id="ap-name" type="text" placeholder="e.g. Sivan">
        </div>
        <div class="form-group" style="display:flex;align-items:center;gap:8px">
          <input type="checkbox" id="ap-admin">
          <label for="ap-admin" style="cursor:pointer;margin:0">System admin</label>
        </div>
        <div class="modal-footer">
          <button class="btn" style="background:transparent;color:var(--muted)" onclick="closeModal()">Cancel</button>
          <button class="btn btn-primary" onclick="submitAddPersonFromGroup('${escAttr(groupJid)}')">Add person</button>
        </div>
      </div>
    </div>`;
  document.getElementById('ap-phone').focus();
}

async function submitAddPersonFromGroup(groupJid) {
  const phone = document.getElementById('ap-phone').value.trim();
  if (!phone) { alert('Phone number is required.'); return; }
  const name  = document.getElementById('ap-name').value.trim();
  const isAdmin = document.getElementById('ap-admin').checked;
  const res = await apiFetch('/people', {
    method: 'POST',
    body: JSON.stringify({
      phone,
      display_name: name || null,
      group_jid: groupJid,
      is_admin: isAdmin,
    }),
  });
  if (!res || !res.ok) {
    const body = await res?.json().catch(() => ({}));
    alert('Failed to add person: ' + (body?.detail || 'Unknown error'));
    return;
  }
  closeModal();
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

let _unregisteredParticipants = [];

async function renderPeople(app) {
  app.innerHTML = layout('people', '<p style="color:var(--muted)">Loading...</p>');
  const [peopleRes, pendingRes, unregRes] = await Promise.all([
    apiFetch('/people'),
    apiFetch('/people/pending'),
    apiFetch('/people/unregistered-participants'),
  ]);
  if (!peopleRes) return;
  const people = await peopleRes.json();
  const pending = pendingRes ? await pendingRes.json() : [];
  _unregisteredParticipants = unregRes ? await unregRes.json() : [];

  const peopleRows = people.length
    ? people.map(p => `
        <tr>
          <td>${escHtml(p.phone)}</td>
          <td>${escHtml(p.display_name || '—')}</td>
          <td style="font-size:0.8em;color:var(--muted)">${escHtml(p.group_jid || '—')}</td>
          <td>${p.is_admin ? '<span class="badge" style="background:#eff6ff;color:var(--accent)">Admin</span>' : ''}</td>
          <td style="white-space:nowrap">
            <button class="btn" style="padding:4px 8px;font-size:11px;border:1px solid var(--border)"
              onclick="openPersonEdit(${escAttr(JSON.stringify(JSON.stringify(p)))})">Edit</button>
            <button class="btn btn-danger" style="padding:4px 8px;font-size:11px;margin-left:3px" onclick="deletePerson('${escAttr(p.phone)}')">✕</button>
          </td>
        </tr>`).join('')
    : '<tr><td colspan="5" class="empty">No people registered yet.</td></tr>';

  const pendingSection = pending.length ? `
    <h3 style="margin:28px 0 12px;font-size:15px">⏳ Pending Registrations</h3>
    <div class="table-wrap"><table class="table">
      <thead><tr><th>Group JID</th><th>Members</th><th>Type</th><th></th></tr></thead>
      <tbody>
        ${pending.map(g => `
          <tr>
            <td style="font-size:0.8em">${escHtml(g.group_jid)}</td>
            <td style="font-size:0.85em">${escHtml(g.human_phones.join(', ') || '—')}</td>
            <td>
              <select id="type-${escAttr(g.group_jid)}" style="background:var(--bg);border:1px solid var(--border);color:var(--text);padding:4px 8px;border-radius:4px;font-size:12px">
                <option value="personal" ${g.candidate_type === 'personal' ? 'selected' : ''}>Personal</option>
                <option value="shared" ${g.candidate_type === 'shared' ? 'selected' : ''}>Shared</option>
                <option value="sys_admin">Sys Admin</option>
              </select>
            </td>
            <td>
              <div style="white-space:nowrap">
                <button class="btn btn-primary" style="padding:4px 8px;font-size:11px" onclick="approveRegistration('${escAttr(g.group_jid)}')">Approve</button>
                <button class="btn btn-danger" style="padding:4px 8px;font-size:11px;margin-left:3px" onclick="rejectRegistration('${escAttr(g.group_jid)}')">Reject</button>
              </div>
            </td>
          </tr>`).join('')}
      </tbody>
    </table></div>` : '';

  app.innerHTML = layout('people', `
    <div class="page-header"><h2>People</h2></div>
    ${pendingSection}
    <h3 style="margin:${pending.length ? '28px' : '0'} 0 12px;font-size:15px">Registered</h3>
    <div class="table-wrap"><table class="table">
      <thead><tr><th>Phone</th><th>Display Name</th><th>Group JID</th><th></th><th></th></tr></thead>
      <tbody>${peopleRows}</tbody>
    </table></div>
    <details style="margin-top:20px">
      <summary style="cursor:pointer;font-size:13px;color:var(--accent)">+ Add person</summary>
      <div style="padding:16px 0;display:flex;flex-wrap:wrap;gap:8px;align-items:flex-start">
        <div style="position:relative;width:180px">
          <input id="new-person-phone" type="text" placeholder="Phone e.g. 972501234567" autocomplete="off"
            onfocus="onNewPersonFieldFocus('new-person-phone')"
            oninput="onNewPersonFieldFocus('new-person-phone')"
            onblur="hideNewPersonSuggestionsSoon()"
            style="width:100%;background:var(--bg);border:1px solid var(--border);color:var(--text);padding:8px 10px;border-radius:6px;font-size:13px;box-sizing:border-box">
          <div id="new-person-phone-suggestions" style="display:none;position:absolute;top:100%;left:0;right:0;z-index:10;background:var(--bg);border:1px solid var(--border);border-radius:6px;margin-top:2px;max-height:240px;overflow-y:auto;box-shadow:0 4px 12px rgba(0,0,0,0.15)"></div>
        </div>
        <div style="position:relative;width:160px">
          <input id="new-person-name" type="text" placeholder="Display name (optional)" autocomplete="off"
            onfocus="onNewPersonFieldFocus('new-person-name')"
            oninput="onNewPersonFieldFocus('new-person-name')"
            onblur="hideNewPersonSuggestionsSoon()"
            style="width:100%;background:var(--bg);border:1px solid var(--border);color:var(--text);padding:8px 10px;border-radius:6px;font-size:13px;box-sizing:border-box">
          <div id="new-person-name-suggestions" style="display:none;position:absolute;top:100%;left:0;right:0;z-index:10;background:var(--bg);border:1px solid var(--border);border-radius:6px;margin-top:2px;max-height:240px;overflow-y:auto;box-shadow:0 4px 12px rgba(0,0,0,0.15)"></div>
        </div>
        <input id="new-person-jid" type="text" placeholder="Group JID (optional)" style="width:200px;background:var(--bg);border:1px solid var(--border);color:var(--text);padding:8px 10px;border-radius:6px;font-size:13px">
        <label style="display:flex;align-items:center;gap:6px;font-size:13px;cursor:pointer;padding:8px 0">
          <input type="checkbox" id="new-person-admin"> Admin
        </label>
        <button class="btn btn-primary" onclick="addPerson()">Add</button>
      </div>
      <p style="font-size:11px;color:var(--muted);margin:0 0 12px">
        Click the phone or name field to see people already seen in a registered group who aren't in this list yet.
      </p>
    </details>
    <div id="person-modal-wrap"></div>`);
}

function openPersonEdit(personJson) {
  const p = JSON.parse(personJson);
  const groups = p.accounting_groups || [];
  const primaryJid = p.primary_accounting_group_jid || '';

  const acctGroupSection = groups.length > 0 ? `
    <div class="form-group">
      <label>Primary Accounting Group</label>
      <select id="edit-primary-acct-group" style="width:100%;background:var(--bg);border:1px solid var(--border);color:var(--text);padding:8px 10px;border-radius:6px;font-size:13px">
        ${groups.map(g => `<option value="${escAttr(g.group_jid)}" ${g.group_jid === primaryJid ? 'selected' : ''}>${escHtml(g.group_jid)}${g.is_primary ? ' ★' : ''}</option>`).join('')}
      </select>
      <p style="font-size:11px;color:var(--muted);margin:4px 0 0">Bot-initiated accounting messages land here. ★ = current primary.</p>
    </div>` : '';

  document.getElementById('person-modal-wrap').innerHTML = `
    <div class="modal-overlay" onclick="if(event.target===this)closePersonModal()">
      <div class="modal">
        <h3>Edit Person</h3>
        <p class="subtitle">${escHtml(p.phone)}</p>
        <div class="form-group">
          <label>Display Name</label>
          <input id="edit-display-name" type="text" value="${escAttr(p.display_name || '')}">
        </div>
        <div class="form-group">
          <label>Email</label>
          <input id="edit-email" type="email" value="${escAttr(p.email || '')}">
        </div>
        <div class="form-group">
          <label>Admin</label>
          <label style="display:flex;align-items:center;gap:8px;cursor:pointer">
            <input type="checkbox" id="edit-is-admin" ${p.is_admin ? 'checked' : ''}>
            <span>System admin</span>
          </label>
        </div>
        <div class="form-group">
          <label>Admin Label</label>
          <input id="edit-admin-label" type="text" value="${escAttr(p.admin_label || '')}" placeholder="e.g. owner, family">
        </div>
        ${acctGroupSection}
        <div class="modal-footer">
          <button class="btn" onclick="closePersonModal()">Cancel</button>
          <button class="btn btn-primary" onclick="savePersonEdit('${escAttr(p.phone)}')">Save</button>
        </div>
      </div>
    </div>`;
}

function closePersonModal() {
  const wrap = document.getElementById('person-modal-wrap');
  if (wrap) wrap.innerHTML = '';
}

async function savePersonEdit(phone) {
  const display_name = document.getElementById('edit-display-name').value.trim();
  const email = document.getElementById('edit-email').value.trim();
  const is_admin = document.getElementById('edit-is-admin').checked;
  const admin_label = document.getElementById('edit-admin-label').value.trim();
  const primarySel = document.getElementById('edit-primary-acct-group');
  const primary_accounting_group_jid = primarySel ? primarySel.value : undefined;
  await apiFetch('/people/' + encodeURIComponent(phone), {
    method: 'PATCH',
    body: JSON.stringify({
      display_name: display_name || null,
      email: email || null,
      is_admin,
      admin_label: admin_label || null,
      ...(primary_accounting_group_jid !== undefined ? { primary_accounting_group_jid } : {}),
    }),
  });
  closePersonModal();
  renderPeople(document.getElementById('app'));
}

async function approveRegistration(groupJid) {
  const type = document.getElementById('type-' + groupJid)?.value || 'personal';
  await apiFetch('/people/pending/' + encodeURIComponent(groupJid) + '/approve', {
    method: 'POST',
    body: JSON.stringify({ group_type: type }),
  });
  renderPeople(document.getElementById('app'));
}

async function rejectRegistration(groupJid) {
  if (!confirm('Reject and remove this group?')) return;
  await apiFetch('/people/pending/' + encodeURIComponent(groupJid) + '/reject', { method: 'POST' });
  renderPeople(document.getElementById('app'));
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

// Shared "add person" suggestions — surfaces people already seen in a
// registered group (GroupParticipant) who never made it into People,
// e.g. anyone who joined before register_group's people-sync fix. Fires
// on focus too (not just typing) so clicking either field shows the full
// list immediately, per how this was asked for.
//
// _newPersonHideTimer tracks the one pending hideNewPersonSuggestionsSoon
// timeout, if any. Without this, clicking a field, clicking away, then
// clicking back in fast (each blur/focus cycle stacks another independent
// setTimeout) left an earlier blur's hide firing ~150ms AFTER the dropdown
// had already been legitimately reopened — the box would flash open then
// vanish on its own a moment later. Every focus/input now cancels any
// pending hide, and every blur replaces (not stacks) the pending one.
let _newPersonHideTimer = null;

function onNewPersonFieldFocus(sourceInputId) {
  if (_newPersonHideTimer) { clearTimeout(_newPersonHideTimer); _newPersonHideTimer = null; }
  const isPhoneField = sourceInputId === 'new-person-phone';
  const input = document.getElementById(sourceInputId);
  const box = document.getElementById(sourceInputId + '-suggestions');
  const q = input.value.trim().toLowerCase();

  const matches = _unregisteredParticipants.filter(p =>
    !q ||
    (p.phone && p.phone.toLowerCase().includes(q)) ||
    (p.name && p.name.toLowerCase().includes(q))
  ).slice(0, 8);

  if (!matches.length) { box.style.display = 'none'; box.innerHTML = ''; return; }

  box.innerHTML = matches.map(p => `
    <div style="padding:8px 10px;cursor:pointer;font-size:13px;border-bottom:1px solid var(--border)"
      onmousedown="event.preventDefault();selectUnregisteredParticipant('${escAttr(p.phone)}')">
      <strong>${escHtml(p.name || p.phone)}</strong>
      ${p.name ? `<span style="color:var(--muted)"> — ${escHtml(p.phone)}</span>` : ''}
      <div style="font-size:11px;color:var(--muted)">${escHtml(p.group_jid)}</div>
    </div>`).join('');
  box.style.display = 'block';
}

function hideNewPersonSuggestionsSoon() {
  if (_newPersonHideTimer) { clearTimeout(_newPersonHideTimer); }
  _newPersonHideTimer = setTimeout(() => {
    for (const id of ['new-person-phone-suggestions', 'new-person-name-suggestions']) {
      const box = document.getElementById(id);
      if (box) { box.style.display = 'none'; }
    }
    _newPersonHideTimer = null;
  }, 150);
}

function selectUnregisteredParticipant(phone) {
  if (_newPersonHideTimer) { clearTimeout(_newPersonHideTimer); _newPersonHideTimer = null; }
  const person = _unregisteredParticipants.find(p => p.phone === phone);
  if (!person) return;
  document.getElementById('new-person-phone').value = person.phone;
  document.getElementById('new-person-name').value = person.name || '';
  document.getElementById('new-person-jid').value = person.group_jid || '';
  for (const id of ['new-person-phone-suggestions', 'new-person-name-suggestions']) {
    const box = document.getElementById(id);
    if (box) { box.style.display = 'none'; box.innerHTML = ''; }
  }
}

async function addPerson() {
  const phone = document.getElementById('new-person-phone').value.trim();
  if (!phone) { alert('Phone number is required.'); return; }
  const jid = document.getElementById('new-person-jid').value.trim();
  const isAdmin = document.getElementById('new-person-admin').checked;
  if (!jid && !isAdmin) {
    alert('Please provide a Group JID or check "Admin" — a person must have at least one.');
    return;
  }
  const res = await apiFetch('/people', {
    method: 'POST',
    body: JSON.stringify({
      phone,
      display_name: document.getElementById('new-person-name').value.trim() || null,
      group_jid: jid || null,
      is_admin: isAdmin,
    }),
  });
  if (!res || !res.ok) {
    const body = await res?.json().catch(() => ({}));
    alert('Failed to add person: ' + (body?.detail || 'Unknown error'));
    return;
  }
  renderPeople(document.getElementById('app'));
}

async function deletePerson(phone) {
  if (!confirm(`Remove ${phone}? This removes their user account (admin status unchanged).`)) return;
  await apiFetch('/people/' + encodeURIComponent(phone), { method: 'DELETE' });
  renderPeople(document.getElementById('app'));
}

// ── Households ────────────────────────────────────────────────────────────────

// Registered people, cached per-render for the "add member" search box —
// keyed by household id so each card's picked group_jid is tracked
// independently (cleared whenever that card's search input is hand-edited
// after a pick, so a stale jid never gets submitted for an edited phone).
let _peopleForHouseholds = [];
const _pickedMemberGroupJid = {};

async function renderHouseholds(app) {
  app.innerHTML = layout('households', '<p style="color:var(--muted)">Loading...</p>');
  const [hhRes, peopleRes] = await Promise.all([
    apiFetch('/households'),
    apiFetch('/people'),
  ]);
  if (!hhRes) return;
  const households = await hhRes.json();
  _peopleForHouseholds = peopleRes ? await peopleRes.json() : [];

  const cardsHtml = households.length
    ? households.map(h => `
        <div class="table-wrap" style="margin-bottom:20px">
          <div style="display:flex;justify-content:space-between;align-items:center;padding:10px 14px">
            <strong>${escHtml(h.name)}</strong>
            <button class="btn btn-danger" style="padding:4px 8px;font-size:11px" onclick="deleteHousehold('${escAttr(h.id)}','${escAttr(h.name)}')">Delete household</button>
          </div>
          <table class="table">
            <thead><tr><th>Phone</th><th>Display Name</th><th>Linked</th><th></th></tr></thead>
            <tbody>
              ${h.members.length ? h.members.map(m => `
                <tr>
                  <td>${escHtml(m.phone)}</td>
                  <td>${escHtml(m.display_name || '—')}</td>
                  <td>${m.linked ? '✅' : '—'}</td>
                  <td style="white-space:nowrap">
                    <button class="btn" style="padding:4px 8px;font-size:11px;border:1px solid var(--border)"
                      onclick="openMemberEdit(${escAttr(JSON.stringify(JSON.stringify({ household_id: h.id, ...m })))})">Edit</button>
                    <button class="btn btn-danger" style="padding:4px 8px;font-size:11px;margin-left:3px" onclick="removeHouseholdMember('${escAttr(h.id)}','${escAttr(m.phone)}')">✕</button>
                  </td>
                </tr>`).join('') : '<tr><td colspan="4" class="empty">No members yet.</td></tr>'}
            </tbody>
          </table>
          <details style="padding:12px 14px">
            <summary style="cursor:pointer;font-size:13px;color:var(--accent)">+ Add member</summary>
            <div style="padding:12px 0;display:flex;flex-wrap:wrap;gap:8px;align-items:flex-start">
              <div style="position:relative;width:220px">
                <input id="new-member-search-${escAttr(h.id)}" type="text" placeholder="Search by phone or name…" autocomplete="off"
                  oninput="onMemberSearchInput('${escAttr(h.id)}')"
                  onblur="hideMemberSuggestionsSoon('${escAttr(h.id)}')"
                  style="width:100%;background:var(--bg);border:1px solid var(--border);color:var(--text);padding:8px 10px;border-radius:6px;font-size:13px;box-sizing:border-box">
                <div id="new-member-suggestions-${escAttr(h.id)}"
                  style="display:none;position:absolute;top:100%;left:0;right:0;z-index:10;background:var(--bg);border:1px solid var(--border);border-radius:6px;margin-top:2px;max-height:220px;overflow-y:auto;box-shadow:0 4px 12px rgba(0,0,0,0.15)"></div>
              </div>
              <div style="position:relative;width:160px">
                <input id="new-member-name-${escAttr(h.id)}" type="text" placeholder="Display name (optional)" autocomplete="off"
                  oninput="onMemberSearchInput('${escAttr(h.id)}', 'new-member-name-${escAttr(h.id)}')"
                  onblur="hideMemberSuggestionsSoon('${escAttr(h.id)}')"
                  style="width:100%;background:var(--bg);border:1px solid var(--border);color:var(--text);padding:8px 10px;border-radius:6px;font-size:13px;box-sizing:border-box">
                <div id="new-member-name-suggestions-${escAttr(h.id)}"
                  style="display:none;position:absolute;top:100%;left:0;right:0;z-index:10;background:var(--bg);border:1px solid var(--border);border-radius:6px;margin-top:2px;max-height:220px;overflow-y:auto;box-shadow:0 4px 12px rgba(0,0,0,0.15)"></div>
              </div>
              <button class="btn btn-primary" onclick="addHouseholdMember('${escAttr(h.id)}')">Add</button>
            </div>
            <p style="font-size:11px;color:var(--muted);margin:6px 0 0">
              Pick a registered person from the dropdown, or type a new phone number for someone not registered yet.
            </p>
          </details>
        </div>`).join('')
    : '<p class="empty">No households yet.</p>';

  app.innerHTML = layout('households', `
    <div class="page-header"><h2>Households</h2></div>
    ${cardsHtml}
    <details style="margin-top:20px">
      <summary style="cursor:pointer;font-size:13px;color:var(--accent)">+ Add household</summary>
      <div style="padding:16px 0;display:flex;flex-wrap:wrap;gap:8px;align-items:flex-end">
        <input id="new-household-name" type="text" placeholder="Household name" style="width:240px;background:var(--bg);border:1px solid var(--border);color:var(--text);padding:8px 10px;border-radius:6px;font-size:13px">
        <button class="btn btn-primary" onclick="addHousehold()">Add</button>
      </div>
    </details>
    <div id="household-modal-wrap"></div>`);
}

// Both the phone/search box and the Display Name box trigger the same
// live search against registered people — someone might think of a
// household member by phone, by their registered name, or by a nickname
// close to it, and shouldn't have to know which field "does the search."
// sourceInputId defaults to the phone/search field for its own oninput.
//
// _memberHideTimers (keyed by householdId, since several household cards
// can be open at once) tracks each one's pending hideMemberSuggestionsSoon
// timeout. Same fix as the People-tab search: without cancelling on the
// next input/selection, an earlier blur's hide could fire after the
// dropdown had already legitimately reopened, flashing it shut again.
const _memberHideTimers = {};

function onMemberSearchInput(householdId, sourceInputId) {
  if (_memberHideTimers[householdId]) { clearTimeout(_memberHideTimers[householdId]); _memberHideTimers[householdId] = null; }
  sourceInputId = sourceInputId || ('new-member-search-' + householdId);
  const isNameField = sourceInputId.startsWith('new-member-name-');
  _pickedMemberGroupJid[householdId] = null;
  const input = document.getElementById(sourceInputId);
  const box = document.getElementById((isNameField ? 'new-member-name-suggestions-' : 'new-member-suggestions-') + householdId);
  const q = input.value.trim().toLowerCase();
  if (!q) { box.style.display = 'none'; box.innerHTML = ''; return; }

  const matches = _peopleForHouseholds.filter(p =>
    (p.phone && p.phone.toLowerCase().includes(q)) ||
    (p.display_name && p.display_name.toLowerCase().includes(q))
  ).slice(0, 8);

  if (!matches.length) { box.style.display = 'none'; box.innerHTML = ''; return; }

  box.innerHTML = matches.map(p => `
    <div style="padding:8px 10px;cursor:pointer;font-size:13px;border-bottom:1px solid var(--border)"
      onmousedown="event.preventDefault();selectMemberSuggestion('${escAttr(householdId)}','${escAttr(p.phone)}')">
      <strong>${escHtml(p.display_name || p.phone)}</strong>
      ${p.display_name ? `<span style="color:var(--muted)"> — ${escHtml(p.phone)}</span>` : ''}
    </div>`).join('');
  box.style.display = 'block';
}

function hideMemberSuggestionsSoon(householdId) {
  // Delayed so a suggestion's own mousedown (which already preventDefault()s
  // to keep focus) has time to fire its click before this hides the list.
  if (_memberHideTimers[householdId]) { clearTimeout(_memberHideTimers[householdId]); }
  _memberHideTimers[householdId] = setTimeout(() => {
    const box1 = document.getElementById('new-member-suggestions-' + householdId);
    const box2 = document.getElementById('new-member-name-suggestions-' + householdId);
    if (box1) { box1.style.display = 'none'; }
    if (box2) { box2.style.display = 'none'; }
    _memberHideTimers[householdId] = null;
  }, 150);
}

function selectMemberSuggestion(householdId, phone) {
  if (_memberHideTimers[householdId]) { clearTimeout(_memberHideTimers[householdId]); _memberHideTimers[householdId] = null; }
  const person = _peopleForHouseholds.find(p => p.phone === phone);
  if (!person) return;
  document.getElementById('new-member-search-' + householdId).value = person.phone;
  document.getElementById('new-member-name-' + householdId).value = person.display_name || '';
  _pickedMemberGroupJid[householdId] = person.group_jid || person.primary_accounting_group_jid || null;
  for (const idPrefix of ['new-member-suggestions-', 'new-member-name-suggestions-']) {
    const box = document.getElementById(idPrefix + householdId);
    if (box) { box.style.display = 'none'; box.innerHTML = ''; }
  }
}

async function addHousehold() {
  const nameInput = document.getElementById('new-household-name');
  const name = nameInput.value.trim();
  if (!name) { alert('Household name is required.'); return; }
  const res = await apiFetch('/households', { method: 'POST', body: JSON.stringify({ name }) });
  if (!res || !res.ok) {
    const body = await res?.json().catch(() => ({}));
    alert('Failed to add household: ' + (body?.detail || 'Unknown error'));
    return;
  }
  renderHouseholds(document.getElementById('app'));
}

async function deleteHousehold(id, name) {
  if (!confirm(`Delete household "${name}"? This removes all its members too.`)) return;
  const res = await apiFetch('/households/' + encodeURIComponent(id), { method: 'DELETE' });
  if (!res || !res.ok) {
    const body = await res?.json().catch(() => ({}));
    alert('Failed to delete household: ' + (body?.detail || 'Unknown error'));
    return;
  }
  renderHouseholds(document.getElementById('app'));
}

async function addHouseholdMember(householdId) {
  const phone = document.getElementById('new-member-search-' + householdId).value.trim();
  if (!phone) { alert('Phone number is required — search and pick a person, or type a new phone number.'); return; }
  const display_name = document.getElementById('new-member-name-' + householdId).value.trim();
  // Only trust the picked group_jid if the search box still holds that exact
  // phone — onMemberSearchInput already clears this on any further typing,
  // but this is a second guard against a stale pick slipping through.
  const picked = _pickedMemberGroupJid[householdId];
  const person = picked ? _peopleForHouseholds.find(p => p.phone === phone) : null;
  const private_group_jid = person ? picked : null;
  const res = await apiFetch('/households/' + encodeURIComponent(householdId) + '/members', {
    method: 'POST',
    body: JSON.stringify({
      phone,
      display_name: display_name || null,
      private_group_jid: private_group_jid || null,
    }),
  });
  if (!res || !res.ok) {
    const body = await res?.json().catch(() => ({}));
    alert('Failed to add member: ' + (body?.detail || 'Unknown error'));
    return;
  }
  delete _pickedMemberGroupJid[householdId];
  renderHouseholds(document.getElementById('app'));
}

async function removeHouseholdMember(householdId, phone) {
  if (!confirm(`Remove ${phone} from this household?`)) return;
  const res = await apiFetch('/households/' + encodeURIComponent(householdId) + '/members/' + encodeURIComponent(phone), {
    method: 'DELETE',
  });
  if (!res || !res.ok) {
    const body = await res?.json().catch(() => ({}));
    alert('Failed to remove member: ' + (body?.detail || 'Unknown error'));
    return;
  }
  renderHouseholds(document.getElementById('app'));
}

function openMemberEdit(memberJson) {
  const m = JSON.parse(memberJson);
  document.getElementById('household-modal-wrap').innerHTML = `
    <div class="modal-overlay" onclick="if(event.target===this)closeMemberModal()">
      <div class="modal">
        <h3>Edit Member</h3>
        <p class="subtitle">${escHtml(m.phone)}</p>
        <div class="form-group">
          <label>Display Name</label>
          <input id="edit-member-name" type="text" value="${escAttr(m.display_name || '')}">
        </div>
        <div class="modal-footer">
          <button class="btn" onclick="closeMemberModal()">Cancel</button>
          <button class="btn btn-primary" onclick="saveMemberEdit('${escAttr(m.household_id)}','${escAttr(m.phone)}')">Save</button>
        </div>
      </div>
    </div>`;
}

function closeMemberModal() {
  const wrap = document.getElementById('household-modal-wrap');
  if (wrap) wrap.innerHTML = '';
}

async function saveMemberEdit(householdId, phone) {
  const display_name = document.getElementById('edit-member-name').value.trim();
  // private_group_jid is deliberately not editable here — it's an internal
  // linkage set automatically when a person registers, not something an
  // admin should hand-edit. Omitting it from the body leaves it untouched.
  const res = await apiFetch('/households/' + encodeURIComponent(householdId) + '/members/' + encodeURIComponent(phone), {
    method: 'PATCH',
    body: JSON.stringify({
      display_name: display_name || null,
    }),
  });
  if (!res || !res.ok) {
    const body = await res?.json().catch(() => ({}));
    alert('Failed to save member: ' + (body?.detail || 'Unknown error'));
    return;
  }
  closeMemberModal();
  renderHouseholds(document.getElementById('app'));
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
    </table>
    <div id="allowlist-section" style="margin-top:32px"></div>`);
  await renderAllowlistSection();
}

async function renderAllowlistSection() {
  const section = document.getElementById('allowlist-section');
  if (!section) return;

  const res = await apiFetch('/settings/email-allowlist');
  if (!res) return;
  const entries = await res.json();

  const rows = entries.length === 0
    ? `<tr><td colspan="3" class="empty">No addresses — all recipients are permitted.</td></tr>`
    : entries.map(e => `
        <tr>
          <td>${escHtml(e.display_name || '—')}</td>
          <td>${escHtml(e.email)}</td>
          <td style="white-space:nowrap">
            <button class="btn btn-danger" onclick="removeAllowlistEntry('${escAttr(e.email)}')">✕</button>
          </td>
        </tr>`).join('');

  section.innerHTML = `
    <h3 style="margin:0 0 12px;font-size:15px">Email Allowlist</h3>
    <div style="background:var(--surface);border:1px solid var(--border);border-radius:8px;overflow:hidden">
      <table class="table">
        <thead>
          <tr>
            <th>Display Name</th>
            <th>Email</th>
            <th></th>
          </tr>
        </thead>
        <tbody>${rows}</tbody>
      </table>
      <div class="add-row" style="padding:12px;border-top:1px solid var(--border)">
        <input id="al-name" type="text" placeholder="Display name (optional)" style="flex:1;min-width:0">
        <input id="al-email" type="email" placeholder="Email address" style="flex:1.5;min-width:0">
        <button class="btn btn-primary" onclick="addAllowlistEntry()">Add</button>
      </div>
    </div>`;
}

async function addAllowlistEntry() {
  const email = document.getElementById('al-email').value.trim();
  const display_name = document.getElementById('al-name').value.trim() || null;
  if (!email) return;
  const res = await apiFetch('/settings/email-allowlist', {
    method: 'POST',
    body: JSON.stringify({ email, display_name }),
  });
  if (!res) return;
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    alert(body.detail || 'Failed to add entry.');
    return;
  }
  document.getElementById('al-email').value = '';
  document.getElementById('al-name').value = '';
  await renderAllowlistSection();
}

async function removeAllowlistEntry(email) {
  if (!confirm(`Remove ${email} from the allowlist?`)) return;
  const res = await apiFetch('/settings/email-allowlist/' + encodeURIComponent(email), { method: 'DELETE' });
  if (!res || !res.ok) return;
  await renderAllowlistSection();
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

// ── Logs page ────────────────────────────────────────────────────────────────

async function renderLogs(app) {
  app.innerHTML = layout('logs', '<p style="color:var(--muted)">Loading...</p>');
  const res = await apiFetch('/logs?limit=100');
  if (!res) return;
  const logs = await res.json();

  const stopColor = s => ({
    end_turn: 'color:#16a34a', tool_use: 'color:var(--accent)',
    max_tokens: 'color:#d97706', max_tool_turns: 'color:#dc2626',
  }[s] || 'color:var(--muted)');

  const rows = logs.length
    ? logs.map(l => `
        <tr>
          <td style="font-size:0.75em;color:var(--muted);white-space:nowrap">${l.created_at ? l.created_at.slice(0,19).replace('T',' ') : ''}</td>
          <td style="font-size:0.8em">${escHtml((l.group_jid || '').slice(0,20))}</td>
          <td><span class="badge">${escHtml(l.blueprint_id || '')}</span></td>
          <td style="${stopColor(l.stop_reason)};font-size:0.85em;font-weight:500">${escHtml(l.stop_reason || '—')}</td>
          <td style="font-size:0.8em">${l.history_pairs}p / ${l.tool_count}t</td>
          <td style="font-size:0.8em">${(l.tool_calls_made || []).map(t => escHtml(t.name)).join(', ') || '—'}</td>
          <td style="font-size:0.8em;color:var(--muted)">${l.duration_ms != null ? l.duration_ms + 'ms' : '—'}</td>
          <td style="color:#dc2626;font-size:0.75em">${l.error ? '⚠ ' + escHtml(l.error.slice(0,60)) : ''}</td>
        </tr>`).join('')
    : '<tr><td colspan="8" class="empty">No logs yet.</td></tr>';

  app.innerHTML = layout('logs', `
    <div class="page-header"><h2>Request Logs</h2></div>
    <div class="table-wrap"><table class="table">
      <thead><tr>
        <th>Time</th><th>Group</th><th>Blueprint</th><th>Stop</th>
        <th>Ctx/Tools</th><th>Tools Called</th><th>Duration</th><th>Error</th>
      </tr></thead>
      <tbody>${rows}</tbody>
    </table></div>`);
}

// ── Utils ─────────────────────────────────────────────────────────────────────

function escHtml(s) {
  return String(s ?? '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}
// escHtml alone is insufficient here: several onclick="fn('${escAttr(x)}')"
// call sites (e.g. removeAllowlistEntry) interpolate into a SINGLE-quoted JS
// string, and escHtml never escapes "'" — a value containing one (e.g. an
// attacker-set profile email, since set_report_email is reachable by any
// non-admin group member) breaks out of the string and executes as JS in
// the admin's authenticated session (security review finding).
function escAttr(s) { return escHtml(s).replace(/'/g, '&#39;'); }
