/* Ewinet Route Monitor - Dashboard JavaScript */

const API = {
    status: '/api/status',
    snapshots: '/api/snapshots',
    performance: '/api/performance',
    anomalies: '/api/anomalies',
    providers: '/api/providers',
    history: '/api/history',
    baselines: '/api/baselines',
};

let refreshInterval = null;
const REFRESH_MS = 15000; // 15 seconds

/* ────────── Init ────────── */
document.addEventListener('DOMContentLoaded', () => {
    refreshAll();
    refreshInterval = setInterval(refreshAll, REFRESH_MS);
});

async function refreshAll() {
    try {
        const [status, snapshots, perf, anomalies, providers, baselines] = await Promise.all([
            fetchJSON(API.status),
            fetchJSON(API.snapshots),
            fetchJSON(API.performance),
            fetchJSON(API.anomalies),
            fetchJSON(API.providers),
            fetchJSON(API.baselines),
        ]);

        renderStatus(status);
        renderSummaryCards(snapshots, anomalies, perf, providers);
        renderProviders(snapshots, anomalies, providers);
        renderPerformance(perf);
        renderAlerts(anomalies);
        renderBaselines(baselines, providers);
        populateHistoryFilter(providers);
    } catch (e) {
        console.error('Refresh failed:', e);
        document.getElementById('status-text').textContent = 'Error';
        document.getElementById('status-dot').className = 'status-dot error';
    }
}

async function fetchJSON(url) {
    const res = await fetch(url);
    if (!res.ok) throw new Error(`${url}: ${res.status}`);
    return res.json();
}

/* ────────── Status Bar ────────── */
function renderStatus(data) {
    const dot = document.getElementById('status-dot');
    const text = document.getElementById('status-text');
    const cycle = document.getElementById('cycle-count');
    const lastUp = document.getElementById('last-update');
    const footerUp = document.getElementById('footer-uptime');
    const footerCycle = document.getElementById('footer-last-cycle');

    const s = data.status || 'unknown';
    dot.className = 'status-dot ' + (s === 'running' ? 'running' : s === 'starting' ? 'starting' : 'error');
    text.textContent = s.charAt(0).toUpperCase() + s.slice(1);
    cycle.textContent = data.cycle_count || 0;
    lastUp.textContent = data.last_update_human || '--';

    // Public IP
    const ipValue = document.getElementById('public-ip-value');
    if (ipValue && data.public_ip) {
        ipValue.textContent = data.public_ip;
    }

    // Uptime
    if (data.uptime) {
        const upValue = document.getElementById('uptime-value');
        if (upValue) upValue.textContent = formatDuration(data.uptime);
        if (footerUp) footerUp.textContent = 'Uptime: ' + formatDuration(data.uptime);
    }

    if (footerCycle) {
        footerCycle.textContent = 'Cycle: ' + (data.cycle_count || 0);
    }
}

/* ────────── KPI Cards ────────── */
function renderSummaryCards(snapshots, anomalies, perf, providers) {
    document.getElementById('total-destinations').textContent = providers.length;

    let routesOk = 0;
    let totalAsns = new Set();
    snapshots.forEach(s => {
        if (s.status === 'normal') routesOk++;
        if (s.as_path_numbers) {
            s.as_path_numbers.forEach(n => totalAsns.add(n));
        }
    });
    document.getElementById('routes-ok').textContent = routesOk;
    document.getElementById('anomaly-count').textContent = anomalies.length;
    document.getElementById('asns-detected').textContent = totalAsns.size;

    // Avg latency
    let totalRtt = 0, count = 0;
    perf.forEach(p => {
        if (p.rtt_avg > 0) { totalRtt += p.rtt_avg; count++; }
    });
    const avg = count > 0 ? (totalRtt / count).toFixed(1) + 'ms' : '--';
    document.getElementById('avg-latency').textContent = avg;
}

