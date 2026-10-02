(() => {
  'use strict';
  const state = { clients: [], client: null, groups: [], driveCount: undefined, editingStory: null, selectedClientId: null, selectionVersion: 0, clientListVersion: 0, homeSummaryVersion: 0, teams: [], me: null, clientSearch: '' };
  const LOCKED_STORY_STATES = new Set(['publicando', 'publicado', 'cancelada']);
  const storyIsLocked = (story) => LOCKED_STORY_STATES.has(story?.estado);
  const $ = (id) => document.getElementById(id);
  const escapeHtml = (value) => String(value ?? '').replace(/[&<>'"]/g, (char) => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'})[char]);
  function avatarColor(id) { let hash = 0; for (const ch of String(id)) hash = (hash * 31 + ch.charCodeAt(0)) >>> 0; return hash % 12; }
  let fontFacesInjected = false;
  function injectFontFaces(fonts) {
    // Lets the <option> list below render each font's name IN that font,
    // so an employee can eyeball how it looks before picking it.
    if (fontFacesInjected) return; fontFacesInjected = true;
    const style = document.createElement('style');
    style.textContent = fonts.map((font) =>
      `@font-face{font-family:'sf-${font.key}';src:url('/fonts/${encodeURIComponent(font.file)}');font-display:swap;}`
    ).join('');
    document.head.appendChild(style);
  }

  async function api(path, options = {}) {
    const response = await fetch(path, {credentials: 'same-origin', headers: {'Content-Type':'application/json', ...(options.headers || {})}, ...options});
    if (response.status === 401) { window.location.assign('/login'); throw new Error('Sesión vencida'); }
    if (response.status === 403) { showMessage('No tenés permiso para acceder a este contenido.', true); throw new Error('Acceso denegado'); }
    if (!response.ok) {
      let detail = 'No se pudo completar la operación.';
      try { detail = (await response.json()).detail || detail; } catch (_) { /* no JSON response */ }
      throw new Error(detail);
    }
    return response.status === 204 ? null : response.json();
  }
  let toastTimer = null;
  function showMessage(text, error = false) {
    $('toast-text').textContent = text;
    $('toast').className = `toast${error ? ' error' : ''}`;
    if (toastTimer) clearTimeout(toastTimer);
    toastTimer = setTimeout(clearMessage, 4000);
  }
  function clearMessage() {
    $('toast').className = 'toast hidden';
    if (toastTimer) { clearTimeout(toastTimer); toastTimer = null; }
  }
  function confirmDialog(text, {okLabel = 'Confirmar', danger = false} = {}) {
    return new Promise((resolve) => {
      const dialog = $('confirm-dialog');
      $('confirm-text').textContent = text;
      $('confirm-ok').textContent = okLabel;
      $('confirm-ok').className = `btn primary${danger ? ' danger' : ''}`;
      const settle = (value) => { dialog.close(); resolve(value); };
      const onOk = () => { cleanup(); settle(true); };
      const onCancel = () => { cleanup(); settle(false); };
      function cleanup() {
        $('confirm-ok').removeEventListener('click', onOk);
        $('confirm-cancel').removeEventListener('click', onCancel);
        $('confirm-close').removeEventListener('click', onCancel);
        dialog.removeEventListener('cancel', onCancel);
      }
      $('confirm-ok').addEventListener('click', onOk);
      $('confirm-cancel').addEventListener('click', onCancel);
      $('confirm-close').addEventListener('click', onCancel);
      dialog.addEventListener('cancel', onCancel); // Esc key
      dialog.showModal();
    });
  }
  function initials(name) { return String(name || '?').split(/\s+/).slice(0, 2).map((part) => part[0]).join('').toUpperCase(); }
  function setControlsDisabled(disabled) {
    ['save-description','save-focus','try-prompt','save-story','generate-weekly'].forEach((id) => { $(id).disabled = disabled; });
    $('generate-weekly').disabled = disabled || ritmoSaving || weeklyGenerating;
    $('edit-ritmo').disabled = disabled || ritmoLoading;
  }
  function driveUrl(folderId) { return folderId ? `https://drive.google.com/drive/folders/${encodeURIComponent(folderId)}` : null; }
  function clearSelection() {
    state.selectionVersion += 1; state.selectedClientId = null; state.client = null; state.groups = []; state.editingStory = null;
    $('client-view').classList.add('hidden'); $('empty').classList.remove('hidden'); $('empty').textContent = 'Seleccioná un cliente para gestionar su contenido.';
    $('header-client').classList.add('hidden'); $('header-default').classList.remove('hidden'); $('drive-link').classList.add('hidden');
    $('home-view').classList.add('hidden'); $('nav-home').classList.remove('active'); $('nav-clients').classList.add('active');
  }
  function healthChips(items, emptyLabel, labelFor) {
    if (!items.length) return `<div class="empty">${escapeHtml(emptyLabel)}</div>`;
    return `<div class="idle-list">${items.map((item) => `<button type="button" class="idle-chip clickable" data-client-id="${escapeHtml(item.id)}" title="Ir a este cliente">${escapeHtml(item.name)} — ${escapeHtml(labelFor(item))}</button>`).join('')}</div>`;
  }
  function renderHealthUnavailable() {
    const unavailable = '<div class="empty">El estado de salud no está disponible en este momento.</div>';
    ['health-generacion', 'health-pool', 'health-errores'].forEach((id) => { $(id).innerHTML = unavailable; });
  }
  async function loadHomeSummary() {
    const summaryVersion = ++state.homeSummaryVersion;
    $('home-teams').textContent = 'Cargando...'; $('home-idle').textContent = 'Cargando...';
    $('health-generacion').textContent = 'Cargando...'; $('health-pool').textContent = 'Cargando...'; $('health-errores').textContent = 'Cargando...';
    ['stat-edicion','stat-agendadas','stat-publicadas','stat-promedio','stat-aprobacion'].forEach((id) => { $(id).textContent = '–'; });
    const [summaryResult, healthResult] = await Promise.allSettled([api('/portal/resumen'), api('/portal/salud')]);
    // Inicio can be opened twice before its first request returns, or closed
    // while it is in flight. Only the latest visible request may update it.
    if (summaryVersion !== state.homeSummaryVersion || $('home-view').classList.contains('hidden')) return;
    if (summaryResult.status === 'fulfilled') {
      const summary = summaryResult.value;
      $('stat-edicion').textContent = summary.historias_en_edicion;
      $('stat-agendadas').textContent = summary.historias_agendadas;
      $('stat-publicadas').textContent = summary.historias_publicadas;
      $('stat-promedio').textContent = summary.promedio_por_cliente;
      $('stat-aprobacion').textContent = summary.aprobacion_pct === null ? '—' : `${summary.aprobacion_pct}%`;
      $('home-teams').innerHTML = summary.por_equipo.length
        ? `<table class="team-table"><thead><tr><th>Equipo</th><th>Clientes</th><th>En edición</th><th>Agendadas</th><th>% aprobación</th></tr></thead><tbody>${summary.por_equipo.map((team) => `<tr><td>${escapeHtml(team.team_name)}</td><td>${team.clientes}</td><td>${team.historias_en_edicion}</td><td>${team.historias_agendadas}</td><td>${team.aprobacion_pct === null ? '—' : team.aprobacion_pct + '%'}</td></tr>`).join('')}</tbody></table>`
        : '<div class="empty">Todavía no hay clientes.</div>';
      $('home-idle').innerHTML = summary.clientes_sin_actividad.length
        ? `<div class="idle-list">${summary.clientes_sin_actividad.map((c) => `<span class="idle-chip neutral">${escapeHtml(c.name)}</span>`).join('')}</div>`
        : '<div class="empty">Todos los clientes tienen historias agendadas. 🎉</div>';
    } else {
      $('home-teams').textContent = ''; $('home-idle').textContent = '';
      const error = summaryResult.reason;
      if (!['Acceso denegado','Sesión vencida'].includes(error.message)) showMessage(error.message, true);
    }
    if (healthResult.status === 'fulfilled') {
      const health = healthResult.value;
      $('health-generacion').innerHTML = healthChips(health.clientes_con_error_generacion, 'Sin errores de generación. 🎉', (c) => dayLabel((c.generation_error_at || '').slice(0,10)) || 'reciente');
      $('health-pool').innerHTML = healthChips(health.clientes_con_pool_bajo, 'Ningún cliente con pool bajo ahora mismo.', (c) => dayLabel((c.pool_bajo_at || '').slice(0,10)) || 'reciente');
      $('health-errores').innerHTML = healthChips(health.clientes_con_historias_en_error, 'Sin historias pendientes de reintentar.', (c) => `${c.historias_en_error} historia${c.historias_en_error===1?'':'s'}`);
    } else renderHealthUnavailable();
  }
  function showHome() {
    $('nav-home').classList.add('active'); $('nav-clients').classList.remove('active');
    $('home-view').classList.remove('hidden'); $('empty').classList.add('hidden'); $('client-view').classList.add('hidden');
    $('header-client').classList.add('hidden'); $('header-default').classList.remove('hidden'); $('drive-link').classList.add('hidden');
    loadHomeSummary();
  }
  function showClients() {
    $('nav-clients').classList.add('active'); $('nav-home').classList.remove('active');
    $('home-view').classList.add('hidden');
    if (state.client) {
      $('client-view').classList.remove('hidden'); $('header-default').classList.add('hidden'); $('header-client').classList.remove('hidden');
      if (driveUrl(state.client.drive_folder_id)) $('drive-link').classList.remove('hidden');
    } else {
      $('empty').classList.remove('hidden');
    }
  }

  async function loadMeAndTeams() {
    try {
      const [me, teams, fonts] = await Promise.all([api('/portal/me'), api('/portal/equipos'), api('/portal/tipografias')]);
      state.me = me; state.teams = teams; state.fontChoices = fonts;
      $('client-team').innerHTML = '<option value="">Sin equipo</option>' + teams.map((team) => `<option value="${escapeHtml(team.id)}">${escapeHtml(team.name)}</option>`).join('');
      injectFontFaces(fonts);
      const fontOptions = fonts.map((font) => `<option value="${escapeHtml(font.key)}" style="font-family:'sf-${escapeHtml(font.key)}',sans-serif">${escapeHtml(font.label)}</option>`).join('');
      $('client-font').innerHTML = '<option value="">Default</option>' + fontOptions;
      $('story-font').innerHTML = '<option value="">Default del cliente</option>' + fontOptions;
      $('me-avatar').textContent = initials(me.name || me.email); $('me-name').textContent = me.name || me.email;
      $('me-role').textContent = me.role === 'admin' ? 'Administrador' : 'Empleado'; $('me-card').classList.remove('hidden');
      $('add-clients-bulk').classList.toggle('hidden', me.role !== 'admin');
    } catch (error) { /* non-fatal: the sidebar just falls back to a flat, ungrouped list */ }
  }
  function teamName(teamId) { const team = state.teams.find((item) => item.id === teamId); return team ? team.name : 'Sin equipo'; }
  async function createTeam() {
    const name = window.prompt('Nombre del equipo nuevo:');
    if (!name || !name.trim()) return;
    try {
      const team = await api('/portal/equipos', {method:'POST', body:JSON.stringify({name:name.trim()})});
      state.teams.push(team); state.teams.sort((a,b)=>a.name.localeCompare(b.name,'es'));
      $('client-team').innerHTML = '<option value="">Sin equipo</option>' + state.teams.map((item) => `<option value="${escapeHtml(item.id)}">${escapeHtml(item.name)}</option>`).join('');
      renderClients(); showMessage('Equipo creado.');
    } catch(error) { showMessage(error.message, true); }
  }

  function parseBulkClientsInput(text) {
    return text.split('\n').map((line) => line.trim()).filter(Boolean).map((line, idx) => {
      const [name, drive, businessDescription, teamName] = line.split(',').map((part) => (part || '').trim());
      return {fila: idx + 1, name: name || '', drive: drive || '', businessDescription: businessDescription || '', teamName: teamName || ''};
    });
  }
  function resolveTeamId(teamName) {
    if (!teamName) return {teamId: null, error: null};
    const match = state.teams.find((team) => team.name.toLocaleLowerCase('es') === teamName.toLocaleLowerCase('es'));
    return match ? {teamId: match.id, error: null} : {teamId: null, error: `Equipo no encontrado: "${teamName}"`};
  }
  function renderBulkClientsResults(rows) {
    if (!rows.length) { $('bulk-clients-results').innerHTML = ''; return; }
    $('bulk-clients-results').innerHTML = `<div class="idle-list">${rows.map((row) =>
      `<span class="idle-chip${row.status === 'creado' ? ' success' : ''}">Fila ${row.fila} — ${escapeHtml(row.name || '(sin nombre)')}: ${row.status === 'creado' ? 'creado' : escapeHtml(row.motivo)}</span>`
    ).join('')}</div>`;
  }
  async function submitBulkClients() {
    const rows = parseBulkClientsInput($('bulk-clients-input').value);
    if (!rows.length) {
      $('bulk-clients-feedback').textContent = 'Pegá al menos un cliente.';
      $('bulk-clients-feedback').classList.remove('hidden');
      return;
    }
    $('bulk-clients-feedback').classList.add('hidden');
    const localErrors = [];
    const payload = [];
    const payloadRowNumbers = [];
    for (const row of rows) {
      if (!row.name || !row.drive) {
        localErrors.push({fila: row.fila, name: row.name, status: 'error', motivo: 'Faltan columnas obligatorias (nombre y carpeta de Drive).'});
        continue;
      }
      const {teamId, error} = resolveTeamId(row.teamName);
      if (error) {
        localErrors.push({fila: row.fila, name: row.name, status: 'error', motivo: error});
        continue;
      }
      payload.push({name: row.name, drive_folder_id: row.drive, business_description: row.businessDescription || null, team_id: teamId});
      payloadRowNumbers.push(row.fila);
    }
    $('submit-bulk-clients').disabled = true;
    try {
      let serverResults = [];
      if (payload.length) {
        const response = await api('/portal/clientes/alta-masiva', {method: 'POST', body: JSON.stringify({clientes: payload})});
        serverResults = response.resultados.map((result, i) => ({...result, fila: payloadRowNumbers[i]}));
      }
      const allResults = [...localErrors, ...serverResults].sort((a, b) => a.fila - b.fila);
      renderBulkClientsResults(allResults);
      const createdCount = serverResults.filter((row) => row.status === 'creado').length;
      if (createdCount) {
        showMessage(`${createdCount} cliente${createdCount === 1 ? '' : 's'} creado${createdCount === 1 ? '' : 's'}.`);
        loadClients();
      }
    } catch (error) {
      $('bulk-clients-feedback').textContent = error.message;
      $('bulk-clients-feedback').classList.remove('hidden');
    } finally {
      $('submit-bulk-clients').disabled = false;
    }
  }

  async function loadClients() {
    clearMessage(); $('client-list').textContent = 'Cargando...';
    const listVersion = ++state.clientListVersion;
    const suffix = $('only-mine').checked ? '?solo_mios=true' : '';
    try {
      const clients = await api(`/portal/clientes${suffix}`);
      if (listVersion !== state.clientListVersion) return;
      state.clients = clients; renderClients();
      const selectionStillVisible = state.selectedClientId && clients.some((client) => client.id === state.selectedClientId);
      if (!selectionStillVisible) {
        clearSelection(); renderClients();
        if (clients.length) await selectClient(clients[0].id);
      }
    } catch (error) {
      if (listVersion !== state.clientListVersion) return;
      $('client-list').textContent = 'No se pudieron cargar los clientes.';
      if (!['Acceso denegado','Sesión vencida'].includes(error.message)) showMessage(error.message, true);
    }
  }
  function renderClients() {
    if (!state.clients.length) { $('client-list').textContent = 'No hay clientes disponibles.'; return; }
    const query = (state.clientSearch || '').trim().toLocaleLowerCase('es');
    const visibleClients = query ? state.clients.filter((client) => client.name.toLocaleLowerCase('es').includes(query)) : state.clients;
    if (!visibleClients.length) { $('client-list').textContent = 'Ningún cliente coincide con la búsqueda.'; return; }
    const byTeam = new Map();
    for (const client of visibleClients) {
      const key = client.team_id || '';
      if (!byTeam.has(key)) byTeam.set(key, []);
      byTeam.get(key).push(client);
    }
    const myTeam = state.me?.team_id || '';
    const keys = [...byTeam.keys()].sort((a, b) => {
      if (a === b) return 0;
      if (myTeam && a === myTeam) return -1;
      if (myTeam && b === myTeam) return 1;
      if (a === '') return 1;
      if (b === '') return -1;
      return teamName(a).localeCompare(teamName(b), 'es');
    });
    $('client-list').innerHTML = keys.map((key) => {
      const cards = byTeam.get(key).map((client) => {
        const active = state.client?.id === client.id;
        const count = client.stories_count ?? 0;
        const countLabel = `${count} historia${count === 1 ? '' : 's'}${active ? ' · Esta semana' : ''}`;
        return `<button class="client-item${active ? ' active' : ''}" data-client-id="${escapeHtml(client.id)}"><span class="avatar avatar-${avatarColor(client.id)}">${escapeHtml(initials(client.name))}</span><span class="meta"><span class="name">${escapeHtml(client.name)}</span><span class="count">${escapeHtml(countLabel)}</span></span></button>`;
      }).join('');
      return `<div class="team-group"><h4 class="team-label">${escapeHtml(key ? teamName(key) : 'Sin equipo')}</h4>${cards}</div>`;
    }).join('');
  }
  async function selectClient(clientId) {
    const selectionVersion = ++state.selectionVersion;
    state.selectedClientId = clientId; state.client = null; state.groups = []; state.editingStory = null; draftDates = new Set(); setControlsDisabled(true);
    if ($('story-dialog').open) $('story-dialog').close();
    clearMessage(); $('empty').classList.add('hidden'); $('client-view').classList.remove('hidden');
    $('home-view').classList.add('hidden'); $('nav-home').classList.remove('active'); $('nav-clients').classList.add('active');
    $('header-default').classList.add('hidden'); $('header-client').classList.remove('hidden');
    $('client-name').textContent = 'Cargando...'; $('stories').textContent = 'Cargando...'; $('week-badge').classList.add('hidden');
    $('activity-list').innerHTML = ''; $('activity-summary').innerHTML = ''; $('header-tags').innerHTML = ''; $('drive-link').classList.add('hidden');
    $('plan-panel').classList.add('hidden'); state.driveCount = undefined;
    try {
      const [client, groups, driveInfo] = await Promise.all([
        api(`/portal/clientes/${encodeURIComponent(clientId)}`),
        api(`/portal/clientes/${encodeURIComponent(clientId)}/historias`),
        api(`/portal/clientes/${encodeURIComponent(clientId)}/drive-info`).catch(() => ({count: null})),
      ]);
      if (selectionVersion !== state.selectionVersion || clientId !== state.selectedClientId) return;
      state.client = client; state.groups = groups; state.driveCount = driveInfo?.count ?? null;
      renderClients(); renderClient();
    } catch (error) {
      if (selectionVersion === state.selectionVersion) {
        $('stories').textContent = '';
        if (!['Acceso denegado','Sesión vencida'].includes(error.message)) showMessage(error.message, true);
      }
    } finally {
      if (selectionVersion === state.selectionVersion) setControlsDisabled(false);
    }
  }
  function setFieldMode(name, editing) {
    $(`${name}-view`).classList.toggle('hidden', editing);
    $(`${name}-edit`).classList.toggle('hidden', !editing);
    $(`${name}-view-actions`).classList.toggle('hidden', editing);
    $(`${name}-edit-actions`).classList.toggle('hidden', !editing);
  }
  function updateFieldView(name, value, emptyPlaceholder) {
    $(`${name}-text`).textContent = value || emptyPlaceholder;
    $(`${name}-view`).classList.toggle('empty', !value);
  }
  function renderClient() {
    $('client-name').textContent = state.client.name; $('client-avatar').textContent = initials(state.client.name);
    $('business-description').value = state.client.business_description || ''; $('weekly-focus').value = state.client.weekly_focus || '';
    $('desc-count').textContent = String($('business-description').value.length); $('focus-count').textContent = String($('weekly-focus').value.length);
    updateFieldView('description', state.client.business_description, 'Todavía no hay descripción. Hacé click en Editar para agregarla.');
    updateFieldView('focus', state.client.weekly_focus, 'Sin enfoque puntual para esta semana.');
    setFieldMode('description', false); setFieldMode('focus', false);
    $('client-team').value = state.client.team_id || '';
    $('client-font').value = state.client.font_choice || '';
    $('gen-dot').className = `gen-dot${state.client.generation_error ? ' warn' : ''}`;
    $('header-tags').innerHTML = (state.client.topics || []).slice(0, 4).map((topic) => `<span class="tag">${escapeHtml(topic)}</span>`).join('');
    const url = driveUrl(state.client.drive_folder_id);
    if (url) { $('drive-link').href = url; $('drive-link').classList.remove('hidden'); $('qa-drive').href = url; $('qa-drive').classList.remove('hidden'); }
    else { $('drive-link').classList.add('hidden'); $('qa-drive').classList.add('hidden'); }
    $('prompt-preview').classList.add('hidden'); renderStories(); renderPlan(); renderActivity(); loadCalendar();
  }
  function dayLabel(isoDate) {
    return isoDate ? new Intl.DateTimeFormat('es-AR', {weekday:'short', day:'numeric', timeZone:'UTC'}).format(new Date(`${isoDate}T00:00:00Z`)) : '';
  }
  function activeGroup() {
    // Prefer the nearest group still awaiting confirmation; if everything is
    // already scheduled, keep showing the nearest confirmed publication.
    return state.groups.find((group) => (group.stories || []).some((story) => !storyIsScheduled(story, group))) || state.groups[0];
  }
  function storyIsScheduled(story, group) {
    // story.agendado is the per-date source of truth.  The group fallback
    // keeps records created before the per-story migration readable.
    return story.agendado === true || (story.agendado === undefined && group.agendado === true);
  }
  function activeDates() {
    // Every date covered by every not-yet-agendado group, plus any date the
    // employee has clicked on the calendar this session. It is valid to have
    // a manual upload and the weekly AI batch awaiting confirmation together;
    // neither may disappear from the editable area.
    const dates = new Set();
    const agendadoDates = new Set();
    for (const g of state.groups) {
      let groupHasDate = false;
      for (const story of (g.stories || [])) {
        if (!story.fecha_publicacion) continue;
        if (storyIsScheduled(story, g)) agendadoDates.add(story.fecha_publicacion);
        else {
          dates.add(story.fecha_publicacion);
          groupHasDate = true;
        }
      }
      if (!groupHasDate && !(g.stories || []).length && !g.agendado && g.scheduled_date) dates.add(g.scheduled_date);
    }
    for (const iso of draftDates) if (!agendadoDates.has(iso)) dates.add(iso);
    return dates;
  }
  function planItemsByDate() {
    // Only confirmed groups belong in Plan. Pending AI and manual groups stay
    // in "Historias generadas" until the employee schedules them.
    const byDate = new Map();
    for (const group of state.groups) {
      for (const story of (group.stories || [])) {
        if (!story.fecha_publicacion) continue;
        if (!storyIsScheduled(story, group)) continue;
        if (!byDate.has(story.fecha_publicacion)) byDate.set(story.fecha_publicacion, {stories: [], descripcion: null});
        const entry = byDate.get(story.fecha_publicacion);
        entry.stories.push(story);
        if (!entry.descripcion && group.descripcion) entry.descripcion = group.descripcion;
      }
    }
    return byDate;
  }
  function renderPlan() {
    $('plan-panel').classList.remove('hidden');
    const byDate = planItemsByDate();
    const dates = [...byDate.keys()].sort();
    if (!dates.length) {
      $('plan-range').textContent = '';
      $('plan-days').innerHTML = '<div class="empty">No hay más publicaciones programadas todavía.</div>';
      return;
    }
    $('plan-range').textContent = dates.length === 1 ? dayLabel(dates[0]) : `${dayLabel(dates[0])} – ${dayLabel(dates[dates.length - 1])}`;
    $('plan-days').innerHTML = dates.map((iso) => {
      const entry = byDate.get(iso);
      const stories = [...entry.stories].sort((a,b) => a.order - b.order);
      const first = stories[0];
      const title = entry.descripcion || stories.map((s) => s.text).find(Boolean) || 'Sin texto todavía';
      const shortTitle = title.length > 40 ? title.slice(0, 37) + '…' : title;
      const hora = first.hora_publicacion ? String(first.hora_publicacion).slice(0,5) : '';
      const count = stories.length;
      const hasFailedStory = stories.some((story) => story.estado === 'error');
      const retryBlocked = stories.some((story) => story.estado === 'publicando');
      const canRetry = hasFailedStory && !retryBlocked;
      const retryButton = canRetry ? `<button class="icon-btn plan-chip-retry" data-plan-retry="${escapeHtml(iso)}" aria-label="Reintentar publicación fallida" title="Reintentar la publicación fallida de este día">🔁</button>` : '';
      const canDelete = stories.every((story) => !storyIsLocked(story));
      const deleteButton = canDelete ? `<button class="icon-btn plan-chip-delete" data-plan-delete="${escapeHtml(iso)}" aria-label="Eliminar toda la publicación de este día">🗑</button>` : '';
      return `<div class="plan-chip" data-plan-date="${escapeHtml(iso)}"><span class="grip">⠿</span><span class="thumb">${first.image_url ? `<img src="${escapeHtml(first.image_url)}" alt="">` : '🖼'}</span><div class="plan-chip-body"><div class="date">${escapeHtml(dayLabel(iso))}${hora ? ' · ' + hora : ''}</div><div class="title">${escapeHtml(shortTitle)}</div><div class="type">${count} historia${count===1?'':'s'}</div></div>${retryButton}${deleteButton}</div>`;
    }).join('');
  }
  function groupDate(group) {
    const raw = group.scheduled_date || group.generation_week;
    return raw ? new Intl.DateTimeFormat('es-AR', {dateStyle:'long', timeZone:'UTC'}).format(new Date(`${raw}T00:00:00Z`)) : 'Sin fecha';
  }
  function renderStories() {
    // One row per date: every date is its own horizontal strip of cards ending
    // in a "+" tile, so any day can hold as many images as needed — never just one.
    const active = activeDates();
    const byDate = new Map();
    const manualGroupByDate = new Map(); // date -> its manual (non-AI) group, if any
    for (const group of state.groups) {
      for (const story of (group.stories || [])) {
        if (storyIsScheduled(story, group)) continue; // confirmed date lives in Plan
        if (!story.fecha_publicacion || !active.has(story.fecha_publicacion)) continue;
        if (!byDate.has(story.fecha_publicacion)) byDate.set(story.fecha_publicacion, []);
        byDate.get(story.fecha_publicacion).push({group, story});
        if (!group.generation_week) manualGroupByDate.set(story.fecha_publicacion, group);
      }
    }
    for (const iso of active) if (!byDate.has(iso)) byDate.set(iso, []);
    const dates = [...byDate.keys()].sort();
    if (!dates.length) {
      $('stories').innerHTML = '<div class="empty">No hay historias próximas para este cliente. Clickeá un día del calendario para agregar una.</div>';
      $('week-badge').classList.add('hidden');
      return;
    }
    const first = dates[0], last = dates[dates.length - 1];
    $('week-badge').textContent = first === last ? dayLabel(first) : `${dayLabel(first)} – ${dayLabel(last)}`;
    $('week-badge').classList.remove('hidden');
    const rows = dates.map((iso) => {
      const entries = byDate.get(iso);
      const stories = entries.map(({story}) => story);
      const batches = new Map();
      for (const {group, story} of entries) {
        if (!batches.has(group.id)) batches.set(group.id, {group, stories: []});
        batches.get(group.id).stories.push(story);
      }
      const batchRows = [...batches.values()].map(({group, stories: batchStories}) => {
        batchStories.sort((a,b) => a.order - b.order);
        const label = group.generation_week ? 'Generación IA' : 'Carga manual';
        // The database rejects reordering a group once any story is locked.
        // Reflect that constraint in the UI instead of offering a drag that
        // can only fail.
        const batchReorderable = (group.stories || []).every((story) => !storyIsLocked(story));
        const cards = batchStories.map((story,index) => storyCard(story,index,group.id,batchReorderable)).join('');
        return `<div class="story-batch" data-story-group-id="${escapeHtml(group.id)}"><div class="story-batch-label">${label}</div><div class="day-row-cards">${cards}</div></div>`;
      }).join('');
      const manualGroup = manualGroupByDate.get(iso);
      // A day publishes once — AI batch and manual uploads sharing a date are
      // one publication, so "listo para agendar" only needs everything for
      // that date approved, regardless of which group(s) contributed it.
      const actionableStories = stories.filter((story) => !storyIsLocked(story));
      const readyToSchedule = actionableStories.length > 0 && actionableStories.every((story) => story.aprobado);
      const retryableStories = stories.filter((story) => story.estado === 'pendiente' || story.estado === 'error');
      const publishNowBlocked = stories.some((story) => story.estado === 'publicando');
      const readyToPublishNow = retryableStories.length > 0 && !publishNowBlocked && retryableStories.every((story) => story.aprobado);
      const scheduleBtn = readyToSchedule ? `<button class="badge schedule-btn" data-schedule-date="${escapeHtml(iso)}">📅 Agendar</button>` : '';
      const publishNowBtn = readyToPublishNow ? `<button class="badge publish-now-btn" data-publish-now-date="${escapeHtml(iso)}">📤 Publicar ahora</button>` : '';
      const dayLocked = stories.some((story) => storyIsLocked(story));
      const timeSource = actionableStories[0] || stories[0];
      const timeValue = timeSource?.hora_publicacion ? String(timeSource.hora_publicacion).slice(0,5) : '09:00';
      const timeInput = `<input type="time" class="day-time" data-time-for="${escapeHtml(iso)}" value="${escapeHtml(timeValue)}"${dayLocked ? ' disabled title="La publicación de este día ya está en curso o finalizada"' : ''}>`;
      const descInput = manualGroup ? `<input type="text" class="day-desc" data-desc-for="${escapeHtml(iso)}" maxlength="200" value="${escapeHtml(manualGroup.descripcion || '')}" placeholder="Descripción interna (opcional) — ¿de qué van estas historias?"${dayLocked ? ' disabled' : ''}>` : '';
      const addRow = dayLocked ? '' : `<div class="day-row-cards"><div class="story add-placeholder" data-add-date="${escapeHtml(iso)}"><span class="add-icon">+</span></div></div>`;
      return `<div class="day-row" data-date="${escapeHtml(iso)}"><div class="day-row-head"><div class="day-row-top"><span class="section-title">${escapeHtml(dayLabel(iso))}</span>${scheduleBtn}${publishNowBtn}${timeInput}</div>${descInput}</div><div class="story-batches">${batchRows}${addRow}</div></div>`;
    }).join('');
    $('stories').innerHTML = `<section class="group">${rows}</section>`;
  }
  function renderActivity() {
    const next = activeGroup();
    const activeCount = next ? (next.stories || []).length : 0;
    const rows = [];
    if (next) {
      rows.push(`<div class="activity-item"><span class="dot">✓</span><div><div>${activeCount} historia${activeCount===1?'':'s'} lista${activeCount===1?'':'s'}</div><div class="sub">Generadas con la dirección actual</div></div></div>`);
    } else {
      rows.push(`<div class="activity-item"><span class="dot">–</span><div><div>Sin historias programadas</div><div class="sub">Todavía no se generó contenido para este cliente</div></div></div>`);
    }
    if (state.driveCount !== null && state.driveCount !== undefined) {
      rows.push(`<div class="activity-item"><span class="dot">📁</span><div><div>${state.driveCount} imagen${state.driveCount===1?'':'es'} disponible${state.driveCount===1?'':'s'} en Drive</div><div class="sub">De la carpeta del cliente</div></div></div>`);
    }
    if (next) rows.push(`<div class="activity-item"><span class="dot">📅</span><div><div>Próxima publicación: ${escapeHtml(groupDate(next))}${next.scheduled_time ? ' · ' + next.scheduled_time.slice(0,5) + 'hs' : ''}</div><div class="sub">Según el plan generado</div></div></div>`);
    $('activity-list').innerHTML = rows.join('');
    $('activity-summary').innerHTML = state.client.generation_error
      ? `<div class="status-card warn"><span>⚠️</span><div><strong>Necesita atención</strong><p>${escapeHtml(state.client.generation_error)}</p></div></div>`
      : `<div class="status-card ok"><span>✓</span><div><strong>Todo en orden</strong><p>No hay errores de generación pendientes.</p></div></div>`;
  }
  function storyCard(story, index, groupId, batchReorderable = true) {
    const dateBadge = story.fecha_publicacion ? `<span class="story-date">${escapeHtml(dayLabel(story.fecha_publicacion))}</span>` : '';
    const approvedClass = story.aprobado ? ' approved' : '';
    const approvedBadge = story.aprobado ? '<span class="approved-badge" title="Aprobada">✓</span>' : '';
    const locked = storyIsLocked(story);
    const reorderable = batchReorderable && !locked;
    const mutationActions = locked ? '' : '<button class="icon-btn" data-action="edit" aria-label="Editar">✎</button><button class="icon-btn" data-action="delete" aria-label="Eliminar">×</button>';
    return `<article class="story${approvedClass}${locked ? ' read-only' : ''}" draggable="${reorderable ? 'true' : 'false'}" data-reorderable="${reorderable ? 'true' : 'false'}" data-story-id="${escapeHtml(story.id)}" data-story-group-id="${escapeHtml(groupId)}">${story.image_url ? `<img src="${escapeHtml(story.image_url)}" alt="Historia ${index+1}">` : ''}<span class="story-num">${index+1}</span>${dateBadge}${approvedBadge}<div class="story-actions"><button class="icon-btn" data-action="preview" aria-label="Ver en grande">👁</button>${mutationActions}</div><div class="story-overlay"><p class="story-text">${escapeHtml(story.text || 'Sin texto todavía')}</p></div></article>`;
  }
  async function saveClientField(field, buttonId) {
    const name = field === 'weekly_focus' ? 'focus' : 'description';
    const button=$(buttonId), input=$(field==='business_description'?'business-description':'weekly-focus'), value=input.value.trim(), clientId=state.client?.id, selectionVersion=state.selectionVersion;
    if (!clientId) return; button.disabled=true;
    try {
      const updated = await api(`/portal/clientes/${encodeURIComponent(clientId)}`, {method:'PATCH', body:JSON.stringify({[field]:value || null})});
      if (selectionVersion !== state.selectionVersion || clientId !== state.client?.id) return;
      state.client = updated;
      updateFieldView(name, field==='weekly_focus'?updated.weekly_focus:updated.business_description,
        field==='weekly_focus'?'Sin enfoque puntual para esta semana.':'Todavía no hay descripción. Hacé click en Editar para agregarla.');
      setFieldMode(name, false);
      showMessage(field==='weekly_focus'?'Enfoque semanal guardado.':'Descripción guardada.');
    } catch(error) { if (selectionVersion === state.selectionVersion && !['Acceso denegado','Sesión vencida'].includes(error.message)) showMessage(error.message,true); }
    finally { if (selectionVersion === state.selectionVersion && clientId === state.client?.id) button.disabled=false; }
  }
  async function saveClientTeam() {
    const select=$('client-team'), clientId=state.client?.id, selectionVersion=state.selectionVersion, teamId=select.value || null;
    if (!clientId || teamId === (state.client?.team_id || null)) return; select.disabled=true;
    try {
      const updated = await api(`/portal/clientes/${encodeURIComponent(clientId)}/equipo`, {method:'PATCH', body:JSON.stringify({team_id:teamId})});
      if (selectionVersion !== state.selectionVersion || clientId !== state.client?.id) return;
      state.client.team_id = updated.team_id; renderClients(); showMessage('Equipo actualizado.');
    } catch(error) { if (selectionVersion === state.selectionVersion && !['Acceso denegado','Sesión vencida'].includes(error.message)) { showMessage(error.message,true); select.value = state.client?.team_id || ''; } }
    finally { if (selectionVersion === state.selectionVersion && clientId === state.client?.id) select.disabled=false; }
  }
  async function saveClientFont() {
    const select=$('client-font'), clientId=state.client?.id, selectionVersion=state.selectionVersion, fontChoice=select.value || null;
    if (!clientId || fontChoice === (state.client?.font_choice || null)) return; select.disabled=true;
    try {
      const updated = await api(`/portal/clientes/${encodeURIComponent(clientId)}/tipografia`, {method:'PATCH', body:JSON.stringify({font_choice:fontChoice})});
      if (selectionVersion !== state.selectionVersion || clientId !== state.client?.id) return;
      state.client.font_choice = updated.font_choice; showMessage('Tipografía actualizada.');
    } catch(error) { if (selectionVersion === state.selectionVersion && !['Acceso denegado','Sesión vencida'].includes(error.message)) { showMessage(error.message,true); select.value = state.client?.font_choice || ''; } }
    finally { if (selectionVersion === state.selectionVersion && clientId === state.client?.id) select.disabled=false; }
  }
  async function tryPrompt() {
    const button=$('try-prompt'), preview=$('prompt-preview'), clientId=state.client?.id, selectionVersion=state.selectionVersion;
    if (!clientId) return; button.disabled=true; preview.classList.remove('hidden'); preview.textContent='Generando prueba...';
    try {
      const result=await api(`/portal/clientes/${encodeURIComponent(clientId)}/probar-prompt`, {method:'POST'});
      if (selectionVersion !== state.selectionVersion || clientId !== state.client?.id) return;
      preview.innerHTML=`<ol>${result.historias.map((text)=>`<li>${escapeHtml(text)}</li>`).join('')}</ol>`;
    } catch(error) { if (selectionVersion === state.selectionVersion) { preview.textContent=error.message; preview.classList.add('error'); } }
    finally { if (selectionVersion === state.selectionVersion && clientId === state.client?.id) button.disabled=false; }
  }
  const CAL_LABELS = ['Lun','Mar','Mié','Jue','Vie','Sáb','Dom'];
  let ritmoDays = []; // [{day, time, count}], 1–4 days whose counts always total four stories
  let ritmoLoading = false;
  let ritmoSaving = false;
  let weeklyGenerating = false;
  let calMonthOffset = 0;
  let pendingUploadDate = null; // set right before triggering the hidden file input
  let pendingUploadHora = '09:00'; // read from that day's inline time input at the same moment
  const dayTimeSaving = new Set(); // one serialized save loop per client selection and publication date
  const pendingDayTimes = new Map(); // latest value wins within that same scoped save loop
  const publishingDates = new Set(); // block repeated manual publish clicks for the same client selection/date
  function dayOperationKey(clientId, selectionVersion, iso) {
    return `${selectionVersion}:${clientId}:${iso}`;
  }
  let draftDates = new Set(); // ISO dates clicked on the calendar, waiting for an image — shown as empty "+" cards in "Historias generadas"
  function monthBase() { const d = new Date(); d.setDate(1); d.setMonth(d.getMonth() + calMonthOffset); return d; }
  function isoDate(year, month, day) { return `${year}-${String(month+1).padStart(2,'0')}-${String(day).padStart(2,'0')}`; }
  function todayIso() { const d = new Date(); return isoDate(d.getFullYear(), d.getMonth(), d.getDate()); }
  function renderCalendarMonth() {
    const base = monthBase(), year = base.getFullYear(), month = base.getMonth();
    const monthLabel = base.toLocaleDateString('es-AR', {month:'long', year:'numeric'});
    $('cal-month-label').textContent = monthLabel.charAt(0).toUpperCase() + monthLabel.slice(1);
    const firstWeekday = (new Date(year, month, 1).getDay() + 6) % 7; // Monday = 0
    const daysInMonth = new Date(year, month + 1, 0).getDate();
    const cells = Array(firstWeekday).fill(null).concat(Array.from({length: daysInMonth}, (_, i) => i + 1));
    while (cells.length % 7 !== 0) cells.push(null);
    const contentDates = new Set();
    for (const group of state.groups) for (const story of (group.stories || [])) if (story.fecha_publicacion) contentDates.add(story.fecha_publicacion);
    const today = todayIso();
    $('calendar-grid').innerHTML = cells.map((day) => {
      if (day === null) return '<div class="cal-cell outside"></div>';
      const iso = isoDate(year, month, day);
      const isPast = iso < today;
      const pending = draftDates.has(iso) && !contentDates.has(iso);
      return `<div class="cal-cell${pending?' pending':''}${isPast?' past':''}" data-date="${iso}">${day}${contentDates.has(iso)?'<span class="dot"></span>':''}</div>`;
    }).join('');
  }
  function renderRitmoChips() {
    const activeDays = new Set(ritmoDays.map((e) => e.day));
    $('ritmo-chips').innerHTML = CAL_LABELS.map((label, day) =>
      `<span class="ritmo-chip${activeDays.has(day)?' active':''}" data-day="${day}">${label}</span>`
    ).join('');
  }
  function evenSplitCounts(n) {
    const counts = Array(n).fill(0);
    for (let i = 0; i < 4; i++) counts[(i * n) / 4 | 0]++;
    return counts;
  }
  function normalizeRitmoDays(days, publishTogether=false) {
    const sorted = (Array.isArray(days) ? days : [])
      .map((entry) => ({day:Number(entry.day), time:String(entry.time || '09:00'), count:entry.count}))
      .sort((a,b) => a.day - b.day);
    const explicit = sorted.length > 0 && sorted.every((entry) => Number.isInteger(entry.count) && entry.count >= 1 && entry.count <= 4)
      && sorted.reduce((sum, entry) => sum + entry.count, 0) === 4;
    if (explicit) return sorted;
    if (publishTogether && sorted.length) return [{...sorted[0], count:4}];
    const counts = evenSplitCounts(sorted.length);
    return sorted.map((entry, index) => ({...entry, count:counts[index]}));
  }
  function ritmoDetailEntries() {
    return [...document.querySelectorAll('#ritmo-days-detail .ritmo-day-detail')].map((row) => ({
      day:Number(row.dataset.day),
      time:row.querySelector('[data-ritmo-time]').value,
      count:Number(row.querySelector('[data-ritmo-count]').value),
    }));
  }
  function refreshRitmoDetailTotal() {
    const entries = ritmoDetailEntries();
    const valid = entries.length > 0 && entries.every((entry) => /^\d{2}:\d{2}$/.test(entry.time)
      && Number.isInteger(entry.count) && entry.count >= 1 && entry.count <= 4);
    const total = entries.reduce((sum, entry) => sum + (Number.isFinite(entry.count) ? entry.count : 0), 0);
    $('ritmo-count-total').textContent = `Total: ${total} / 4 historias`;
    $('save-ritmo-detail').disabled = ritmoSaving || weeklyGenerating || !valid || total !== 4;
    return {entries, valid, total};
  }
  function renderRitmoDaysDetail() {
    ritmoDays.sort((a,b) => a.day - b.day);
    $('ritmo-days-detail').innerHTML = ritmoDays.map((entry) => `
      <div class="ritmo-day-detail" data-day="${entry.day}">
        <strong>${CAL_LABELS[entry.day]}</strong>
        <label>Hora <input type="time" data-ritmo-time data-day="${entry.day}" value="${escapeHtml(entry.time)}"></label>
        <label>Historias <input type="number" min="1" max="4" data-ritmo-count data-day="${entry.day}" value="${entry.count}"></label>
      </div>`).join('');
    refreshRitmoDetailTotal();
  }
  function showRitmoDetailMessage(message, isError=false) {
    const feedback = $('ritmo-detail-message');
    feedback.textContent = message;
    feedback.classList.toggle('hidden', !message);
    feedback.classList.toggle('success', Boolean(message) && !isError);
  }
  function refreshRitmoControls() {
    const rhythmControlsBlocked = ritmoLoading || ritmoSaving || weeklyGenerating;
    $('ritmo-chips').classList.toggle('is-saving', rhythmControlsBlocked);
    $('ritmo-chips').setAttribute('aria-disabled', String(rhythmControlsBlocked));
    document.querySelectorAll('#ritmo-days-detail input').forEach((input) => { input.disabled = rhythmControlsBlocked; });
    refreshRitmoDetailTotal();
    $('generate-weekly').disabled = rhythmControlsBlocked || !state.client;
    $('edit-ritmo').disabled = rhythmControlsBlocked || !state.client;
  }
  function setRitmoSaving(saving) {
    ritmoSaving = saving;
    refreshRitmoControls();
  }
  async function loadCalendar() {
    const clientId = state.client?.id, selectionVersion = state.selectionVersion;
    ritmoLoading = true;
    ritmoDays = [];
    renderRitmoChips();
    renderRitmoDaysDetail();
    refreshRitmoControls();
    calMonthOffset = 0;
    draftDates = new Set();
    renderCalendarMonth();
    let days = state.client?.publish_days;
    try {
      if (!days) { try { days = (await api('/portal/ritmo-default')).publish_days; } catch(_) { days = [{day:0,time:'09:00',count:1},{day:2,time:'09:00',count:1},{day:4,time:'09:00',count:1},{day:6,time:'09:00',count:1}]; } }
      if (selectionVersion !== state.selectionVersion || clientId !== state.client?.id) return;
      ritmoDays = normalizeRitmoDays(days, Boolean(state.client?.publish_together));
      renderRitmoChips();
      renderRitmoDaysDetail();
    } finally {
      if (selectionVersion === state.selectionVersion && clientId === state.client?.id) {
        ritmoLoading = false;
        refreshRitmoControls();
      }
    }
  }
  async function toggleRitmoDay(day) {
    if (ritmoSaving || weeklyGenerating) return;
    const clientId = state.client?.id, selectionVersion = state.selectionVersion;
    if (!clientId) return;
    const renderedTimes = new Map(ritmoDetailEntries().map((entry) => [entry.day, entry.time]));
    ritmoDays = ritmoDays.map((entry) => ({
      ...entry,
      time:renderedTimes.has(entry.day) ? renderedTimes.get(entry.day) : entry.time,
    }));
    const idx = ritmoDays.findIndex((e) => e.day === day);
    const next = ritmoDays.map((entry) => ({...entry}));
    if (idx >= 0) {
      if (next.length === 1) { showMessage('Elegí al menos un día de publicación.', true); return; }
      next.splice(idx, 1);
    }
    else { if (next.length >= 4) { showMessage('Ya elegiste 4 días — sacá uno para agregar otro.', true); return; } next.push({day, time:'09:00', count:1}); }
    next.sort((a,b) => a.day - b.day);
    const counts = evenSplitCounts(next.length);
    next.forEach((entry, index) => { entry.count = counts[index]; });
    setRitmoSaving(true);
    try {
      const updated = await api(`/portal/clientes/${encodeURIComponent(clientId)}/ritmo`, {method:'PATCH', body:JSON.stringify({publish_days:next})});
      if (selectionVersion !== state.selectionVersion || clientId !== state.client?.id) return;
      state.client = {...state.client, ...updated}; ritmoDays = normalizeRitmoDays(updated.publish_days); renderRitmoChips(); renderRitmoDaysDetail();
      showMessage('Días automáticos actualizados.');
    } catch(error) { showMessage(error.message, true); }
    finally { setRitmoSaving(false); }
  }
  function openRitmoDialog() {
    if (ritmoLoading || !state.client) return;
    const feedback = $('ritmo-generation-message');
    feedback.textContent = '';
    feedback.classList.add('hidden');
    showRitmoDetailMessage('');
    renderRitmoDaysDetail();
    $('ritmo-dialog').showModal();
  }
  async function saveRitmoDetail() {
    if (ritmoSaving || weeklyGenerating) return;
    const clientId = state.client?.id, selectionVersion = state.selectionVersion;
    if (!clientId) return;
    const {entries, valid, total} = refreshRitmoDetailTotal();
    if (!valid || total !== 4) {
      showRitmoDetailMessage('Los conteos deben sumar exactamente 4 y cada horario debe ser válido.', true);
      return;
    }
    showRitmoDetailMessage('');
    setRitmoSaving(true);
    try {
      const updated = await api(`/portal/clientes/${encodeURIComponent(clientId)}/ritmo`, {method:'PATCH', body:JSON.stringify({publish_days:entries})});
      if (selectionVersion !== state.selectionVersion || clientId !== state.client?.id) return;
      state.client = {...state.client, ...updated};
      ritmoDays = normalizeRitmoDays(updated.publish_days);
      renderRitmoChips(); renderRitmoDaysDetail();
      showRitmoDetailMessage('Días y horarios guardados.');
    } catch(error) {
      showRitmoDetailMessage(error.message, true);
    } finally {
      setRitmoSaving(false);
    }
  }
  async function generateWeeklyNow() {
    if (ritmoSaving || weeklyGenerating) return;
    const button = $('generate-weekly'), feedback = $('ritmo-generation-message');
    const clientId = state.client?.id, selectionVersion = state.selectionVersion;
    if (!clientId) return;
    const previousLabel = button.textContent;
    feedback.textContent = ''; feedback.classList.add('hidden');
    weeklyGenerating = true; refreshRitmoControls(); button.textContent = 'Generando...';
    try {
      const result = await api(`/portal/clientes/${encodeURIComponent(clientId)}/generar-semana`, {method:'POST'});
      if (selectionVersion !== state.selectionVersion || clientId !== state.client?.id) return;
      $('ritmo-dialog').close();
      await selectClient(clientId);
      showMessage(result.detail);
    } catch(error) {
      if (selectionVersion === state.selectionVersion && clientId === state.client?.id) {
        feedback.textContent = error.message;
        feedback.classList.remove('hidden');
      }
    } finally {
      weeklyGenerating = false; refreshRitmoControls(); button.textContent = previousLabel;
    }
  }
  async function uploadManualImage(iso, hora, file) {
    const clientId = state.client?.id, selectionVersion = state.selectionVersion;
    if (!clientId) return;
    if (!/^([01]\d|2[0-3]):[0-5]\d$/.test(hora)) { showMessage('Hora inválida. Usá el formato HH:MM.', true); return; }
    const formData = new FormData();
    formData.append('fecha_publicacion', iso); formData.append('hora_publicacion', hora); formData.append('image', file);
    try {
      const response = await fetch(`/portal/clientes/${encodeURIComponent(clientId)}/historias/manual`, {method:'POST', credentials:'same-origin', body: formData});
      if (response.status === 401) { window.location.assign('/login'); return; }
      if (!response.ok) { let detail = 'No se pudo subir la imagen.'; try { detail = (await response.json()).detail || detail; } catch(_) {} throw new Error(detail); }
      const story = await response.json();
      if (selectionVersion !== state.selectionVersion || clientId !== state.client?.id) return;
      // Append locally instead of refetching /historias — with several
      // uploads firing close together, whichever refetch resolved last could
      // clobber state.groups with a snapshot taken before an earlier upload's
      // write had landed, silently dropping it from the screen.
      let group = state.groups.find((g) => g.id === story.story_group_id);
      if (!group) {
        group = {id: story.story_group_id, client_id: clientId, scheduled_date: iso,
          scheduled_time: `${hora}:00`, generation_week: null, agendado: false,
          descripcion: null, stories: []};
        state.groups.push(group);
        state.groups.sort((a,b) => String(a.scheduled_date||'').localeCompare(String(b.scheduled_date||'')));
      }
      group.stories = [...(group.stories || []), story];
      draftDates.add(iso); // keep the row open so more images can be added
      renderStories(); renderPlan(); renderCalendarMonth(); renderActivity();
      showMessage('Imagen agregada.');
    } catch(error) { showMessage(error.message, true); }
  }
  async function toggleApproval(story) {
    const clientId = state.client?.id, selectionVersion = state.selectionVersion;
    if (!clientId || storyIsLocked(story)) return;
    const previous = story.aprobado, next = !previous;
    story.aprobado = next; // optimistic — feels instant, reverted below on failure
    renderStories(); renderPlan();
    try {
      const updated = await api(`/portal/historias/${encodeURIComponent(story.id)}/aprobar`, {method:'PATCH', body:JSON.stringify({aprobado: next})});
      if (selectionVersion !== state.selectionVersion || clientId !== state.client?.id) return;
      Object.assign(story, updated);
      renderStories(); renderPlan();
      if (next && story.fecha_publicacion) maybePromptSchedule(story.fecha_publicacion);
    } catch(error) {
      story.aprobado = previous;
      if (selectionVersion === state.selectionVersion) {
        renderStories(); renderPlan();
        if (!['Acceso denegado','Sesión vencida'].includes(error.message)) showMessage(error.message, true);
      }
    }
  }
  async function maybePromptSchedule(iso) {
    // A day publishes once — every story dated iso (AI batch, manual upload,
    // or both) counts toward "ready", from any group not already agendado.
    const dayStories = [];
    for (const group of state.groups) {
      for (const story of (group.stories || [])) {
        if (!storyIsScheduled(story, group) && story.fecha_publicacion === iso) dayStories.push(story);
      }
    }
    if (!dayStories.length || !dayStories.every((s) => s.aprobado)) return;
    const confirmed = await confirmDialog(`Se aprobaron todas las historias del ${dayLabel(iso)}. ¿Agendar la publicación para ese día?`, {okLabel:'Agendar'});
    if (confirmed) scheduleDay(iso);
  }
  async function scheduleDay(iso) {
    const clientId = state.client?.id, selectionVersion = state.selectionVersion;
    if (!clientId) return;
    try {
      await api(`/portal/clientes/${encodeURIComponent(clientId)}/dias/${encodeURIComponent(iso)}/agendar`, {method:'PATCH'});
      if (selectionVersion !== state.selectionVersion || clientId !== state.client?.id) return;
      const groups = await api(`/portal/clientes/${encodeURIComponent(clientId)}/historias`);
      if (selectionVersion !== state.selectionVersion || clientId !== state.client?.id) return;
      state.groups = groups;
      renderStories(); renderPlan(); renderCalendarMonth(); renderActivity();
      showMessage('Publicación agendada.');
    } catch(error) { showMessage(error.message, true); }
  }
  async function publishDayNow(iso) {
    const clientId = state.client?.id, selectionVersion = state.selectionVersion;
    if (!clientId) return;
    const operationKey = dayOperationKey(clientId, selectionVersion, iso);
    if (publishingDates.has(operationKey)) return;
    const confirmed = await confirmDialog(
      `Esto publica de verdad en Instagram ahora mismo, sin esperar a la fecha agendada. ¿Confirmás para el ${dayLabel(iso)}?`,
      {okLabel:'Publicar ahora', danger:true});
    if (!confirmed) return;
    if (selectionVersion !== state.selectionVersion || clientId !== state.client?.id) return;
    if (publishingDates.has(operationKey)) return; // another confirmation won the race
    publishingDates.add(operationKey);
    const button = document.querySelector(`[data-publish-now-date="${CSS.escape(iso)}"], [data-plan-retry="${CSS.escape(iso)}"]`);
    const previousLabel = button ? button.textContent : null;
    if (button) { button.disabled = true; button.textContent = 'Publicando...'; }
    let result = null;
    try {
      try {
        result = await api(`/portal/clientes/${encodeURIComponent(clientId)}/dias/${encodeURIComponent(iso)}/publicar-ahora`, {method:'POST'});
      } catch(error) {
        if (selectionVersion === state.selectionVersion && clientId === state.client?.id) showMessage(error.message, true);
        return;
      }
      if (selectionVersion !== state.selectionVersion || clientId !== state.client?.id) return;
      let groups;
      try {
        groups = await api(`/portal/clientes/${encodeURIComponent(clientId)}/historias`);
      } catch(refreshError) {
        showMessage(`Publicadas: ${result.publicadas}. Fallidas: ${result.fallidas}. La publicación se realizó, pero no se pudo actualizar la pantalla: ${refreshError.message}`, true);
        return;
      }
      if (selectionVersion !== state.selectionVersion || clientId !== state.client?.id) return;
      state.groups = groups;
      renderStories(); renderPlan(); renderCalendarMonth(); renderActivity();
      showMessage(`Publicadas: ${result.publicadas}. Fallidas: ${result.fallidas}.`, result.fallidas > 0);
    } finally {
      publishingDates.delete(operationKey);
      if (button && previousLabel !== null && button.isConnected) {
        button.disabled = false; button.textContent = previousLabel;
      }
    }
  }
  async function updateDayTime(iso, hhmm, input) {
    const clientId = state.client?.id, selectionVersion = state.selectionVersion;
    if (!clientId || !/^([01]\d|2[0-3]):[0-5]\d$/.test(hhmm)) return;
    const operationKey = dayOperationKey(clientId, selectionVersion, iso);
    const storiesForDay = state.groups.flatMap((group) => (group.stories||[]).filter((story) => story.fecha_publicacion === iso));
    const hasStories = storiesForDay.length > 0;
    if (!hasStories) return; // empty row: the chosen time is just read from the input at upload time
    if (storiesForDay.some((story) => storyIsLocked(story))) return;
    if (dayTimeSaving.has(operationKey)) {
      pendingDayTimes.set(operationKey, hhmm);
      return;
    }
    dayTimeSaving.add(operationKey);
    let nextValue = hhmm;
    let saveError = null;
    let groups = null;
    let retryValue = null;
    try {
      while (true) {
        await api(`/portal/clientes/${encodeURIComponent(clientId)}/dias/${encodeURIComponent(iso)}/hora`, {method:'PATCH', body:JSON.stringify({hora_publicacion:nextValue})});
        nextValue = pendingDayTimes.get(operationKey) || null;
        pendingDayTimes.delete(operationKey);
        if (nextValue) continue;
        groups = await api(`/portal/clientes/${encodeURIComponent(clientId)}/historias`);
        // A change that arrived while reloading must still be persisted. Check
        // before rendering; no event can interleave between this check and the
        // synchronous cleanup below.
        nextValue = pendingDayTimes.get(operationKey) || null;
        pendingDayTimes.delete(operationKey);
        if (!nextValue) break;
      }
    } catch(error) {
      saveError = error;
      retryValue = pendingDayTimes.get(operationKey) || null;
      pendingDayTimes.delete(operationKey);
      // Always reload, including after an error: an earlier queued write may
      // already have succeeded, so local rollback alone can be stale.
      try {
        groups = await api(`/portal/clientes/${encodeURIComponent(clientId)}/historias`);
      } catch(refreshError) {
        saveError = saveError || refreshError;
      }
    }
    try {
      if (selectionVersion !== state.selectionVersion || clientId !== state.client?.id) return;
      if (groups) state.groups = groups;
      renderStories(); renderPlan(); renderCalendarMonth(); renderActivity();
      if (saveError) showMessage(saveError.message, true);
      else showMessage('Hora actualizada.');
    } finally {
      dayTimeSaving.delete(operationKey);
      const queued = pendingDayTimes.get(operationKey) || retryValue;
      pendingDayTimes.delete(operationKey);
      if (queued && selectionVersion === state.selectionVersion && clientId === state.client?.id) {
        updateDayTime(iso, queued, input);
      }
    }
  }
  async function updateDayDescription(iso, descripcion) {
    const clientId = state.client?.id, selectionVersion = state.selectionVersion;
    if (!clientId) return;
    const storiesForDay = state.groups.flatMap((group) => (group.stories||[]).filter((story) => story.fecha_publicacion === iso));
    if (storiesForDay.some((story) => storyIsLocked(story))) return;
    try {
      await api(`/portal/clientes/${encodeURIComponent(clientId)}/historias/manual/${encodeURIComponent(iso)}/descripcion`, {method:'PATCH', body:JSON.stringify({descripcion})});
      if (selectionVersion !== state.selectionVersion || clientId !== state.client?.id) return;
      const groups = await api(`/portal/clientes/${encodeURIComponent(clientId)}/historias`);
      if (selectionVersion !== state.selectionVersion || clientId !== state.client?.id) return;
      state.groups = groups;
      renderStories(); renderPlan();
      showMessage('Descripción guardada.');
    } catch(error) { showMessage(error.message, true); }
  }
  async function openHistory() {
    const clientId = state.client?.id; if (!clientId) return;
    $('history-list').textContent = 'Cargando...'; $('history-dialog').showModal();
    try {
      const rows = await api(`/portal/clientes/${encodeURIComponent(clientId)}/historial`);
      $('history-list').innerHTML = rows.length ? rows.map((row) => `<div class="history-row"><div class="field">${escapeHtml(row.field)}</div><div class="meta">${row.changed_by_name ? escapeHtml(row.changed_by_name)+' · ' : ''}${new Date(row.changed_at).toLocaleString('es-AR')}</div><div class="diff"><span class="old">${escapeHtml(row.old_value || '(vacío)')}</span><span>${escapeHtml(row.new_value || '(vacío)')}</span></div></div>`).join('') : '<div class="empty">Todavía no hay cambios registrados para este cliente.</div>';
    } catch (error) { $('history-list').textContent = error.message; }
  }
  function findStory(storyId) { for (const group of state.groups) { const story=(group.stories||[]).find((item)=>item.id===storyId); if(story) return {group,story}; } return null; }
  function openStory(story, group) {
    if (storyIsLocked(story)) return;
    state.editingStory=story; $('story-text').value=story.text || '';
    $('story-edit-img').src = story.image_url || '';
    // Only offer AI-generated text for manually-uploaded stories — never for
    // an AI-thread story, whose text is already carefully written per-slot.
    const isManual = !!group && !group.generation_week;
    $('generate-ai-text').classList.toggle('hidden', !isManual);
    $('generate-ai-text').textContent = story.text ? '🔄 Generar otra vez' : '✨ Generar con IA';
    // Changing the font recomposes the existing text onto the image, so it
    // only makes sense once the story actually has text saved.
    $('story-font').value = story.font_choice || '';
    $('story-font').disabled = !story.text;
    $('story-dialog').showModal();
  }
  async function saveStoryFont() {
    const select=$('story-font'), story=state.editingStory, clientId=state.client?.id, selectionVersion=state.selectionVersion, fontChoice=select.value || null;
    if (!story || storyIsLocked(story) || !clientId || fontChoice === (story.font_choice || null)) return;
    select.disabled=true;
    try {
      const updated = await api(`/portal/historias/${encodeURIComponent(story.id)}/tipografia`, {method:'PATCH', body:JSON.stringify({font_choice:fontChoice})});
      if (selectionVersion !== state.selectionVersion || clientId !== state.client?.id || story !== state.editingStory) return;
      Object.assign(story, updated);
      $('story-edit-img').src = updated.image_url || '';
      renderStories(); renderPlan();
      showMessage('Tipografía actualizada.');
    } catch(error) {
      if (selectionVersion === state.selectionVersion && !['Acceso denegado','Sesión vencida'].includes(error.message)) { showMessage(error.message,true); select.value = story?.font_choice || ''; }
    }
    finally { if (selectionVersion === state.selectionVersion && story === state.editingStory) select.disabled=false; }
  }
  async function generateStoryText() {
    const button=$('generate-ai-text'), story=state.editingStory, clientId=state.client?.id, selectionVersion=state.selectionVersion;
    if (!story || storyIsLocked(story) || !clientId) return;
    const previousLabel = button.textContent;
    button.disabled=true; button.textContent='Generando...';
    try {
      const updated = await api(`/portal/historias/${encodeURIComponent(story.id)}/generar-texto`, {method:'POST'});
      if (selectionVersion !== state.selectionVersion || clientId !== state.client?.id || story !== state.editingStory) return;
      Object.assign(story, updated);
      $('story-text').value = updated.text || '';
      $('story-edit-img').src = updated.image_url || '';
      $('story-font').value = updated.font_choice || ''; $('story-font').disabled = false;
      button.textContent = '🔄 Generar otra vez';
      renderStories(); renderPlan();
      showMessage('Texto generado con IA.');
    } catch(error) {
      button.textContent = previousLabel;
      if (selectionVersion === state.selectionVersion && !['Acceso denegado','Sesión vencida'].includes(error.message)) showMessage(error.message, true);
    }
    finally { button.disabled=false; }
  }
  let previewStories = [], previewIndex = 0;
  function renderPreviewBars() {
    // Only stories already passed are filled — like Instagram, the one
    // currently showing starts empty rather than already complete.
    $('ig-preview-bars').innerHTML = previewStories.map((_, i) => `<span class="ig-bar${i<previewIndex?' filled':''}"></span>`).join('');
  }
  function showPreviewStory() {
    const story = previewStories[previewIndex];
    // The text is already baked into the composed image itself — no
    // separate caption overlay, or it would show up twice.
    $('ig-preview-img').src = story.image_url || '';
    const locked = storyIsLocked(story);
    $('ig-preview-edit').classList.toggle('hidden', locked);
    $('ig-preview-delete').classList.toggle('hidden', locked);
    renderPreviewBars();
  }
  function openPreview(stories) {
    previewStories = stories; previewIndex = 0;
    showPreviewStory();
    $('ig-preview-dialog').showModal();
  }
  function previewNext() { if (previewIndex < previewStories.length - 1) { previewIndex++; showPreviewStory(); } else { $('ig-preview-dialog').close(); } }
  function previewPrev() { if (previewIndex > 0) { previewIndex--; showPreviewStory(); } }
  async function saveStory() {
    const button=$('save-story'), textoNuevo=$('story-text').value.trim(), story=state.editingStory, clientId=state.client?.id, selectionVersion=state.selectionVersion;
    if(!textoNuevo || !story || storyIsLocked(story) || !clientId)return; button.disabled=true;
    try {
      const updated=await api(`/portal/historias/${encodeURIComponent(story.id)}`, {method:'PATCH', body:JSON.stringify({texto_nuevo:textoNuevo})});
      if (selectionVersion !== state.selectionVersion || clientId !== state.client?.id || story !== state.editingStory) return;
      Object.assign(story,updated); $('story-dialog').close(); renderStories(); renderPlan(); showMessage('Historia actualizada.');
    } catch(error) { if (selectionVersion === state.selectionVersion && !['Acceso denegado','Sesión vencida'].includes(error.message)) showMessage(error.message,true); }
    finally { if (selectionVersion === state.selectionVersion) button.disabled=false; }
  }
  async function reorderStory(group, draggedId, targetId) {
    const clientId=state.client?.id, selectionVersion=state.selectionVersion;
    if (!clientId || draggedId === targetId) return;
    if ((group.stories || []).some((story) => storyIsLocked(story))) return;
    const stories=[...group.stories].sort((a,b)=>a.order-b.order);
    const fromIndex=stories.findIndex((item)=>item.id===draggedId), toIndex=stories.findIndex((item)=>item.id===targetId);
    if (fromIndex<0 || toIndex<0) return;
    const [moved]=stories.splice(fromIndex,1); stories.splice(toIndex,0,moved);
    const historias=stories.map((item,position)=>({story_id:item.id,nuevo_order:position+1}));
    try { await api('/portal/historias/reordenar',{method:'PATCH',body:JSON.stringify({historias})}); if(selectionVersion!==state.selectionVersion||clientId!==state.client?.id)return; stories.forEach((item,position)=>{item.order=position+1;}); group.stories=stories; renderStories(); renderPlan(); showMessage('Orden actualizado.'); }
    catch(error) { if (selectionVersion===state.selectionVersion&&!['Acceso denegado','Sesión vencida'].includes(error.message)) showMessage(error.message,true); }
  }
  async function deleteStory(group, story) {
    const clientId=state.client?.id, selectionVersion=state.selectionVersion; if(!clientId || storyIsLocked(story))return false;
    const confirmed = await confirmDialog('¿Cancelar esta historia? Esta acción no se puede deshacer.', {okLabel:'Sí, cancelar', danger:true});
    if (!confirmed || selectionVersion!==state.selectionVersion || clientId!==state.client?.id) return false;
    try {
      await api(`/portal/historias/${encodeURIComponent(story.id)}`,{method:'DELETE'});
      if (selectionVersion!==state.selectionVersion||clientId!==state.client?.id) return false;
      group.stories=group.stories.filter((item)=>item.id!==story.id);
      renderStories(); renderPlan(); renderActivity(); showMessage('Historia cancelada.');
      return true;
    }
    catch(error) { if (selectionVersion===state.selectionVersion&&!['Acceso denegado','Sesión vencida'].includes(error.message)) showMessage(error.message,true); return false; }
  }
  async function deletePlanDay(iso) {
    // Bulk-delete every story making up this day's already-scheduled
    // publication (AI + manual groups combined), same "in case something
    // wrong got uploaded" escape hatch deleteStory gives for one image.
    const clientId=state.client?.id, selectionVersion=state.selectionVersion;
    const entry = planItemsByDate().get(iso);
    if (!clientId || !entry || !entry.stories.length) return;
    if (entry.stories.some((story) => storyIsLocked(story))) return;
    const count = entry.stories.length;
    const confirmed = await confirmDialog(
      `¿Eliminar ${count === 1 ? 'esta publicación' : `las ${count} historias`} del ${dayLabel(iso)}? Esta acción no se puede deshacer.`,
      {okLabel:'Sí, eliminar', danger:true});
    if (!confirmed || selectionVersion!==state.selectionVersion || clientId!==state.client?.id) return;
    const ids = entry.stories.map((story) => story.id);
    try {
      await Promise.all(ids.map((id) => api(`/portal/historias/${encodeURIComponent(id)}`,{method:'DELETE'})));
      if (selectionVersion!==state.selectionVersion||clientId!==state.client?.id) return;
      const idSet = new Set(ids);
      for (const group of state.groups) group.stories = (group.stories||[]).filter((story) => !idSet.has(story.id));
      renderStories(); renderPlan(); renderActivity();
      showMessage('Publicación eliminada.');
    } catch(error) {
      if (selectionVersion===state.selectionVersion && !['Acceso denegado','Sesión vencida'].includes(error.message)) showMessage(error.message,true);
    }
  }
  function editPreviewStory() {
    const story = previewStories[previewIndex];
    if (!story || storyIsLocked(story)) return;
    const found = findStory(story.id);
    $('ig-preview-dialog').close();
    openStory(story, found ? found.group : null);
  }
  async function deletePreviewStory() {
    const story = previewStories[previewIndex];
    if (!story || storyIsLocked(story)) return;
    const found = story && findStory(story.id);
    if (!found) return;
    const deleted = await deleteStory(found.group, story);
    if (!deleted) return;
    previewStories = previewStories.filter((item) => item.id !== story.id);
    if (!previewStories.length) { $('ig-preview-dialog').close(); return; }
    if (previewIndex >= previewStories.length) previewIndex = previewStories.length - 1;
    showPreviewStory();
  }
  $('nav-home').addEventListener('click',showHome);
  $('nav-clients').addEventListener('click',showClients);
  $('home-view').addEventListener('click',(event)=>{
    const chip = event.target.closest('[data-client-id]'); if (!chip) return;
    selectClient(chip.dataset.clientId);
  });
  $('client-list').addEventListener('click',(event)=>{const item=event.target.closest('[data-client-id]');if(item)selectClient(item.dataset.clientId);});
  $('only-mine').addEventListener('change',loadClients);
  $('client-search').addEventListener('input',()=>{state.clientSearch=$('client-search').value;renderClients();});
  $('save-description').addEventListener('click',()=>saveClientField('business_description','save-description'));
  $('save-focus').addEventListener('click',()=>saveClientField('weekly_focus','save-focus'));
  $('try-prompt').addEventListener('click',tryPrompt);
  $('client-team').addEventListener('change',saveClientTeam);
  $('client-font').addEventListener('change',saveClientFont);
  $('add-team').addEventListener('click',createTeam);
  $('add-clients-bulk').addEventListener('click', () => {
    $('bulk-clients-input').value = ''; $('bulk-clients-feedback').classList.add('hidden'); $('bulk-clients-results').innerHTML = '';
    $('bulk-clients-dialog').showModal();
  });
  $('close-bulk-clients').addEventListener('click', () => $('bulk-clients-dialog').close());
  $('close-bulk-clients-2').addEventListener('click', () => $('bulk-clients-dialog').close());
  $('submit-bulk-clients').addEventListener('click', submitBulkClients);
  $('business-description').addEventListener('input',()=>{$('desc-count').textContent=String($('business-description').value.length);});
  $('weekly-focus').addEventListener('input',()=>{$('focus-count').textContent=String($('weekly-focus').value.length);});
  $('edit-description').addEventListener('click',()=>{setFieldMode('description',true); $('business-description').focus();});
  $('cancel-description').addEventListener('click',()=>{$('business-description').value=state.client?.business_description || ''; $('desc-count').textContent=String($('business-description').value.length); setFieldMode('description',false);});
  $('edit-focus').addEventListener('click',()=>{setFieldMode('focus',true); $('weekly-focus').focus();});
  $('cancel-focus').addEventListener('click',()=>{$('weekly-focus').value=state.client?.weekly_focus || ''; $('focus-count').textContent=String($('weekly-focus').value.length); setFieldMode('focus',false);});
  $('content-panel-toggle').addEventListener('click',()=>$('content-panel').classList.toggle('collapsed'));
  $('qa-history').addEventListener('click',openHistory);
  $('close-history').addEventListener('click',()=>$('history-dialog').close());
  $('close-history-2').addEventListener('click',()=>$('history-dialog').close());
  $('cal-prev').addEventListener('click',()=>{calMonthOffset--; renderCalendarMonth();});
  $('cal-next').addEventListener('click',()=>{calMonthOffset++; renderCalendarMonth();});
  $('calendar-grid').addEventListener('click',(event)=>{
    const cell = event.target.closest('[data-date]'); if (!cell) return;
    const iso = cell.dataset.date;
    if (iso < todayIso()) return; // past dates are view-only — the dot still shows what ran that day
    if (draftDates.has(iso)) draftDates.delete(iso); else draftDates.add(iso);
    renderStories(); renderPlan(); renderCalendarMonth();
  });
  $('manual-upload-input').addEventListener('change',(event)=>{
    const file = event.target.files[0], iso = pendingUploadDate, hora = pendingUploadHora;
    pendingUploadDate = null; event.target.value = '';
    if (file && iso) uploadManualImage(iso, hora, file);
  });
  $('edit-ritmo').addEventListener('click',openRitmoDialog);
  $('close-ritmo').addEventListener('click',()=>$('ritmo-dialog').close());
  $('close-ritmo-2').addEventListener('click',()=>$('ritmo-dialog').close());
  $('generate-weekly').addEventListener('click',generateWeeklyNow);
  $('save-ritmo-detail').addEventListener('click',saveRitmoDetail);
  $('ritmo-days-detail').addEventListener('input',refreshRitmoDetailTotal);
  $('ig-preview-close').addEventListener('click',()=>$('ig-preview-dialog').close());
  $('ig-preview-prev').addEventListener('click',previewPrev);
  $('ig-preview-next').addEventListener('click',previewNext);
  $('ritmo-chips').addEventListener('click',(event)=>{
    const chip = event.target.closest('[data-day]'); if (!chip) return;
    toggleRitmoDay(Number(chip.dataset.day));
  });
  $('qa-focus').addEventListener('click',()=>{$('content-panel').classList.remove('collapsed'); setFieldMode('description',true); $('business-description').scrollIntoView({behavior:'smooth',block:'center'}); $('business-description').focus();});
  $('stories').addEventListener('click',(event)=>{
    const scheduleBtn=event.target.closest('[data-schedule-date]');
    if (scheduleBtn) { scheduleDay(scheduleBtn.dataset.scheduleDate); return; }
    const publishNowBtn=event.target.closest('[data-publish-now-date]');
    if (publishNowBtn) { publishDayNow(publishNowBtn.dataset.publishNowDate); return; }
    const addCard=event.target.closest('[data-add-date]');
    if (addCard) {
      pendingUploadDate=addCard.dataset.addDate;
      const timeInput=document.querySelector(`.day-time[data-time-for="${CSS.escape(pendingUploadDate)}"]`);
      pendingUploadHora=timeInput ? timeInput.value : '09:00';
      $('manual-upload-input').click();
      return;
    }
    const action=event.target.closest('[data-action]'),card=event.target.closest('[data-story-id]');
    if (!card) return;
    const found=findStory(card.dataset.storyId); if(!found) return;
    if (action) {
      if(action.dataset.action==='preview')openPreview([found.story]);
      if(storyIsLocked(found.story)) return;
      if(action.dataset.action==='edit')openStory(found.story,found.group);
      if(action.dataset.action==='delete')deleteStory(found.group,found.story);
      return;
    }
    if (storyIsLocked(found.story)) return;
    toggleApproval(found.story);
  });
  let draggedStoryId = null;
  let draggedStoryGroupId = null;
  $('stories').addEventListener('dragstart',(event)=>{
    const card=event.target.closest('.story[data-story-id]'); if(!card)return;
    const found=findStory(card.dataset.storyId);
    if(card.dataset.reorderable!=='true' || !found || storyIsLocked(found.story)) { event.preventDefault(); return; }
    draggedStoryId=card.dataset.storyId;
    draggedStoryGroupId=card.dataset.storyGroupId;
    event.dataTransfer.effectAllowed='move';
    card.classList.add('dragging');
  });
  $('stories').addEventListener('dragend',(event)=>{
    const card=event.target.closest('.story[data-story-id]'); if(card)card.classList.remove('dragging');
    document.querySelectorAll('#stories .drag-over').forEach((el)=>el.classList.remove('drag-over'));
    draggedStoryId=null;
    draggedStoryGroupId=null;
  });
  $('stories').addEventListener('dragover',(event)=>{
    const card=event.target.closest('.story[data-story-id]'); if(!card||!draggedStoryId)return;
    if (card.dataset.reorderable !== 'true') return;
    if (card.dataset.storyGroupId !== draggedStoryGroupId) return;
    event.preventDefault();
    if (card.dataset.storyId!==draggedStoryId) card.classList.add('drag-over');
  });
  $('stories').addEventListener('dragleave',(event)=>{
    const card=event.target.closest('.story[data-story-id]'); if(card)card.classList.remove('drag-over');
  });
  $('stories').addEventListener('drop',(event)=>{
    const card=event.target.closest('.story[data-story-id]'); if(!card||!draggedStoryId)return;
    if (card.dataset.reorderable !== 'true') return;
    if (card.dataset.storyGroupId !== draggedStoryGroupId) return;
    event.preventDefault(); card.classList.remove('drag-over');
    const targetId=card.dataset.storyId; if(targetId===draggedStoryId)return;
    const found=findStory(draggedStoryId); if(found && !storyIsLocked(found.story))reorderStory(found.group,draggedStoryId,targetId);
  });
  $('stories').addEventListener('change',(event)=>{
    const timeInput=event.target.closest('[data-time-for]');
    if (timeInput) { updateDayTime(timeInput.dataset.timeFor, timeInput.value, timeInput); return; }
    const descInput=event.target.closest('[data-desc-for]');
    if (descInput) updateDayDescription(descInput.dataset.descFor, descInput.value.trim());
  });
  $('plan-days').addEventListener('click',(event)=>{
    const retryBtn = event.target.closest('[data-plan-retry]');
    if (retryBtn) { event.stopPropagation(); publishDayNow(retryBtn.dataset.planRetry); return; }
    const deleteBtn = event.target.closest('[data-plan-delete]');
    if (deleteBtn) { event.stopPropagation(); deletePlanDay(deleteBtn.dataset.planDelete); return; }
    const chip = event.target.closest('[data-plan-date]'); if (!chip) return;
    const entry = planItemsByDate().get(chip.dataset.planDate);
    if (entry && entry.stories.length) openPreview([...entry.stories].sort((a,b) => a.order - b.order));
  });
  $('save-story').addEventListener('click',saveStory); $('close-story').addEventListener('click',()=>$('story-dialog').close()); $('cancel-story-edit').addEventListener('click',()=>$('story-dialog').close());
  $('generate-ai-text').addEventListener('click',generateStoryText);
  $('story-font').addEventListener('change',saveStoryFont);
  $('ig-preview-edit').addEventListener('click',editPreviewStory);
  $('ig-preview-delete').addEventListener('click',deletePreviewStory);
  $('toast-close').addEventListener('click',clearMessage);
  loadMeAndTeams().finally(loadClients);
})();
