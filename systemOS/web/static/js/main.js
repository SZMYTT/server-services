function toggleSidebar() {
  document.getElementById('sidebar').classList.toggle('open');
  document.getElementById('sidebar-overlay')?.classList.toggle('visible');
}

function openModal(id) {
  document.getElementById(id).classList.remove('hidden');
}

function closeModal(id) {
  document.getElementById(id).classList.add('hidden');
}

// Close modal on overlay click
document.addEventListener('click', (e) => {
  if (e.target.classList.contains('modal-overlay')) {
    e.target.classList.add('hidden');
  }
});

// Filter tabs — filter .topic-row elements by data-status
function filterTopics(status) {
  document.querySelectorAll('.filter-tab').forEach(t => t.classList.remove('active'));
  document.querySelector(`[data-filter="${status}"]`)?.classList.add('active');

  document.querySelectorAll('.topic-row[data-status]').forEach(row => {
    if (status === 'all' || row.dataset.status === status) {
      row.style.display = '';
    } else {
      row.style.display = 'none';
    }
  });

  const visible = document.querySelectorAll('.topic-row[data-status]:not([style*="none"])').length;
  const empty = document.getElementById('topics-empty');
  if (empty) empty.style.display = visible === 0 ? '' : 'none';
}

// Auto-dismiss alerts after 4s
setTimeout(() => {
  document.querySelectorAll('.alert').forEach(a => {
    a.style.transition = 'opacity 0.4s';
    a.style.opacity = '0';
    setTimeout(() => a.remove(), 400);
  });
}, 4000);
