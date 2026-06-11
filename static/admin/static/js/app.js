/**
 * AI RADAR — Admin Dashboard Main Router
 */

let currentPage = 'dashboard';
let refreshInterval = null;

document.addEventListener('DOMContentLoaded', () => {
    initNavigation();
    initModals();
    initFilters();
    initRefresh();

    // Initial load
    loadPage(currentPage);

    // Auto-refresh dashboard and pipeline every 10s (skip if user is editing)
    refreshInterval = setInterval(() => {
        if (currentPage === 'dashboard') {
            loadDashboard();
        } else if (currentPage === 'pipeline') {
            const active = document.activeElement;
            if (active && (active.tagName === 'INPUT' || active.tagName === 'SELECT' || active.tagName === 'TEXTAREA')) {
                return;
            }
            loadPipeline();
            loadScheduler();
        }
    }, 10000);
});

function initNavigation() {
    document.querySelectorAll('.nav-item').forEach(item => {
        item.addEventListener('click', (e) => {
            e.preventDefault();
            const page = item.dataset.page;
            if (page) {
                switchPage(page);
            }
        });
    });
}

function switchPage(page) {
    currentPage = page;

    // Update nav
    document.querySelectorAll('.nav-item').forEach(item => {
        item.classList.toggle('active', item.dataset.page === page);
    });

    // Update page title
    const titles = {
        dashboard: 'Дашборд',
        pipeline: 'Pipeline',
        tasks: 'Задачи парсеров',
        logs: 'Логи',
        sources: 'Источники',
        dbstats: 'База данных',
        faiss: 'FAISS',
        grafana: 'Grafana',
    };
    document.getElementById('page-title').textContent = titles[page] || page;

    // Show page
    document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));
    document.getElementById(`page-${page}`).classList.add('active');

    // Load data
    loadPage(page);
}

function loadPage(page) {
    switch (page) {
        case 'dashboard':
            loadDashboard();
            break;
        case 'pipeline':
            loadPipeline();
            loadScheduler();
            break;
        case 'tasks':
            loadTasks();
            break;
        case 'logs':
            loadLogs();
            break;
        case 'sources':
            loadSources();
            break;
        case 'dbstats':
            loadDbStats();
            break;
        case 'faiss':
            loadFaiss();
            break;
        case 'grafana':
            break;
    }
}

function initFilters() {
    document.getElementById('btn-filter-tasks')?.addEventListener('click', () => {
        loadTasks();
    });

    document.getElementById('btn-filter-logs')?.addEventListener('click', () => {
        loadLogs();
    });
}

function initRefresh() {
    document.getElementById('btn-refresh').addEventListener('click', () => {
        loadPage(currentPage);
        showToast('Данные обновлены', 'info');
    });
}
