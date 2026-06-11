/**
 * AI RADAR — Main Application
 * Interests loaded from DB categories (parser/LLM data).
 */

const API = {
    session: "/api/v1/user/session",
    me: "/api/v1/user/me",
    categories: "/api/v1/categories",
    interests: "/api/v1/user/interests",
    profile: "/api/v1/user/profile",
};

let availableCategories = [];
let selectedInterests = new Set();
let currentUser = null;

async function apiFetch(url, options = {}) {
    const response = await fetch(url, {
        credentials: "same-origin",
        headers: {
            "Content-Type": "application/json",
            ...(options.headers || {}),
        },
        ...options,
    });

    if (!response.ok) {
        const error = await response.json().catch(() => ({ detail: response.statusText }));
        throw new Error(error.detail || "API error");
    }

    if (response.status === 204) {
        return null;
    }
    return response.json();
}

async function init() {
    const screenEl = document.getElementById("screen-search");
    if (screenEl) screenEl.style.height = window.innerHeight + "px";
    document.getElementById("btn-start").addEventListener("click", async () => {
        await loadCategories();
        showScreen("interests");
        renderInterests("interests-grid");
    });

    document.getElementById("btn-save-interests").addEventListener("click", saveInterests);
    document.getElementById("btn-skip-interests").addEventListener("click", async () => {
        await saveInterests(true);
    });

    document.querySelectorAll(".tile").forEach(tile => {
        tile.addEventListener("click", async () => {
            const tileName = tile.dataset.tile;
            if (tileName === "settings") {
                await openSettings();
            }
            showScreen(tileName);
        });
    });

    document.querySelectorAll("[data-back]").forEach(btn => {
        btn.addEventListener("click", (e) => {
            e.preventDefault();
            showScreen("main");
        });
    });

    document.getElementById("btn-save-settings").addEventListener("click", saveSettings);

    document.getElementById("btn-logout").addEventListener("click", async () => {
        selectedInterests = new Set();
        currentUser = null;
        document.cookie = "ai_radar_user_session=; Max-Age=0; path=/";
        showScreen("onboarding");
    });

    try {
        currentUser = await apiFetch(API.session, { method: "POST" });
        syncUserToUi();
        await loadCategories();
        hydrateSelectedInterests();
        navigateAfterAuth();
        initChat();
    } catch (error) {
        console.error("Session init failed:", error);
        showScreen("onboarding");
        showToast("Не удалось подключиться к API. Проверьте сервер.", "info");
    }
}

function navigateAfterAuth() {
    if (currentUser && !currentUser.profile.onboarding_completed) {
        showScreen("onboarding");
        return;
    }
    showScreen("main");
}

function syncUserToUi() {
    if (!currentUser) {
        return;
    }
    const email = currentUser.profile.email || `user-${String(currentUser.id).slice(0, 8)}@ai-radar.local`;
    document.getElementById("user-email").textContent = email;
    document.getElementById("settings-email").textContent = email;
}

function hydrateSelectedInterests() {
    selectedInterests = new Set((currentUser?.interests || []).map(i => i.category));
}

async function loadCategories() {
    availableCategories = await apiFetch(API.categories);
}

function showScreen(name) {
    document.querySelectorAll(".screen").forEach(s => s.classList.remove("active"));
    document.getElementById(`screen-${name}`).classList.add("active");
    window.scrollTo(0, 0);
}

function renderInterests(containerId) {
    const container = document.getElementById(containerId);
    if (!availableCategories.length) {
        container.innerHTML = `<p class="subtitle">Категории пока не собраны парсерами. Запустите сбор в админке.</p>`;
        return;
    }

    container.innerHTML = availableCategories.map(category => `
        <div class="interest-chip ${selectedInterests.has(category.slug) ? "selected" : ""}" data-id="${category.slug}">
            <span>${category.icon}</span>
            <span>${category.label}</span>
            <span class="interest-count">${category.item_count}</span>
        </div>
    `).join("");

    container.querySelectorAll(".interest-chip").forEach(chip => {
        chip.addEventListener("click", () => {
            const id = chip.dataset.id;
            if (selectedInterests.has(id)) {
                selectedInterests.delete(id);
                chip.classList.remove("selected");
            } else {
                selectedInterests.add(id);
                chip.classList.add("selected");
            }
        });
    });
}

