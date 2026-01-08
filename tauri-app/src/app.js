// Tauri API
const { invoke } = window.__TAURI__.core;

// State
let sessions = [];
let currentSession = null;
let currentSessionData = null;
let searchTimeout = null;
let sortNewestFirst = true;
let selectedMessageIndex = -1;
let watchInterval = null;

// Virtual scroll state
let allMessages = [];           // All messages (sorted)
let messageHeights = new Map(); // msg_id -> actual height
let messageOffsets = [];        // Cumulative offsets for each message
let totalHeight = 0;
let visibleRange = { start: 0, end: 20 };
const ESTIMATED_HEIGHT = 150;   // Default estimate per message
const BUFFER_PX = 400;          // Render buffer above/below viewport
const GAP = 16;                 // Gap between messages

// DOM elements
const sessionList = document.getElementById('session-list');
const sessionCount = document.getElementById('session-count');
const content = document.getElementById('content');
const searchInput = document.getElementById('search-input');
const syncBtn = document.getElementById('sync-btn');
const sortBtn = document.getElementById('sort-btn');
const scrollTopBtn = document.getElementById('scroll-top-btn');
const shortcutsBtn = document.getElementById('shortcuts-btn');
const shortcutsModal = document.getElementById('shortcuts-modal');
const modalClose = document.getElementById('modal-close');
const statusText = document.getElementById('status-text');
const syncStatusEl = document.getElementById('sync-status');

// API calls via Tauri invoke
async function fetchSessions() {
    return await invoke('get_sessions', { limit: 1000 });
}

async function fetchMessages(sessionId) {
    return await invoke('get_messages', { sessionId });
}

async function searchMessages(query) {
    return await invoke('search', { query, limit: 50 });
}

async function triggerSync() {
    syncBtn.disabled = true;
    syncBtn.textContent = '↻ Syncing...';
    try {
        const stats = await invoke('trigger_sync');
        syncStatusEl.textContent = `Synced ${stats.synced} sessions`;
        await loadSessions();
    } finally {
        syncBtn.disabled = false;
        syncBtn.textContent = '↻ Sync';
    }
}

// Watch for session updates
function startWatching(sessionId) {
    if (watchInterval) {
        clearInterval(watchInterval);
    }
    if (!sessionId) return;

    watchInterval = setInterval(async () => {
        try {
            const updated = await invoke('check_session_update', { sessionId });
            if (updated && currentSession && currentSession.session_id === sessionId) {
                await invoke('sync_session', { sessionId });
                const messages = await fetchMessages(sessionId);
                currentSessionData = { session: currentSession, messages };
                renderSession(currentSessionData);
            }
        } catch (e) {
            console.error('Watch error:', e);
        }
    }, 1500);
}

// Sort functions
function toggleSort() {
    sortNewestFirst = !sortNewestFirst;
    sortBtn.textContent = sortNewestFirst ? '↓ Newest first' : '↑ Oldest first';
    if (currentSessionData) {
        renderSession(currentSessionData);
    }
}

// Render functions
function renderSessionList() {
    sessionCount.textContent = sessions.length;
    sessionList.innerHTML = sessions.map(s => `
        <li class="session-item ${currentSession?.session_id === s.session_id ? 'active' : ''}"
            data-id="${s.session_id}">
            <div class="session-project">${escapeHtml(s.project || '')}</div>
            <div class="session-title">${escapeHtml(s.first_message || 'No message')}</div>
            <div class="session-meta">
                <span class="agent-name ${s.agent || 'claude'}">${formatAgentName(s.agent)}</span>
                <span class="meta-sep">·</span>
                <span>${s.message_count} msgs</span>
                <span class="meta-sep">·</span>
                <span>${formatDate(s.started_at)}</span>
            </div>
        </li>
    `).join('');

    sessionList.querySelectorAll('.session-item').forEach(item => {
        item.addEventListener('click', () => loadSession(item.dataset.id));
    });
}

