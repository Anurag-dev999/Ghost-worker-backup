function showToast(msg, err) {
  const t = document.getElementById('toast');
  t.textContent = msg;
  t.style.background = err ? '#3d0f0f' : '#1e2d45';
  t.style.color = err ? '#f85149' : '#58a6ff';
  t.style.display = 'block';
  setTimeout(() => t.style.display = 'none', 3000);
}



function copyText(text, btn) {
  if (navigator.clipboard && window.isSecureContext) {
    navigator.clipboard.writeText(text).then(() => {
      const o = btn.textContent;
      btn.textContent = 'Copied!';
      setTimeout(() => btn.textContent = o, 1500);
      showToast('Tracker link copied!');
    });
  } else {
    const ta = document.createElement('textarea');
    ta.value = text;
    ta.style.position = 'fixed';
    ta.style.opacity = '0';
    document.body.appendChild(ta);
    ta.focus();
    ta.select();
    try {
      document.execCommand('copy');
      const o = btn.textContent;
      btn.textContent = 'Copied!';
      setTimeout(() => btn.textContent = o, 1500);
      showToast('Tracker link copied!');
    } catch (e) {
      prompt('Copy this tracker link manually:', text);
    }
    document.body.removeChild(ta);
  }
}

function updateStatus(id, status, btn) {
  fetch('/api/update_status', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ lead_id: id, new_status: status })
  })
  .then(r => r.json())
  .then(d => {
    if (d.success) {
      showToast('Status updated: ' + status);
      setTimeout(() => location.reload(), 800);
    } else {
      showToast(d.error || 'Update failed', true);
    }
  })
  .catch(e => showToast('Network error', true));
}


function runQuery() {
  const inp = document.getElementById('query-inp');
  const q = inp.value.trim();
  if (!q) { showToast('Enter a search query first', true); return; }

  const badge = document.getElementById('running-badge');
  if (badge) badge.style.display = 'inline-block';

  // Step 1: Add to scheduler queue
  fetch('/api/add_query', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ query: q })
  }).then(r => r.json()).then(d => {
    if (!d.success) {
      showToast(d.error || 'Failed to add query', true);
      if (badge) badge.style.display = 'none';
      return;
    }

    // Step 2: Run pipeline immediately
    fetch('/api/run_query', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ query: q })
    }).then(r => r.json()).then(d2 => {
      if (d2.success) {
        showToast('Pipeline started + added to scheduler: ' + q);
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
  const existing = Array.from(tags.querySelectorAll('.qtag'))
    .map(el => el.dataset.query);
  if (existing.includes(q)) return;
  const div = document.createElement('div');
  div.className = 'qtag';
  div.dataset.query = q;
  div.innerHTML = q + ' <span class="qtag-x" onclick="removeQuery(this.parentElement.dataset.query)">&times;</span>';
  tags.appendChild(div);
}

function removeQuery(q) {
  if (!q) return;
  fetch('/api/remove_query', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ query: q })
  })
  .then(r => r.json())
  .then(d => {
    if (d.success) {
      document.querySelectorAll('.qtag').forEach(el => {
        if (el.dataset.query === q) el.remove();
      });
      showToast('Query removed from scheduler');
    }
  });
}

function saveConfig() {
  const scrape   = parseInt(document.getElementById('scrape-interval').value) || 12;
  const pipeline = parseInt(document.getElementById('pipeline-interval').value) || 1;
  const life     = parseInt(document.getElementById('lifecycle-interval').value) || 6;

  if (scrape < 1 || pipeline < 1 || life < 1) {
    showToast('Values must be at least 1 hour', true);
    return;
  }

  fetch('/api/update_config', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      scrape_interval_hours:   scrape,
      pipeline_interval_hours: pipeline,
      lifecycle_check_hours:   life
    })
  })
  .then(r => r.json())
  .then(d => {
    if (d.success) showToast('Scheduler settings saved!');
    else showToast(d.error || 'Save failed', true);
  });
}

function refreshLogs() {
  fetch('/api/logs')
  .then(r => r.json())
  .then(d => {
    const box = document.getElementById('log-box');
    if (box) {
      box.textContent = d.logs || 'No logs yet.';
      box.scrollTop = box.scrollHeight;
    }
  })
  .catch(() => {
    const box = document.getElementById('log-box');
    if (box) box.textContent = 'Could not load logs.';
  });
}

// Init on page load
document.addEventListener('DOMContentLoaded', () => {
  // Enter key on query input
  const inp = document.getElementById('query-inp');
  if (inp) {
    inp.addEventListener('keydown', e => {
      if (e.key === 'Enter') runQuery();
    });
  }

  // Load logs immediately
  refreshLogs();

  // Refresh logs every 15 seconds
  setInterval(refreshLogs, 15000);

  // Auto-refresh full page every 60 seconds
  setInterval(() => location.reload(), 60000);
});

// Pause/Resume outreach for a lead
document.addEventListener('DOMContentLoaded', function() {
  document.querySelectorAll('.pause-btn').forEach(function(btn) {
    btn.addEventListener('click', function() {
      const id     = parseInt(this.dataset.id);
      const paused = parseInt(this.dataset.paused);
      const newPaused = paused === 1 ? 0 : 1;
      const action = newPaused === 1 ? 'Pause' : 'Resume';
      if (!confirm(action + ' outreach for this lead?')) return;
      fetch('/api/pause_lead', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({lead_id: id, paused: newPaused})
      }).then(r => r.json()).then(d => {
        if (d.success) {
          showToast('Outreach ' + (newPaused ? 'paused' : 'resumed'));
          setTimeout(() => location.reload(), 600);
        } else showToast(d.error, true);
      });
    });
  });

  // Toggle message drawer
  document.querySelectorAll('.msg-btn').forEach(function(btn) {
    btn.addEventListener('click', function() {
      const id     = this.dataset.id;
      const drawer = document.getElementById('msgs-' + id);
      if (drawer) {
        const isOpen = drawer.style.display !== 'none';
        drawer.style.display = isOpen ? 'none' : 'block';
        this.textContent = isOpen ? '📋 Msgs' : '✖ Close';
      }
    });
  });
});
