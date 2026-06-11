/**
 * AI RADAR — API Client
 */

const API_BASE = '/api/v1/admin';

async function fetchJSON(url, options = {}) {
    const headers = new Headers(options.headers || {});
    if (options.body != null && !headers.has('Content-Type')) {
        headers.set('Content-Type', 'application/json');
    }

    const response = await fetch(url, {
        ...options,
        headers,
        credentials: 'include',  // ← ВАЖНО: отправляем cookie
    });

    // При 401 — редирект на логин
    if (response.status === 401) {
        window.location.href = '/admin/login';
        return;
    }

    if (!response.ok) {
        const err = await response.json().catch(() => ({}));
        throw new Error(err.detail || err.error || `HTTP ${response.status}`);
    }
    return response.json();
}

function api(path, options = {}) {
    return fetchJSON(`${API_BASE}${path}`, options);
}
