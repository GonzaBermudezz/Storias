"""Console frontend for the CM with AI: assertions on the static files and the template."""
from __future__ import annotations

import re
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app

PAGE = Path("templates/portal.html")
CM_JS = Path("static/portal-cm.js")


def _page() -> str:
    return PAGE.read_text(encoding="utf-8")


def _portal_js() -> str:
    return TestClient(app).get("/static/portal.js").text


def _cm_js() -> str:
    response = TestClient(app).get("/static/portal-cm.js")
    assert response.status_code == 200
    return response.text


def test_cm_script_loads_after_portal_js():
    page = _page()
    first = page.index('<script src="/static/portal.js" defer></script>')
    second = page.index('<script src="/static/portal-cm.js" defer></script>')
    assert first < second


def test_cm_sidebar_entry_is_hidden_until_the_agency_has_the_cm():
    page = _page()
    assert 'data-client-tab="cm"' in page
    entry = re.search(r'<span[^>]*id="nav-cm"[^>]*>', page).group(0)
    assert 'data-client-tab="cm"' in entry and "hidden" in entry
    assert "CM con IA" in page.split('id="nav-cm"', 1)[1].split("</span></span>", 1)[0]
    assert 'id="tab-cm" data-tab="cm"' in page


def test_cm_tab_layout_has_list_chat_and_composer():
    page = _page()
    tab = page.split('id="tab-cm"', 1)[1].split('id="tab-fotos"', 1)[0]
    for element in ("cm-list", "cm-new-title", "cm-new", "cm-messages", "cm-form", "cm-input", "cm-count", "cm-send", "cm-status"):
        assert f'id="{element}"' in tab
    assert 'maxlength="2000"' in tab.split('id="cm-input"', 1)[1].split(">", 1)[0]


def test_new_html_and_css_do_not_use_template_syntax():
    page = _page()
    cm_block = page.split('id="tab-cm"', 1)[1].split('id="tab-fotos"', 1)[0]
    assert "{{" not in cm_block and "{%" not in cm_block and "{#" not in cm_block
    css = page.split(".cm-layout", 1)[1].split("</style>", 1)[0]
    assert "{{" not in css and "{%" not in css and "{#" not in css
    sizes = [float(size) for size in re.findall(r"font-size:([\d.]+)px", page.split("/* CM con IA */", 1)[1].split("</style>", 1)[0])]
    assert sizes and min(sizes) >= 11


def test_portal_js_knows_the_cm_tab_and_hides_it_when_disabled():
    source = _portal_js()
    assert "const CLIENT_TABS = ['semana', 'marca', 'fotos', 'historial', 'ajustes', 'cm']" in source
    assert "me?.cm_enabled === true" in source
    assert "$('nav-cm').classList.toggle('hidden', !state.cmEnabled)" in source
    assert "if (tab === 'cm' && !state.cmEnabled) tab = 'semana'" in source


def test_portal_js_hooks_the_cm_module_through_a_small_host():
    source = _portal_js()
    assert "window.StoriasPortalHost = {api, showMessage, refreshClient: refreshClientAfterCm}" in source
    assert "window.StoriasCM?.setClient(" in source
    assert "window.StoriasCM?.show()" in source
    assert "window.StoriasCM?.prefill(" in source


def test_marca_swaps_the_brand_panel_for_a_cm_card_only_when_enabled():
    page, source = _page(), _portal_js()
    assert 'id="brand-cm"' in page and 'id="brand-cm-open"' in page
    assert 'id="brand-ai"' in page  # the old panel stays in the page
    assert "$('brand-ai').classList.toggle('hidden', state.cmEnabled)" in source
    assert "$('brand-cm').classList.toggle('hidden', !state.cmEnabled)" in source
    assert "$('brand-cm-open').addEventListener('click', openCmForBrand)" in source
    assert "a partir de su web" in source
    goto = source.split("function goToSetup(key) {", 1)[1].split("\n", 3)[2]
    assert "if (state.cmEnabled) { openCmForBrand(); return; }" in goto


def test_cm_module_talks_to_the_backend_endpoints():
    source = _cm_js()
    assert "/cm/conversaciones" in source
    assert "/portal/cm/conversaciones/" in source
    assert "/mensajes" in source
    assert "/portal/cm/acciones/" in source
    assert "/aprobar" in source and "/descartar" in source
    assert "window.StoriasCM = {" in source
    for method in ("setClient", "show", "prefill"):
        assert method in source.split("window.StoriasCM = {", 1)[1]


