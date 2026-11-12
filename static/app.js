/* 淘宝订单导出工具 - 前端逻辑 */
const $ = s => document.querySelector(s);
const state = {
  items: [],
  selected: new Set(),
  fields: [],
  catalog: [],
  defaultKeys: [],
  search: '',
  onlySelected: false,
  polling: null,
};
const dirty = {};
let saveTimer = null;

const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const money = v => (v === null || v === undefined || v === '' || isNaN(v)) ? '' : Number(v).toFixed(2);

async function api(path, opts) {
  const r = await fetch(path, opts ? {
    method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(opts)
  } : undefined);
  return r.json();
}
function toast(msg) {
  const t = $('#toast'); t.textContent = msg; t.hidden = false;
  clearTimeout(t._t); t._t = setTimeout(() => t.hidden = true, 3200);
}

/* ---------------- 数据 ---------------- */
async function loadState() {
  const s = await api('/api/state');
  state.catalog = s.fields;
  state.defaultKeys = s.defaultKeys;
  initFields();
  updateBadge(s);
  await loadData(s.itemCount === 0);
}
function updateBadge(s) {
  const sourceName = {cart: '购物车', orders: '订单', demo: '演示数据'}[s.source] || '订单';
  $('#dataBadge').textContent = s.hasData
    ? `${sourceName} · ${s.orderCount} 组 · ${s.itemCount} 件商品 · ${s.scrapedAt || ''}`
    : '暂无数据';
}
async function loadData(resetSelection = false) {
  const d = await api('/api/data');
  state.items = d.items;
  if (resetSelection || !state.selected.size) {
    state.selected = new Set(state.items.map(i => i.id));
  } else {
    const valid = new Set(state.items.map(i => i.id));
    state.selected = new Set([...state.selected].filter(id => valid.has(id)));
  }
  const s = await api('/api/state'); updateBadge(s);
  render();
}

/* ---------------- 字段配置 ---------------- */
function initFields() {
  let saved = null;
  try { saved = JSON.parse(localStorage.getItem('tb_fields') || 'null'); } catch (e) {}
  if (saved && saved.length) {
    const known = new Set(state.catalog.map(f => f.key));
    state.fields = saved.filter(f => known.has(f.key));
    state.catalog.forEach(f => {
      if (!state.fields.some(x => x.key === f.key)) state.fields.push({key: f.key, label: f.label, on: false});
    });
  } else {
    state.fields = state.catalog.map(f => ({
      key: f.key, label: f.label, on: state.defaultKeys.includes(f.key)
    }));
  }
  renderFields();
}
function saveFields() { localStorage.setItem('tb_fields', JSON.stringify(state.fields)); }
function renderFields() {
  const ul = $('#fieldList');
  ul.innerHTML = state.fields.map((f, i) => `
    <li class="${f.on ? '' : 'off'}" data-i="${i}">
      <input type="checkbox" ${f.on ? 'checked' : ''} data-act="toggle">
      <input type="text" value="${esc(f.label)}" data-act="label">
      <span class="ord">
        <button data-act="up" title="上移">▲</button>
        <button data-act="down" title="下移">▼</button>
      </span>
    </li>`).join('');
}
$('#fieldList').addEventListener('change', e => {
  const li = e.target.closest('li'); if (!li) return;
  const i = +li.dataset.i, act = e.target.dataset.act;
  if (act === 'toggle') { state.fields[i].on = e.target.checked; li.classList.toggle('off', !e.target.checked); }
  if (act === 'label') state.fields[i].label = e.target.value;
  saveFields();
});
$('#fieldList').addEventListener('click', e => {
  const act = e.target.dataset.act;
  if (act !== 'up' && act !== 'down') return;
  const i = +e.target.closest('li').dataset.i;
  const j = act === 'up' ? i - 1 : i + 1;
  if (j < 0 || j >= state.fields.length) return;
  [state.fields[i], state.fields[j]] = [state.fields[j], state.fields[i]];
  saveFields(); renderFields();
});
$('#btnResetFields').onclick = () => { localStorage.removeItem('tb_fields'); initFields(); toast('已恢复默认字段'); };

/* ---------------- 表格 ---------------- */
function visibleItems() {
  const kw = state.search.trim().toLowerCase();
  return state.items.filter(it => {
    if (state.onlySelected && !state.selected.has(it.id)) return false;
    if (!kw) return true;
    return [it.title, it.shop, it.orderNo, it.spec].some(v => String(v || '').toLowerCase().includes(kw));
  });
}