/* ────────── Provider Cards ────────── */
function renderProviders(snapshots, anomalies, providers) {
    const grid = document.getElementById('providers-grid');

    if (!snapshots.length) {
        grid.innerHTML = '<div class="empty-state">No hay datos de rutas aún. Esperando primer ciclo de monitoreo...</div>';
        return;
    }

    // Group snapshots by provider (use destination as fallback if no provider name)
    const byProvider = {};
    snapshots.forEach(s => {
        const key = s.provider_name || s.as_path_numbers?.[0] ? 'AS' + (s.as_path_numbers?.[0] || '') : s.destination;
        if (!byProvider[key]) byProvider[key] = [];
        byProvider[key].push(s);
    });

    // Get provider config for expected ASNs
    const providerConfig = {};
    providers.forEach(p => { providerConfig[p.name] = p; });

    // Get anomaly ASNs per provider
    const anomalyASNs = {};
    anomalies.forEach(a => {
        const key = a.provider_name || 'unknown';
        if (!anomalyASNs[key]) anomalyASNs[key] = new Set();
        (a.unexpected_asns || []).forEach(asn => anomalyASNs[key].add(asn.number));
    });

    let html = '';
    for (const [name, providerSnaps] of Object.entries(byProvider)) {
        const cfg = providerConfig[name] || {};
        const unexpected = anomalyASNs[name] || new Set();

        // Determine worst status
        let worstStatus = 'normal';
        providerSnaps.forEach(s => {
            if (s.status === 'critical' || s.status === 'anomaly') worstStatus = 'critical';
            else if (s.status === 'degraded' && worstStatus !== 'critical') worstStatus = 'degraded';
            else if (s.status === 'warning' && worstStatus === 'normal') worstStatus = 'warning';
        });

        // Check for anomalies
        const providerAnomalies = anomalies.filter(a => (a.provider_name || 'unknown') === name);
        if (providerAnomalies.length > 0 && worstStatus === 'normal') worstStatus = 'warning';

        const statusLabel = worstStatus.charAt(0).toUpperCase() + worstStatus.slice(1);
        const displayName = name || providerSnaps[0].destination;

        html += `<div class="provider-card status-${worstStatus}">`;

        // Header
        html += `<div class="provider-header">
            <div class="provider-name">
                <h3>${esc(displayName)}</h3>
                ${providerSnaps[0].provider_asn ? `<span class="provider-asn">AS${providerSnaps[0].provider_asn}</span>` : ''}
            </div>
            <span class="provider-status ${worstStatus}">${statusLabel}</span>
        </div>`;

        html += '<div class="provider-body">';

        // Meta info - only show if available
        const snap = providerSnaps[0];
        const metaItems = [];
        if (snap.physical_interface || cfg.interface) metaItems.push(`<span>🔌 ${esc(snap.physical_interface || cfg.interface)}</span>`);
        if (snap.vlan_id || cfg.vlan_id) metaItems.push(`<span>🏷️ VLAN ${esc(snap.vlan_id || cfg.vlan_id)}</span>`);
        if (snap.gateway) metaItems.push(`<span>📡 ${esc(snap.gateway)}</span>`);
        if (metaItems.length) {
            html += `<div class="provider-meta">${metaItems.join('')}</div>`;
        }

        // For each destination, show AS path
        providerSnaps.forEach(ps => {
            html += `<div class="as-path-container">`;
            html += `<div class="as-path-label">→ ${esc(ps.destination)}</div>`;
            html += '<div class="as-path">';

            const asns = parseASPath(ps.as_path);
            asns.forEach((asn, i) => {
                if (i > 0) html += '<span class="asn-arrow">→</span>';
                const isUnexpected = unexpected.has(asn.number);
                const isIntl = i > 0 && i < asns.length - 1 && isInternationalASN(asn.number);
                let type = i === 0 ? 'local' : i === asns.length - 1 ? 'destination' : 'transit';
                let cls = isUnexpected ? 'unexpected' : type;
                if (isIntl) cls += ' intl';
                html += `<div class="asn-node ${cls}">
                    <span class="asn-number">AS${asn.number}</span>
                    <span class="asn-name">${esc(asn.name || '--')}</span>
                </div>`;
            });

            html += '</div>'; // .as-path

            // Collapsible hops table
            if (ps.hops && ps.hops.length) {
                const hopId = `hops-${name}-${ps.destination}`.replace(/[^a-zA-Z0-9-]/g, '_');
                html += `<div class="hops-section">
                    <div class="hops-title" onclick="toggleHops('${hopId}', this)">
                        ${ps.hops.length} saltos
                    </div>
                    <table class="hops-table" id="${hopId}">
                        <thead><tr>
                            <th>#</th><th>IP</th><th>ASN</th><th>RTT (ms)</th><th>Loss %</th>
                        </tr></thead><tbody>`;
                ps.hops.forEach(h => {
                    const lossCls = h.loss_percent === 0 ? 'loss-low' : h.loss_percent < 20 ? 'loss-medium' : 'loss-high';
                    const asnNum = h.asn ? h.asn.number : 0;
                    const rowCls = asnNum && isInternationalASN(asnNum) ? 'hop-intl' : '';
                    html += `<tr class="${rowCls}">
                        <td>${h.hop_number}</td>
                        <td>${esc(h.ip_address)}</td>
                        <td>${h.asn ? 'AS' + h.asn.number : '--'}</td>
                        <td>${h.rtt_avg > 0 ? h.rtt_avg.toFixed(1) : '--'}</td>
                        <td class="${lossCls}">${h.loss_percent.toFixed(1)}%</td>
                    </tr>`;
                });
                html += '</tbody></table></div>';

                // Show final destination server
                if (ps.hops && ps.hops.length) {
                    const lastHop = ps.hops[ps.hops.length - 1];
                    if (lastHop && lastHop.asn) {
                        const isLastIntl = isInternationalASN(lastHop.asn.number);
                        const intlBadge = isLastIntl ? '<span class="dest-intl-badge">INTERNATIONAL</span>' : '';
                        html += `<div class="final-destination">
                            <span class="final-dest-label">DESTINO FINAL</span>
                            <span class="final-dest-ip">${esc(lastHop.ip_address)}</span>
                            <span class="final-dest-asn">AS${lastHop.asn.number} ${esc(lastHop.asn.name || '')}</span>
                            <span class="final-dest-rtt">${lastHop.rtt_avg > 0 ? lastHop.rtt_avg.toFixed(1) + 'ms' : ''}</span>
                            ${intlBadge}
                        </div>`;
                    }
                }
            }

            html += '</div>'; // .as-path-container
        });

        // Anomaly count
        if (providerAnomalies.length) {
            html += `<div style="margin-top:12px; font-size:12px; color:var(--accent-red);">
                ⚠ ${providerAnomalies.length} anomalía(s) detectada(s)
            </div>`;
        }

        html += '</div></div>'; // .provider-body, .provider-card
    }

    grid.innerHTML = html;
}

