'use strict';
(() => {
  const form = document.querySelector('[data-provision]');
  if (!form) return;
  const dialog = form.querySelector('dialog');
  const messages = {
    'flash-install': 'This will re-flash your radio and install the selected firmware pack. On an existing Flux radio, settings and unrelated installed modules and themes will be preserved. Included modules may be updated and included startup artwork replaced.',
    flash: 'This will re-flash your radio. On an existing Flux radio, settings and installed modules and themes will be preserved.',
    install: 'This will install or update the included modules. Settings and unrelated installed modules and themes will be preserved.',
    theme: 'This will install the named theme into an empty slot. Existing modules and themes will be preserved.',
    splash: 'This will back up and replace the stored full-screen startup artwork. Raster-boot firmware requires Designer and the LOGO boot mode to display custom artwork.'
  };
  const submit = () => {
    dialog.close();
    form.requestSubmit();
  };
  form.addEventListener('submit', event => {
    if (form.elements.action.value !== 'preview' && form.elements.confirm.value !== 'yes') event.preventDefault();
  });
  form.querySelectorAll('[data-provision-action]').forEach(button => button.addEventListener('click', () => {
    const action = button.dataset.provisionAction;
    form.elements.action.value = action;
    form.elements.confirm.value = '';
    if (action === 'preview') { form.requestSubmit(); return; }
    dialog.querySelector('[data-confirm-message]').textContent = messages[action];
    dialog.querySelector('[data-confirm-first]').hidden =
      !['flash', 'flash-install'].includes(action) || form.elements.mode.value !== 'update';
    dialog.showModal();
  }));
  dialog.querySelector('[data-confirm-cancel]').addEventListener('click', () => dialog.close());
  dialog.querySelector('[data-confirm-ok]').addEventListener('click', () => {
    form.elements.confirm.value = 'yes';
    submit();
  });
})();
