const initializedPages = new WeakMap();
const sharedConfigurationTabs = new Set(['recognition', 'rules']);
const storageKey = 'receipt-settings-tab';

/** Keep settings sections mounted while exposing one panel at a time. */
export function initializeSettingsTabs(document) {
  const page = document?.querySelector('section[data-page="settings"]');
  if (!page) return;
  if (initializedPages.has(page)) return initializedPages.get(page);

  const tablist = page.querySelector('.settings-tabs[role="tablist"]');
  if (!tablist) return;
  const panels = [...page.querySelectorAll('.settings-tab-panel[data-settings-panel]')];
  const tabs = [...tablist.querySelectorAll('button.settings-tab[data-settings-tab]')]
    .filter(tab => panels.some(panel => panel.dataset.settingsPanel === tab.dataset.settingsTab));
  if (!tabs.length) return;
  const savePanel = page.querySelector('.settings-auto-save-panel');

  function activate(key, {focus = false} = {}) {
    const selected = tabs.find(tab => tab.dataset.settingsTab === key);
    if (!selected) return false;
    tabs.forEach(tab => {
      const active = tab === selected;
      tab.setAttribute('aria-selected', String(active));
      tab.setAttribute('tabindex', active ? '0' : '-1');
    });
    panels.forEach(panel => { panel.hidden = panel.dataset.settingsPanel !== key; });
    if (savePanel) savePanel.hidden = !sharedConfigurationTabs.has(key);
    try { document.defaultView?.sessionStorage?.setItem(storageKey, key); } catch (_) {}
    if (focus) selected.focus();
    return true;
  }

  tabs.forEach((tab, index) => {
    tab.addEventListener('click', () => activate(tab.dataset.settingsTab));
    tab.addEventListener('keydown', event => {
      let next;
      if (event.key === 'ArrowRight') next = (index + 1) % tabs.length;
      else if (event.key === 'ArrowLeft') next = (index + tabs.length - 1) % tabs.length;
      else if (event.key === 'Home') next = 0;
      else if (event.key === 'End') next = tabs.length - 1;
      else return;
      event.preventDefault();
      activate(tabs[next].dataset.settingsTab, {focus: true});
    });
  });

  const controller = {activate};
  initializedPages.set(page, controller);
  let savedKey;
  try { savedKey = document.defaultView?.sessionStorage?.getItem(storageKey); } catch (_) {}
  const initialTab = tabs.find(tab => tab.dataset.settingsTab === savedKey) || tabs[0];
  activate(initialTab.dataset.settingsTab);
  return controller;
}