/* ────────── Performance ────────── */
function renderPerformance(perf) {
    const grid = document.getElementById('perf-grid');

    if (!perf.length) {
        grid.innerHTML = '<div class="empty-state">Sin datos de rendimiento</div>';
        return;
    }

    let html = '';
    perf.forEach(p => {
        const rttClass = p.rtt_avg < 50 ? 'good' : p.rtt_avg < 120 ? 'warn' : 'bad';
        const lossClass = p.packet_loss < 5 ? 'good' : p.packet_loss < 20 ? 'warn' : 'bad';
        const lossBarPct = Math.min(p.packet_loss, 100);
        const lossBarClass = lossClass;

        html += `<div class="perf-card">
            <div class="perf-header">
                <span class="perf-provider">${esc(p.provider_name)}</span>
                <span class="perf-dest">${esc(p.destination)}</span>
            </div>
            <div class="perf-metrics">
                <div class="perf-metric">
                    <span class="perf-metric-label">RTT Promedio</span>
                    <span class="perf-metric-value ${rttClass}">${p.rtt_avg > 0 ? p.rtt_avg.toFixed(1) + 'ms' : '--'}</span>
                </div>
                <div class="perf-metric">
                    <span class="perf-metric-label">RTT Min / Max</span>
                    <span class="perf-metric-value" style="font-size:14px;">
                        ${p.rtt_min > 0 ? p.rtt_min.toFixed(1) : '--'} / ${p.rtt_max > 0 ? p.rtt_max.toFixed(1) : '--'} ms
                    </span>
                </div>
                <div class="perf-metric">
                    <span class="perf-metric-label">Pérdida de Paquetes</span>
                    <span class="perf-metric-value ${lossClass}">${p.packet_loss.toFixed(1)}%</span>
                </div>
                <div class="perf-metric">
                    <span class="perf-metric-label">Paquetes</span>
                    <span class="perf-metric-value" style="font-size:14px;">
                        ${p.packets_received}/${p.packets_sent}
                    </span>
                </div>
            </div>
            <div class="perf-bar-container">
                <div class="perf-bar-label">
                    <span>Pérdida</span>
                    <span>${p.packet_loss.toFixed(1)}%</span>
                </div>
                <div class="perf-bar">
                    <div class="perf-bar-fill ${lossBarClass}" style="width:${lossBarPct}%"></div>
                </div>
            </div>
        </div>`;
    });

    grid.innerHTML = html;
}

