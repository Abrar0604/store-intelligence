/**
 * Store Intelligence Dashboard — Client-Side JavaScript
 * 
 * Connects to the API via SSE for live updates and polls endpoints
 * for detailed metrics, funnel, heatmap, and anomaly data.
 */

const API_BASE = window.location.origin;
const STORE_ID = 'STORE_BLR_002';
const POLL_INTERVAL = 3000; // 3 seconds

// ─── State ──────────────────────────────────────────────────────────────
let eventSource = null;
let pollTimer = null;

// ─── Initialize ─────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
    connectSSE();
    fetchAllData();
    pollTimer = setInterval(fetchAllData, POLL_INTERVAL);
});

// ─── SSE Connection ─────────────────────────────────────────────────────
function connectSSE() {
    try {
        eventSource = new EventSource(`${API_BASE}/events/stream`);

        eventSource.onmessage = (event) => {
            try {
                const data = JSON.parse(event.data);
                if (data.type === 'metrics_update') {
                    updateSSEMetrics(data);
                }
            } catch (e) {
                console.warn('SSE parse error:', e);
            }
        };

        eventSource.onopen = () => {
            setConnectionStatus(true);
        };

        eventSource.onerror = () => {
            setConnectionStatus(false);
            // Reconnect after 5 seconds
            setTimeout(() => {
                if (eventSource) eventSource.close();
                connectSSE();
            }, 5000);
        };
    } catch (e) {
        console.error('SSE not supported:', e);
        setConnectionStatus(false);
    }
}

function setConnectionStatus(connected) {
    const dot = document.querySelector('.status-dot');
    const text = document.querySelector('.status-text');
    if (connected) {
        dot.classList.add('connected');
        text.textContent = 'Live';
    } else {
        dot.classList.remove('connected');
        text.textContent = 'Reconnecting...';
    }
}

function updateSSEMetrics(data) {
    if (data.total_events !== undefined) {
        document.getElementById('events-count').textContent = data.total_events.toLocaleString();
    }
    document.getElementById('last-update').textContent = 
        `Last update: ${new Date(data.timestamp).toLocaleTimeString()}`;
}

// ─── Data Fetching ──────────────────────────────────────────────────────
async function fetchAllData() {
    await Promise.allSettled([
        fetchMetrics(),
        fetchFunnel(),
        fetchHeatmap(),
        fetchAnomalies(),
        fetchHealth(),
    ]);
}

async function fetchJSON(url) {
    try {
        const resp = await fetch(url);
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        return await resp.json();
    } catch (e) {
        console.warn(`Fetch error (${url}):`, e.message);
        return null;
    }
}

// ─── Metrics ────────────────────────────────────────────────────────────
async function fetchMetrics() {
    const data = await fetchJSON(`${API_BASE}/stores/${STORE_ID}/metrics`);
    if (!data) return;

    animateValue('metric-visitors', data.unique_visitors);
    animateValue('metric-conversion', `${(data.conversion_rate * 100).toFixed(1)}%`);
    animateValue('metric-queue', data.current_queue_depth);
    animateValue('metric-revenue', `₹${data.total_revenue.toLocaleString()}`);
    animateValue('metric-abandonment', `${(data.abandonment_rate * 100).toFixed(1)}%`);
    animateValue('metric-transactions', data.total_transactions);

    // Update dwell chart
    renderDwellChart(data.avg_dwell_by_zone);
}

function animateValue(elementId, newValue) {
    const el = document.getElementById(elementId);
    if (!el) return;
    const oldValue = el.textContent;
    const newStr = String(newValue);
    if (oldValue !== newStr) {
        el.textContent = newStr;
        el.classList.add('updated');
        setTimeout(() => el.classList.remove('updated'), 600);
    }
}

// ─── Funnel ─────────────────────────────────────────────────────────────
async function fetchFunnel() {
    const data = await fetchJSON(`${API_BASE}/stores/${STORE_ID}/funnel`);
    if (!data || !data.stages) return;

    const container = document.getElementById('funnel-container');
    container.innerHTML = '';

    data.stages.forEach((stage, i) => {
        const stageEl = document.createElement('div');
        stageEl.className = 'funnel-stage';
        
        const dropoff = stage.drop_off_pct > 0 
            ? `<span class="funnel-dropoff">↓ ${stage.drop_off_pct.toFixed(1)}%</span>` 
            : '<span class="funnel-dropoff"></span>';

        stageEl.innerHTML = `
            <span class="funnel-label">${stage.stage}</span>
            <div class="funnel-bar-container">
                <div class="funnel-bar stage-${i}" style="width: ${Math.max(stage.percentage, 2)}%"></div>
            </div>
            <span class="funnel-value">${stage.count}</span>
            ${dropoff}
        `;
        container.appendChild(stageEl);
    });
}

