async function triggerAction(action, taskId, rowElementId) {
    const row = document.getElementById(rowElementId);
    if (!row) return;
    
    // Optimistically disable buttons
    const buttons = row.querySelectorAll('button');
    buttons.forEach(btn => {
        btn.disabled = true;
        btn.style.opacity = '0.5';
        btn.style.cursor = 'not-allowed';
    });
    
    try {
        const response = await fetch(`/api/tasks/${taskId}/${action}`, {
            method: 'POST'
        });
        
        if (response.ok) {
            // Smoothly remove row
            row.classList.add('fade-out');
            setTimeout(() => {
                row.remove();
                checkIfTableEmpty();
            }, 250); 
        } else {
            console.error(`Failed to ${action} task ${taskId}`);
            // Re-enable if failed
            buttons.forEach(btn => {
                btn.disabled = false;
                btn.style.opacity = '1';
                btn.style.cursor = 'pointer';
            });
        }
    } catch (err) {
        console.error(err);
    }
}

function approveTask(taskId, rowId) {
    triggerAction('approve', taskId, rowId);
}

function declineTask(taskId, rowId) {
    triggerAction('decline', taskId, rowId);
}

function checkIfTableEmpty() {
    const tbody = document.querySelector('.table-glass tbody');
    if (tbody && tbody.children.length === 0) {
        // Show empty state if nothing left
        const table = document.querySelector('.table-glass');
        const parent = table.parentElement;
        table.remove();
        parent.innerHTML += `
            <div class="empty-state">
                <h2>All caught up!</h2>
                <p>No more pending tasks require your approval right now.</p>
            </div>
        `;
    }
}