/* ────────── Alerts ────────── */
function renderAlerts(anomalies) {
    const list = document.getElementById('alerts-list');
    const badge = document.getElementById('active-alerts-badge');

    badge.textContent = anomalies.length;

    if (!anomalies.length) {
        list.innerHTML = '<div class="empty-state" style="color:var(--accent-green);">✅ No hay alertas activas - Todas las rutas están dentro de los parámetros esperados</div>';
        return;
    }

    let html = '';
    anomalies.slice().reverse().forEach(a => {
        const sevIcon = a.severity === 'critical' ? '🚨' : a.severity === 'warning' ? '⚠️' : 'ℹ️';
        html += `<div class="alert-item severity-${a.severity}">
            <div class="alert-header">
                <div class="alert-title">
                    ${sevIcon} ${esc(a.provider_name)} → ${esc(a.destination)}
                </div>
                <span class="alert-time">${esc(a.timestamp_human || '')}</span>
            </div>
            <div class="alert-body">
                <p>${esc(a.description)}</p>
                <div class="alert-paths">
                    <div class="alert-path-box">
                        <div class="alert-path-label">Ruta Esperada (Baseline)</div>
                        <div class="alert-path-value">${esc(a.baseline_as_path)}</div>
                    </div>
                    <div class="alert-path-box">
                        <div class="alert-path-label">Ruta Actual (Detectada)</div>
                        <div class="alert-path-value" style="color:var(--accent-red)">${esc(a.current_as_path)}</div>
                    </div>
                </div>
                ${a.latency_increase_percent > 0
                    ? `<div style="margin-top:8px; font-size:13px; color:var(--accent-orange);">
                        ⏱ Aumento de latencia: +${a.latency_increase_percent.toFixed(1)}%
                      </div>` : ''}
                ${a.unexpected_asns && a.unexpected_asns.length
                    ? `<div style="margin-top:4px; font-size:13px; color:var(--accent-red);">
                        🔴 ASNs inesperados: ${a.unexpected_asns.map(x => 'AS' + x.number + (x.name ? ' (' + x.name + ')' : '')).join(', ')}
                      </div>` : ''}
            </div>
        </div>`;
    });

    list.innerHTML = html;
}

