// Client panel (/app) — "Tu semana". a81.
// Every request relies on the HTTP-only client_session cookie: the server
// takes the client from the session, so this file never sends who the client is.
(() => {
  'use strict';

  const $ = (id) => document.getElementById(id);
  const CHECK_ICON = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M20 6 9 17l-5-5"/></svg>';
  const LOCKED = new Set(['generando', 'publicando', 'publicado', 'cancelada']);
  const state = {week: null, busy: false};

  function escapeHtml(value) {
    return String(value ?? '').replace(/[&<>"']/g, (ch) => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[ch]));
  }

  async function api(path, options = {}) {
    const response = await fetch(path, {credentials: 'same-origin', headers: {'Content-Type': 'application/json'}, ...options});
    if (response.status === 401) { window.location.assign('/app'); throw new Error('Tu sesión terminó.'); }
    if (!response.ok) {
      let detail = 'No se pudo completar. Probá de nuevo en un rato.';
      try {
        const body = await response.json();
        if (typeof body.detail === 'string') detail = body.detail;
        else if (Array.isArray(body.detail) && body.detail[0]?.msg) detail = String(body.detail[0].msg).replace(/^Value error, /, '');
      } catch (_) { /* no JSON body */ }
      throw new Error(detail);
    }
    return response.json();
  }

  let toastTimer = null;
  function showMessage(text, error = false) {
    const toast = $('toast');
    toast.textContent = text;
    toast.className = `toast${error ? ' error' : ''}`;
    toast.setAttribute('role', error ? 'alert' : 'status');
    if (toastTimer) clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { toast.className = 'toast hidden'; }, error ? 7000 : 3500);
  }

  // "2026-11-16" -> "Lunes 16 de noviembre". Built from the parts in UTC so the
  // browser's time zone can never move the date by one day.
  function formatDay(isoDate) {
    const [year, month, day] = String(isoDate).split('-').map(Number);
    const date = new Date(Date.UTC(year, month - 1, day));
    const weekday = new Intl.DateTimeFormat('es-AR', {weekday: 'long', timeZone: 'UTC'}).format(date);
    const rest = new Intl.DateTimeFormat('es-AR', {day: 'numeric', month: 'long', timeZone: 'UTC'}).format(date);
    return `${weekday.charAt(0).toUpperCase()}${weekday.slice(1)} ${rest}`;
  }

  function initials(name) {
    return String(name || '?').split(/\s+/).filter(Boolean).slice(0, 2).map((part) => part[0]).join('').toUpperCase();
  }

  function renderHeader(cliente) {
    const avatar = $('client-avatar');
    const picture = cliente.avatar_url || cliente.logo_url;
    avatar.textContent = '';
    if (picture) {
      const img = document.createElement('img');
      img.src = picture; img.alt = '';
      avatar.appendChild(img);
    } else {
      avatar.textContent = initials(cliente.nombre);
    }
    $('client-name').textContent = cliente.nombre || '';
  }

  function pendingCount(days) {
    return days.reduce((total, day) => total + day.historias.filter((story) => !LOCKED.has(story.estado) && !story.aprobado).length, 0);
  }

  function renderStatus(days) {
    const status = $('week-status');
    status.className = 'status';
    if (!days.length) {
      status.textContent = 'Todavía no hay historias para esta semana. Se preparan los viernes a las 18:00.';
      return;
    }
    const pending = pendingCount(days);
    if (pending) {
      status.textContent = pending === 1 ? 'Falta aprobar 1 historia' : `Faltan aprobar ${pending} historias`;
    } else {
      status.className = 'status ok';
      status.innerHTML = `${CHECK_ICON}<span>Todo aprobado</span>`;
    }
  }

  function renderFocus(week) {
    $('week-focus').value = week.enfoque || '';
    $('week-format').innerHTML = '<option value="">Automático</option>' + week.formatos.map((f) => `<option value="${escapeHtml(f.key)}" title="${escapeHtml(f.hint)}">${escapeHtml(f.label)}</option>`).join('');
    $('week-format').value = week.formatos.some((f) => f.key === week.formato) ? week.formato : '';
  }

  function storyHtml(story) {
    const image = story.imagen ? `<img src="${escapeHtml(story.imagen)}" alt="" loading="lazy">` : '';
    let action;
    if (story.estado === 'publicado') action = '<span class="tag">Publicada</span>';
    else if (LOCKED.has(story.estado)) action = '<span class="tag">Publicando</span>';
    else if (story.aprobado) action = `<button type="button" class="btn approved" data-story="${escapeHtml(story.id)}" data-approve="false" aria-pressed="true">${CHECK_ICON}Aprobada</button>`;
    else action = `<button type="button" class="btn" data-story="${escapeHtml(story.id)}" data-approve="true" aria-pressed="false">Aprobar</button>`;
    return `<article class="story"><div class="story-img">${image}</div><p class="story-text">${escapeHtml(story.texto)}</p>${action}</article>`;
  }

  function dayHtml(day) {
    const actionable = day.historias.some((story) => !LOCKED.has(story.estado));
    const title = day.hora ? `${formatDay(day.fecha)} · ${day.hora} hs` : formatDay(day.fecha);
    const button = !actionable ? ''
      : day.aprobado
        ? `<button type="button" class="btn" data-day="${escapeHtml(day.fecha)}" data-approve="false">Quitar aprobación</button>`
        : `<button type="button" class="btn primary" data-day="${escapeHtml(day.fecha)}" data-approve="true">Aprobar día</button>`;
    return `<section class="card day"><div class="day-head"><h2 class="day-title">${escapeHtml(title)}</h2>${button}</div><div class="stories">${day.historias.map(storyHtml).join('')}</div></section>`;
  }

  function renderDays(days) {
    $('days').innerHTML = days.length
      ? days.map(dayHtml).join('')
      : '<div class="card empty">Cuando tengamos tus historias listas, las vas a ver acá para aprobarlas.</div>';
  }

  function render() {
    const week = state.week;
    renderHeader(week.cliente);
    renderStatus(week.dias);
    renderDays(week.dias);
  }

  async function loadWeek() {
    try {
      state.week = await api('/app/api/semana');
      renderFocus(state.week);
      render();
    } catch (error) {
      $('days').innerHTML = `<div class="error-box" role="alert">${escapeHtml('No pudimos cargar tu semana. Revisá tu conexión y recargá la página.')}</div>`;
    }
  }

  async function saveWeek(event) {
    event.preventDefault();
    const button = $('save-week');
    button.disabled = true;
    try {
      const saved = await api('/app/api/semana', {method: 'PATCH', body: JSON.stringify({enfoque: $('week-focus').value, formato: $('week-format').value || null})});
      if (state.week) { state.week.enfoque = saved.enfoque; state.week.formato = saved.formato; }
      $('week-focus').value = saved.enfoque || '';
      showMessage(saved.enfoque ? 'Listo, lo tenemos en cuenta para tus próximas historias.' : 'Listo, elegimos el tema por vos.');
    } catch (error) {
      showMessage(error.message, true);
    } finally {
      button.disabled = false;
    }
  }

  function findStory(id) {
    for (const day of state.week.dias) {
      const story = day.historias.find((item) => item.id === id);
      if (story) return {day, story};
    }
    return null;
  }

  function refreshDayApproval(day) {
    day.aprobado = day.historias.every((story) => LOCKED.has(story.estado) || story.aprobado);
  }

  async function onDaysClick(event) {
    const button = event.target.closest('button[data-approve]');
    if (!button || state.busy || !state.week) return;
    const approve = button.dataset.approve === 'true';
    state.busy = true;
    button.disabled = true;
    try {
      if (button.dataset.story) {
        const saved = await api(`/app/api/historias/${encodeURIComponent(button.dataset.story)}/aprobar`, {method: 'PATCH', body: JSON.stringify({aprobado: approve})});
        const found = findStory(button.dataset.story);
        if (found) { found.story.aprobado = saved.aprobado; refreshDayApproval(found.day); }
      } else if (button.dataset.day) {
        await api(`/app/api/dias/${encodeURIComponent(button.dataset.day)}/aprobar`, {method: 'PATCH', body: JSON.stringify({aprobado: approve})});
        const day = state.week.dias.find((item) => item.fecha === button.dataset.day);
        if (day) {
          day.historias.forEach((story) => { if (!LOCKED.has(story.estado)) story.aprobado = approve; });
          refreshDayApproval(day);
        }
        showMessage(approve ? 'Día aprobado.' : 'Quitaste la aprobación del día.');
      }
      render();
    } catch (error) {
      showMessage(error.message, true);
      button.disabled = false;
    } finally {
      state.busy = false;
    }
  }

  $('focus-form').addEventListener('submit', saveWeek);
  $('days').addEventListener('click', onDaysClick);
  loadWeek();
})();