async function saveInterests(skip = false) {
    try {
        const categories = skip ? [] : Array.from(selectedInterests);
        currentUser = await apiFetch(API.interests, {
            method: "PUT",
            body: JSON.stringify({ categories }),
        });
        hydrateSelectedInterests();
        showScreen("main");
        if (skip) {
            showToast("Онбординг пропущен", "success");
        } else {
            showToast(`Сохранено ${categories.length} интересов`, "success");
        }
    } catch (error) {
        console.error("Save interests failed:", error);
        showToast("Не удалось сохранить интересы", "info");
    }
}

async function openSettings() {
    try {
        currentUser = await apiFetch(API.me);
        hydrateSelectedInterests();
        syncUserToUi();
        renderInterests("settings-interests-grid");
        document.getElementById("setting-email").checked = currentUser.profile.email_notifications;
        document.getElementById("setting-frequency").value = currentUser.profile.digest_frequency || "daily";
    } catch (error) {
        console.error("Settings load failed:", error);
        showToast("Не удалось загрузить настройки", "info");
    }
}

async function saveSettings() {
    try {
        const categories = Array.from(selectedInterests);
        const emailNotifications = document.getElementById("setting-email").checked;
        const digestFrequency = document.getElementById("setting-frequency").value;

        currentUser = await apiFetch(API.interests, {
            method: "PUT",
            body: JSON.stringify({ categories }),
        });
        currentUser = await apiFetch(API.profile, {
            method: "PUT",
            body: JSON.stringify({
                email_notifications: emailNotifications,
                digest_frequency: digestFrequency,
            }),
        });

        hydrateSelectedInterests();
        showToast("Настройки сохранены", "success");
        showScreen("main");
    } catch (error) {
        console.error("Save settings failed:", error);
        showToast("Не удалось сохранить настройки", "info");
    }
}

function showToast(message, type = "info") {
    const toast = document.createElement("div");
    toast.style.cssText = `
        position: fixed; bottom: 24px; right: 24px; z-index: 2000;
        background: #1e293b; border: 1px solid #334155; border-radius: 8px;
        padding: 14px 20px; min-width: 280px; box-shadow: 0 4px 12px rgba(0,0,0,0.3);
        display: flex; align-items: center; gap: 12px; animation: slideIn 0.3s ease;
        border-left: 3px solid ${type === "success" ? "#10b981" : "#3b82f6"};
        color: #f1f5f9; font-size: 14px;
    `;
    toast.innerHTML = `<span>${type === "success" ? "&#9989;" : "&#8505;"}</span><span>${message}</span>`;
    document.body.appendChild(toast);
    setTimeout(() => { toast.style.opacity = "0"; setTimeout(() => toast.remove(), 300); }, 3000);
}

// ─── Chat (AI Assistant) ───

const CHAT_API = {
    chat: "/api/v1/vector/chat",
    chats: "/api/v1/vector/chats",
};

let chatAbortController = null;
let isChatSending = false;
let isChatInitialized = false;
let currentChatId = null;
let chatList = [];

function scrollChatToBottom() {
    const container = document.getElementById("chat-messages");
    if (container) {
        container.scrollTop = container.scrollHeight;
    }
}

function hideWelcome() {
    const welcome = document.getElementById("chat-welcome");
    if (welcome) welcome.style.display = "none";
}

function renderMessage(role, content) {
    const container = document.getElementById("chat-messages");
    if (!container) return;

    hideWelcome();

    const div = document.createElement("div");
    div.className = `message message-${role === "user" ? "user" : "bot"}`;

    const avatar = document.createElement("div");
    avatar.className = "message-avatar";
    avatar.textContent = role === "user" ? "U" : "AI";

    const bubble = document.createElement("div");
    bubble.className = `message-bubble ${role === "user" ? "user" : "bot"}`;
    bubble.textContent = content;

    div.appendChild(avatar);
    div.appendChild(bubble);
    container.appendChild(div);
    scrollChatToBottom();
}