/* ────────── Baselines ────────── */
function renderBaselines(baselines, providers) {
    const grid = document.getElementById('baselines-grid');

    if (!Object.keys(baselines).length && !providers.length) {
        grid.innerHTML = '<div class="empty-state">Sin baselines configuradas - se aprenderán en el primer ciclo</div>';
        return;
    }

    let html = '';

    // From API providers data
    providers.forEach(p => {
        const b = p.baseline || {};
        html += `<div class="baseline-card">
            <h4>📍 ${esc(p.name)} ${p.asn ? `<span class="provider-asn">AS${p.asn}</span>` : ''}</h4>
            <div class="baseline-row">
                <span class="baseline-label">AS-PATH Esperado</span>
                <span class="baseline-value">${esc(b.expected_as_path_str || '--')}</span>
            </div>
            ${b.expected_first_hop_asn ? `<div class="baseline-row">
                <span class="baseline-label">Primer Salto ASN</span>
                <span class="baseline-value">AS${b.expected_first_hop_asn}</span>
            </div>` : ''}
            ${b.known_transit_asns && b.known_transit_asns.length ? `<div class="baseline-row">
                <span class="baseline-label">Transits Conocidos</span>
                <span class="baseline-value">${b.known_transit_asns.map(x => 'AS' + x).join(', ')}</span>
            </div>` : ''}
            <div class="baseline-row">
                <span class="baseline-label">RTT Baseline</span>
                <span class="baseline-value">${b.baseline_rtt_avg > 0 ? b.baseline_rtt_avg.toFixed(1) + 'ms' : 'Auto'}</span>
            </div>
            ${p.destinations && p.destinations.length ? `<div class="baseline-row">
                <span class="baseline-label">Destino</span>
                <span class="baseline-value">${p.destinations.join(', ')}</span>
            </div>` : ''}
        </div>`;
    });

    grid.innerHTML = html;
}

/* ────────── History ────────── */
function populateHistoryFilter(providers) {
    const sel = document.getElementById('history-filter');
    if (sel.options.length > 1) return;

    providers.forEach(p => {
        const opt = document.createElement('option');
        opt.value = p.name;
        opt.textContent = p.name;
        sel.appendChild(opt);
    });
}

async function loadHistory() {
    const filter = document.getElementById('history-filter').value;
    const tbody = document.getElementById('history-tbody');

    try {
        const data = await fetchJSON(API.history + '?limit=100');
        const filtered = filter === 'all' ? data : data.filter(r => r.provider === filter || r.destination === filter);

        if (!filtered.length) {
            tbody.innerHTML = '<tr><td colspan="6" class="empty-state">Sin datos de historial</td></tr>';
            return;
        }

        let html = '';
        filtered.slice().reverse().forEach(r => {
            const ts = r.timestamp ? new Date(r.timestamp * 1000).toLocaleString() : '--';
            const statusCls = r.status === 'normal' ? 'normal' : r.status === 'degraded' ? 'degraded' : 'anomaly';
            html += `<tr>
                <td class="mono">${esc(ts)}</td>
                <td>${esc(r.provider || '')}</td>
                <td class="mono">${esc(r.destination || '')}</td>
                <td class="mono">${esc(r.as_path || '')}</td>
                <td>${r.hop_count || '--'}</td>
                <td><span class="provider-status ${statusCls}">${esc(r.status || '')}</span></td>
            </tr>`;
        });
        tbody.innerHTML = html;
    } catch (e) {
        tbody.innerHTML = '<tr><td colspan="6" class="empty-state">Error cargando historial</td></tr>';
    }
}

/* ────────── Helpers ────────── */
function toggleHops(id, el) {
    const table = document.getElementById(id);
    if (table) {
        table.classList.toggle('visible');
        el.classList.toggle('open');
    }
}

function parseASPath(pathStr) {
    if (!pathStr) return [];
    const parts = pathStr.split('->').map(s => s.trim());
    return parts.map(p => {
        const match = p.match(/AS(\d+)(?:\s*\(([^)]+)\))?/i);
        if (match) {
            return { number: parseInt(match[1]), name: match[2] || '' };
        }
        return { number: 0, name: p };
    });
}

// Known Venezuelan ASNs
const VZ_ASNS = new Set([10929, 15135, 263220, 269693, 264628, 271910, 263702, 271886, 267798, 271949, 27984, 273087, 272058, 264681, 272800, 266793]);

