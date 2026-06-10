/**
 * AI RADAR — FAISS Index Page
 */

async function loadFaiss() {
    try {
        const info = await fetchJSON('/api/v1/vector/index-info');

        document.getElementById('faiss-size').textContent = formatNumber(info.size);
        document.getElementById('faiss-dim').textContent = info.dim;
        document.getElementById('faiss-status').textContent = info.loaded ? 'Загружен' : 'Пуст';
        document.getElementById('faiss-status').style.color = info.loaded ? '#16a34a' : '#9ca3af';
    } catch (err) {
        console.error('FAISS load error:', err);
        showToast('Ошибка загрузки информации о FAISS', 'error');
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
});