function showTypingIndicator() {
    const container = document.getElementById("chat-messages");
    if (!container) return;

    const existing = document.getElementById("typing-indicator");
    if (existing) existing.remove();

    const div = document.createElement("div");
    div.className = "message message-bot";
    div.id = "typing-indicator";

    const avatar = document.createElement("div");
    avatar.className = "message-avatar";
    avatar.textContent = "AI";

    const bubble = document.createElement("div");
    bubble.className = "message-bubble bot";
    bubble.innerHTML = '<div class="typing-indicator"><span></span><span></span><span></span></div>';

    div.appendChild(avatar);
    div.appendChild(bubble);
    container.appendChild(div);
    scrollChatToBottom();
}

function hideTypingIndicator() {
    const el = document.getElementById("typing-indicator");
    if (el) el.remove();
}

function updateChatSendButtonState() {
    const input = document.getElementById("chat-input");
    const sendBtn = document.getElementById("chat-send");
    const stopBtn = document.getElementById("chat-stop");

    if (!input || !sendBtn || !stopBtn) return;

    if (isChatSending) {
        sendBtn.style.display = "none";
        stopBtn.style.display = "flex";
    } else {
        sendBtn.style.display = "flex";
        stopBtn.style.display = "none";
    }

    sendBtn.disabled = isChatSending || !input.value.trim();
}

function autoResizeTextarea(textarea) {
    textarea.style.height = "auto";
    const newHeight = Math.min(textarea.scrollHeight, 120);
    textarea.style.height = newHeight + "px";
}

async function loadChatList() {
    try {
        const response = await fetch(CHAT_API.chats, {
            credentials: "same-origin",
        });
        if (!response.ok) return;
        chatList = await response.json();
        renderChatList();
    } catch (e) {
        console.error("Failed to load chats:", e);
    }
}

function renderChatList() {
    const list = document.getElementById("chat-list");
    if (!list) return;
    if (chatList.length === 0) {
        list.innerHTML = '<div class="chat-list-empty">Нет чатов. Создайте новый!</div>';
        return;
    }
    list.innerHTML = chatList.map(chat => `
        <button class="chat-list-item ${chat.id === currentChatId ? "active" : ""}" data-chat-id="${chat.id}">
            <span class="chat-list-item-title">${chat.title}</span>
            <span class="chat-list-item-delete" data-chat-id="${chat.id}" title="Удалить чат">&#10005;</span>
        </button>
    `).join("");

    // Chat switching
    list.querySelectorAll(".chat-list-item").forEach(item => {
        item.addEventListener("click", (e) => {
            if (e.target.classList.contains("chat-list-item-delete")) return;
            const chatId = item.dataset.chatId;
            if (chatId !== currentChatId) {
                switchChat(chatId);
            }
        });
    });

    // Delete buttons
    list.querySelectorAll(".chat-list-item-delete").forEach(btn => {
        btn.addEventListener("click", async (e) => {
            e.stopPropagation();
            const chatId = btn.dataset.chatId;
            if (confirm("Удалить этот чат?")) {
                await deleteChat(chatId);
            }
        });
    });
}

function showWelcome() {
    const welcome = document.getElementById("chat-welcome");
    if (welcome) welcome.style.display = "";
}

async function switchChat(chatId) {
    currentChatId = chatId;
    renderChatList();

    // Clear messages
    const container = document.getElementById("chat-messages");
    container.innerHTML = "";
    showWelcome();

    // Load messages from API
    try {
        const response = await fetch(`${CHAT_API.chats}/${chatId}/messages`, {
            credentials: "same-origin",
        });
        if (!response.ok) throw new Error("Failed to load messages");
        const messages = await response.json();
        if (messages.length > 0) {
            container.innerHTML = "";
            for (const msg of messages) {
                renderMessage(msg.role, msg.content);
            }
        }

        // Update chat title in header
        const chat = chatList.find(c => c.id === chatId);
        const titleEl = document.getElementById("chat-title");
        if (chat && titleEl) {
            titleEl.textContent = chat.title;
        }
    } catch (e) {
        console.error("Failed to load messages:", e);
    }

    // Enable input
    const input = document.getElementById("chat-input");
    if (input) input.disabled = false;
}