// Known international transit ASNs
const INTL_ASNS = new Set([3356, 1299, 174, 6453, 3257, 2914, 6939, 1273, 9002, 3491, 5511, 6762, 7018, 3320, 15169, 13335, 20940, 8075, 16509, 32934, 20473, 14061, 54113, 7922, 396982, 16265, 24940]);

function isInternationalASN(asn) {
    if (VZ_ASNS.has(asn)) return false;
    if (INTL_ASNS.has(asn)) return true;
    return false;
}

function formatDuration(seconds) {
    const d = Math.floor(seconds / 86400);
    const h = Math.floor((seconds % 86400) / 3600);
    const m = Math.floor((seconds % 3600) / 60);
    const parts = [];
    if (d > 0) parts.push(d + 'd');
    if (h > 0) parts.push(h + 'h');
    parts.push(m + 'm');
    return parts.join(' ');
}

function esc(str) {
    if (!str && str !== 0) return '';
    const div = document.createElement('div');
    div.textContent = String(str);
    return div.innerHTML;
}

/* ════════════════ SPEEDTEST ════════════════ */

let speedtestPollInterval = null;

async function checkSpeedtestStatus() {
    try {
        const data = await fetchJSON('/api/speedtest/status');
        const badge = document.getElementById('speedtest-status');
        const btn = document.getElementById('btn-run-speedtest');

        if (!data.available) {
            badge.textContent = 'NO DISPONIBLE';
            badge.className = 'speedtest-status-badge unavailable';
            if (btn) btn.disabled = true;
            return;
        }

        badge.textContent = data.version || 'Disponible';
        badge.className = 'speedtest-status-badge available';
        if (btn) btn.disabled = false;

        if (data.last_result) {
            displaySpeedtestResult(data.last_result);
        }
    } catch (e) {
        document.getElementById('speedtest-status').textContent = 'Error';
    }
}

async function loadSpeedtestServers() {
    const select = document.getElementById('speedtest-server');
    select.innerHTML = '<option value="">Cargando servidores...</option>';

    try {
        const servers = await fetchJSON('/api/speedtest/servers');

        select.innerHTML = '<option value="">Auto (mejor servidor)</option>';

        // Group by country
        const byCountry = {};
        servers.forEach(s => {
            if (!byCountry[s.country]) byCountry[s.country] = [];
            byCountry[s.country].push(s);
        });

        Object.keys(byCountry).sort().forEach(country => {
            const group = document.createElement('optgroup');
            group.label = country;
            byCountry[country].forEach(s => {
                const opt = document.createElement('option');
                opt.value = s.server_id;
                opt.textContent = `${s.sponsor || s.name} - ${s.city} (${s.distance_km}km, ${s.latency_ms}ms)`;
                group.appendChild(opt);
            });
            select.appendChild(group);
        });
    } catch (e) {
        select.innerHTML = '<option value="">Error cargando servidores</option>';
    }
}

async function runSpeedtest() {
    const btn = document.getElementById('btn-run-speedtest');
    const progress = document.getElementById('speedtest-progress');
    const results = document.getElementById('speedtest-results');

    if (btn.disabled) return;

    const serverId = document.getElementById('speedtest-server').value;

    btn.disabled = true;
    btn.classList.add('running');
    btn.innerHTML = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg><span>EJECUTANDO...</span>';

    progress.style.display = 'block';
    results.style.opacity = '0.3';

    try {
        const res = await fetch('/api/speedtest/run', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ server_id: serverId }),
        });

        if (!res.ok) {
            const err = await res.json();
            throw new Error(err.error || 'Failed to start');
        }

        // Poll for result
        startSpeedtestPoll();
    } catch (e) {
        resetSpeedtestBtn();
        progress.style.display = 'none';
        results.style.opacity = '1';
        alert('Error: ' + e.message);
    }
}

