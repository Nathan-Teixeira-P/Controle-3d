function addRow(t, c) { document.getElementById(c).append(document.getElementById(t).content.cloneNode(true)); }
function rm(b) { b.closest('.row').remove(); }
function pick(sel) {
  const o = sel.selectedOptions[0], r = sel.closest('.row'), p = r.querySelector('[name=preco]');
  if (o && o.dataset.preco && p && !p.value) p.value = o.dataset.preco;
}