// Calculate message offsets based on known or estimated heights
function calculateOffsets() {
    messageOffsets = [];
    if (allMessages.length === 0) {
        totalHeight = 0;
        return 0;
    }
    let offset = 0;
    for (let i = 0; i < allMessages.length; i++) {
        messageOffsets.push(offset);
        const height = messageHeights.get(allMessages[i].msg_id) || ESTIMATED_HEIGHT;
        offset += height + GAP;
    }
    totalHeight = Math.max(0, offset - GAP); // Remove last gap, ensure non-negative
    return totalHeight;
}

// Update container height after offset recalculation
function updateContainerHeight() {
    const messagesContainer = document.querySelector('.messages');
    if (messagesContainer) {
        messagesContainer.style.minHeight = `${totalHeight}px`;
    }
}

// Find which messages are visible given scroll position
function getVisibleRange(scrollTop, containerHeight) {
    if (allMessages.length === 0) return { start: 0, end: 0 };

    const viewStart = Math.max(0, scrollTop - BUFFER_PX);
    const viewEnd = scrollTop + containerHeight + BUFFER_PX;

    let start = 0;
    let end = allMessages.length;

    // Binary search for start
    let lo = 0, hi = allMessages.length - 1;
    while (lo <= hi) {
        const mid = Math.floor((lo + hi) / 2);
        const msgBottom = messageOffsets[mid] + (messageHeights.get(allMessages[mid].msg_id) || ESTIMATED_HEIGHT);
        if (msgBottom < viewStart) {
            lo = mid + 1;
        } else {
            hi = mid - 1;
        }
    }
    start = Math.max(0, lo - 1);

    // Binary search for end
    lo = start;
    hi = allMessages.length - 1;
    while (lo <= hi) {
        const mid = Math.floor((lo + hi) / 2);
        if (messageOffsets[mid] > viewEnd) {
            hi = mid - 1;
        } else {
            lo = mid + 1;
        }
    }
    end = Math.min(allMessages.length, hi + 2);

    return { start, end };
}

// Debounced height recalculation
let heightRecalcTimeout = null;
function scheduleHeightRecalc() {
    if (heightRecalcTimeout) return;
    heightRecalcTimeout = setTimeout(() => {
        heightRecalcTimeout = null;
        calculateOffsets();
        updateContainerHeight();
        // Always re-render minimap since offsets may have changed even if totalHeight didn't
        renderMinimap();
        updateMinimapViewport(content.scrollTop, content.clientHeight);
    }, 100);
}

// Render only visible messages
function renderVisibleMessages() {
    const messagesContainer = document.querySelector('.messages');
    if (!messagesContainer) return;

    const { start, end } = visibleRange;
    const topSpacer = start > 0 ? messageOffsets[start] : 0;
    const bottomSpacer = end < allMessages.length
        ? totalHeight - messageOffsets[end - 1] - (messageHeights.get(allMessages[end - 1]?.msg_id) || ESTIMATED_HEIGHT)
        : 0;

    let html = `<div class="message-spacer" style="height: ${topSpacer}px;"></div>`;

    for (let i = start; i < end && i < allMessages.length; i++) {
        const m = allMessages[i];
        html += `
            <div class="message ${m.role} ${i === selectedMessageIndex ? 'selected' : ''}"
                 id="${m.msg_id}" data-index="${i}">
                <div class="message-header">
                    <span class="message-role">${m.role}</span>
                    <span class="message-time">${formatTime(m.timestamp)}</span>
                </div>
                <div class="message-content">${formatContent(m.content)}</div>
            </div>
        `;
    }

    html += `<div class="message-spacer" style="height: ${Math.max(0, bottomSpacer)}px;"></div>`;

    messagesContainer.innerHTML = html;

    // Measure rendered messages and update heights
    let heightsChanged = false;
    messagesContainer.querySelectorAll('.message').forEach(msg => {
        const id = msg.id;
        const height = msg.offsetHeight;
        if (height > 0 && messageHeights.get(id) !== height) {
            messageHeights.set(id, height);
            heightsChanged = true;
        }
        msg.addEventListener('click', () => {
            selectMessage(parseInt(msg.dataset.index));
        });
    });

    // Schedule recalculation if heights changed
    if (heightsChanged) {
        scheduleHeightRecalc();
    }
}