function render() {
  const items = visibleItems();
  const tb = $('#tbody');
  $('#empty').style.display = state.items.length ? 'none' : 'block';

  let html = '', lastOrder = null, n = 0;
  for (const it of items) {
    if (it.orderIndex !== lastOrder) {
      lastOrder = it.orderIndex;
      const groupIds = items.filter(x => x.orderIndex === lastOrder).map(x => x.id);
      const allOn = groupIds.every(id => state.selected.has(id));
      const isCart = !it.orderNo;
      html += `<tr class="order-row" data-order="${it.orderIndex}"><td>
          <input type="checkbox" class="order-check" data-ids="${groupIds.join(',')}" ${allOn ? 'checked' : ''}></td>
        <td colspan="10"><div class="oinfo">
          ${isCart ? '' : `<span>订单号 <b>${esc(it.orderNo)}</b></span>`}
          ${it.orderTime ? `<span>${esc(it.orderTime)}</span>` : ''}
          <span>店铺 <b>${esc(it.shop || '—')}</b></span>
          ${it.paid ? `<span>实付 <b>¥${money(it.paid)}</b></span>` : ''}
          <span>${esc(it.status || '')}</span>
          <span class="muted">${isCart ? '本店' : '本单'} ${groupIds.length} 件</span>
        </div></td></tr>`;
    }
    n++;
    const on = state.selected.has(it.id);
    const sub = (parseFloat(it.price) || 0) * (parseFloat(it.qty) || 0);
    html += `<tr class="item-row ${on ? '' : 'off'}" data-id="${it.id}">
      <td><input type="checkbox" class="row-check" ${on ? 'checked' : ''}></td>
      <td class="muted">${n}</td>
      <td>
        ${it.url ? `<a class="pname" href="${esc(it.url)}" target="_blank" rel="noopener">${esc(it.title)}</a>`
                 : `<span class="pname">${esc(it.title)}</span>`}
        ${it.url ? `<a class="plink" href="${esc(it.url)}" target="_blank" rel="noopener">打开商品页 ↗</a>` : ''}
      </td>
      <td><input class="cell-input" data-k="spec" value="${esc(it.spec)}" placeholder="—"></td>
      <td><input class="cell-input qty" data-k="qty" value="${esc(it.qty ?? '')}"></td>
      <td><input class="cell-input price" data-k="price" value="${money(it.price)}" placeholder="0.00"></td>
      <td class="money sub-val" data-sub>¥${money(sub)}</td>
      <td>${esc(it.shop)}</td>
      <td class="muted">${esc(it.orderNo)}</td>
      <td class="muted">${esc(it.orderTime)}</td>
      <td>${esc(it.status)}</td>
    </tr>`;
  }
  tb.innerHTML = html;
  const visIds = items.map(i => i.id);
  $('#headCheck').checked = visIds.length > 0 && visIds.every(id => state.selected.has(id));
  $('#footInfo').textContent = `显示 ${items.length} / 共 ${state.items.length} 项，已选 ${state.selected.size} 项`;
  updateStats();
}

function updateStats() {
  const sel = state.items.filter(i => state.selected.has(i.id));
  const qty = sel.reduce((a, b) => a + (parseFloat(b.qty) || 0), 0);
  const total = sel.reduce((a, b) => a + (parseFloat(b.price) || 0) * (parseFloat(b.qty) || 0), 0);
  $('#statCount').textContent = sel.length;
  $('#statQty').textContent = qty;
  $('#statTotal').textContent = '¥' + money(total);
}

/* 行交互 */
$('#tbody').addEventListener('change', e => {
  const tr = e.target.closest('tr'); if (!tr) return;
  if (e.target.classList.contains('row-check')) {
    const id = tr.dataset.id;
    e.target.checked ? state.selected.add(id) : state.selected.delete(id);
    tr.classList.toggle('off', !e.target.checked);
    refreshFooter(); updateStats(); return;
  }
  if (e.target.classList.contains('order-check')) {
    const ids = e.target.dataset.ids.split(',');
    ids.forEach(id => e.target.checked ? state.selected.add(id) : state.selected.delete(id));
    render(); return;
  }
  if (e.target.classList.contains('cell-input')) {
    const id = tr.dataset.id, k = e.target.dataset.k;
    const it = state.items.find(x => x.id === id); if (!it) return;
    let v = e.target.value.trim();
    if (k === 'qty') v = parseInt(v, 10) || 0;
    if (k === 'price') v = parseFloat(v) || 0;
    it[k] = v;
    dirty[id] = Object.assign(dirty[id] || {}, {[k]: v});
    tr.querySelector('[data-sub]').textContent = '¥' + money((parseFloat(it.price) || 0) * (parseFloat(it.qty) || 0));
    updateStats();
    clearTimeout(saveTimer);
    saveTimer = setTimeout(async () => {
      await api('/api/items', {patch: dirty});
      for (const k2 in dirty) delete dirty[k2];
    }, 800);
  }
});
$('#tbody').addEventListener('keydown', e => {
  if (e.target.classList.contains('cell-input') && e.key === 'Enter') e.target.blur();
});