// ─── Heatmap ────────────────────────────────────────────────────────────
async function fetchHeatmap() {
    const data = await fetchJSON(`${API_BASE}/stores/${STORE_ID}/heatmap`);
    if (!data || !data.zones) return;

    const container = document.getElementById('heatmap-container');
    container.innerHTML = '';

    data.zones.forEach(zone => {
        const cell = document.createElement('div');
        cell.className = 'heatmap-cell';
        
        // Color based on intensity
        const hue = 260 - (zone.intensity * 1.6); // purple → red as intensity grows
        const sat = 60 + (zone.intensity * 0.3);
        const light = 15 + (zone.intensity * 0.2);
        cell.style.backgroundColor = `hsla(${hue}, ${sat}%, ${light}%, 0.6)`;
        cell.style.borderLeft = `3px solid hsl(${hue}, ${sat}%, ${light + 20}%)`;

        const dwellSec = (zone.avg_dwell_ms / 1000).toFixed(1);
        const confidenceTag = zone.data_confidence === 'low' 
            ? '<span style="color: var(--accent-amber); font-size: 10px;">⚠ Low data</span>' 
            : '';

        cell.innerHTML = `
            <div class="heatmap-zone-name">${zone.zone_name}</div>
            <div class="heatmap-visits">${zone.visit_count}</div>
            <div class="heatmap-dwell">${dwellSec}s avg dwell</div>
            ${confidenceTag}
        `;
        container.appendChild(cell);
    });
}

// ─── Anomalies ──────────────────────────────────────────────────────────
async function fetchAnomalies() {
    const data = await fetchJSON(`${API_BASE}/stores/${STORE_ID}/anomalies`);
    if (!data) return;

    const list = document.getElementById('anomalies-list');
    const countBadge = document.getElementById('anomaly-count');
    const anomalies = data.active_anomalies || [];

    countBadge.textContent = anomalies.length;
    countBadge.className = `anomaly-count ${anomalies.length === 0 ? 'zero' : ''}`;

    if (anomalies.length === 0) {
        list.innerHTML = `
            <div class="no-anomalies">
                <span class="check-icon">✅</span>
                No active anomalies
            </div>
        `;
        return;
    }

    list.innerHTML = '';
    anomalies.forEach(anomaly => {
        const item = document.createElement('div');
        item.className = `anomaly-item severity-${anomaly.severity}`;
        item.innerHTML = `
            <span class="anomaly-severity ${anomaly.severity}">${anomaly.severity}</span>
            <div class="anomaly-details">
                <div class="anomaly-description">${anomaly.description}</div>
                <div class="anomaly-action">💡 ${anomaly.suggested_action}</div>
            </div>
        `;
        list.appendChild(item);
    });
}

// ─── Dwell Chart ────────────────────────────────────────────────────────
function renderDwellChart(zones) {
    const container = document.getElementById('dwell-chart');
    if (!zones || zones.length === 0) {
        container.innerHTML = '<div class="dwell-loading">No dwell data available</div>';
        return;
    }

    // Find max dwell for normalization
    const maxDwell = Math.max(...zones.map(z => z.avg_dwell_ms), 1);

    container.innerHTML = '';
    zones.forEach(zone => {
        const pct = (zone.avg_dwell_ms / maxDwell) * 100;
        const dwellSec = (zone.avg_dwell_ms / 1000).toFixed(1);
        
        const row = document.createElement('div');
        row.className = 'dwell-bar-row';
        row.innerHTML = `
            <span class="dwell-zone-label">${zone.zone_name}</span>
            <div class="dwell-bar-wrapper">
                <div class="dwell-bar" style="width: ${Math.max(pct, 3)}%"></div>
            </div>
            <span class="dwell-value">${dwellSec}s (${zone.visit_count})</span>
        `;
        container.appendChild(row);
    });
}

// ─── Health ─────────────────────────────────────────────────────────────
async function fetchHealth() {
    const data = await fetchJSON(`${API_BASE}/health`);
    if (!data) return;

    // Update health bar
    const statusIcon = data.status === 'healthy' ? '💚' : '🟡';
    document.querySelector('#health-status .health-icon').textContent = statusIcon;
    document.querySelector('#health-status strong').textContent = 
        data.status.charAt(0).toUpperCase() + data.status.slice(1);

    document.querySelector('#health-db strong').textContent = 
        data.database.charAt(0).toUpperCase() + data.database.slice(1);

    const uptime = formatUptime(data.uptime_seconds);
    document.getElementById('uptime-value').textContent = uptime;
}

function formatUptime(seconds) {
    if (seconds < 60) return `${Math.floor(seconds)}s`;
    if (seconds < 3600) return `${Math.floor(seconds / 60)}m ${Math.floor(seconds % 60)}s`;
    const h = Math.floor(seconds / 3600);
    const m = Math.floor((seconds % 3600) / 60);
    return `${h}h ${m}m`;
}
