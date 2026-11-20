'use strict';
(function () {
  var form = document.getElementById('builder-form');
  if (!form) return;
  var live = document.querySelector('[data-builder-live]');
  var status = document.querySelector('[data-builder-status]');
  var outTheme = document.querySelector('[data-builder-theme]');
  var outTxProfile = document.querySelector('[data-builder-tx-profile]');
  var outPacks = document.querySelector('[data-builder-packs-out]');
  var outCaps = document.querySelector('[data-builder-caps]');
  var outModules = document.querySelector('[data-builder-modules]');
  var outGates = document.querySelector('[data-builder-gates]');
  var flashMeter = document.querySelector('[data-flash-estimate]');
  var flashMeterBar = document.querySelector('.flash-meter');
  var flashFill = document.querySelector('.flash-meter-fill');
  var flashNote = document.querySelector('[data-flash-estimate-note]');
  var buildButton = document.getElementById('build-button');
  // JS is active: require a validated preview before build.
  buildButton.disabled = true;
  var fallback = document.getElementById('fallback-result');
  if (fallback) fallback.hidden = true;
  var timer = null;
  var controller = null;
  var seq = 0;

  function selection() {
    var theme = (form.querySelector('input[name="theme"]:checked') || {}).value || 'flux';
    var packs = Array.prototype.map.call(
      form.querySelectorAll('input[name="pack"]:checked:not(:disabled)'),
      function (el) { return el.value; }
    );
    var txProfile = (form.querySelector('input[name="tx_profile"]:checked') || {}).value || 'ce';
    return { theme: theme, packs: packs, tx_profile: txProfile };
  }

  function renderError(message) {
    live.hidden = false;
    status.textContent = message;
    outTheme.textContent = selection().theme;
    outTxProfile.textContent = '—';
    outPacks.textContent = '—';
    outCaps.textContent = '—';
    outModules.textContent = '—';
    outGates.textContent = '{}';
    flashMeter.textContent = '—';
    flashMeterBar.setAttribute('aria-valuenow', '0');
    flashMeterBar.setAttribute('aria-valuetext', 'Estimate unavailable');
    flashMeter.className = '';
    flashFill.style.width = '0%';
    flashNote.textContent = 'A valid profile estimate is not available for this selection.';
    buildButton.disabled = true;
  }

  function schedule() {
    if (timer) clearTimeout(timer);
    ++seq;
    if (controller) controller.abort();
    buildButton.disabled = true;
    live.hidden = false;
    status.textContent = 'Checking selection…';
    timer = setTimeout(preview, 250);
  }

  async function preview() {
    var sel = selection();
    var mine = ++seq;
    if (controller) controller.abort();
    controller = new AbortController();
    live.hidden = false;
    status.textContent = 'Checking selection…';
    buildButton.disabled = true;
    try {
      var res = await fetch(form.dataset.previewUrl, {
        method: 'POST',
        headers: { Accept: 'application/json' },
        body: new FormData(form),
        signal: controller.signal
      });
      var data = await res.json();
      if (mine !== seq) return; // stale response
      if (data && data.ok) {
        var estimate = data.estimate || {};
        var allowed = estimate.allowed !== false;
        status.textContent = allowed ? 'Selection is valid. Choose Build to start a build.' :
          (estimate.unknown_packs && estimate.unknown_packs.length ?
            'Some selected feature sets have no measured size. Build to check the actual size.' :
            'Estimated image may exceed the 120 KiB limit. You can build to check the actual size.');
        outTheme.textContent = (sel.packs.includes('designer') && nameField.value) || (data.plan && data.plan.theme) || sel.theme;
        outTxProfile.textContent = (data.plan && data.plan.tx_profile) || 'ce';
        outPacks.textContent = (data.plan.packs || []).join(' + ') || 'base';
        outCaps.textContent = (data.plan.capabilities || []).join(', ') || '—';
        outModules.textContent = (data.plan.modules || []).join(', ') || '—';
        outGates.textContent = JSON.stringify(data.gates || {}, null, 2);
        flashMeter.textContent = Number(estimate.used || 0).toLocaleString() + ' / ' + Number(estimate.limit || 122880).toLocaleString() + ' bytes';
        flashMeterBar.setAttribute('aria-valuenow', String(Math.min(122880, estimate.used || 0)));
        flashMeterBar.setAttribute('aria-valuetext', flashMeter.textContent + (allowed ? '' : ', over budget'));
        flashMeterBar.classList.toggle('over-budget', !allowed);
        flashFill.style.width = Math.min(100, estimate.percent || 0) + '%';
        var details = estimate.note || 'Approximate estimate; actual linked size is authoritative.';
        if (estimate.unknown_packs && estimate.unknown_packs.length) {
          details += ' No size delta is available for: ' + estimate.unknown_packs.join(', ') + '.';
        }
        if (!allowed && !(estimate.unknown_packs && estimate.unknown_packs.length)) {
          details += ' Over the 120 KiB hard limit by ' + Math.abs(estimate.remaining).toLocaleString() + ' bytes.';
        }
        if (estimate.base_target_over) {
          details += ' This Base image is ' + Number(estimate.base_target_over_by || 0).toLocaleString() +
            ' bytes above the 96 KiB Base design goal.';
        }
        flashNote.textContent = details;
        buildButton.disabled = false;
      } else {
        renderError((data && data.error) || ('Preview failed (HTTP ' + res.status + ').'));
      }
    } catch (err) {
      if (mine !== seq || (err && err.name === 'AbortError')) return;
      renderError('Preview unavailable: ' + (err && err.message ? err.message : err));
    }
  }

  var designer = form.querySelector('input[name="pack"][value="designer"]');
  var panel = form.querySelector('[data-designer-panel]');
  function syncDesigner() {
    if (!designer || !panel) return;
    panel.hidden = !designer.checked;
    panel.querySelectorAll('input[name="theme"]').forEach(function(el) {el.disabled = !designer.checked;});
  }
  var nameField = form.querySelector('[data-builder-theme-name]');
  var cards = form.querySelector('[data-builder-themes]');
  var draftKey = 'flux-builder-selection-v1';
  function syncThemeName() {
    var current = form.querySelector('input[name="theme"]:checked');
    nameField.value = current ? current.dataset.themeName || '' : '';
  }
  // Preserve older browser-only library entries; named disk themes take precedence.
  try {
    var diskNames = new Set(Array.from(cards.querySelectorAll('[data-saved-theme-card] input')).map(el => el.dataset.themeName));
    var legacy = JSON.parse(localStorage.getItem('flux-theme-library-v1') || '[]');
    legacy.forEach(item => {
      if (!item || typeof item.name !== 'string' || typeof item.token !== 'string' || !/^custom-[01]{2}-[0-9a-f]{16}(?:-|$)/.test(item.token) || diskNames.has(item.name)) return;
      var label = document.createElement('label'); label.className = 'theme-card';
      var input = document.createElement('input'); input.type = 'radio'; input.name = 'theme'; input.value = item.token; input.dataset.themeName = item.name;
      var body = document.createElement('span'); body.className = 'theme-card-body';
      var preview = document.createElement('span'); preview.className = 'theme-preview saved-theme-preview'; preview.textContent = 'CUSTOM';
      var title = document.createElement('strong'); title.textContent = item.name;
      var detail = document.createElement('span'); detail.className = 'theme-desc'; detail.textContent = 'Browser only · save in Theme editor to put on disk';
      body.append(preview, title, detail); label.append(input, body); cards.append(label);
    });
  } catch (_) {}
  function saveDraft() {
    try { sessionStorage.setItem(draftKey, JSON.stringify({...selection(), themeName: nameField.value})); } catch (_) {}
  }
  if (form.dataset.restoreDraft === 'true') {
    try {
      var draft = JSON.parse(sessionStorage.getItem(draftKey) || 'null');
      if (draft) {
        form.querySelectorAll('input[name="pack"]:not(:disabled)').forEach(el => el.checked = draft.packs.includes(el.value));
        var tx = Array.from(form.querySelectorAll('input[name="tx_profile"]')).find(el => el.value === draft.tx_profile);
        if (tx) tx.checked = true;
        var choices = Array.from(form.querySelectorAll('input[name="theme"]'));
        var theme = (draft.themeName && choices.find(el => el.dataset.themeName === draft.themeName)) || choices.find(el => el.value === draft.theme);
        if (theme) theme.checked = true;
      }
    } catch (_) {}
  }
  form.addEventListener('change', function(event) {
    if (event.target === designer) syncDesigner();
    syncThemeName();
    saveDraft();
    schedule();
  });
  syncDesigner();
  syncThemeName();
  saveDraft();
  // Refresh the library after returning via the browser back/forward cache.
  window.addEventListener('pageshow', event => { if (event.persisted) location.reload(); });
  form.addEventListener('submit', function (event) {
    if (event.submitter === buildButton) {
      if (buildButton.disabled) event.preventDefault();
      return;
    }
    event.preventDefault();
    if (timer) clearTimeout(timer);
    preview();
  });
  // Explicit preview only; never auto-build.
  schedule();
})();
