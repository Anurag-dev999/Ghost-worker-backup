function showToast(msg, err) {
  const t = document.getElementById('toast');
  t.textContent = msg;
  t.className = 'toast' + (err ? ' err' : '');
  t.style.display = 'block';
  setTimeout(() => t.style.display = 'none', 3000);
}

function copyText(text, btn) {
  if (!text) { showToast('Nothing to copy', true); return; }
  if (navigator.clipboard && window.isSecureContext) {
    navigator.clipboard.writeText(text).then(() => {
      const o = btn.textContent;
      btn.textContent = 'Copied!';
      setTimeout(() => btn.textContent = o, 1500);
      showToast('Copied!');
    });
  } else {
    const ta = document.createElement('textarea');
    ta.value = text;
    ta.style.cssText = 'position:fixed;opacity:0;top:0;left:0';
    document.body.appendChild(ta);
    ta.focus(); ta.select();
    try {
      document.execCommand('copy');
      const o = btn.textContent;
      btn.textContent = 'Copied!';
      setTimeout(() => btn.textContent = o, 1500);
      showToast('Copied!');
    } catch (e) { prompt('Copy this:', text); }
    document.body.removeChild(ta);
  }
}

function runQuery() {
  const inp   = document.getElementById('query-inp');
  const q     = inp.value.trim();
  if (!q) { showToast('Enter a search query first', true); return; }

  const badge = document.getElementById('running-badge');
  if (badge) badge.style.display = 'inline-block';

  // Add to scheduler queue first
  fetch('/api/add_query', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({query: q})
  }).then(r => r.json()).then(d => {
    if (!d.success) {
      showToast(d.error || 'Failed to add query', true);
      if (badge) badge.style.display = 'none';
      return;
    }
    // Then run pipeline
    fetch('/api/run_query', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({query: q})
    }).then(r => r.json()).then(d2 => {
      if (d2.success) {
        showToast('Pipeline started: ' + q);
        inp.value = '';
        addQueryTag(q);
        let count = 0;
        const iv = setInterval(() => {
          refreshLogs();
          count++;
          if (count > 30) {
            clearInterval(iv);
            if (badge) badge.style.display = 'none';
            location.reload();
          }
        }, 5000);
      } else {
        showToast(d2.error || 'Pipeline failed', true);
        if (badge) badge.style.display = 'none';
      }
    });
  }).catch(e => {
    showToast('Network error: ' + e, true);
    if (badge) badge.style.display = 'none';
  });
}

function addQueryTag(q) {
  const tags = document.getElementById('query-tags');
  if (!tags) return;
  const existing = Array.from(tags.querySelectorAll('.qtag')).map(el => el.dataset.query);
  if (existing.includes(q)) return;
  const div = document.createElement('div');
  div.className = 'qtag';
  div.dataset.query = q;
  div.innerHTML = q + ' <span class="qx" data-query="' + q.replace(/"/g,'&quot;') + '">×</span>';
  div.querySelector('.qx').addEventListener('click', function() { removeQuery(this.dataset.query); });
  tags.appendChild(div);
}

function removeQuery(q) {
  if (!q) return;
  fetch('/api/remove_query', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({query: q})
  }).then(r => r.json()).then(d => {
    if (d.success) {
      document.querySelectorAll('.qtag').forEach(el => {
        if (el.dataset.query === q) el.remove();
      });
      showToast('Query removed from scheduler');
    }
  });
}

function saveConfig() {
  const scrape = parseInt(document.getElementById('scrape-interval').value) || 12;
  const life   = parseInt(document.getElementById('lifecycle-interval').value) || 6;
  if (scrape < 1 || life < 1) { showToast('Values must be at least 1 hour', true); return; }
  fetch('/api/update_config', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({scrape_interval_hours: scrape, lifecycle_check_hours: life})
  }).then(r => r.json()).then(d => {
    if (d.success) showToast('Settings saved!');
    else showToast(d.error || 'Save failed', true);
  });
}