async function createNewChat() {
    try {
        const response = await fetch(CHAT_API.chats, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ title: "Новый чат" }),
            credentials: "same-origin",
        });
        if (!response.ok) throw new Error("Failed to create chat");
        const chat = await response.json();
        chatList.unshift(chat);
        await switchChat(chat.id);
    } catch (e) {
        console.error("Failed to create chat:", e);
    }
}

async function deleteChat(chatId) {
    try {
        const response = await fetch(`${CHAT_API.chats}/${chatId}`, {
            method: "DELETE",
            credentials: "same-origin",
        });
        if (!response.ok) throw new Error("Failed to delete chat");
        chatList = chatList.filter(c => c.id !== chatId);
        if (currentChatId === chatId) {
            currentChatId = null;
            const container = document.getElementById("chat-messages");
            container.innerHTML = "";
            const titleEl = document.getElementById("chat-title");
            if (titleEl) titleEl.textContent = "";
            showWelcome();
        }
        renderChatList();
    } catch (e) {
        console.error("Failed to delete chat:", e);
    }
}

async function sendChatMessage() {
    if (isChatSending) return;

    // Auto-create chat if none selected
    if (!currentChatId) {
        await createNewChat();
        if (!currentChatId) return;
    }

    const input = document.getElementById("chat-input");
    const message = input.value.trim();
    if (!message) return;

    input.value = "";
    input.style.height = "auto";

    // Add user message
    renderMessage("user", message);

    // Show typing
    showTypingIndicator();
    isChatSending = true;
    updateChatSendButtonState();

    chatAbortController = new AbortController();

    // Build history from current messages in DOM
    const history = [];
    const container = document.getElementById("chat-messages");
    const existingMessages = container.querySelectorAll(".message");
    for (const msgEl of existingMessages) {
        const bubble = msgEl.querySelector(".message-bubble");
        const isUser = msgEl.classList.contains("message-user");
        const isTyping = msgEl.id === "typing-indicator";
        if (bubble && !isTyping) {
            history.push({
                role: isUser ? "user" : "assistant",
                content: bubble.textContent,
            });
        }
    }

    try {
        const response = await fetch(CHAT_API.chat, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                chat_id: currentChatId,
                message: message,
                history: history.slice(-30, -1),
            }),
            signal: chatAbortController.signal,
            credentials: "same-origin",
        });

        if (!response.ok) {
            const error = await response.json().catch(() => ({ detail: "Unknown error" }));
            throw new Error(error.detail || `HTTP ${response.status}`);
        }

        const data = await response.json();
        hideTypingIndicator();
        renderMessage("assistant", data.reply);

        // Refresh chat list to get updated title/timestamp
        await loadChatList();

    } catch (error) {
        hideTypingIndicator();
        if (error.name === "AbortError") {
            renderMessage("assistant", "Ответ прерван.");
        } else {
            renderMessage("assistant", `Ошибка: ${error.message}`);
        }
    } finally {
        isChatSending = false;
        chatAbortController = null;
        updateChatSendButtonState();
    }
}

function stopChatMessage() {
    if (chatAbortController) {
        chatAbortController.abort();
        chatAbortController = null;
    }
}

function initChat() {
    if (isChatInitialized) return;
    isChatInitialized = true;

    const input = document.getElementById("chat-input");
    const sendBtn = document.getElementById("chat-send");
    const stopBtn = document.getElementById("chat-stop");
    const newChatBtn = document.getElementById("chat-new-btn");

    if (!input || !sendBtn || !stopBtn) return;

    // Load chat list
    loadChatList();

    // New chat button
    if (newChatBtn) {
        newChatBtn.addEventListener("click", createNewChat);
    }

    // Send button
    sendBtn.addEventListener("click", sendChatMessage);

    // Stop button
    stopBtn.addEventListener("click", stopChatMessage);

    // Keyboard events
    input.addEventListener("keydown", (e) => {
        if (e.key === "Enter" && !e.shiftKey) {
            e.preventDefault();
            sendChatMessage();
        }
    });

    // Auto-resize
    input.addEventListener("input", () => {
        autoResizeTextarea(input);
        updateChatSendButtonState();
    });
}

window.addEventListener("resize", () => {
    const el = document.getElementById("screen-search");
    if (el) el.style.height = window.innerHeight + "px";
});

document.addEventListener("DOMContentLoaded", init);
