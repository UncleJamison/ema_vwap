/* =========================================================================
   WebSocket Client for Real-Time Dashboard Updates
   ========================================================================= */

let _wsConnection = null;
let _wsReconnectAttempts = 0;
const _wsMaxReconnectAttempts = 10;
const _wsReconnectDelay = 2000;

function initWebSocketDashboard() {
  const wsUrl = `${window.location.protocol === 'https:' ? 'wss:' : 'ws:'}//${window.location.host}/ws/dashboard`;

  function connect() {
    try {
      _wsConnection = new WebSocket(wsUrl);

      _wsConnection.onopen = () => {
        console.log('[WebSocket] Connected to dashboard server');
        _wsReconnectAttempts = 0;
        // Request initial status
        _wsConnection.send(JSON.stringify({ type: 'get_status' }));
        _wsConnection.send(JSON.stringify({ type: 'get_positions' }));
      };

      _wsConnection.onmessage = (event) => {
        try {
          const msg = JSON.parse(event.data);
          handleWebSocketMessage(msg);
        } catch (e) {
          console.warn('[WebSocket] Failed to parse message:', e);
        }
      };

      _wsConnection.onclose = () => {
        console.log('[WebSocket] Disconnected');
        if (_wsReconnectAttempts < _wsMaxReconnectAttempts) {
          _wsReconnectAttempts++;
          console.log(`[WebSocket] Reconnecting... (attempt ${_wsReconnectAttempts})`);
          setTimeout(connect, _wsReconnectDelay);
        }
      };

      _wsConnection.onerror = (err) => {
        console.error('[WebSocket] Error:', err);
      };
    } catch (e) {
      console.error('[WebSocket] Connection failed:', e);
    }
  }

  connect();
}

function handleWebSocketMessage(msg) {
  const type = msg?.type;
  const data = msg?.data;

  switch (type) {
    case 'init':
    case 'status_update':
      updatePaperDashboardFromWS(data);
      break;
    case 'positions_update':
      updatePaperPositionsFromWS(data);
      break;
    case 'engine_step_result':
      console.log('[WebSocket] Engine step result:', data);
      // Refresh positions after engine step
      if (_wsConnection && _wsConnection.readyState === WebSocket.OPEN) {
        _wsConnection.send(JSON.stringify({ type: 'get_positions' }));
      }
      break;
    case 'pong':
      // Heartbeat response
      break;
    case 'subscribed':
      console.log('[WebSocket] Subscribed to channels:', data.channels);
      break;
    default:
      console.log('[WebSocket] Unknown message type:', type, data);
  }
}

