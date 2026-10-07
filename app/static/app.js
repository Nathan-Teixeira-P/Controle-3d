function addRow(t, c) { document.getElementById(c).append(document.getElementById(t).content.cloneNode(true)); }
function rm(b) { b.closest('.row').remove(); }
function pick(sel) {
  const o = sel.selectedOptions[0], r = sel.closest('.row'), p = r.querySelector('[name=preco]');
  if (o && o.dataset.preco && p && !p.value) p.value = o.dataset.preco;
}

// Modo noturno: tema salvo no navegador (padrão = preferência do sistema)
function rotuloTema() { const b = document.getElementById('tema-btn'); if (b) b.textContent = document.documentElement.dataset.tema === 'dark' ? '☀️ Modo claro' : '🌙 Modo noturno'; }
function alternarTema() {
  const n = document.documentElement.dataset.tema === 'dark' ? 'light' : 'dark';
  document.documentElement.dataset.tema = n;
  try { localStorage.setItem('tema', n); } catch (e) {}
  rotuloTema();
}
rotuloTema();