// Throttled scroll handler
let scrollRaf = null;
function handleScroll() {
    if (scrollRaf) return;
    scrollRaf = requestAnimationFrame(() => {
        scrollRaf = null;
        const scrollTop = content.scrollTop;
        const containerHeight = content.clientHeight;
        const newRange = getVisibleRange(scrollTop, containerHeight);

        if (newRange.start !== visibleRange.start || newRange.end !== visibleRange.end) {
            visibleRange = newRange;
            renderVisibleMessages();
        }

        updateMinimapViewport(scrollTop, containerHeight);
    });
}

// Render minimap
function renderMinimap() {
    const minimap = document.querySelector('.minimap');
    if (!minimap || allMessages.length === 0) return;

    const canvas = minimap.querySelector('canvas');
    const ctx = canvas.getContext('2d');
    const dpr = window.devicePixelRatio || 1;

    // Set canvas size
    canvas.width = 80 * dpr;
    canvas.height = minimap.clientHeight * dpr;
    ctx.scale(dpr, dpr);

    const canvasHeight = minimap.clientHeight;
    const scale = canvasHeight / Math.max(totalHeight, 1);

    ctx.clearRect(0, 0, 80, canvasHeight);

    // Draw messages
    for (let i = 0; i < allMessages.length; i++) {
        const m = allMessages[i];
        const y = messageOffsets[i] * scale;
        const h = Math.max(2, (messageHeights.get(m.msg_id) || ESTIMATED_HEIGHT) * scale);

        ctx.fillStyle = m.role === 'user' ? '#58a6ff' : '#9d7cd8';
        ctx.fillRect(8, y, 64, h - 1);
    }
}

// Update minimap viewport indicator
function updateMinimapViewport(scrollTop, containerHeight) {
    const viewport = document.querySelector('.minimap-viewport');
    if (!viewport || totalHeight === 0) return;

    const minimap = document.querySelector('.minimap');
    const canvasHeight = minimap.clientHeight;
    const scale = canvasHeight / Math.max(totalHeight, 1);

    const top = scrollTop * scale;
    const height = containerHeight * scale;

    viewport.style.top = `${top}px`;
    viewport.style.height = `${height}px`;
}

// Handle minimap click
function handleMinimapClick(e) {
    const minimap = document.querySelector('.minimap');
    if (!minimap) return;

    const rect = minimap.getBoundingClientRect();
    const y = e.clientY - rect.top;
    const scale = minimap.clientHeight / Math.max(totalHeight, 1);
    const scrollTop = y / scale - content.clientHeight / 2;

    content.scrollTo({ top: Math.max(0, scrollTop) });
}

function renderSession(data) {
    const { session, messages } = data;
    allMessages = [...messages];
    if (sortNewestFirst) {
        allMessages.reverse();
    }
    selectedMessageIndex = -1;

    // Clear stale height cache from previous sessions
    messageHeights.clear();
    messageOffsets = [];

    // Calculate initial offsets (will use estimated heights)
    calculateOffsets();

    // Initial visible range
    visibleRange = getVisibleRange(0, content.clientHeight || 800);

    content.innerHTML = `
        <div class="content-wrapper">
            <div class="messages-container">
                <div class="messages" style="min-height: ${totalHeight}px;"></div>
            </div>
            <div class="minimap">
                <canvas></canvas>
                <div class="minimap-viewport"></div>
            </div>
        </div>
    `;

    // Render visible messages
    renderVisibleMessages();

    // Setup scroll handler
    content.removeEventListener('scroll', handleScroll);
    content.addEventListener('scroll', handleScroll, { passive: true });

    // Setup minimap
    const minimap = document.querySelector('.minimap');
    if (minimap) {
        minimap.addEventListener('click', handleMinimapClick);
    }

    // Render minimap after a brief delay to ensure heights are measured
    setTimeout(() => {
        calculateOffsets();
        renderMinimap();
        updateMinimapViewport(content.scrollTop, content.clientHeight);
    }, 50);
}

