// Generic line editor for formsets: add rows from <template data-empty-line>, mark removed rows, show the item's unit.
(() => {
  const form = document.querySelector('[data-lines-form]');
  if (!form) return;
  const units = JSON.parse(document.getElementById('unit-data')?.textContent || '{}');
  const box = form.querySelector('[data-lines]');
  const total = form.querySelector('[name$="-TOTAL_FORMS"]');
  const max = parseInt(form.querySelector('[name$="-MAX_NUM_FORMS"]').value, 10) || 50;
  const refresh = () => box.querySelectorAll('[data-line]').forEach(row => {
    const del = row.querySelector('[name$="-DELETE"]');
    row.classList.toggle('is-removed', !!del?.checked);
    const unit = row.querySelector('[data-unit]');
    const product = row.querySelector('[name$="-product"]');
    if (unit && product) unit.textContent = units[product.value] || '';
  });
  form.querySelector('[data-add-line]')?.addEventListener('click', () => {
    const n = parseInt(total.value, 10);
    if (n >= max) return;
    const html = form.querySelector('[data-empty-line]').innerHTML.replace(/__prefix__/g, n);
    box.insertAdjacentHTML('beforeend', html);
    total.value = n + 1;
    box.lastElementChild.querySelector('select, input')?.focus();
    refresh();
  });
  form.addEventListener('change', refresh);
  refresh();
})();
