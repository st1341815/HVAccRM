/* ============================================================================
   首页看板 2.0 前端（v1.16.0）
   ----------------------------------------------------------------------------
   职责边界：
     · 服务端：算数据、直出 KPI 与列表（无 JS 也能看）
     · 本文件：把 JSON 画成图（Chart.js 4 / 自研 SVG·DOM）、拖拽、增删改模块
   约定：
     · 金额后端一律给「元」，本文件统一除以 10000 显示「万」，与模板 |wan 一致
     · 颜色全部从 CSS 变量读，切换深色主题时重渲染，不写死色值
   ========================================================================== */
(function () {
  'use strict';

  var grid = document.getElementById('dashGrid');
  if (!grid) return;

  var CSRF = (document.querySelector('meta[name="csrf-token"]') || {}).content || '';
  var DATA = {};              // id -> 模块数据（供重绘 / 换图形 / 撤销）
  var ITEMS = {};             // id -> 布局项
  var charts = {};            // id -> Chart 实例（必须 destroy，否则切方案会泄漏）
  var editing = false;
  var undoStack = [];         // [{html, index}] 仅记录删除，够用且不会误导
  var dirty = false;          // 编辑态是否已有未保存改动

  // ------------------------------------------------------------------ 工具
  function $(sel, root) { return (root || document).querySelector(sel); }
  function $$(sel, root) { return Array.prototype.slice.call((root || document).querySelectorAll(sel)); }
  function cssv(name) { return getComputedStyle(document.documentElement).getPropertyValue(name).trim(); }

  function palette() {
    return ['--ch-1', '--ch-2', '--ch-3', '--ch-4', '--ch-5', '--ch-6'].map(cssv);
  }

  function alpha(hex, a) {
    var h = (hex || '').replace('#', '');
    if (h.length === 3) h = h[0] + h[0] + h[1] + h[1] + h[2] + h[2];
    if (h.length !== 6) return hex;
    var r = parseInt(h.slice(0, 2), 16), g = parseInt(h.slice(2, 4), 16), b = parseInt(h.slice(4, 6), 16);
    return 'rgba(' + r + ',' + g + ',' + b + ',' + a + ')';
  }

  function fmtNum(v) {
    if (v === null || v === undefined || isNaN(v)) return '0';
    var n = Math.abs(v);
    if (n >= 1000) return Math.round(v).toLocaleString('zh-CN');
    return (Math.round(v * 10) / 10).toString();
  }

  function suffix(unit) { return unit === 'wan' ? ' 万' : ''; }

  function scaled(d) {
    var div = d.unit === 'wan' ? 10000 : 1;
    return (d.series || []).map(function (s) {
      return { name: s.name, data: (s.data || []).map(function (v) { return +(v / div).toFixed(2); }) };
    });
  }

  function toast(msg, bad) {
    var el = document.createElement('div');
    el.className = 'dash-toast' + (bad ? ' bad' : '');
    el.textContent = msg;
    document.body.appendChild(el);
    setTimeout(function () { el.classList.add('out'); }, 1800);
    setTimeout(function () { el.remove(); }, 2300);
  }

  function api(url, opts) {
    opts = opts || {};
    opts.headers = Object.assign({ 'X-CSRF-Token': CSRF }, opts.headers || {});
    if (opts.body && typeof opts.body !== 'string') {
      opts.body = JSON.stringify(opts.body);
      opts.headers['Content-Type'] = 'application/json';
    }
    return fetch(url, opts).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (body) {
        if (!r.ok) throw new Error(body.detail || ('请求失败 ' + r.status));
        return body;
      });
    });
  }

  // ------------------------------------------------------------------ 读初始数据
  function loadInitial() {
    $$('.dash-card', grid).forEach(function (card) {
      var id = card.dataset.id;
      ITEMS[id] = {
        id: id,
        metric: card.dataset.metric,
        kind: card.dataset.kind,
        span: Number(card.dataset.span) || 12,
        h: Number(card.dataset.h) || 0,
        opts: safeJSON(card.dataset.opts)
      };
      var payload = $('.js-payload', card);
      if (payload) {
        try { DATA[id] = JSON.parse(payload.textContent); } catch (e) { DATA[id] = null; }
      }
    });
  }

  function safeJSON(s) {
    if (!s) return {};
    try { return JSON.parse(s) || {}; } catch (e) { return {}; }
  }

  // ------------------------------------------------------------------ 渲染入口
  function renderAll() {
    $$('.dash-card', grid).forEach(function (card) { renderCard(card); });
  }

  function destroyChart(id) {
    if (charts[id]) { charts[id].destroy(); delete charts[id]; }
  }

  function renderCard(card) {
    var id = card.dataset.id;
    var kind = card.dataset.kind;
    destroyChart(id);

    var box = $('.chart-box', card);
    if (!box) return;                       // kpi / list 由服务端直出，不归前端管
    box.innerHTML = '';

    var d = DATA[id];
    if (!d || (d.labels && !d.labels.length) || (d.cells && !d.cells.length)) {
      box.innerHTML = '<p class="chart-empty">暂无数据</p>';
      return;
    }
    if (kind === 'stack') { renderStack(box, d); return; }
    if (kind === 'heat') { renderHeat(box, d); return; }

    var canvas = document.createElement('canvas');
    box.appendChild(canvas);
    var cfg = buildConfig(kind, d);
    if (!cfg) { box.innerHTML = '<p class="chart-empty">不支持的图形</p>'; return; }
    charts[id] = new Chart(canvas, cfg);
  }

  // ------------------------------------------------------------------ Chart.js 配置
  function baseOptions(d, withScales) {
    var cols = palette();
    var muted = cssv('--muted');
    var gridColor = cssv('--ch-grid');
    var opts = {
      responsive: true,
      maintainAspectRatio: false,
      animation: { duration: 220 },
      interaction: { mode: 'index', intersect: false },
      layout: { padding: { top: 4, right: 4, bottom: 0, left: 0 } },
      plugins: {
        legend: {
          display: (d.series || []).length > 1,
          position: 'bottom',
          labels: { boxWidth: 8, boxHeight: 8, usePointStyle: true, pointStyle: 'rectRounded', color: muted, font: { size: 11 }, padding: 10 }
        },
        tooltip: {
          backgroundColor: 'rgba(22,32,46,.94)',
          padding: 8,
          titleFont: { size: 11 },
          bodyFont: { size: 12 },
          displayColors: true,
          callbacks: {
            label: function (c) {
              var v = c.parsed && typeof c.parsed === 'object' ? (c.parsed.y !== undefined ? c.parsed.y : c.parsed.x) : c.parsed;
              return ' ' + c.dataset.label + '：' + fmtNum(v) + suffix(d.unit);
            }
          }
        }
      }
    };
    if (withScales) {
      opts.scales = {
        x: {
          grid: { display: false },
          border: { color: gridColor },
          ticks: { color: muted, font: { size: 11 }, maxRotation: 0, autoSkip: true, autoSkipPadding: 12 }
        },
        y: {
          beginAtZero: true,
          grid: { color: gridColor, drawTicks: false },
          border: { display: false },
          ticks: { color: muted, font: { size: 11 }, padding: 4 }
        }
      };
    }
    return opts;
  }

  function buildConfig(kind, d) {
    var cols = palette();
    var series = scaled(d);

    if (kind === 'line' || kind === 'area') {
      return {
        type: 'line',
        data: {
          labels: d.labels,
          datasets: series.map(function (s, i) {
            var c = cols[i % cols.length];
            return {
              label: s.name,
              data: s.data,
              borderColor: c,
              backgroundColor: kind === 'area' ? alpha(c, 0.14) : c,
              fill: kind === 'area',
              tension: 0.32,
              borderWidth: 2,
              pointRadius: 0,
              pointHoverRadius: 4,
              pointHoverBackgroundColor: c,
              pointHoverBorderColor: cssv('--card'),
              pointHoverBorderWidth: 2
            };
          })
        },
        options: baseOptions(d, true)
      };
    }

    if (kind === 'bar' || kind === 'hist') {
      var opt = baseOptions(d, true);
      opt.scales.x.stacked = false;
      if (kind === 'hist') {
        opt.scales.x.grid = { display: false };
        opt.scales.y.grid = { color: cssv('--ch-grid'), drawTicks: false };
      }
      return {
        type: 'bar',
        data: {
          labels: d.labels,
          datasets: series.map(function (s, i) {
            var c = cols[i % cols.length];
            return {
              label: s.name,
              data: s.data,
              backgroundColor: series.length > 1 ? c : alpha(c, 0.85),
              hoverBackgroundColor: c,
              borderRadius: kind === 'hist' ? 2 : 3,
              borderSkipped: false,
              maxBarThickness: 42,
              barPercentage: kind === 'hist' ? 1 : 0.72,
              categoryPercentage: kind === 'hist' ? 0.96 : 0.78
            };
          })
        },
        options: opt
      };
    }

    if (kind === 'donut') {
      var opt2 = baseOptions(d, false);
      opt2.cutout = '62%';
      opt2.plugins.legend.position = 'right';
      opt2.plugins.legend.labels.boxWidth = 10;
      opt2.plugins.tooltip.callbacks.label = function (c) {
        var total = c.dataset.data.reduce(function (a, b) { return a + b; }, 0);
        var pct = total ? Math.round(c.parsed / total * 1000) / 10 : 0;
        return ' ' + c.label + '：' + fmtNum(c.parsed) + suffix(d.unit) + '（' + pct + '%）';
      };
      return {
        type: 'doughnut',
        data: {
          labels: d.labels,
          datasets: [{
            data: (series[0] || {}).data || [],
            backgroundColor: d.labels.map(function (_, i) { return cols[i % cols.length]; }),
            borderColor: cssv('--card'),
            borderWidth: 2,
            hoverOffset: 4
          }]
        },
        options: opt2
      };
    }

    return null;
  }

  // ------------------------------------------------------------------ 自研：堆叠条
  function renderStack(box, d) {
    var cols = palette();
    var rows = d.labels.map(function (label, i) {
      var total = d.series.reduce(function (a, s) { return a + (s.data[i] || 0); }, 0);
      var segs = d.series.map(function (s, si) {
        var v = s.data[i] || 0;
        if (!v) return '';
        var pct = total ? (v / total * 100) : 0;
        var txt = pct > 13 ? v : '';
        return '<span class="stack-seg" style="width:' + pct.toFixed(2) + '%;background:' +
          cols[si % cols.length] + '" title="' + s.name + '：' + v + '">' + txt + '</span>';
      }).join('');
      return '<div class="stack-row"><span class="stack-label" title="' + label + '">' + label +
        '</span><span class="stack-bar">' + segs + '</span><span class="stack-total">' + total + '</span></div>';
    }).join('');
    var legend = d.series.map(function (s, i) {
      return '<span><i class="li" style="background:' + cols[i % cols.length] + '"></i>' + s.name + '</span>';
    }).join('');
    box.innerHTML = '<div class="stack-wrap">' + rows + '</div><div class="stack-legend">' + legend + '</div>';
  }

  // ------------------------------------------------------------------ 自研：热力图
  function renderHeat(box, d) {
    var cells = d.cells || [];
    var max = Math.max(1, d.max || 1);
    var base = cssv('--ch-1');
    var empty = cssv('--paper-3');
    var weeks = [];
    for (var i = 0; i < cells.length; i += 7) weeks.push(cells.slice(i, i + 7));

    var html = weeks.map(function (col) {
      return '<div class="heat-col">' + col.map(function (c) {
        var lv = c.v === 0 ? 0 : Math.min(4, Math.ceil(c.v / max * 4));
        var bg = lv === 0 ? empty : base;
        var op = lv === 0 ? 1 : [0, 0.3, 0.5, 0.72, 1][lv];
        return '<i class="heat-cell" style="background:' + bg + ';opacity:' + op +
          '" title="' + c.d + '：' + c.v + ' 条"></i>';
      }).join('') + '</div>';
    }).join('');

    var legend = '<div class="heat-legend">少' +
      [0, 1, 2, 3, 4].map(function (lv) {
        var bg = lv === 0 ? empty : base;
        var op = lv === 0 ? 1 : [0, 0.3, 0.5, 0.72, 1][lv];
        return '<i class="heat-cell" style="background:' + bg + ';opacity:' + op + '"></i>';
      }).join('') + '多</div>';

    box.innerHTML = '<div class="heat-wrap">' + html + '</div>' + legend;
  }

  // ------------------------------------------------------------------ 编辑态
  function setEditing(on) {
    editing = on;
    document.body.classList.toggle('dash-editing', on);
    $('#editBar').hidden = !on;
    $('#btnEdit').textContent = on ? '退出编辑' : '编辑布局';
    if (!on) { closePop(); closeLib(); clearDirty(); }
  }

  // ------------------------------------------------------------------ 草稿模式
  // 编辑态的一切操作（增删、拖动、换指标/图形/尺寸）只改 DOM 与 ITEMS，**不落库**；
  // 只有点「保存并完成」才 PATCH 一次。
  // 为什么这么改：v1.16.1 之前每次加模块都「存库 + location.reload()」，reload 顺手
  // 把编辑态也关了 —— 用户想连加两个模块，得重新进一次编辑态；想反悔也没有退路。
  function markDirty() {
    if (dirty) return;
    dirty = true;
    var b = $('#btnDone');
    if (b) b.textContent = '保存并完成';
  }

  function clearDirty() {
    dirty = false;
    var b = $('#btnDone');
    if (b) b.textContent = '完成';
  }

  // 退出编辑态的统一入口：有未保存改动就先确认，确认放弃就整页重载回原样
  function exitEditing() {
    if (dirty) {
      if (!window.confirm('有未保存的布局改动，确定放弃？')) return;
      location.reload();
      return;
    }
    setEditing(false);
  }

  function catalogList() {
    var cat = safeJSON(($('#dashCatalog') || {}).textContent);
    return Array.isArray(cat) ? cat : (cat.metrics || []);
  }

  function metricTitle(key) {
    var m = catalogList().filter(function (x) { return x.key === key; })[0];
    return m ? m.title : key;
  }

  // 与服务端 |wan 过滤器同口径：元 → 万元，1 位小数，千分位
  function wanJS(v) {
    return (Number(v || 0) / 10000).toFixed(1).replace(/\B(?=(\d{3})+(?!\d))/g, ',');
  }

  function el(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    return n;
  }

  // 卡片外壳（与服务端模板 dashboard.html 的结构保持一致，只是不含内联数据块）
  var HEAD_HTML =
    '<header class="card-head">' +
      '<span class="drag-handle" aria-hidden="true" title="拖动调整位置">' +
        '<svg viewBox="0 0 24 24" fill="currentColor"><circle cx="9" cy="6" r="1.7"/><circle cx="15" cy="6" r="1.7"/><circle cx="9" cy="12" r="1.7"/><circle cx="15" cy="12" r="1.7"/><circle cx="9" cy="18" r="1.7"/><circle cx="15" cy="18" r="1.7"/></svg>' +
      '</span>' +
      '<span class="card-title"></span>' +
      '<div class="card-tools">' +
        '<button type="button" class="t js-gear" title="设置这个模块" aria-label="设置">' +
          '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"><circle cx="12" cy="12" r="3"/><path d="M12 3v2M12 19v2M3 12h2M19 12h2M5.6 5.6l1.4 1.4M17 17l1.4 1.4M18.4 5.6L17 7M7 17l-1.4 1.4"/></svg>' +
        '</button>' +
        '<button type="button" class="t js-remove" title="移除这个模块" aria-label="移除">' +
          '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M6 6l12 12M18 6L6 18"/></svg>' +
        '</button>' +
      '</div>' +
    '</header>';

  function buildCard(item) {
    var sec = document.createElement('section');
    sec.className = 'card dash-card';
    sec.dataset.id = item.id;
    sec.dataset.metric = item.metric;
    sec.dataset.kind = item.kind;
    sec.dataset.span = item.span;
    sec.dataset.h = item.h || 0;
    sec.dataset.opts = JSON.stringify(item.opts || {});
    sec.style.gridColumn = 'span ' + item.span;
    sec.innerHTML = HEAD_HTML;
    $('.card-title', sec).textContent = metricTitle(item.metric);
    return sec;
  }

  // 按 kind 重建卡片内容（KPI / 列表 / 图表）。图表复用 renderCard()。
  function renderBody(card, item, data) {
    destroyChart(item.id);
    Array.prototype.slice.call(card.children).forEach(function (n) {
      if (!n.classList.contains('card-head')) n.remove();
    });
    var oldSub = $('.card-sub', card);
    if (oldSub) oldSub.remove();
    $('.card-title', card).textContent = metricTitle(item.metric);
    DATA[item.id] = data || null;

    if (!data) { card.appendChild(el('p', 'dash-empty', '暂无数据')); return; }

    if (item.kind === 'kpi') {
      var box = el('div', 'kpi-body');
      var num = el('span', 'kpi-num');
      num.textContent = data.unit === 'wan'
        ? '¥ ' + wanJS(data.value) + ' 万'
        : String(data.value) + (item.metric === 'kpi.recovery' ? '%' : '');
      box.appendChild(num);
      box.appendChild(el('span', 'kpi-label', data.label || ''));
      if (data.delta) {
        box.appendChild(el('span', 'kpi-delta ' + (data.delta > 0 ? 'up' : 'down'),
          (data.delta > 0 ? '+' : '') + Number(data.delta).toFixed(1) + '%'));
      }
      if (data.note) box.appendChild(el('span', 'kpi-note', data.note));
      card.appendChild(box);
      return;
    }

    if (item.kind === 'list') {
      var rows = data.rows || [];
      if (!rows.length) { card.appendChild(el('p', 'dash-empty', '暂无数据')); return; }
      var cols = data.columns || ['名称', '数值'];
      var sub = el('span', 'card-sub', '共 ' + (data.total != null ? data.total : rows.length) + ' 条');
      var head = $('.card-head', card);
      head.insertBefore(sub, $('.card-tools', card));

      var t = el('table', 'tight dash-list');
      var thead = el('thead'), tr = el('tr');
      tr.appendChild(el('th', null, cols[0] || '名称'));
      var th2 = el('th', null, cols[1] || '数值');
      th2.style.textAlign = 'right';
      tr.appendChild(th2);
      if (rows[0].meta !== undefined) tr.appendChild(el('th', null, '状态'));
      thead.appendChild(tr);
      t.appendChild(thead);

      var tb = el('tbody');
      rows.forEach(function (r) {
        var row = el('tr');
        var c1 = el('td');
        var a = el('a', 'dash-link', r.label);
        a.href = r.href || '#';
        c1.appendChild(a);
        row.appendChild(c1);
        var c2 = el('td', 'num');
        c2.textContent = data.unit === 'wan' ? '¥ ' + wanJS(r.value) : String(r.value);
        row.appendChild(c2);
        if (r.meta !== undefined) {
          var c3 = el('td');
          c3.appendChild(el('span', 'badge b-' + r.meta, r.meta));
          row.appendChild(c3);
        }
        tb.appendChild(row);
      });
      t.appendChild(tb);
      card.appendChild(t);
      return;
    }

    var cb = el('div', 'chart-box');
    cb.style.height = (item.h || 240) + 'px';
    card.appendChild(cb);
    renderCard(card);
  }

  // 取一个模块的数据并重绘（换指标 / 换图形后必须重取：同一指标的不同图形，
  // 返回结构可能不同 —— kpi 给 value，line 给 labels+series）
  function refreshCard(card, item) {
    var url = '/api/dashboard/widget?metric=' + encodeURIComponent(item.metric);
    Object.keys(item.opts || {}).forEach(function (k) {
      url += '&' + encodeURIComponent(k) + '=' + encodeURIComponent(item.opts[k]);
    });
    return api(url).then(function (r) {
      renderBody(card, item, r.data);
    }).catch(function (e) {
      renderBody(card, item, null);
      toast(e.message || '模块数据加载失败', true);
    });
  }

  function collectItems() {
    return $$('.dash-card', grid).map(function (card) {
      var it = ITEMS[card.dataset.id] || {};
      return {
        id: card.dataset.id,
        metric: card.dataset.metric,
        kind: card.dataset.kind,
        span: Number(card.dataset.span) || 12,
        h: Number(card.dataset.h) || 0,
        opts: it.opts || {}
      };
    });
  }

  function saveLayout() {
    return api('/api/dashboard/views/' + grid.dataset.viewId, {
      method: 'PATCH',
      body: { items: collectItems() }
    }).then(function () {
      toast('布局已保存');
    });
  }

  // 拖拽：被拖卡片 fixed 跟手，原位留虚线占位，落点实时重排
  var drag = null;

  function onHandleDown(e) {
    if (!editing) return;
    var handle = e.target.closest('.drag-handle');
    if (!handle) return;
    var card = handle.closest('.dash-card');
    if (!card) return;
    e.preventDefault();

    var rect = card.getBoundingClientRect();
    var ph = document.createElement('div');
    ph.className = 'dash-ph';
    ph.style.gridColumn = 'span ' + (card.dataset.span || 12);
    ph.style.height = rect.height + 'px';
    card.parentNode.insertBefore(ph, card.nextSibling);

    card.style.position = 'fixed';
    card.style.left = rect.left + 'px';
    card.style.top = rect.top + 'px';
    card.style.width = rect.width + 'px';
    card.style.height = rect.height + 'px';
    card.style.pointerEvents = 'none';
    card.style.zIndex = '40';
    card.classList.add('is-drag');

    drag = { card: card, ph: ph, dx: e.clientX - rect.left, dy: e.clientY - rect.top };
    handle.setPointerCapture && handle.setPointerCapture(e.pointerId);
  }

  function onPointerMove(e) {
    if (!drag) return;
    drag.card.style.left = (e.clientX - drag.dx) + 'px';
    drag.card.style.top = (e.clientY - drag.dy) + 'px';

    var under = document.elementFromPoint(e.clientX, e.clientY);
    var over = under && under.closest ? under.closest('.dash-card') : null;
    if (!over || over === drag.card) return;
    var r = over.getBoundingClientRect();
    var below = (e.clientY - r.top) > r.height / 2;
    over.parentNode.insertBefore(drag.ph, below ? over.nextSibling : over);
  }

  function onPointerUp() {
    if (!drag) return;
    var card = drag.card;
    drag.ph.parentNode.insertBefore(card, drag.ph);
    drag.ph.remove();
    card.style.position = '';
    card.style.left = '';
    card.style.top = '';
    card.style.width = '';
    card.style.height = '';
    card.style.pointerEvents = '';
    card.style.zIndex = '';
    card.classList.remove('is-drag');
    drag = null;
    // 重排后图表尺寸变了，Chart.js 需要 resize
    Object.keys(charts).forEach(function (id) { charts[id].resize(); });
    markDirty();
  }

  // 删除
  function removeCard(card) {
    var id = card.dataset.id;
    undoStack.push({ html: card.outerHTML, index: Array.prototype.indexOf.call(grid.children, card) });
    destroyChart(id);
    card.remove();
    delete DATA[id];
    delete ITEMS[id];
    markDirty();
    toast('已移除，可点「撤销」恢复');
  }

  // ------------------------------------------------------------------ 卡片设置浮层
  var popCard = null;

  function openPop(card) {
    popCard = card;
    var pop = $('#cardPop');
    var it = ITEMS[card.dataset.id] || {};
    $('#popTitle').textContent = (it.metric || '') + ' · 设置';

    // 指标下拉：只列当前图形类型支持的
    var catalog = safeJSON(($('#dashCatalog') || {}).textContent);
    var metrics = catalog.metrics || catalog || [];
    var sel = $('#popMetric');
    sel.innerHTML = '';
    metrics.forEach(function (m) {
      var o = document.createElement('option');
      o.value = m.key;
      o.textContent = m.title;
      if (m.key === it.metric) o.selected = true;
      sel.appendChild(o);
    });

    renderKindSeg(it);
    renderSpanSeg(it);
    renderHSeg(it);

    var r = card.getBoundingClientRect();
    pop.hidden = false;
    var top = r.top + 40;
    var left = Math.min(r.right - 268, window.innerWidth - 280);
    pop.style.top = Math.max(8, Math.min(top, window.innerHeight - pop.offsetHeight - 8)) + 'px';
    pop.style.left = Math.max(8, left) + 'px';
  }

  function closePop() { $('#cardPop').hidden = true; popCard = null; }

  function metricKinds(key) {
    var catalog = safeJSON(($('#dashCatalog') || {}).textContent);
    var list = catalog.metrics || catalog || [];
    var m = list.filter(function (x) { return x.key === key; })[0];
    return m ? m.kinds : [];
  }

  var KIND_LABEL = { kpi: '数字', line: '折线', area: '面积', bar: '柱状', hist: '直方', donut: '环形', list: '列表', stack: '堆叠', heat: '热力' };

  function renderKindSeg(it) {
    var box = $('#popKind');
    box.innerHTML = '';
    metricKinds(it.metric).forEach(function (k) {
      var b = document.createElement('button');
      b.type = 'button';
      b.textContent = KIND_LABEL[k] || k;
      if (k === it.kind) b.className = 'on';
      b.onclick = function () { changeKind(k); };
      box.appendChild(b);
    });
  }

  function renderSpanSeg(it) {
    var box = $('#popSpan');
    box.innerHTML = '';
    [[3, '1/4'], [4, '1/3'], [6, '1/2'], [8, '2/3'], [12, '整行']].forEach(function (p) {
      var b = document.createElement('button');
      b.type = 'button';
      b.textContent = p[1];
      if (Number(it.span) === p[0]) b.className = 'on';
      b.onclick = function () { changeSpan(p[0]); };
      box.appendChild(b);
    });
  }

  function renderHSeg(it) {
    var box = $('#popH');
    box.innerHTML = '';
    [[180, '矮'], [240, '中'], [320, '高']].forEach(function (p) {
      var b = document.createElement('button');
      b.type = 'button';
      b.textContent = p[1];
      if ((Number(it.h) || 240) === p[0]) b.className = 'on';
      b.onclick = function () { changeH(p[0]); };
      box.appendChild(b);
    });
  }

  function applyToCard(card, patch) {
    var id = card.dataset.id;
    ITEMS[id] = ITEMS[id] || {};
    Object.keys(patch).forEach(function (k) { ITEMS[id][k] = patch[k]; });
    if (patch.metric != null) card.dataset.metric = patch.metric;
    if (patch.kind != null) card.dataset.kind = patch.kind;
    if (patch.span != null) { card.dataset.span = patch.span; card.style.gridColumn = 'span ' + patch.span; }
    if (patch.h != null) card.dataset.h = patch.h;
  }

  function changeKind(kind) {
    if (!popCard) return;
    var card = popCard;
    var it = ITEMS[card.dataset.id];
    applyToCard(card, { kind: kind });
    // 数字/列表不需要高度；图表类若没设过高度给个默认值
    if (kind === 'kpi' || kind === 'list') it.h = 0;
    else if (!it.h) it.h = 240;
    card.dataset.h = it.h || 0;
    renderKindSeg(it);
    renderHSeg(it);
    refreshCard(card, it);
    markDirty();
  }

  function changeSpan(span) {
    if (!popCard) return;
    applyToCard(popCard, { span: span });
    renderSpanSeg(ITEMS[popCard.dataset.id]);
    Object.keys(charts).forEach(function (id) { charts[id].resize(); });
    markDirty();
  }

  function changeH(h) {
    if (!popCard) return;
    applyToCard(popCard, { h: h });
    var box = $('.chart-box', popCard);
    if (box) box.style.height = h + 'px';
    renderHSeg(ITEMS[popCard.dataset.id]);
    var ch = charts[popCard.dataset.id];
    if (ch) ch.resize();
    markDirty();
  }

  // ------------------------------------------------------------------ 模块库
  function openLib() {
    var body = $('#libBody');
    var catalog = safeJSON(($('#dashCatalog') || {}).textContent);
    // 后端 /catalog 直出数组；这里兼容一下「包了一层 metrics」的形态
    var metrics = Array.isArray(catalog) ? catalog : (catalog.metrics || []);
    var groups = {};
    metrics.forEach(function (m) { (groups[m.group] = groups[m.group] || []).push(m); });
    var html = '';
    Object.keys(groups).forEach(function (g) {
      html += '<div class="dd-sec">' + g + '</div>';
      groups[g].forEach(function (m) {
        html += '<button type="button" class="dd-item" data-key="' + m.key + '">' +
          '<span>' + m.title + '</span>' +
          '<span class="dd-kinds">' + m.kinds.map(function (k) { return KIND_LABEL[k] || k; }).join(' / ') + '</span>' +
          '<span class="dd-plus">+</span></button>';
      });
    });
    body.innerHTML = html || '<p class="muted small">当前账号没有可用指标</p>';
    $('#libDrawer').hidden = false;
    $('#dashScrim').hidden = false;
  }

  function closeLib() {
    $('#libDrawer').hidden = true;
    $('#dashScrim').hidden = true;
  }

  function addMetric(key) {
    var kinds = metricKinds(key);
    if (!kinds.length) { toast('这个指标没有可用的图形', true); return; }
    var kind = kinds[0];
    var item = {
      id: 'w' + Date.now().toString(36),
      metric: key,
      kind: kind,
      span: kind === 'kpi' ? 3 : 6,
      h: ['kpi', 'list'].indexOf(kind) >= 0 ? 0 : 240,
      opts: {}
    };
    ITEMS[item.id] = item;
    var card = buildCard(item);
    grid.appendChild(card);
    closeLib();
    markDirty();
    refreshCard(card, item);
    // 新卡在页面底部，滚动过去让用户看见
    card.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    toast('已添加「' + metricTitle(key) + '」，记得点「保存并完成」');
  }

  // ------------------------------------------------------------------ 方案切换
  function switchView(id) {
    if (!id) return;
    if (dirty && !window.confirm('有未保存的布局改动，切换方案会丢弃，继续？')) {
      $('#viewSelect').value = grid.dataset.viewId;
      return;
    }
    location.href = '/?view=' + encodeURIComponent(id);
  }

  function saveAs() {
    var name = window.prompt('新方案名字', '我的看板 ' + new Date().toLocaleDateString('zh-CN'));
    if (!name) return;
    api('/api/dashboard/views', {
      method: 'POST',
      body: { name: name.trim(), items: collectItems() }
    }).then(function (r) {
      location.href = '/?view=' + r.view.id;
    }).catch(function (e) { toast(e.message || '另存失败', true); });
  }

  // ------------------------------------------------------------------ 事件绑定
  function bind() {
    var gridEl = grid;

    gridEl.addEventListener('pointerdown', onHandleDown);
    window.addEventListener('pointermove', onPointerMove);
    window.addEventListener('pointerup', onPointerUp);
    window.addEventListener('pointercancel', onPointerUp);

    gridEl.addEventListener('click', function (e) {
      if (!editing) return;
      var gear = e.target.closest('.js-gear');
      if (gear) { openPop(gear.closest('.dash-card')); return; }
      var rm = e.target.closest('.js-remove');
      if (rm) { removeCard(rm.closest('.dash-card')); return; }
    });

    $('#btnEdit').addEventListener('click', function () {
      if (editing) { exitEditing(); return; }
      setEditing(true);
    });

    $('#btnDone').addEventListener('click', function () {
      var btn = this;
      btn.disabled = true;
      saveLayout().then(function () {
        setEditing(false);
        btn.disabled = false;
      }).catch(function (e) {
        btn.disabled = false;
        toast(e.message || '保存失败', true);
      });
    });

    $('#btnUndo').addEventListener('click', function () {
      var last = undoStack.pop();
      if (!last) { toast('没有可撤销的操作'); return; }
      var tmp = document.createElement('div');
      tmp.innerHTML = last.html;
      var node = tmp.firstElementChild;
      var ref = grid.children[last.index] || null;
      grid.insertBefore(node, ref);
      // 恢复后重新读入数据与图表
      var payload = $('.js-payload', node);
      if (payload) { try { DATA[node.dataset.id] = JSON.parse(payload.textContent); } catch (err) {} }
      ITEMS[node.dataset.id] = {
        id: node.dataset.id, metric: node.dataset.metric, kind: node.dataset.kind,
        span: Number(node.dataset.span) || 12, h: Number(node.dataset.h) || 0,
        opts: safeJSON(node.dataset.opts)
      };
      renderCard(node);
      markDirty();
      toast('已恢复，记得点「保存并完成」');
    });

    $('#btnLibrary').addEventListener('click', openLib);
    $('#btnSaveAs').addEventListener('click', saveAs);

    $('#libBody').addEventListener('click', function (e) {
      var item = e.target.closest('.dd-item');
      if (item) addMetric(item.dataset.key);
    });

    $('.js-close-lib').addEventListener('click', closeLib);
    $('#dashScrim').addEventListener('click', closeLib);

    $('#viewSelect').addEventListener('change', function (e) { switchView(e.target.value); });

    $('#cardPop').addEventListener('click', function (e) {
      if (e.target.closest('.js-close-pop')) closePop();
    });
    $('#popRemove').addEventListener('click', function () {
      if (popCard) { var c = popCard; closePop(); removeCard(c); }
    });
    $('#popMetric').addEventListener('change', function (e) {
      if (!popCard) return;
      var card = popCard;
      var kind = metricKinds(e.target.value)[0] || 'bar';
      applyToCard(card, { metric: e.target.value, kind: kind });
      var it = ITEMS[card.dataset.id];
      if (kind === 'kpi' || kind === 'list') it.h = 0;
      else if (!it.h) it.h = 240;
      card.dataset.h = it.h || 0;
      $('#popTitle').textContent = e.target.value + ' · 设置';
      renderKindSeg(it);
      renderSpanSeg(it);
      renderHSeg(it);
      refreshCard(card, it);
      markDirty();
    });

    // 点空白关闭浮层
    document.addEventListener('pointerdown', function (e) {
      var pop = $('#cardPop');
      if (pop.hidden) return;
      if (!pop.contains(e.target) && !e.target.closest('.js-gear')) closePop();
    });

    // 快捷键：编辑态 Esc 退出
    document.addEventListener('keydown', function (e) {
      if (e.key !== 'Escape' || !editing) return;
      if (!$('#cardPop').hidden) { closePop(); return; }
      if (!$('#libDrawer').hidden) { closeLib(); return; }
      exitEditing();
    });
  }

  // ------------------------------------------------------------------ 主题切换重绘
  function watchTheme() {
    var last = document.documentElement.getAttribute('data-theme') || '';
    new MutationObserver(function () {
      var now = document.documentElement.getAttribute('data-theme') || '';
      if (now === last) return;
      last = now;
      renderAll();     // 颜色取自 CSS 变量，重建最省事也最不会出错
    }).observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] });
  }

  // ------------------------------------------------------------------ 启动
  function boot() {
    if (typeof Chart === 'undefined') {
      $$('.chart-box', grid).forEach(function (b) {
        b.innerHTML = '<p class="chart-empty">图表组件加载失败，刷新重试</p>';
      });
    } else {
      Chart.defaults.font.family = getComputedStyle(document.body).fontFamily;
      Chart.defaults.font.size = 11;
      Chart.defaults.color = cssv('--muted');
    }
    loadInitial();
    if (typeof Chart !== 'undefined') renderAll();
    bind();
    watchTheme();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot);
  } else {
    boot();
  }
})();