function startSpeedtestPoll() {
    if (speedtestPollInterval) clearInterval(speedtestPollInterval);

    speedtestPollInterval = setInterval(async () => {
        try {
            const data = await fetchJSON('/api/speedtest/status');

            if (data.running) {
                document.getElementById('speedtest-status').textContent = 'Ejecutando...';
                document.getElementById('speedtest-status').className = 'speedtest-status-badge running';
                return;
            }

            // Test finished
            clearInterval(speedtestPollInterval);
            speedtestPollInterval = null;

            if (data.last_result) {
                displaySpeedtestResult(data.last_result);
            }

            resetSpeedtestBtn();
            document.getElementById('speedtest-progress').style.display = 'none';
            document.getElementById('speedtest-results').style.opacity = '1';

            // Refresh history
            loadSpeedtestHistory();
        } catch (e) {
            clearInterval(speedtestPollInterval);
            resetSpeedtestBtn();
        }
    }, 2000);
}

function displaySpeedtestResult(r) {
    if (r.error) {
        document.getElementById('st-download').textContent = 'ERR';
        document.getElementById('st-upload').textContent = 'ERR';
        return;
    }

    document.getElementById('st-download').textContent = r.download_mbps > 0 ? r.download_mbps.toFixed(1) : '--';
    document.getElementById('st-upload').textContent = r.upload_mbps > 0 ? r.upload_mbps.toFixed(1) : '--';
    document.getElementById('st-ping').textContent = r.latency_ms > 0 ? r.latency_ms.toFixed(1) : '--';
    document.getElementById('st-jitter').textContent = r.jitter_ms > 0 ? r.jitter_ms.toFixed(1) : '--';

    // Bars (scale to 1000 Mbps max)
    const downPct = Math.min((r.download_mbps / 1000) * 100, 100);
    const upPct = Math.min((r.upload_mbps / 1000) * 100, 100);
    document.getElementById('st-download-bar').style.width = downPct + '%';
    document.getElementById('st-upload-bar').style.width = upPct + '%';

    // Meta
    const serverParts = [r.server_sponsor || r.server_name, r.server_city, r.server_country].filter(Boolean);
    document.getElementById('st-server').textContent = serverParts.join(' - ') || '--';
    document.getElementById('st-isp').textContent = r.isp || '--';
    document.getElementById('st-ip').textContent = r.external_ip || '--';
    document.getElementById('st-duration').textContent = r.duration_seconds > 0 ? r.duration_seconds.toFixed(1) + 's' : '--';
}

function resetSpeedtestBtn() {
    const btn = document.getElementById('btn-run-speedtest');
    btn.disabled = false;
    btn.classList.remove('running');
    btn.innerHTML = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polygon points="5 3 19 12 5 21 5 3"/></svg><span>EJECUTAR TEST</span>';

    document.getElementById('speedtest-status').textContent = 'Disponible';
    document.getElementById('speedtest-status').className = 'speedtest-status-badge available';
}

async function loadSpeedtestHistory() {
    try {
        const history = await fetchJSON('/api/speedtest/history');
        if (!history.length) return;

        document.getElementById('speedtest-history-section').style.display = 'block';
        const list = document.getElementById('speedtest-history-list');

        let html = '';
        history.slice().reverse().forEach(r => {
            if (r.error) return;
            const time = new Date(r.timestamp * 1000).toLocaleTimeString();
            html += `<div class="st-history-item">
                <span class="st-h-time">${esc(time)}</span>
                <span class="st-h-down">${r.download_mbps.toFixed(1)} Mbps</span>
                <span class="st-h-up">${r.upload_mbps.toFixed(1)} Mbps</span>
                <span class="st-h-ping">${r.latency_ms.toFixed(1)}ms</span>
                <span class="st-h-server">${esc(r.server_sponsor || r.server_name || '')}</span>
            </div>`;
        });
        list.innerHTML = html || '<div class="empty-state">Sin historial</div>';
    } catch (e) {}
}