function selectMessage(index, direction = 0) {
    if (allMessages.length === 0) return;

    index = Math.max(0, Math.min(index, allMessages.length - 1));
    selectedMessageIndex = index;

    // Scroll to the message position
    const msgOffset = messageOffsets[index] || 0;
    content.scrollTo({ top: Math.max(0, msgOffset - 100) });

    // Re-render to show selection (scroll handler will update visible range)
    setTimeout(() => {
        const msgEl = document.getElementById(allMessages[index].msg_id);
        if (msgEl) {
            content.querySelectorAll('.message').forEach(m => m.classList.remove('selected'));
            msgEl.classList.add('selected');
        }
    }, 50);
}

function navigateMessages(direction) {
    if (allMessages.length === 0) return;

    if (selectedMessageIndex === -1) {
        selectMessage(direction > 0 ? 0 : allMessages.length - 1, direction);
    } else {
        selectMessage(selectedMessageIndex + direction, direction);
    }
}

function navigateSessions(direction) {
    if (sessions.length === 0) return;

    const currentIndex = currentSession
        ? sessions.findIndex(s => s.session_id === currentSession.session_id)
        : -1;

    let newIndex;
    if (currentIndex === -1) {
        newIndex = direction > 0 ? 0 : sessions.length - 1;
    } else {
        newIndex = Math.max(0, Math.min(currentIndex + direction, sessions.length - 1));
    }

    if (newIndex !== currentIndex) {
        loadSession(sessions[newIndex].session_id);
    }
}

function renderSearchResults(query, results) {
    if (results.length === 0) {
        content.innerHTML = `
            <div class="empty-state">
                <h2>No results</h2>
                <p>No messages found for "${escapeHtml(query)}"</p>
            </div>
        `;
        return;
    }

    content.innerHTML = `
        <div class="search-results">
            <h2>Search results for "${escapeHtml(query)}" (${results.length})</h2>
            ${results.map(r => `
                <div class="search-result" data-session="${r.session_id}" data-msg="${r.msg_id}">
                    <div class="search-result-meta">
                        <span class="badge">${escapeHtml(r.project)}</span>
                        <span>${escapeHtml(r.role)}</span>
                    </div>
                    <div>${safeSnippet(r.snippet) || escapeHtml(r.content.substring(0, 200))}</div>
                </div>
            `).join('')}
        </div>
    `;

    content.querySelectorAll('.search-result').forEach(item => {
        item.addEventListener('click', () => {
            loadSession(item.dataset.session, item.dataset.msg);
        });
    });
}

// Load functions
async function loadSessions() {
    sessions = await fetchSessions();
    renderSessionList();
    statusText.textContent = `${sessions.length} sessions`;
}

async function loadSession(id, scrollToMsg = null) {
    const session = sessions.find(s => s.session_id === id);
    if (!session) return;

    currentSession = session;
    const messages = await fetchMessages(id);
    currentSessionData = { session, messages };
    renderSession(currentSessionData);
    renderSessionList();

    startWatching(id);

    if (scrollToMsg) {
        setTimeout(() => {
            const el = document.getElementById(scrollToMsg);
            if (el) el.scrollIntoView({ behavior: 'smooth', block: 'center' });
        }, 100);
    }
}

async function doSearch(query) {
    if (!query.trim()) {
        if (currentSession) {
            await loadSession(currentSession.session_id);
        } else {
            content.innerHTML = `
                <div class="empty-state">
                    <h2>Select a session</h2>
                    <p>Choose a session from the sidebar or search for messages</p>
                </div>
            `;
        }
        return;
    }

    const results = await searchMessages(query);
    renderSearchResults(query, results);
}