function refreshFooter() {
  const visIds = visibleItems().map(i => i.id);
  $('#headCheck').checked = visIds.length > 0 && visIds.every(id => state.selected.has(id));
  $('#footInfo').textContent = `显示 ${visIds.length} / 共 ${state.items.length} 项，已选 ${state.selected.size} 项`;
}

/* 工具栏 */
$('#search').addEventListener('input', e => { state.search = e.target.value; render(); });
$('#onlySelected').addEventListener('change', e => { state.onlySelected = e.target.checked; render(); });
$('#btnAll').onclick = () => { visibleItems().forEach(i => state.selected.add(i.id)); render(); };
$('#btnNone').onclick = () => { visibleItems().forEach(i => state.selected.delete(i.id)); render(); };
$('#btnInvert').onclick = () => {
  visibleItems().forEach(i => state.selected.has(i.id) ? state.selected.delete(i.id) : state.selected.add(i.id));
  render();
};
$('#headCheck').addEventListener('change', e => {
  visibleItems().forEach(i => e.target.checked ? state.selected.add(i.id) : state.selected.delete(i.id));
  render();
});

/* ---------------- 抓取 ---------------- */
$('#btnScrape').onclick = async () => {
  const r = await api('/api/scrape', {source: $('#source').value, maxPages: +$('#maxPages').value || 200, wait: 600});
  if (!r.ok) { toast(r.msg || '启动失败'); return; }
  $('#btnScrape').disabled = true; $('#btnScrape').textContent = '抓取中…';
  $('#log').hidden = false; $('#log').textContent = '';
  toast('请在弹出的浏览器窗口扫码登录');
  state.polling = setInterval(poll, 1500);
  poll();
};
async function poll() {
  const s = await api('/api/scrape/status');
  $('#log').textContent = s.logs.join('\n');
  $('#log').scrollTop = $('#log').scrollHeight;
  if (!s.running) {
    clearInterval(state.polling);
    $('#btnScrape').disabled = false; $('#btnScrape').textContent = '开始抓取';
    if (s.error) toast('抓取失败：' + s.error);
    else { toast(`抓取完成，共 ${s.orderCount} 个订单`); await loadData(true); }
  }
}
$('#btnCartChecked').onclick = () => {
  const marked = state.items.filter(i => i.checked);
  if (!marked.length) return toast('当前数据里没有「购物车已勾选」的商品');
  state.selected = new Set(marked.map(i => i.id));
  render();
  toast(`已选中 ${marked.length} 件购物车勾选商品`);
};
$('#btnReload').onclick = () => loadData().then(() => toast('已重新载入'));
$('#btnDemo').onclick = async () => { await api('/api/demo', {}); await loadData(true); toast('已载入演示数据'); };

/* ---------------- 导出 ---------------- */
async function doExport(fmt) {
  const ids = state.items.filter(i => state.selected.has(i.id)).map(i => i.id);
  const fields = state.fields.filter(f => f.on).map(f => ({key: f.key, label: f.label}));
  if (!ids.length) return toast('请先勾选要导出的商品');
  if (!fields.length) return toast('请至少勾选一个导出字段');
  const r = await api('/api/export', {ids, fields, format: fmt});
  if (!r.ok) return toast(r.msg || '导出失败');
  const box = $('#result');
  box.hidden = false;
  box.innerHTML = `<b>已导出 ${r.count} 项</b>，合计 ¥${money(r.total)}<br>
    <span class="muted">${esc(r.path)}</span>
    <div class="r-actions">
      <button class="link" id="rOpen">打开文件</button>
      <a class="link" href="/api/download?name=${encodeURIComponent(r.name)}">下载</a>
    </div>`;
  $('#rOpen').onclick = () => api('/api/open', {name: r.name});
  toast('导出成功');
}
$('#btnXlsx').onclick = () => doExport('xlsx');
$('#btnCsv').onclick = () => doExport('csv');

$('#btnStop').onclick = async () => {
  if (!confirm('确定停止本地服务吗？\n停止后本页面将无法操作，已导出的文件不受影响。')) return;
  try { await api('/api/shutdown', {}); } catch (e) {}
  document.body.innerHTML = '<div style="max-width:520px;margin:120px auto;text-align:center;font:15px/2 -apple-system,\'Microsoft YaHei\',sans-serif;color:#1f2329">'
    + '<h2 style="font-size:20px">服务已停止</h2>'
    + '<p style="color:#8a919c">本页面可以关闭了。<br>下次使用请双击 <b>启动导出工具.bat</b>。</p></div>';
};

loadState();