function refreshLogs() {
  fetch('/api/logs').then(r => r.json()).then(d => {
    const box = document.getElementById('log-box');
    if (box) { box.textContent = d.logs || 'No logs yet.'; box.scrollTop = box.scrollHeight; }
  }).catch(() => {
    const box = document.getElementById('log-box');
    if (box) box.textContent = 'Could not load logs.';
  });
}

// ── Sort ─────────────────────────────────────────────────────────────────────

function applySort() {
  const mode      = document.getElementById('sort-sel').value;
  const batchWrap = document.getElementById('batch-wrap');
  const batches   = Array.from(batchWrap.querySelectorAll('.batch'));

  if (mode === 'batch') {
    // Default: newest batch first — reload preserves server order
    location.reload();
    return;
  }

  // Collect ALL cards across all batches
  const allCards = [];
  batches.forEach(batch => {
    batch.querySelectorAll('.lead-card').forEach(card => {
      allCards.push({card, batch});
    });
  });

  if (mode === 'id_asc') {
    allCards.sort((a, b) => parseInt(a.card.dataset.id) - parseInt(b.card.dataset.id));
  } else if (mode === 'name_az') {
    allCards.sort((a, b) => (a.card.dataset.name || '').localeCompare(b.card.dataset.name || ''));
  } else if (mode === 'scraped_new') {
    allCards.sort((a, b) => {
      const da = a.card.dataset.created || '';
      const db = b.card.dataset.created || '';
      return db.localeCompare(da);
    });
  }

  // Flatten into a single batch when sorted
  batchWrap.innerHTML = '';
  const flatBatch = document.createElement('div');
  flatBatch.className = 'batch';
  flatBatch.innerHTML = '<div class="batch-header"><span class="batch-label">All Leads — sorted by ' +
    {id_asc:'ID', name_az:'Name A–Z', scraped_new:'Newest First'}[mode] +
    '</span><span class="batch-count">' + allCards.length + ' leads</span></div>';
  allCards.forEach(({card}) => flatBatch.appendChild(card));
  batchWrap.appendChild(flatBatch);

  // Re-wire buttons in the newly moved cards
  wireButtons();
}

// ── Button wiring (called on load + after sort) ────────────────────────────

function wireButtons() {
  document.querySelectorAll('.pause-btn').forEach(function(btn) {
    // Remove existing listener by cloning
    const clone = btn.cloneNode(true);
    btn.parentNode.replaceChild(clone, btn);
    clone.addEventListener('click', function() {
      const id      = parseInt(this.dataset.id);
      const paused  = parseInt(this.dataset.paused);
      const newPaused = paused === 1 ? 0 : 1;
      const action  = newPaused === 1 ? 'Pause' : 'Resume';
      if (!confirm(action + ' outreach for this lead?')) return;
      fetch('/api/pause_lead', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({lead_id: id, paused: newPaused})
      }).then(r => r.json()).then(d => {
        if (d.success) { showToast('Outreach ' + (newPaused ? 'paused' : 'resumed')); setTimeout(() => location.reload(), 600); }
        else showToast(d.error, true);
      });
    });
  });

  document.querySelectorAll('.msg-btn').forEach(function(btn) {
    const clone = btn.cloneNode(true);
    btn.parentNode.replaceChild(clone, btn);
    clone.addEventListener('click', function() {
      const id     = this.dataset.id;
      const drawer = document.getElementById('msgs-' + id);
      if (!drawer) return;
      const isOpen = drawer.style.display !== 'none';
      drawer.style.display = isOpen ? 'none' : 'block';
      this.textContent = isOpen ? '📋 Msgs' : '✖ Close';
    });
  });
}

// ── Init ──────────────────────────────────────────────────────────────────────

document.addEventListener('DOMContentLoaded', function() {
  wireButtons();
  refreshLogs();
  setInterval(refreshLogs, 15000);
  setInterval(() => location.reload(), 60000);
});