def test_cm_module_never_renders_model_or_user_text_as_html():
    source = _cm_js()
    assert "innerHTML" not in source
    assert "insertAdjacentHTML" not in source
    assert "outerHTML" not in source
    assert "document.write" not in source
    assert "textContent" in source


def test_cm_module_composer_behaviour():
    source = _cm_js()
    assert "MAX_CHARS = 2000" in source
    assert "event.key === 'Enter' && !event.shiftKey" in source
    assert "El CM está pensando…" in source
    assert "segundos" in source or "elapsed" in source
    assert "El CM llegó al límite de este mensaje; podés pedirle que siga." in source
    assert "limite" in source and "agotado" in source
    assert "showMessage(" in source


def test_cm_module_renders_proposal_cards_with_states_and_actions():
    source = _cm_js()
    for label in ("Antes", "Propuesta", "Aprobar", "Descartar"):
        assert label in source
    for state in ("pending", "approved", "discarded", "stale", "failed"):
        assert state in source
    for label in ("Pendiente", "Aprobada", "Descartada", "Desactualizada", "Falló"):
        assert label in source
    for kind in ("descripcion", "temas", "ejemplo_tono", "enfoque_semana", "texto_historia"):
        assert kind in source
    assert "refreshClient" in source
    assert "**" in source  # simple bold rendering


def test_cm_module_is_scoped_to_the_selected_client():
    source = _cm_js()
    assert "clientId" in source
    assert "requestVersion" in source or "version" in source


def test_cm_create_and_send_invalidate_a_late_auto_open():
    source = _cm_js()
    create = source.split("async function createConversation(", 1)[1].split("async function onNewConversation", 1)[0]
    assert "st.openToken += 1" in create
    send = source.split("async function send()", 1)[1].split("// ── Public hooks", 1)[0]
    assert "st.openToken += 1" in send.split("setBusy(true)", 1)[0] + send.split("setBusy(true)", 1)[1][:60]


def test_portal_js_does_not_reset_the_cm_when_the_same_client_is_reselected():
    source = _portal_js()
    select = source.split("async function selectClient(clientId) {", 1)[1].split("showClientTabContent(state.clientTab)", 1)[0]
    assert "clientId !== state.selectedClientId" in select
    assert "window.StoriasCM?.setClient(null)" in select
    assert "if (clientId !== state.selectedClientId) window.StoriasCM?.setClient(null)" in select


def test_cm_decide_errors_do_not_clobber_an_in_flight_send():
    source = _cm_js()
    decide = source.split("async function decide(", 1)[1].split("// ── Chat", 1)[0]
    assert "!st.sending &&" in decide.split("catch (error)", 1)[1]
    assert "st.sending" in decide.split("try {", 1)[0]  # buttons stay disabled while sending is checked up front


def test_refresh_after_cm_keeps_unsaved_brand_edits():
    source = _portal_js()
    refresh = source.split("async function refreshClientAfterCm() {", 1)[1].split("window.StoriasPortalHost", 1)[0]
    assert "selectionVersion" in refresh
    assert "unsavedBrandFields" in refresh
    assert "business-description" in source.split("function unsavedBrandFields", 1)[1][:600]


def test_cm_enter_ignores_ime_composition_keycode():
    assert "event.keyCode !== 229" in _cm_js()


def test_cm_live_region_is_dedicated_not_the_whole_chat():
    page, source = _page(), _cm_js()
    assert 'id="cm-messages" aria-live' not in page
    assert 'id="cm-list" aria-live' not in page
    assert 'id="cm-live"' in page
    assert "$('cm-live')" in source


def test_cm_history_puts_each_card_after_the_assistant_message_of_its_turn():
    source = _cm_js()
    merge = source.split("function mergeItems(", 1)[1].split("async function loadList", 1)[0]
    assert "rol === 'assistant'" in merge


def test_portal_js_loads_the_cm_when_the_page_opens_on_the_cm_tab():
    # The stored tab can be 'cm' on reload: the tab is shown before the client loads,
    # so once the client arrives portal.js must ask the CM to show (like Fotos does).
    js = Path("static/portal.js").read_text(encoding="utf-8")
    after = js.split("window.StoriasCM?.setClient(client);", 1)[1][:400]
    assert "if (state.clientTab === 'cm') window.StoriasCM?.show();" in after