// Utilities
function escapeHtml(text) {
    if (!text) return '';
    return text
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;');
}

// Safely render FTS snippet, only allowing <mark> tags
function safeSnippet(snippet) {
    if (!snippet) return '';
    // Escape everything first
    let safe = escapeHtml(snippet);
    // Then restore only <mark> and </mark> tags
    safe = safe.replace(/&lt;mark&gt;/g, '<mark>');
    safe = safe.replace(/&lt;\/mark&gt;/g, '</mark>');
    return safe;
}

function formatContent(text) {
    if (!text) return '';
    let html = escapeHtml(text);
    html = html.replace(/```(\w*)\n([\s\S]*?)```/g, '<pre><code>$2</code></pre>');
    html = html.replace(/`([^`]+)`/g, '<code>$1</code>');
    html = html.replace(/\[Thinking\]\n([\s\S]*?)(?=\n\[|$)/g,
        '<div class="thinking-block"><div class="thinking-label">Thinking</div>$1</div>');
    html = html.replace(/\[(Tool|Read|Write|Edit|Bash|Glob|Grep|Task|Question|Todo List|Entering Plan Mode|Exiting Plan Mode)([^\]]*)\]([\s\S]*?)(?=\n\[|\n\n|<div|$)/g,
        '<div class="tool-block">[$1$2]$3</div>');
    return html;
}

function formatDate(ts) {
    if (!ts) return '';
    return new Date(ts).toLocaleDateString();
}

function formatTime(ts) {
    if (!ts) return '';
    return new Date(ts).toLocaleTimeString();
}

function formatAgentName(agent) {
    return { claude: 'Claude', codex: 'Codex' }[agent] || agent || 'Claude';
}

function openShortcutsModal() { shortcutsModal.classList.add('visible'); }
function closeShortcutsModal() { shortcutsModal.classList.remove('visible'); }

// Event handlers
searchInput.addEventListener('input', (e) => {
    clearTimeout(searchTimeout);
    searchTimeout = setTimeout(() => doSearch(e.target.value), 300);
});

syncBtn.addEventListener('click', triggerSync);
sortBtn.addEventListener('click', toggleSort);
scrollTopBtn.addEventListener('click', () => content.scrollTo({ top: 0 }));
shortcutsBtn.addEventListener('click', openShortcutsModal);
modalClose.addEventListener('click', closeShortcutsModal);

shortcutsModal.addEventListener('click', (e) => {
    if (e.target === shortcutsModal) closeShortcutsModal();
});

document.addEventListener('keydown', (e) => {
    const isModalOpen = shortcutsModal.classList.contains('visible');
    const isInputFocused = document.activeElement === searchInput;

    if (e.key === 'Escape') {
        if (isModalOpen) {
            closeShortcutsModal();
            e.preventDefault();
        } else if (isInputFocused) {
            searchInput.value = '';
            searchInput.blur();
            doSearch('');
        }
        return;
    }

    if (isModalOpen) return;

    if ((e.metaKey || e.ctrlKey) && e.key === 'k') {
        e.preventDefault();
        searchInput.focus();
        searchInput.select();
        return;
    }

    if (isInputFocused) return;

    if (e.key === 'ArrowDown' || e.key === 'j') {
        e.preventDefault();
        navigateMessages(1);
    } else if (e.key === 'ArrowUp' || e.key === 'k') {
        e.preventDefault();
        navigateMessages(-1);
    } else if (e.key === ']') {
        e.preventDefault();
        navigateSessions(1);
    } else if (e.key === '[') {
        e.preventDefault();
        navigateSessions(-1);
    } else if (e.key === 'o') {
        e.preventDefault();
        toggleSort();
    } else if (e.key === 'r') {
        e.preventDefault();
        triggerSync();
    } else if (e.key === '?') {
        e.preventDefault();
        openShortcutsModal();
    }
});

// Initialize
(async () => {
    await loadSessions();
    if (sessions.length > 0) {
        await loadSession(sessions[0].session_id);
    }
})();
