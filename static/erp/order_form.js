// Order form: add/remove items, list-price lookup, GST split (CGST+SGST vs IGST) and live totals.
(() => {
  const form = document.querySelector('[data-order-form]');
  if (!form) return;
  const data = JSON.parse(document.getElementById('order-data').textContent);
  const kind = form.dataset.kind;
  const box = form.querySelector('[data-lines]');
  const total = form.querySelector('[name$="-TOTAL_FORMS"]');
  const field = name => form.querySelector(`[name="${name}"]`);
  const inr = (n, d = 2) => '₹' + Number(n).toLocaleString('en-IN', { minimumFractionDigits: d, maximumFractionDigits: d });
  const r2 = n => Math.round((n + Number.EPSILON) * 100) / 100;

  const states = () => {
    const site = data.sites[field('site')?.value] || {};
    const partner = data.partners[field('partner')?.value] || {};
    const pos = field('place_of_supply')?.value;
    if (kind === 'Purchase') return [partner.state, site.state];
    return [site.state, pos || partner.state || site.state];
  };
  const interstate = () => { const [a, b] = states(); return !!(a && b && a !== b); };

  const recalc = () => {
    const inter = interstate();
    let taxable = 0, cgst = 0, sgst = 0, igst = 0;
    box.querySelectorAll('[data-line]').forEach(row => {
      const del = row.querySelector('[name$="-DELETE"]');
      row.classList.toggle('is-removed', !!del?.checked);
      const product = data.products[row.querySelector('[name$="-product"]').value];
      const qty = parseFloat(row.querySelector('[name$="-quantity"]').value) || 0;
      const rateInput = row.querySelector('[name$="-rate"]');
      const rate = parseFloat(rateInput.value || rateInput.dataset.list || 0) || 0;
      row.querySelector('[data-unit]').textContent = product ? product.unit : '';
      const gst = product && product.gst !== '' ? parseFloat(product.gst) : null;
      row.querySelector('[data-gst]').textContent = gst === null ? (product ? 'Not set' : '—') : gst + '%';
      const amount = r2(qty * rate);
      row.querySelector('[data-amount]').textContent = inr(amount);
      if (del?.checked || !product) return;
      taxable += amount;
      if (gst) {
        if (inter) igst += r2(amount * gst / 100);
        else { const half = r2(amount * gst / 200); cgst += half; sgst += half; }
      }
    });
    const exact = taxable + cgst + sgst + igst, grand = Math.round(exact);
    const set = (k, v, d) => { form.querySelector(`[data-t="${k}"]`).textContent = inr(v, d); };
    set('taxable', taxable); set('cgst', cgst); set('sgst', sgst); set('igst', igst); set('round', grand - exact); set('grand', grand, 0);
    form.querySelectorAll('[data-intra]').forEach(el => { el.hidden = inter; });
    form.querySelectorAll('[data-inter]').forEach(el => { el.hidden = !inter; });
    const [a, b] = states();
    form.querySelector('[data-supply]').textContent = !a || !b ? 'Set plant & partner state for GST' : inter ? 'Inter-state · IGST' : 'Intra-state · CGST + SGST';
  };

  let timer;
  const lookup = row => {
    if (kind !== 'Sale') return;
    const product = row.querySelector('[name$="-product"]').value;
    const rateInput = row.querySelector('[name$="-rate"]');
    const hint = row.querySelector('[data-hint]');
    if (!product) { hint.textContent = ''; return; }
    const params = new URLSearchParams({ product, qty: row.querySelector('[name$="-quantity"]').value || 0, site: field('site')?.value || '', partner: field('partner')?.value || '' });
    fetch(data.priceUrl + '?' + params, { headers: { 'X-Requested-With': 'fetch' } }).then(r => r.ok ? r.json() : {}).then(res => {
      if (res.rate) {
        rateInput.dataset.list = res.rate;
        rateInput.placeholder = res.rate;
        const typed = parseFloat(rateInput.value);
        let text = `List ₹${Number(res.rate).toLocaleString('en-IN')} · ${res.scope}`;
        if (typed && typed < parseFloat(res.rate)) text = `Below list (₹${Number(res.rate).toLocaleString('en-IN')}) — needs approval`;
        if (res.next_slab) text += ` · ₹${res.next_slab.rate} from ${res.next_slab.from}`;
        hint.textContent = text;
        hint.classList.toggle('is-warn', !!(typed && typed < parseFloat(res.rate)));
      } else {
        delete rateInput.dataset.list;
        rateInput.placeholder = 'Enter rate';
        hint.textContent = 'No list price — enter a rate';
        hint.classList.add('is-warn');
      }
      recalc();
    }).catch(() => {});
  };
  const lookupAll = () => box.querySelectorAll('[data-line]').forEach(lookup);

  box.addEventListener('change', e => { const row = e.target.closest('[data-line]'); if (row) lookup(row); recalc(); });
  box.addEventListener('input', e => {
    recalc();
    const row = e.target.closest('[data-line]');
    if (row && /quantity|rate/.test(e.target.name)) { clearTimeout(timer); timer = setTimeout(() => lookup(row), 350); }
  });
  ['site', 'partner', 'place_of_supply'].forEach(n => field(n)?.addEventListener('change', () => { lookupAll(); recalc(); }));

  form.querySelector('[data-add-line]').addEventListener('click', () => {
    const n = parseInt(total.value, 10);
    if (n >= 50) return;
    const html = form.querySelector('[data-empty-line]').innerHTML.replace(/__prefix__/g, n);
    box.insertAdjacentHTML('beforeend', html);
    total.value = n + 1;
    box.lastElementChild.querySelector('select')?.focus();
    recalc();
  });
  lookupAll();
  recalc();
})();