// Init speedtest on load
document.addEventListener('DOMContentLoaded', () => {
    setTimeout(() => {
        checkSpeedtestStatus();
        loadSpeedtestHistory();
    }, 1000);
});

/* ════════════════ BGP INFO ════════════════ */

async function loadBGPInfo() {
    const asnInput = document.getElementById('bgp-asn-input');
    const results = document.getElementById('bgp-results');
    const asn = asnInput.value.trim().replace(/^AS/i, '');

    if (!asn) {
        results.innerHTML = '<div class="empty-state">Ingresa un numero ASN</div>';
        return;
    }

    results.innerHTML = '<div class="loading-placeholder"><div class="loading-spinner"></div><p>Consultando BGP para AS' + esc(asn) + '...</p></div>';

    try {
        const data = await fetchJSON('/api/bgp/asn/' + asn);

        let html = '';

        // ASN Header
        html += `<div class="bgp-asn-header">
            <span class="bgp-asn-number">AS${data.asn}</span>
            <div>
                <div class="bgp-asn-name">${esc(data.name || '--')}</div>
                <div class="bgp-asn-country">${esc(data.country || '--')} | ${esc(data.description || '')}</div>
            </div>
        </div>`;

        // Stats overview
        html += `<div class="bgp-overview">
            <div class="bgp-stat">
                <div class="bgp-stat-value">${data.prefixes_v4}</div>
                <div class="bgp-stat-label">Prefixes v4</div>
            </div>
            <div class="bgp-stat">
                <div class="bgp-stat-value">${data.peers_count}</div>
                <div class="bgp-stat-label">BGP Peers</div>
            </div>
            <div class="bgp-stat">
                <div class="bgp-stat-value">${data.upstreams_count}</div>
                <div class="bgp-stat-label">Upstreams</div>
            </div>
            <div class="bgp-stat">
                <div class="bgp-stat-value">${data.downstreams_count}</div>
                <div class="bgp-stat-label">Downstreams</div>
            </div>
        </div>`;

        // Prefixes
        if (data.prefixes && data.prefixes.length) {
            html += `<div class="bgp-section-title">Prefixes Originated (${data.prefixes.length})</div>`;
            html += '<div class="bgp-prefix-list">';
            data.prefixes.forEach(p => {
                html += `<div class="bgp-prefix-item">
                    <span class="prefix">${esc(p.prefix)}</span>
                    <span class="prefix-name">${esc(p.name || p.description || '')}</span>
                </div>`;
            });
            html += '</div>';
        }

        // Upstreams
        if (data.upstreams && data.upstreams.length) {
            html += `<div class="bgp-section-title">Upstream Providers (${data.upstreams.length})</div>`;
            html += '<div class="bgp-peer-list">';
            data.upstreams.forEach(p => {
                html += `<div class="bgp-peer-item">
                    <span class="peer-asn">AS${p.asn}</span>
                    <span class="peer-name">${esc(p.name || '--')}</span>
                    <span class="peer-country">${esc(p.country || '')}</span>
                </div>`;
            });
            html += '</div>';
        }

        // Peers
        if (data.peers && data.peers.length) {
            html += `<div class="bgp-section-title">BGP Peers (${data.peers.length})</div>`;
            html += '<div class="bgp-peer-list">';
            data.peers.forEach(p => {
                html += `<div class="bgp-peer-item">
                    <span class="peer-asn">AS${p.asn}</span>
                    <span class="peer-name">${esc(p.name || '--')}</span>
                    <span class="peer-country">${p.ipv4_prefixes || 0} prefixes</span>
                </div>`;
            });
            html += '</div>';
        }

        results.innerHTML = html;
    } catch (e) {
        results.innerHTML = `<div class="empty-state">Error: ${esc(e.message)}</div>`;
    }
}

// Allow Enter key in ASN input
document.addEventListener('DOMContentLoaded', () => {
    const input = document.getElementById('bgp-asn-input');
    if (input) {
        input.addEventListener('keypress', (e) => {
            if (e.key === 'Enter') loadBGPInfo();
        });
    }
});
