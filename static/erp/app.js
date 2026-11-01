document.getElementById('menu-toggle')?.addEventListener('click', () => document.getElementById('sidebar').classList.toggle('open'));
document.querySelectorAll('[data-confirm]').forEach(form => form.addEventListener('submit', event => { if (!window.confirm(form.dataset.confirm)) event.preventDefault(); }));
document.getElementById('add-line')?.addEventListener('click', () => {
  const total = document.querySelector('[name="lines-TOTAL_FORMS"]');
  if (Number(total.value) >= 50) return;
  const html = document.getElementById('empty-line').innerHTML.replaceAll('__prefix__', total.value);
  document.getElementById('order-lines').insertAdjacentHTML('beforeend', html);
  total.value = Number(total.value) + 1;
});
const statuses = ['Draft', 'Submitted', 'Approved', 'Completed', 'Cancelled', 'Quality hold', 'Released', 'Planned', 'Pass', 'Fail', 'Available', 'Low stock'];
document.querySelectorAll('.cell-value[data-status]').forEach(el => { if (statuses.includes(el.dataset.status)) el.classList.add('status'); });
