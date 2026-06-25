// FactoryBrain — shared helpers

function setLoading(btn, on, idleLabel) {
  if (!btn) return;
  const label = btn.querySelector('.btn-label');
  if (on) {
    btn.disabled = true;
    btn.dataset.idle = label ? label.textContent : '';
    if (label) label.innerHTML = '<span class="spinner" style="display:inline-block;vertical-align:middle"></span>';
    else btn.innerHTML = '<span class="spinner"></span>';
  } else {
    btn.disabled = false;
    if (label) label.textContent = idleLabel || btn.dataset.idle || 'Done';
    else btn.textContent = idleLabel || 'Done';
  }
}

async function toggleTask(taskId, el) {
  const res = await fetch('/api/task-toggle', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ task_id: taskId })
  });
  const data = await res.json();
  if (data.ok) {
    const item = el.closest('.task-item');
    if (data.done) { el.classList.add('checked'); item.classList.add('done'); }
    else { el.classList.remove('checked'); item.classList.remove('done'); }
  }
}

// Generic modal helpers
function openModal(id){ document.getElementById(id).classList.add('show'); }
function closeModal(id){ document.getElementById(id).classList.remove('show'); }
document.addEventListener('click', e => {
  if (e.target.classList.contains('modal-overlay')) e.target.classList.remove('show');
});
