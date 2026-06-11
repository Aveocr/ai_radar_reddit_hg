/**
 * AI RADAR — FAISS Index Page
 */

async function loadFaiss() {
    try {
        const info = await fetchJSON('/api/v1/vector/index-info');

        document.getElementById('faiss-size').textContent = formatNumber(info.size);
        document.getElementById('faiss-db-count').textContent = formatNumber(info.db_count);
        document.getElementById('faiss-dim').textContent = info.dim;
        document.getElementById('faiss-status').textContent = info.loaded ? 'Загружен' : 'Пуст';
        document.getElementById('faiss-status').style.color = info.loaded ? '#16a34a' : '#9ca3af';
    } catch (err) {
        console.error('FAISS load error:', err);
        showToast('Ошибка загрузки информации о FAISS', 'error');
    }
}

async function loadLlmSummaryConfig() {
    try {
        const res = await fetchJSON('/api/v1/admin/llm/summary-config');
        const checkbox = document.getElementById('llm-summary-checkbox');
        const status = document.getElementById('llm-summary-status');
        if (checkbox) checkbox.checked = res.enabled;
        if (status) {
            status.textContent = res.enabled ? '✓ Включено' : '✗ Отключено';
            status.style.color = res.enabled ? '#16a34a' : '#9ca3af';
        }
    } catch (err) {
        console.error('LLM summary config load error:', err);
    }
}

async function toggleLlmSummary(enabled) {
    try {
        const res = await fetchJSON('/api/v1/admin/llm/summary-config', {
            method: 'POST',
            body: JSON.stringify({ enabled }),
        });
        const checkbox = document.getElementById('llm-summary-checkbox');
        const status = document.getElementById('llm-summary-status');
        if (checkbox) checkbox.checked = res.enabled;
        if (status) {
            status.textContent = res.enabled ? '✓ Включено' : '✗ Отключено';
            status.style.color = res.enabled ? '#16a34a' : '#9ca3af';
        }
        showToast(`LLM Summary ${res.enabled ? 'включено' : 'отключено'}`, 'success');
    } catch (err) {
        console.error('LLM summary toggle error:', err);
        showToast('Ошибка переключения LLM Summary', 'error');
        // Revert checkbox
        const checkbox = document.getElementById('llm-summary-checkbox');
        if (checkbox) checkbox.checked = !enabled;
    }
}

document.addEventListener('DOMContentLoaded', () => {
    document.getElementById('btn-rebuild-faiss')?.addEventListener('click', async () => {
        const btn = document.getElementById('btn-rebuild-faiss');
        const log = document.getElementById('faiss-rebuild-log');

        btn.disabled = true;
        btn.textContent = '⏳ Перестроение...';
        log.innerHTML = '<p class="text-muted">Запуск перестроения индекса...</p>';

        try {
            const result = await fetchJSON('/api/v1/vector/rebuild', { method: 'POST' });
            log.innerHTML = `<p style="color:#16a34a;">✓ Индекс перестроен: ${result.indexed_count} векторов</p>`;
            showToast(`FAISS: ${result.indexed_count} векторов проиндексировано`, 'success');
            loadFaiss();
        } catch (err) {
            log.innerHTML = `<p style="color:#dc2626;">✗ Ошибка: ${err.message}</p>`;
            showToast('Ошибка перестроения FAISS', 'error');
        } finally {
            btn.disabled = false;
            btn.textContent = '⚡ Перестроить индекс';
        }
    });

    // LLM Summary toggle
    loadLlmSummaryConfig();
    const checkbox = document.getElementById('llm-summary-checkbox');
    if (checkbox) {
        checkbox.addEventListener('change', () => {
            toggleLlmSummary(checkbox.checked);
        });
    }
});