function updatePaperDashboardFromWS(status) {
  if (!status) return;

  // Update KPIs
  const setTxt = (id, val) => {
    const el = document.getElementById(id);
    if (el) el.textContent = val;
  };

  const formatUsd = (val) => `$${Number(val).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;

  // Total Equity
  if (status.total_equity !== undefined) {
    setTxt('paper-kpi-equity', formatUsd(status.total_equity));
  }
  // Cash Balance
  if (status.cash_balance !== undefined) {
    setTxt('paper-kpi-cash', formatUsd(status.cash_balance));
  }
  // Unrealized PnL
  if (status.unrealized_pnl !== undefined) {
    const pnl = Number(status.unrealized_pnl);
    const pnlPct = status.total_equity ? (pnl / status.total_equity * 100) : 0;
    const pnlColor = pnl >= 0 ? '#00f5a0' : '#ff007a';
    const pnlEl = document.getElementById('paper-kpi-unrealized');
    if (pnlEl) {
      pnlEl.textContent = formatUsd(pnl);
      pnlEl.style.color = pnlColor;
    }
    const pnlPctEl = document.getElementById('paper-kpi-unrealized-pct');
    if (pnlPctEl) {
      pnlPctEl.textContent = `${pnlPct >= 0 ? '+' : ''}${pnlPct.toFixed(2)}% Mark-to-Market`;
      pnlPctEl.style.color = pnlColor;
    }
  }
  // Positions count
  if (status.open_positions_count !== undefined) {
    setTxt('paper-kpi-positions', `${status.open_positions_count} Open`);
  }
  if (status.active_profiles_count !== undefined) {
    setTxt('paper-kpi-profiles-count', `${status.active_profiles_count} Deployed Profiles`);
  }
  // Runner badge
  const runnerBadge = document.getElementById('paper-runner-badge');
  if (runnerBadge) {
    if (status.is_running) {
      runnerBadge.textContent = 'Polling Runner: RUNNING';
      runnerBadge.className = 'badge badge-pulse-green';
    } else {
      runnerBadge.textContent = 'Polling Runner: STOPPED';
      runnerBadge.className = 'badge badge-amber';
    }
  }
  // Positions count tag
  const posCountTag = document.getElementById('paper-positions-count-tag');
  if (posCountTag && status.open_positions_count !== undefined) {
    posCountTag.textContent = `${status.open_positions_count} open`;
  }

  // Update positions table
  if (status.last_evaluations) {
    // The status.last_evaluations contains per-symbol evaluation results
    updatePositionsTableFromEvaluations(status.last_evaluations);
  }
}

function updatePaperPositionsFromWS(positions) {
  if (!Array.isArray(positions)) return;

  const tbody = document.getElementById('paper-positions-body');
  if (!tbody) return;

  if (positions.length === 0) {
    tbody.innerHTML = '<tr><td colspan="12" class="empty-msg">No active paper positions.</td></tr>';
    return;
  }

  tbody.innerHTML = positions.map(p => {
    const pnl = p.unrealized_pnl || 0;
    const pnlPct = p.unrealized_pnl_pct || 0;
    const pnlColor = pnl >= 0 ? '#00f5a0' : '#ff007a';
    const sideBadge = p.side === 'LONG'
      ? '<span class="badge" style="background: rgba(0, 245, 160, 0.15); color: #00f5a0; border-color: rgba(0, 245, 160, 0.3);">LONG</span>'
      : '<span class="badge" style="background: rgba(255, 0, 122, 0.15); color: #ff007a; border-color: rgba(255, 0, 122, 0.3);">SHORT</span>';

    return `
      <tr>
        <td>${p.exchange}</td>
        <td>${p.symbol}</td>
        <td>${sideBadge}</td>
        <td>${Number(p.entry_price).toFixed(4)}</td>
        <td>${Number(p.current_price).toFixed(4)}</td>
        <td>${Number(p.quantity).toLocaleString()}</td>
        <td>$${Number(p.cost_basis).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</td>
        <td>${p.stop_loss ? Number(p.stop_loss).toFixed(4) : '—'}</td>
        <td>${p.take_profit ? Number(p.take_profit).toFixed(4) : '—'}</td>
        <td style="color: ${pnlColor}; font-weight: 600;">${pnl >= 0 ? '+' : ''}$${Number(pnl).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })} (${pnlPct >= 0 ? '+' : ''}${pnlPct.toFixed(2)}%)</td>
        <td>${p.entry_time ? p.entry_time.replace('T', ' ').substring(0, 19) : '—'}</td>
        <td>
          <button class="btn-danger btn-xs" onclick="closePaperPositionManual('${p.exchange}', '${p.symbol}')">Close</button>
        </td>
      </tr>
    `;
  }).join('');
}

function updatePositionsTableFromEvaluations(evaluations) {
  // Build positions from evaluation results
  const positions = [];
  for (const [key, evalData] of Object.entries(evaluations)) {
    if (evalData.action && evalData.action.startsWith('HOLD') && evalData.current_price) {
      // This is an active position update
      // We'd need to fetch from ledger for full position data
      // For now just update the badge
    }
  }
}

// Initialize WebSocket on DOMContentLoaded
document.addEventListener('DOMContentLoaded', () => {
  // Initialize WebSocket after a short delay to ensure DOM is ready
  setTimeout(() => {
    // Only connect WebSocket when on paper trading tab
    const paperTab = document.getElementById('tab-paper');
    if (paperTab) {
      // Use MutationObserver to detect when paper tab becomes active
      const observer = new MutationObserver((mutations) => {
        for (const mutation of mutations) {
          if (mutation.attributeName === 'class' && paperTab.classList.contains('active')) {
            if (!_wsConnection) {
              initWebSocketDashboard();
            }
          }
        }
      });
      observer.observe(paperTab, { attributes: true });

      // Also check immediately if already active
      if (paperTab.classList.contains('active')) {
        initWebSocketDashboard();
      }
    }
  }, 500);
});