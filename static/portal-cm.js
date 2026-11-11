// CM con IA: conversation list, chat and proposal cards for the client console.
// Every string that comes from the model, the team or the database is put on the page
// with textContent / createTextNode, never as markup.
(() => {
  'use strict';
  const MAX_CHARS = 2000;
  const $ = (id) => document.getElementById(id);
  const host = () => window.StoriasPortalHost;
  const SILENT_ERRORS = ['Acceso denegado', 'Sesión vencida'];
  const LIMIT_NOTE = 'El CM llegó al límite de este mensaje; podés pedirle que siga.';

  const TYPE_LABELS = {
    descripcion: 'Descripción del negocio',
    temas: 'Temas',
    ejemplo_tono: 'Ejemplo de tono',
    enfoque_semana: 'Enfoque de la semana',
    texto_historia: 'Texto de una historia',
  };
  const STATE_LABELS = {
    pending: 'Pendiente', applying: 'Aplicando…', approved: 'Aprobada',
    discarded: 'Descartada', stale: 'Desactualizada', failed: 'Falló',
  };

  // version changes whenever the selected client changes; late answers for an old client are dropped.
  const st = {
    clientId: null, version: 0, openToken: 0, conversations: [], activeId: null, conversation: null,
    items: [], listLoadedFor: null, draftNew: false, sending: false, timer: null, started: 0,
  };

  const enc = encodeURIComponent;
  const api = (path, options) => host().api(path, options);
  const notify = (text, error = false) => host().showMessage(text, error);
  const fail = (error) => { if (!SILENT_ERRORS.includes(error.message)) notify(error.message, true); };
  const isVisible = () => !$('tab-cm').classList.contains('hidden');

  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined && text !== null) node.textContent = text;
    return node;
  }

  function formatDate(value) {
    const date = value ? new Date(value) : null;
    if (!date || Number.isNaN(date.getTime())) return '';
    return date.toLocaleString('es-AR', {day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit'});
  }

  // ── Safe rich text: **bold** and "- " bullets, built from DOM nodes ──────────────
  function appendInline(parent, text) {
    String(text).split(/(\*\*[^*]+?\*\*)/g).forEach((part) => {
      if (!part) return;
      if (part.length > 4 && part.startsWith('**') && part.endsWith('**')) parent.appendChild(el('strong', '', part.slice(2, -2)));
      else parent.appendChild(document.createTextNode(part));
    });
  }
  function renderRich(container, text) {
    const lines = String(text || '').replace(/\r\n?/g, '\n').split('\n');
    let paragraph = [], list = null;
    const flushParagraph = () => {
      if (!paragraph.length) return;
      const p = el('p');
      appendInline(p, paragraph.join('\n'));
      container.appendChild(p);
      paragraph = [];
    };
    lines.forEach((line) => {
      const bullet = /^\s*[-*]\s+(.*)$/.exec(line);
      if (bullet) {
        flushParagraph();
        if (!list) { list = el('ul'); container.appendChild(list); }
        const li = el('li');
        appendInline(li, bullet[1]);
        list.appendChild(li);
        return;
      }
      list = null;
      if (!line.trim()) { flushParagraph(); return; }
      paragraph.push(line);
    });
    flushParagraph();
  }

  // ── Proposal cards ───────────────────────────────────────────────────────────────
  function listNode(values) {
    const ul = el('ul', 'cm-diff-list');
    values.forEach((value) => ul.appendChild(el('li', '', value)));
    return ul;
  }
  function column(label, content, isNew) {
    const col = el('div', `cm-diff-col${isNew ? ' is-new' : ''}`);
    col.appendChild(el('span', 'cm-diff-label', label));
    if (content.length === 0) col.appendChild(el('span', 'cm-diff-empty', 'Sin valor'));
    content.forEach((part) => {
      if (Array.isArray(part)) col.appendChild(listNode(part));
      else col.appendChild(el('span', 'cm-diff-text', part));
    });
    return col;
  }
  function textOrEmpty(value) {
    const text = String(value ?? '').trim();
    return text ? [text] : [];
  }
  // before/after of an action as column contents: strings are text blocks, arrays are bullet lists.
  function diffOf(action) {
    const before = action.antes || {}, proposal = action.propuesta || {};
    switch (action.tipo) {
      case 'descripcion':
        return {before: textOrEmpty(before.business_description), after: textOrEmpty(proposal.descripcion)};
      case 'temas':
        return {before: (before.topics || []).length ? [before.topics.map(String)] : [], after: (proposal.temas || []).length ? [proposal.temas.map(String)] : []};
      case 'ejemplo_tono': {
        const current = Array.isArray(before.tone_examples) ? before.tone_examples.length : 0;
        return {
          before: [current ? `${current} ejemplo${current === 1 ? '' : 's'} guardado${current === 1 ? '' : 's'}` : 'Todavía no hay ejemplos'],
          after: ['Se suma como ejemplo nuevo:', (proposal.historias || []).map(String)],
        };
      }
      case 'enfoque_semana':
        return {
          before: [`Enfoque: ${before.weekly_focus || 'sin enfoque'}`, `Formato: ${before.weekly_format ? String(before.weekly_format).replace(/_/g, ' ') : 'automático'}`],
          after: [`Enfoque: ${proposal.enfoque || 'sin cambios'}`, `Formato: ${proposal.formato ? String(proposal.formato).replace(/_/g, ' ') : 'sin cambios'}`],
        };
      case 'texto_historia':
        return {before: textOrEmpty(before.text), after: textOrEmpty(proposal.texto)};
      default:
        return {before: [], after: []};
    }
  }
  function renderCard(action) {
    const card = el('div', 'cm-card');
    const head = el('div', 'cm-card-head');
    head.appendChild(el('span', 'cm-card-title', TYPE_LABELS[action.tipo] || 'Propuesta'));
    head.appendChild(el('span', `cm-state is-${action.estado}`, STATE_LABELS[action.estado] || String(action.estado || '')));
    card.appendChild(head);
    if (action.motivo) card.appendChild(el('p', 'cm-card-motivo', action.motivo));
    const diff = diffOf(action);
    const grid = el('div', 'cm-diff');
    grid.appendChild(column('Antes', diff.before, false));
    grid.appendChild(column('Propuesta', diff.after, true));
    card.appendChild(grid);
    if (action.error) card.appendChild(el('p', 'cm-card-error', action.error));
    if (action.estado === 'pending') {
      const actions = el('div', 'cm-card-actions');
      const approve = el('button', 'btn primary', 'Aprobar');
      const discard = el('button', 'btn', 'Descartar');
      approve.type = 'button'; discard.type = 'button';
      approve.addEventListener('click', () => decide(action, '/aprobar', [approve, discard]));
      discard.addEventListener('click', () => decide(action, '/descartar', [approve, discard]));
      actions.appendChild(approve); actions.appendChild(discard);
      card.appendChild(actions);
    }
    return card;
  }

  async function decide(action, verb, buttons) {
    if (st.sending) { notify('Esperá a que el CM termine de responder.', true); return; }
    const version = st.version, convId = st.activeId;
    buttons.forEach((button) => { button.disabled = true; });
    try {
      const result = await api(`/portal/cm/acciones/${enc(action.id)}${verb}`, {method: 'POST'});
      if (version !== st.version) return;
      const updated = result?.accion;
      if (updated) st.items.forEach((item) => { if (item.type === 'card' && item.action.id === action.id) item.action = updated; });
      renderChat(false);
      if (verb === '/aprobar') {
        notify('Cambio aplicado.');
        host().refreshClient();
      } else {
        notify('Propuesta descartada.');
      }
    } catch (error) {
      fail(error);
      // The row may have moved on (stale, failed, decided elsewhere): show what the server says now.
      if (!st.sending && version === st.version && convId === st.activeId) await openConversation(convId, {quiet: true});
    }
  }

  // ── Chat ─────────────────────────────────────────────────────────────────────────
  function renderChat(scrollToEnd) {
    const box = $('cm-messages');
    const previousTop = box.scrollTop;
    box.replaceChildren();
    if (!st.activeId && !st.items.length) {
      box.appendChild(el('p', 'cm-placeholder', st.draftNew
        ? 'Conversación nueva: escribí tu mensaje abajo para empezar.'
        : 'Elegí una conversación o empezá una nueva. Pedile al CM que revise la marca, la semana o las fotos.'));
    } else if (!st.items.length) {
      box.appendChild(el('p', 'cm-placeholder', 'Todavía no hay mensajes. Escribile al CM abajo.'));
    }
    st.items.forEach((item) => {
      if (item.type === 'card') { box.appendChild(renderCard(item.action)); return; }
      if (item.type === 'notice') { box.appendChild(el('p', 'cm-notice', item.texto)); return; }
      const mine = item.rol === 'user';
      const wrap = el('div', `cm-msg ${mine ? 'is-user' : 'is-cm'}`);
      wrap.appendChild(el('span', 'cm-who', `${mine ? 'Vos' : 'CM'}${item.created_at ? ` · ${formatDate(item.created_at)}` : ''}`));
      const bubble = el('div', 'cm-bubble');
      renderRich(bubble, item.texto);
      wrap.appendChild(bubble);
      box.appendChild(wrap);
    });
    box.scrollTop = scrollToEnd ? box.scrollHeight : previousTop;
    const head = $('cm-chat-head');
    head.replaceChildren();
    const conv = st.conversation;
    head.appendChild(el('span', '', conv ? (conv.titulo || 'Conversación sin título') : 'Conversación nueva'));
    if (conv && conv.cerrada) head.appendChild(el('span', 'cm-state', 'Cerrada'));
    updateComposer();
  }

  function renderList() {
    const list = $('cm-list');
    list.replaceChildren();
    if (!st.conversations.length) {
      list.appendChild(el('p', 'cm-empty', 'Todavía no hay conversaciones con el CM para este cliente.'));
      return;
    }
    st.conversations.forEach((conv) => {
      const button = el('button', `cm-item${conv.id === st.activeId ? ' active' : ''}`);
      button.type = 'button';
      button.disabled = st.sending;
      button.appendChild(el('span', 'cm-item-title', (conv.titulo || 'Conversación sin título') + (conv.cerrada ? ' (cerrada)' : '')));
      button.appendChild(el('span', 'cm-item-date', formatDate(conv.updated_at || conv.created_at)));
      button.addEventListener('click', () => { if (!st.sending && conv.id !== st.activeId) openConversation(conv.id); });
      list.appendChild(button);
    });
  }

  function updateComposer() {
    const input = $('cm-input');
    const length = input.value.length;
    $('cm-count').textContent = String(length);
    const closed = Boolean(st.conversation && st.conversation.cerrada);
    input.disabled = st.sending || !st.clientId;
    $('cm-send').disabled = st.sending || !st.clientId || closed || !input.value.trim();
    $('cm-new').disabled = st.sending || !st.clientId;
    $('cm-new-title').disabled = st.sending || !st.clientId;
  }

  // Small dedicated live region: announces only the new CM reply, not the whole chat.
  function announce(result) {
    const replies = (result.mensajes_nuevos || []).filter((m) => m.rol === 'assistant').map((m) => m.texto);
    const proposals = (result.propuestas || []).length;
    const parts = [];
    if (replies.length) parts.push(`El CM respondió: ${String(replies[replies.length - 1]).slice(0, 300)}`);
    if (proposals) parts.push(`${proposals} propuesta${proposals === 1 ? '' : 's'} para revisar.`);
    $('cm-live').textContent = parts.join(' ');
  }

  function setBusy(busy) {
    st.sending = busy;
    const status = $('cm-status');
    if (st.timer) { clearInterval(st.timer); st.timer = null; }
    if (busy) {
      st.started = Date.now();
      const tick = () => {
        const elapsed = Math.floor((Date.now() - st.started) / 1000);
        status.textContent = `El CM está pensando… (${elapsed} s)`;
      };
      tick();
      st.timer = setInterval(tick, 1000);
      status.classList.remove('hidden');
    } else {
      status.textContent = '';
      status.classList.add('hidden');
    }
    renderList();
    updateComposer();
  }

  // Each proposal card goes right after the assistant message of the turn that created it
  // (the assistant row is saved after the action rows, so created_at alone would put it before).
  function mergeItems(messages, actions) {
    const msgs = (messages || []).map((m) => ({type: 'msg', rol: m.rol, texto: m.texto, created_at: m.created_at, time: Date.parse(m.created_at) || 0}));
    const cards = (actions || []).map((a, index) => ({type: 'card', action: a, time: Date.parse(a.created_at) || 0, index}))
      .sort((a, b) => a.time - b.time || a.index - b.index);
    const items = [];
    msgs.forEach((msg) => {
      items.push(msg);
      if (msg.rol === 'assistant') {
        while (cards.length && cards[0].time <= msg.time) items.push(cards.shift());
      }
    });
    return items.concat(cards);
  }

  async function loadList() {
    const clientId = st.clientId, version = st.version;
    if (!clientId) return;
    try {
      const list = await api(`/portal/clientes/${enc(clientId)}/cm/conversaciones`);
      if (version !== st.version) return;
      st.conversations = Array.isArray(list) ? list : [];
      st.listLoadedFor = clientId;
      renderList();
      if (!st.activeId && !st.draftNew && st.conversations.length) await openConversation(st.conversations[0].id);
      else renderChat(false);
    } catch (error) {
      fail(error);
    }
  }

  async function openConversation(id, {quiet = false} = {}) {
    const version = st.version, token = ++st.openToken;
    try {
      const data = await api(`/portal/cm/conversaciones/${enc(id)}`);
      if (version !== st.version || token !== st.openToken) return;
      st.activeId = id; st.draftNew = false;
      st.conversation = data.conversacion || null;
      st.items = mergeItems(data.mensajes, data.acciones);
      if (st.conversation) st.conversations = st.conversations.map((c) => (c.id === id ? {...c, ...st.conversation} : c));
      renderList();
      renderChat(!quiet);
    } catch (error) {
      fail(error);
    }
  }

  async function createConversation({force = false} = {}) {
    const version = st.version, clientId = st.clientId;
    if (!clientId || (st.sending && !force)) return null;
    st.openToken += 1;
    const title = $('cm-new-title').value.trim();
    const conv = await api(`/portal/clientes/${enc(clientId)}/cm/conversaciones`, {method: 'POST', body: JSON.stringify({titulo: title || null})});
    if (version !== st.version) return null;
    $('cm-new-title').value = '';
    st.conversations = [conv, ...st.conversations.filter((c) => c.id !== conv.id)];
    st.activeId = conv.id; st.conversation = conv; st.items = []; st.draftNew = false;
    renderList();
    renderChat(true);
    return conv;
  }

  async function onNewConversation(event) {
    event.preventDefault();
    if (st.sending) return;
    try {
      const conv = await createConversation();
      if (conv) $('cm-input').focus();
    } catch (error) {
      fail(error);
    }
  }

  async function send() {
    if (st.sending || !st.clientId) return;
    const input = $('cm-input');
    const text = input.value.trim();
    if (!text) return;
    if (text.length > MAX_CHARS) { notify(`El mensaje puede tener hasta ${MAX_CHARS} caracteres.`, true); return; }
    const version = st.version;
    st.openToken += 1;
    setBusy(true);
    let convId = st.activeId, shown = null;
    try {
      if (!convId) {
        const conv = await createConversation({force: true});
        if (!conv) return;
        convId = conv.id;
      }
      shown = {type: 'msg', rol: 'user', texto: text};
      st.items.push(shown);
      input.value = '';
      renderChat(true);
      const result = await api(`/portal/cm/conversaciones/${enc(convId)}/mensajes`, {method: 'POST', body: JSON.stringify({texto: text})});
      if (version !== st.version || convId !== st.activeId) return;
      (result.mensajes_nuevos || []).forEach((m) => st.items.push({type: 'msg', rol: m.rol, texto: m.texto}));
      (result.propuestas || []).forEach((action) => st.items.push({type: 'card', action}));
      if (result.limite && result.limite.agotado) st.items.push({type: 'notice', texto: LIMIT_NOTE});
      announce(result);
      st.conversations = st.conversations.map((c) => (c.id === convId ? {...c, updated_at: new Date().toISOString()} : c));
    } catch (error) {
      fail(error);
      if (version === st.version && convId && convId === st.activeId) {
        // Show what the server kept; give the text back if it did not keep it.
        await openConversation(convId, {quiet: true});
        const lastUser = [...st.items].reverse().find((item) => item.type === 'msg' && item.rol === 'user');
        if (!input.value && (!lastUser || lastUser.texto.trim() !== text)) input.value = text;
      } else if (shown === null && !input.value) {
        input.value = text;
      }
    } finally {
      if (version === st.version) {
        setBusy(false);
        renderList();
        renderChat(true);
        input.focus();
      }
    }
  }

  // ── Public hooks (called by portal.js) ───────────────────────────────────────────
  function resetState() {
    st.version += 1; st.openToken += 1;
    st.conversations = []; st.activeId = null; st.conversation = null; st.items = [];
    st.listLoadedFor = null; st.draftNew = false;
    if (st.timer) { clearInterval(st.timer); st.timer = null; }
    st.sending = false;
    $('cm-status').classList.add('hidden');
    $('cm-input').value = '';
    $('cm-new-title').value = '';
  }
  function setClient(client) {
    const id = client ? client.id : null;
    if (id === null || id !== st.clientId) {
      resetState();
      st.clientId = id;
      renderList(); renderChat(false);
    }
    if (id && isVisible()) show();
  }
  function show() {
    if (!st.clientId) { updateComposer(); return; }
    if (st.listLoadedFor !== st.clientId) loadList();
    else renderChat(false);
  }
  // Used by the brand card: start a new conversation draft with a suggested message.
  function prefill(text) {
    if (st.sending) return;
    st.activeId = null; st.conversation = null; st.items = []; st.draftNew = true; st.openToken += 1;
    $('cm-input').value = String(text || '').slice(0, MAX_CHARS);
    renderList(); renderChat(false);
    $('cm-input').focus();
  }

  $('cm-new-form').addEventListener('submit', onNewConversation);
  $('cm-form').addEventListener('submit', (event) => { event.preventDefault(); send(); });
  $('cm-input').addEventListener('input', updateComposer);
  $('cm-input').addEventListener('keydown', (event) => {
    if (event.key === 'Enter' && !event.shiftKey && !event.isComposing && event.keyCode !== 229) { event.preventDefault(); send(); }
  });

  window.StoriasCM = {setClient, show, prefill};
  updateComposer();
})();
