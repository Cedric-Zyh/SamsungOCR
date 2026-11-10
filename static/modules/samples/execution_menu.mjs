/** A keyboard-accessible mode menu styled by the sample workspace. */
export function createSampleExecutionMenu({environment, ui, modes, changed}) {
  const {$, $$} = ui;
  let value = 'all', disabled = false, open = false;
  const options = () => $$('[data-sample-mode]');
  const focusOption = index => options()[index]?.focus();
  function close(restoreFocus = false) {
    open = false;
    $('#sample-execution-menu').hidden = true;
    $('#sample-execution-mode').setAttribute('aria-expanded', 'false');
    if (restoreFocus) $('#sample-execution-mode').focus();
  }
  function show(edge) {
    if (disabled) return;
    open = true;
    $('#sample-execution-menu').hidden = false;
    $('#sample-execution-mode').setAttribute('aria-expanded', 'true');
    const index = edge === 'last' ? options().length - 1 : edge === 'first' ? 0
      : Math.max(0, options().findIndex(button => button.dataset.sampleMode === value));
    focusOption(index);
  }
  function render(state) {
    value = state.value; disabled = state.disabled;
    const button = $('#sample-execution-mode');
    button.value = value; button.disabled = disabled;
    $('#sample-execution-label').textContent = modes[value].label;
    for (const option of options()) option.setAttribute('aria-checked', String(option.dataset.sampleMode === value));
    if (disabled) close();
  }
  function initialize() {
    $('#sample-execution-mode').addEventListener('click', () => open ? close(true) : show());
    $('#sample-execution-mode').addEventListener('keydown', event => {
      if (!['ArrowDown', 'ArrowUp'].includes(event.key)) return;
      event.preventDefault(); show(event.key === 'ArrowUp' ? 'last' : undefined);
    });
    $('#sample-execution-menu').addEventListener('click', event => {
      const option = event.target.closest('[data-sample-mode]');
      if (!option || disabled || !Object.hasOwn(modes, option.dataset.sampleMode)) return;
      changed(option.dataset.sampleMode); close(true);
    });
    $('#sample-execution-menu').addEventListener('keydown', event => {
      if (event.key === 'Escape') { event.preventDefault(); close(true); return; }
      if (event.key === 'Tab') { close(); return; }
      if (!['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) return;
      event.preventDefault();
      const buttons = options(), current = buttons.indexOf(environment.document.activeElement);
      const index = event.key === 'Home' ? 0 : event.key === 'End' ? buttons.length - 1
        : (current + (event.key === 'ArrowDown' ? 1 : -1) + buttons.length) % buttons.length;
      focusOption(index);
    });
    environment.document.addEventListener?.('click', event => {
      if (open && !event.target.closest?.('.sample-execution-choice')) close();
    });
  }
  return {initialize, render, close};
}
