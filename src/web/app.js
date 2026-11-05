// Global State
let currentTrades = [];
let lastBestOptunaParams = null;
let lastBatchParams = null;
let lastAppliedParams = null;
let _isValidating = false;

// Validation report/context caches (global — initialized here so they are
// available before any user interaction triggers validation calls)
window._validationReportCache = {};
window._validationContextCache = {};
window._activeValidationContext = null;

// Toast notification system (non-blocking)
function showToast(message, type = "info", duration = 3500) {
  let toast = document.getElementById("global-toast");
  if (!toast) {
    toast = document.createElement("div");
    toast.id = "global-toast";
    toast.style.cssText = "position:fixed;top:16px;right:16px;z-index:9999;padding:12px 20px;border-radius:10px;font-size:0.82rem;font-weight:600;color:#fff;pointer-events:auto;max-width:420px;box-shadow:0 6px 30px rgba(0,0,0,0.45);opacity:0;transform:translateY(-8px);transition:opacity 0.25s,transform 0.25s;";
    document.body.appendChild(toast);
  }
  const bg = type === "success" ? "#10b981" : type === "warn" ? "#f59e0b" : type === "error" ? "#ef4444" : "#0ea5e9";
  toast.style.background = bg;
  toast.textContent = message;
  toast.style.opacity = "1";
  toast.style.transform = "translateY(0)";
  clearTimeout(toast._timeout);
  toast._timeout = setTimeout(() => {
    toast.style.opacity = "0";
    toast.style.transform = "translateY(-8px)";
  }, duration);
}

// Default System & Strategy Settings
const DEFAULT_SETTINGS = {
  maker_fee_pct: 0.05,
  taker_fee_pct: 0.075,
  slippage_pct: 0.05,
  fast_ema_min: 3,
  fast_ema_max: 30,
  slow_ema_min: 10,
  slow_ema_max: 100,
  trend_ema_min: 50,
  trend_ema_max: 250,
  volume_multiplier_min: 0.8,
  volume_multiplier_max: 3.5,
  atr_multiplier_min: 2.0,
  atr_multiplier_max: 5.0,
  trailing_stop_min: 1.0,
  trailing_stop_max: 3.0,
  risk_reward_min: 1.0,
  risk_reward_max: 6.0,
  vwap_slope_max_bound: 0.003,
  min_trades: 3,
  n_jobs: 1,
  n_startup_trials: null,
  seed_base_params: true,
  // Validation Gates
  val_min_sharpe: 0.8,
  val_max_drawdown: 30.0,
  val_min_return: 0.0,
  val_min_trades: 3,
  val_wf_efficiency: 50,
  val_profitable_windows: 50,
  val_min_oos_return: 0.0,
  val_trials: 250,
  val_rigor_preset: "standard",
  val_max_ruin: 5.0,
  val_max_mc_drawdown: 35.0,
  mc_sims: 500,
  confidence_levels: [5.0, 50.0, 95.0],
  val_max_ruin: 5.0,
  default_capital: 10000,
  default_risk_pct: 1.0,
  min_warmup_bars: 250,
  market_hours: "crypto_24_7",
  // Crypto Exchange Credentials
  gemini_api_key: "",
  gemini_secret_key: "",
  gemini_sandbox: true,
  kucoin_api_key: "",
  kucoin_secret_key: "",
  kucoin_passphrase: "",
  kucoin_sandbox: false,
  robinhood_api_key: "",
  robinhood_private_key: "",
  robinhood_account_number: "",
  // US Equities & Stock Data Providers
  alpaca_api_key_id: "",
  alpaca_secret_key: "",
  alpaca_paper: true,
  polygon_api_key: "",
  tiingo_api_token: "",
  // Administrator API Auth
  app_api_key: "",
};

// Cached settings for synchronous access (populated at startup)
let cachedSettings = { ...DEFAULT_SETTINGS };
let cachedSettingsLoaded = false;


// Sensitive fields that should NOT be stored in localStorage
const SENSITIVE_FIELDS = new Set([
  "app_api_key",
  "gemini_api_key", "gemini_secret_key",
  "kucoin_api_key", "kucoin_secret_key", "kucoin_passphrase",
  "robinhood_api_key", "robinhood_private_key",
  "alpaca_api_key_id", "alpaca_secret_key",
  "polygon_api_key", "tiingo_api_token",
]);

async function loadSensitiveSettings() {
  console.log('[DEBUG] loadSensitiveSettings ENTERED');
  try {
    const headers = getAuthHeaders();
    console.log('[DEBUG] loadSensitiveSettings headers:', headers);
    const resp = await fetch("/api/settings/sensitive", { headers });
    console.log('[DEBUG] loadSensitiveSettings response:', resp.status, resp.statusText);
    if (resp.ok) {
      const sensitive = await resp.json();
      console.log('[DEBUG] loadSensitiveSettings data:', sensitive);
      cachedSettings = { ...cachedSettings, ...sensitive };
      cachedSettingsLoaded = true;
    } else {
      const errText = await resp.text();
      console.warn('[DEBUG] loadSensitiveSettings error body:', errText);
    }
  } catch (e) {
    console.error("[DEBUG] loadSensitiveSettings CAUGHT ERROR:", e, e.message, e.stack);
  }
}

function getSettings() {
  try {
    const saved = localStorage.getItem("ema_vwap_settings");
    let settings = { ...DEFAULT_SETTINGS };
    if (saved) {
      settings = { ...settings, ...JSON.parse(saved) };
    }
    // Merge in sensitive settings from the startup cache (loaded once from backend)
    settings = { ...settings, ...Object.fromEntries(Object.entries(cachedSettings).filter(([k]) => SENSITIVE_FIELDS.has(k))) };
    cachedSettings = { ...cachedSettings, ...settings };
    cachedSettingsLoaded = true;
    return settings;
  } catch (e) {
    console.warn("Could not load settings from localStorage:", e);
  }
  return { ...DEFAULT_SETTINGS };
}

function saveSettings(settings) {
  try {
    // Filter out sensitive fields before saving to localStorage
    const safeSettings = { ...settings };
    for (const key of SENSITIVE_FIELDS) {
      delete safeSettings[key];
    }
    localStorage.setItem("ema_vwap_settings", JSON.stringify(safeSettings));

    // Send sensitive settings to backend
    const sensitiveSettings = {};
    for (const key of SENSITIVE_FIELDS) {
      if (settings[key] !== undefined && settings[key] !== "") {
        sensitiveSettings[key] = settings[key];
      }
    }
    if (Object.keys(sensitiveSettings).length > 0) {
      fetch("/api/settings/sensitive", {
        method: "POST",
        headers: getAuthHeaders({ "Content-Type": "application/json" }),
        body: JSON.stringify(sensitiveSettings),
      }).catch(err => console.warn("Failed to save sensitive settings:", err));
    }
  } catch (e) {
    console.warn("Could not save settings to localStorage:", e);
  }
}

function initSettingsUI() {
  const s = getSettings();

  // Execution & Fees
  const setVal = (id, val) => {
    const el = document.getElementById(id);
    if (el) el.value = val;
  };

  setVal("setting-maker-fee", s.maker_fee_pct);
  setVal("setting-taker-fee", s.taker_fee_pct);
  setVal("setting-slippage", s.slippage_pct);

  // Optuna Boundaries & Tuning
  setVal("setting-opt-fast-min", s.fast_ema_min);
  setVal("setting-opt-fast-max", s.fast_ema_max);
  setVal("setting-opt-slow-min", s.slow_ema_min);
  setVal("setting-opt-slow-max", s.slow_ema_max);
  setVal("setting-opt-trend-min", s.trend_ema_min);
  setVal("setting-opt-trend-max", s.trend_ema_max);
  setVal("setting-opt-vol-min", s.volume_multiplier_min);
  setVal("setting-opt-vol-max", s.volume_multiplier_max);
  setVal("setting-opt-atr-min", s.atr_multiplier_min);
  setVal("setting-opt-atr-max", s.atr_multiplier_max);
  setVal("setting-opt-rr-min", s.risk_reward_min);
  setVal("setting-opt-rr-max", s.risk_reward_max);
  setVal("setting-opt-slope-max", s.vwap_slope_max_bound);
  setVal("setting-opt-min-trades", s.min_trades);
  setVal("setting-opt-n-jobs", s.n_jobs !== undefined ? s.n_jobs : 1);
  setVal(
    "setting-opt-startup-trials",
    s.n_startup_trials !== null && s.n_startup_trials !== undefined
      ? s.n_startup_trials
      : ""
  );
  const seedEl = document.getElementById("setting-opt-seed-base");
  if (seedEl) seedEl.checked = s.seed_base_params !== false;

  // Risk & Capital
  setVal("setting-default-capital", s.default_capital);
  setVal("setting-default-risk", s.default_risk_pct);

  // Validation Gates
  setVal("setting-val-min-sharpe", s.val_min_sharpe);
  setVal("setting-val-max-drawdown", s.val_max_drawdown);
  setVal("setting-val-min-return", s.val_min_return);
  setVal("setting-val-min-trades", s.val_min_trades);
  setVal("setting-val-wf-efficiency", s.val_wf_efficiency);
  setVal("setting-val-profitable-windows", s.val_profitable_windows);
  setVal("setting-val-min-oos-return", s.val_min_oos_return);
  setVal("setting-val-trials", s.val_trials || 250);
  setVal("setting-val-rigor-preset", s.val_rigor_preset || "standard");
  setVal("setting-val-max-ruin", s.val_max_ruin);
  setVal("setting-val-max-mc-drawdown", s.val_max_mc_drawdown);
  setVal("mc-sims-input", s.mc_sims);
  setVal("setting-val-max-ruin", s.confidence_levels[0]);
  setVal("setting-val-median-mc-drawdown", s.confidence_levels[1]);
  setVal("setting-val-max-ruin", s.val_max_ruin);

  // Crypto Exchanges
  setVal("setting-gemini-api-key", s.gemini_api_key || "");
  setVal("setting-gemini-secret-key", s.gemini_secret_key || "");
  const geminiSandboxEl = document.getElementById("setting-gemini-sandbox");
  if (geminiSandboxEl) geminiSandboxEl.checked = s.gemini_sandbox !== false;

  setVal("setting-kucoin-api-key", s.kucoin_api_key || "");
  setVal("setting-kucoin-secret-key", s.kucoin_secret_key || "");
  setVal("setting-kucoin-passphrase", s.kucoin_passphrase || "");
  const kucoinSandboxEl = document.getElementById("setting-kucoin-sandbox");
  if (kucoinSandboxEl) kucoinSandboxEl.checked = s.kucoin_sandbox === true;

  setVal("setting-robinhood-api-key", s.robinhood_api_key || "");
  setVal("setting-robinhood-private-key", s.robinhood_private_key || "");
  setVal("setting-robinhood-account-number", s.robinhood_account_number || "");

  // US Equities & Stock Data Providers
  setVal("setting-alpaca-key-id", s.alpaca_api_key_id || "");
  setVal("setting-alpaca-secret-key", s.alpaca_secret_key || "");
  const alpacaPaperEl = document.getElementById("setting-alpaca-paper");
  if (alpacaPaperEl) alpacaPaperEl.checked = s.alpaca_paper !== false;
  setVal("setting-polygon-api-key", s.polygon_api_key || "");
  setVal("setting-tiingo-api-token", s.tiingo_api_token || "");
  setVal("setting-app-api-key", s.app_api_key || "");

  // Asynchronously fetch backend masked settings to hydrate stored server keys
  fetch("/api/settings")
    .then((r) => r.json())
    .then((data) => {
      if (data && data.settings) {
        const bs = data.settings;
        if (bs.gemini_api_key) setVal("setting-gemini-api-key", bs.gemini_api_key);
        if (bs.gemini_secret_key) setVal("setting-gemini-secret-key", bs.gemini_secret_key);
        if (bs.gemini_sandbox !== undefined) {
          const gEl = document.getElementById("setting-gemini-sandbox");
          if (gEl) gEl.checked = bs.gemini_sandbox === "true" || bs.gemini_sandbox === true;
        }

        if (bs.kucoin_api_key) setVal("setting-kucoin-api-key", bs.kucoin_api_key);
        if (bs.kucoin_secret_key) setVal("setting-kucoin-secret-key", bs.kucoin_secret_key);
        if (bs.kucoin_passphrase) setVal("setting-kucoin-passphrase", bs.kucoin_passphrase);
        if (bs.kucoin_sandbox !== undefined) {
          const kEl = document.getElementById("setting-kucoin-sandbox");
          if (kEl) kEl.checked = bs.kucoin_sandbox === "true" || bs.kucoin_sandbox === true;
        }

        if (bs.robinhood_api_key) setVal("setting-robinhood-api-key", bs.robinhood_api_key);
        if (bs.robinhood_private_key) setVal("setting-robinhood-private-key", bs.robinhood_private_key);
        if (bs.robinhood_account_number) setVal("setting-robinhood-account-number", bs.robinhood_account_number);

        if (bs.alpaca_api_key_id) setVal("setting-alpaca-key-id", bs.alpaca_api_key_id);
        if (bs.alpaca_secret_key) setVal("setting-alpaca-secret-key", bs.alpaca_secret_key);
        if (bs.alpaca_paper !== undefined) {
          const pEl = document.getElementById("setting-alpaca-paper");
          if (pEl) pEl.checked = bs.alpaca_paper === "true" || bs.alpaca_paper === true;
        }
        if (bs.polygon_api_key) setVal("setting-polygon-api-key", bs.polygon_api_key);
        if (bs.tiingo_api_token) setVal("setting-tiingo-api-token", bs.tiingo_api_token);
      }
    })
    .catch((err) => console.warn("Could not fetch server settings:", err));
}

function setupSettingsModalEvents() {
  const openBtn = document.getElementById("open-settings-btn");
  const closeBtn = document.getElementById("close-settings-btn");
  const modal = document.getElementById("settings-modal");
  const saveBtn = document.getElementById("save-settings-btn");
  const resetBtn = document.getElementById("reset-settings-btn");

  if (openBtn && modal) {
    openBtn.addEventListener("click", async () => {
      if (!cachedSettingsLoaded) {
        await loadSensitiveSettings();
      }
      initSettingsUI();
      modal.style.display = "flex";
    });
  }

  if (closeBtn && modal) {
    closeBtn.addEventListener("click", () => {
      modal.style.display = "none";
    });
  }

  // Modal tab switching
  const modalTabBtns = document.querySelectorAll(".modal-tab-btn");
  modalTabBtns.forEach((btn) => {
    btn.addEventListener("click", () => {
      modalTabBtns.forEach((b) => b.classList.remove("active"));
      document.querySelectorAll(".modal-tab-content").forEach((c) => c.classList.remove("active"));
      btn.classList.add("active");
      const targetId = btn.getAttribute("data-modal-tab");
      const targetContent = document.getElementById(targetId);
      if (targetContent) targetContent.classList.add("active");
      if (targetId === "settings-tab-db") {
        loadDatabaseMaintenanceInfo();
      }
    });
  });

  if (saveBtn && modal) {
    saveBtn.addEventListener("click", () => {
      const getNum = (id, def) => {
        const el = document.getElementById(id);
        return el ? parseFloat(el.value) || def : def;
      };
      const getInt = (id, def) => {
        const el = document.getElementById(id);
        return el ? parseInt(el.value, 10) || def : def;
      };

      const startupInput = document.getElementById("setting-opt-startup-trials");
      const startupVal =
        startupInput && startupInput.value.trim() !== ""
          ? parseInt(startupInput.value, 10)
          : null;
      const seedEl = document.getElementById("setting-opt-seed-base");
      const seedVal = seedEl ? seedEl.checked : true;
      const nJobsEl = document.getElementById("setting-opt-n-jobs");
      const nJobsVal = nJobsEl ? parseInt(nJobsEl.value, 10) : 1;

      const updated = {
        maker_fee_pct: getNum("setting-maker-fee", DEFAULT_SETTINGS.maker_fee_pct),
        taker_fee_pct: getNum("setting-taker-fee", DEFAULT_SETTINGS.taker_fee_pct),
        slippage_pct: getNum("setting-slippage", DEFAULT_SETTINGS.slippage_pct),
        fast_ema_min: getInt("setting-opt-fast-min", DEFAULT_SETTINGS.fast_ema_min),
        fast_ema_max: getInt("setting-opt-fast-max", DEFAULT_SETTINGS.fast_ema_max),
        slow_ema_min: getInt("setting-opt-slow-min", DEFAULT_SETTINGS.slow_ema_min),
        slow_ema_max: getInt("setting-opt-slow-max", DEFAULT_SETTINGS.slow_ema_max),
        trend_ema_min: getInt("setting-opt-trend-min", DEFAULT_SETTINGS.trend_ema_min),
        trend_ema_max: getInt("setting-opt-trend-max", DEFAULT_SETTINGS.trend_ema_max),
        volume_multiplier_min: getNum("setting-opt-vol-min", DEFAULT_SETTINGS.volume_multiplier_min),
        volume_multiplier_max: getNum("setting-opt-vol-max", DEFAULT_SETTINGS.volume_multiplier_max),
        atr_multiplier_min: getNum("setting-opt-atr-min", DEFAULT_SETTINGS.atr_multiplier_min),
        atr_multiplier_max: getNum("setting-opt-atr-max", DEFAULT_SETTINGS.atr_multiplier_max),
        trailing_stop_min: getNum("setting-opt-tsl-min", DEFAULT_SETTINGS.trailing_stop_min),
        trailing_stop_max: getNum("setting-opt-tsl-max", DEFAULT_SETTINGS.trailing_stop_max),
        risk_reward_min: getNum("setting-opt-rr-min", DEFAULT_SETTINGS.risk_reward_min),
        risk_reward_max: getNum("setting-opt-rr-max", DEFAULT_SETTINGS.risk_reward_max),
        vwap_slope_max_bound: getNum("setting-opt-slope-max", DEFAULT_SETTINGS.vwap_slope_max_bound),
        min_trades: getInt("setting-opt-min-trades", DEFAULT_SETTINGS.min_trades),
        n_jobs: nJobsVal,
        n_startup_trials: startupVal,
        seed_base_params: seedVal,
        default_capital: getNum("setting-default-capital", DEFAULT_SETTINGS.default_capital),
        default_risk_pct: getNum("setting-default-risk", DEFAULT_SETTINGS.default_risk_pct),
        min_warmup_bars: getInt("setting-min-warmup-bars", DEFAULT_SETTINGS.min_warmup_bars),
        market_hours: document.getElementById("setting-market-hours")?.value || DEFAULT_SETTINGS.market_hours,
        val_min_sharpe: getNum("setting-val-min-sharpe", DEFAULT_SETTINGS.val_min_sharpe),
        val_max_drawdown: getNum("setting-val-max-drawdown", DEFAULT_SETTINGS.val_max_drawdown),
        val_min_return: getNum("setting-val-min-return", DEFAULT_SETTINGS.val_min_return),
        val_min_trades: getInt("setting-val-min-trades", DEFAULT_SETTINGS.val_min_trades),
        val_wf_efficiency: getNum("setting-val-wf-efficiency", DEFAULT_SETTINGS.val_wf_efficiency),
        val_profitable_windows: getNum("setting-val-profitable-windows", DEFAULT_SETTINGS.val_profitable_windows),
        val_min_oos_return: getNum("setting-val-min-oos-return", DEFAULT_SETTINGS.val_min_oos_return),
        val_trials: getInt("setting-val-trials", DEFAULT_SETTINGS.val_trials),
        val_rigor_preset: document.getElementById("setting-val-rigor-preset")?.value || "standard",
        val_max_ruin: getNum("setting-val-max-ruin", DEFAULT_SETTINGS.val_max_ruin),
        val_max_mc_drawdown: getNum("setting-val-max-mc-drawdown", DEFAULT_SETTINGS.val_max_mc_drawdown),
        confidence_levels: [getNum("setting-val-max-ruin", DEFAULT_SETTINGS.confidence_levels[0]), getNum("setting-val-median-mc-drawdown", DEFAULT_SETTINGS.confidence_levels[1])],
        gemini_api_key: (document.getElementById("setting-gemini-api-key")?.value || "").trim(),
        gemini_secret_key: (document.getElementById("setting-gemini-secret-key")?.value || "").trim(),
        gemini_sandbox: document.getElementById("setting-gemini-sandbox")?.checked ?? true,
        kucoin_api_key: (document.getElementById("setting-kucoin-api-key")?.value || "").trim(),
        kucoin_secret_key: (document.getElementById("setting-kucoin-secret-key")?.value || "").trim(),
        kucoin_passphrase: (document.getElementById("setting-kucoin-passphrase")?.value || "").trim(),
        kucoin_sandbox: document.getElementById("setting-kucoin-sandbox")?.checked ?? false,
        robinhood_api_key: (document.getElementById("setting-robinhood-api-key")?.value || "").trim(),
        robinhood_private_key: (document.getElementById("setting-robinhood-private-key")?.value || "").trim(),
        robinhood_account_number: (document.getElementById("setting-robinhood-account-number")?.value || "").trim(),
        alpaca_api_key_id: (document.getElementById("setting-alpaca-key-id")?.value || "").trim(),
        alpaca_secret_key: (document.getElementById("setting-alpaca-secret-key")?.value || "").trim(),
        alpaca_paper: document.getElementById("setting-alpaca-paper")?.checked ?? true,
        polygon_api_key: (document.getElementById("setting-polygon-api-key")?.value || "").trim(),
        tiingo_api_token: (document.getElementById("setting-tiingo-api-token")?.value || "").trim(),
        app_api_key: (document.getElementById("setting-app-api-key")?.value || "").trim(),
      };

      saveSettings(updated);

      // Persist to backend database
      fetch("/api/settings", {
        method: "POST",
        headers: getAuthHeaders({ "Content-Type": "application/json" }),
        body: JSON.stringify(updated),
      }).catch((err) => console.warn("Could not persist settings to server:", err));

      // Re-initialize WebSocket so it uses the newly saved API key immediately
      if (typeof initWebSocketDashboard === "function") {
        initWebSocketDashboard();
      }

      modal.style.display = "none";
      alert("Settings saved successfully.");
    });
  }

  if (resetBtn) {
    resetBtn.addEventListener("click", () => {
      if (confirm("Reset all settings to recommended system defaults?")) {
        saveSettings(DEFAULT_SETTINGS);
        initSettingsUI();
      }
    });
  }

  setupDatabaseMaintenanceEvents();
}

// -------------------------------------------------------------
// Database Maintenance & Multi-DB Management Handlers
// -------------------------------------------------------------

function getAuthHeaders(extraHeaders = {}) {
  const inputKey = document.getElementById("setting-app-api-key")?.value;
  const key = (inputKey !== undefined && inputKey.trim() !== "" ? inputKey :
    (typeof window.BOOTSTRAP_API_KEY !== "undefined" && window.BOOTSTRAP_API_KEY) ||
    (cachedSettings.app_api_key || "")).trim();
  const headers = { ...extraHeaders };
  if (key) {
    headers["X-API-Key"] = key;
  }
  return headers;
}

function logDbMaintenance(msg, type = "info") {
  const logEl = document.getElementById("db-maintenance-log");
  if (!logEl) return;
  const time = new Date().toLocaleTimeString();
  const prefix = type === "error" ? "❌ [ERROR]" : type === "warn" ? "⚠️ [WARN]" : "ℹ️ [INFO]";
  logEl.textContent += `\n[${time}] ${prefix} ${msg}`;
  logEl.scrollTop = logEl.scrollHeight;
}

function formatBytes(bytes) {
  if (bytes === 0 || bytes === undefined || bytes === null) return "0 B";
  const k = 1024;
  const sizes = ["B", "KB", "MB", "GB"];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + " " + sizes[i];
}

async function loadDatabaseMaintenanceInfo() {
  logDbMaintenance("Inspecting multi-database domain status and file integrity...");
  try {
    const res = await fetch("/api/db/info", {
      headers: getAuthHeaders()
    });
    if (res.status === 403) {
      logDbMaintenance("Authentication failed: HTTP 403 Forbidden. Check your Administrator API Key.", "error");
      showToast("DB Maintenance: 403 Forbidden. Check API Key.", "error");
      return;
    }
    if (!res.ok) {
      throw new Error(`Server returned HTTP ${res.status}`);
    }
    const json = await res.json();
    const data = json.data || {};
    const perDb = data.per_db || {};

    const dbKeys = ["candles", "batch_results", "paper_trading", "system"];
    dbKeys.forEach((key) => {
      const dbInfo = perDb[key];
      const sizeEl = document.getElementById(`val-db-${key}-size`);
      const recordsEl = document.getElementById(`val-db-${key}-records`);
      const modeEl = document.getElementById(`val-db-${key}-mode`);
      const integrityEl = document.getElementById(`val-db-${key}-integrity`);
      const badgeEl = document.getElementById(`badge-db-${key}`);

      if (!dbInfo || dbInfo.status === "unreachable") {
        if (sizeEl) sizeEl.textContent = "Unreachable";
        if (integrityEl) {
          integrityEl.textContent = "Error";
          integrityEl.style.color = "#ff007a";
        }
        if (badgeEl) {
          badgeEl.textContent = "ERR";
          badgeEl.style.background = "rgba(255, 0, 122, 0.2)";
          badgeEl.style.color = "#ff007a";
          badgeEl.style.borderColor = "#ff007a";
        }
        return;
      }

      if (sizeEl) sizeEl.textContent = formatBytes(dbInfo.file_size_bytes || 0);
      if (modeEl) modeEl.textContent = (dbInfo.journal_mode || "delete").toUpperCase();

      let recordDisplay = "--";
      if (key === "candles") {
        recordDisplay = (dbInfo.candle_record_count !== undefined)
          ? Number(dbInfo.candle_record_count).toLocaleString()
          : (dbInfo.tables ? `${dbInfo.tables.length} tables` : "--");
      } else if (key === "batch_results") {
        recordDisplay = dbInfo.tables ? `${dbInfo.tables.length} tables` : "Active";
      } else if (key === "paper_trading") {
        recordDisplay = dbInfo.tables ? `${dbInfo.tables.length} tables` : "Active";
      } else if (key === "system") {
        recordDisplay = dbInfo.tables ? `${dbInfo.tables.length} tables` : "Active";
      }
      if (recordsEl) recordsEl.textContent = recordDisplay;

      const isOk = dbInfo.integrity_check === "ok";
      if (integrityEl) {
        integrityEl.textContent = isOk ? "Clean (ok)" : String(dbInfo.integrity_check);
        integrityEl.style.color = isOk ? "#00f5a0" : "#ff007a";
      }
      if (badgeEl) {
        badgeEl.textContent = isOk ? "OK" : "CORRUPT";
        badgeEl.style.background = isOk ? "rgba(0, 245, 160, 0.2)" : "rgba(255, 0, 122, 0.2)";
        badgeEl.style.color = isOk ? "#00f5a0" : "#ff007a";
        badgeEl.style.borderColor = isOk ? "#00f5a0" : "#ff007a";
      }
    });

    logDbMaintenance("All 4 database domains verified. Integrity checks passed.");
  } catch (err) {
    logDbMaintenance(`Failed to inspect database health: ${err.message}`, "error");
  }
}

async function runDatabaseVacuum() {
  const btn = document.getElementById("btn-db-vacuum-run");
  if (btn) btn.disabled = true;
  logDbMaintenance("Initiating multi-database VACUUM and disk compaction across all domains...");
  try {
    const res = await fetch("/api/db/vacuum", {
      method: "POST",
      headers: getAuthHeaders({ "Content-Type": "application/json" })
    });
    if (res.status === 403) {
      logDbMaintenance("Vacuum failed: HTTP 403 Forbidden. Check Administrator API Key.", "error");
      showToast("Vacuum failed: 403 Forbidden", "error");
      return;
    }
    const data = await res.json();
    if (data.status === "success" || data.status === "partial_failure") {
      logDbMaintenance(`Vacuum complete: ${data.data?.message || data.status}`);
      showToast("Database vacuum and compaction completed successfully.", "success");
      // Small delay to allow DB file handles to settle before re-inspecting
      await new Promise(r => setTimeout(r, 800));
      await loadDatabaseMaintenanceInfo();
    } else {
      logDbMaintenance(`Vacuum returned status: ${data.status}`, "warn");
    }
  } catch (err) {
    logDbMaintenance(`Vacuum error: ${err.message}`, "error");
    showToast(`Vacuum error: ${err.message}`, "error");
  } finally {
    if (btn) btn.disabled = false;
  }
}

async function runDatabaseRepair() {
  if (!confirm("Quarantine any corrupted database and rebuild clean SQLite schemas?\n\nThis will isolate malformed database files with a timestamp suffix and recreate intact SQLite tables.")) {
    return;
  }
  const btn = document.getElementById("btn-db-repair-run");
  if (btn) btn.disabled = true;
  logDbMaintenance("Initiating database repair & quarantine check across all domains...");
  try {
    const res = await fetch("/api/db/repair", {
      method: "POST",
      headers: getAuthHeaders({ "Content-Type": "application/json" })
    });
    if (res.status === 403) {
      logDbMaintenance("Repair failed: HTTP 403 Forbidden. Check Administrator API Key.", "error");
      showToast("Repair failed: 403 Forbidden", "error");
      return;
    }
    const data = await res.json();
    logDbMaintenance(`Repair finished: ${data.data?.message || data.status}`);
    if (data.data?.any_quarantined) {
      logDbMaintenance("⚠️ Notice: One or more corrupt database files were quarantined and re-initialized.", "warn");
    }
    showToast("Database integrity inspection and repair finished.", "success");
    // Small delay to allow DB file handles to settle before re-inspecting
    await new Promise(r => setTimeout(r, 800));
    await loadDatabaseMaintenanceInfo();
  } catch (err) {
    logDbMaintenance(`Repair error: ${err.message}`, "error");
    showToast(`Repair error: ${err.message}`, "error");
  } finally {
    if (btn) btn.disabled = false;
  }
}

async function exportCandlesData(format = "csv") {
  const exchange = document.getElementById("db-export-exchange")?.value || "all";
  const symbol = (document.getElementById("db-export-symbol")?.value || "BTC/USD").trim();
  const timeframe = document.getElementById("db-export-timeframe")?.value || "5m";
  const limit = parseInt(document.getElementById("db-export-limit")?.value, 10) || 500;

  const btn = document.getElementById(format === "csv" ? "btn-db-export-csv" : "btn-db-export-json");
  if (btn) btn.disabled = true;

  logDbMaintenance(`Exporting candles (${format.toUpperCase()}) for ${exchange} / ${symbol} / ${timeframe} (limit: ${limit})...`);
  try {
    const params = new URLSearchParams({
      exchange: exchange,
      symbol: symbol,
      timeframe: timeframe,
      limit: String(limit)
    });
    const url = `/api/db/export/${format}?${params.toString()}`;
    const res = await fetch(url, {
      method: "GET",
      headers: getAuthHeaders()
    });

    if (res.status === 403) {
      logDbMaintenance("Export failed: HTTP 403 Forbidden. Check Administrator API Key.", "error");
      showToast("Export failed: 403 Forbidden", "error");
      return;
    }
    if (!res.ok) {
      throw new Error(`Export returned HTTP ${res.status}`);
    }

    const blob = await res.blob();
    const cleanSym = symbol.replace("/", "_");
    const filename = `candles_${exchange}_${cleanSym}_${timeframe}.${format}`;
    const downloadUrl = window.URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = downloadUrl;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    a.remove();
    window.URL.revokeObjectURL(downloadUrl);

    logDbMaintenance(`Candles export downloaded: ${filename} (${blob.size} bytes).`);
    showToast(`Downloaded ${filename}`, "success");
  } catch (err) {
    logDbMaintenance(`Export error: ${err.message}`, "error");
    showToast(`Export error: ${err.message}`, "error");
  } finally {
    if (btn) btn.disabled = false;
  }
}

async function clearCandleCacheFromModal() {
  if (!confirm("Are you sure you want to clear the candle cache?\n\nCached market candles will be removed from the database.")) {
    return;
  }
  const btn = document.getElementById("btn-db-clear-cache-modal");
  if (btn) btn.disabled = true;
  logDbMaintenance("Flushing candle cache...");
  try {
    const res = await fetch("/api/clear_cache", {
      method: "POST",
      headers: getAuthHeaders({ "Content-Type": "application/json" })
    });
    if (res.status === 403) {
      logDbMaintenance("Cache clear failed: HTTP 403 Forbidden. Check Administrator API Key.", "error");
      showToast("Cache clear failed: 403 Forbidden", "error");
      return;
    }
    const data = await res.json();
    logDbMaintenance(data.message || "Candle cache cleared.");
    showToast(data.message || "Candle cache cleared.", "success");
    await loadDatabaseMaintenanceInfo();
  } catch (err) {
    logDbMaintenance(`Cache clear error: ${err.message}`, "error");
  } finally {
    if (btn) btn.disabled = false;
  }
}

function setupDatabaseMaintenanceEvents() {
  const refreshBtn = document.getElementById("btn-db-refresh-info");
  const integrityBtn = document.getElementById("btn-db-integrity-run");
  const vacuumBtn = document.getElementById("btn-db-vacuum-run");
  const repairBtn = document.getElementById("btn-db-repair-run");
  const clearCacheBtn = document.getElementById("btn-db-clear-cache-modal");
  const exportCsvBtn = document.getElementById("btn-db-export-csv");
  const exportJsonBtn = document.getElementById("btn-db-export-json");
  const clearLogBtn = document.getElementById("btn-db-clear-log");
  const saveKeyBtn = document.getElementById("btn-save-api-key");

  if (refreshBtn) refreshBtn.addEventListener("click", loadDatabaseMaintenanceInfo);
  if (integrityBtn) integrityBtn.addEventListener("click", loadDatabaseMaintenanceInfo);
  if (vacuumBtn) vacuumBtn.addEventListener("click", runDatabaseVacuum);
  if (repairBtn) repairBtn.addEventListener("click", runDatabaseRepair);
  if (clearCacheBtn) clearCacheBtn.addEventListener("click", clearCandleCacheFromModal);
  if (exportCsvBtn) exportCsvBtn.addEventListener("click", () => exportCandlesData("csv"));
  if (exportJsonBtn) exportJsonBtn.addEventListener("click", () => exportCandlesData("json"));

  if (clearLogBtn) {
    clearLogBtn.addEventListener("click", () => {
      const logEl = document.getElementById("db-maintenance-log");
      if (logEl) logEl.textContent = "[System] Activity log cleared.";
    });
  }

  if (saveKeyBtn) {
    saveKeyBtn.addEventListener("click", () => {
      const inputEl = document.getElementById("setting-app-api-key");
      const keyVal = (inputEl?.value || "").trim();
      const currentSettings = getSettings();
      currentSettings.app_api_key = keyVal;
      saveSettings(currentSettings);
      showToast("Administrator API Key saved.", "success");
      logDbMaintenance("Administrator API Key updated and saved to client storage.");
    });
  }
}

// Global error handlers for debugging
window.addEventListener('unhandledrejection', (event) => {
  console.error('[GLOBAL] Unhandled rejection:', event.reason, event.reason?.stack);
});
window.addEventListener('error', (event) => {
  console.error('[GLOBAL] Error:', event.message, event.filename, event.lineno, event.colno, event.error);
});

document.addEventListener("DOMContentLoaded", async () => {
  console.log('[DEBUG] DOMContentLoaded fired, starting loadSensitiveSettings');
  // Load sensitive settings (API keys) from backend at startup
  await loadSensitiveSettings();

  // Ensure data-range-select defaults to "auto" (optimal lookback per timeframe)
  // so backtests use get_optimal_lookback_days (e.g., 90d for 5m) instead of a fixed range.
  const rangeEl = document.getElementById("data-range-select");
  if (rangeEl && !rangeEl.value) rangeEl.value = "auto";

  // Initialize WebSocket dashboard with API key from cached settings
  if (typeof initWebSocketDashboard === "function") {
    initWebSocketDashboard();
  }

  const runBtn = document.getElementById("run-backtest-btn");
  const syncBtn = document.getElementById("sync-db-btn");
  const clearBtn = document.getElementById("clear-db-btn");
  const exportBtn = document.getElementById("export-csv-btn");
  const exchangeSelect = document.getElementById("exchange-select");
  const exchangeBadge = document.getElementById("exchange-badge");

  const runOptBtn = document.getElementById("run-optuna-btn");
  const runWfoBtn = document.getElementById("run-wfo-btn");
  const runMcBtn = document.getElementById("run-mc-btn");
  const applyOptBtn = document.getElementById("apply-opt-params-btn");
  const deployOptBtn = document.getElementById("deploy-opt-paper-btn");
  const deployBacktestBtn = document.getElementById("deploy-backtest-paper-btn");
  const resetMainBtn = document.getElementById("reset-main-defaults-btn");

  if (deployOptBtn) {
    deployOptBtn.addEventListener("click", () => deployOptunaToPaper());
  }
  if (deployBacktestBtn) {
    deployBacktestBtn.addEventListener("click", () => deployCurrentBacktestToPaper());
  }

  const optObjectiveModeEl = document.getElementById("optuna-objective-mode-select");
  const batchObjectiveModeEl = document.getElementById("batch-objective-mode-select");

  if (optObjectiveModeEl) optObjectiveModeEl.addEventListener("change", syncOptunaObjectiveModeUI);
  if (batchObjectiveModeEl) batchObjectiveModeEl.addEventListener("change", syncBatchObjectiveModeUI);

  // Drawdown constraint toggle for Optuna
  const optunaDD = document.getElementById("optuna-enable-dd-constraint");
  if (optunaDD) {
    optunaDD.addEventListener("change", (e) => {
      const group = document.getElementById("optuna-dd-constraint-group");
      if (group) group.style.display = e.target.checked ? "block" : "none";
    });
  }

  // Drawdown constraint toggle for Batch
  const batchDD = document.getElementById("batch-enable-dd-constraint");
  if (batchDD) {
    batchDD.addEventListener("change", (e) => {
      const group = document.getElementById("batch-dd-constraint-group");
      if (group) group.style.display = e.target.checked ? "block" : "none";
    });
  }

  // Max holding bars toggle for Optuna
  const optunaMaxHold = document.getElementById("optuna-enable-max-hold");
  if (optunaMaxHold) {
    optunaMaxHold.addEventListener("change", (e) => {
      const group = document.getElementById("optuna-max-hold-group");
      if (group) group.style.display = e.target.checked ? "block" : "none";
    });
  }

  // Max holding bars toggle for Batch
  const batchMaxHold = document.getElementById("batch-enable-max-hold");
  if (batchMaxHold) {
    batchMaxHold.addEventListener("change", (e) => {
      const group = document.getElementById("batch-max-hold-group");
      if (group) group.style.display = e.target.checked ? "block" : "none";
    });
  }

  // Tab Navigation Handler
  const tabBtns = document.querySelectorAll(".tab-btn");
  tabBtns.forEach((btn) => {
    btn.addEventListener("click", () => {
      tabBtns.forEach((b) => b.classList.remove("active"));
      document.querySelectorAll(".tab-content").forEach((c) => c.classList.remove("active"));
      btn.classList.add("active");
      const tabId = btn.getAttribute("data-tab");
      document.getElementById(tabId).classList.add("active");
    });
  });

  // Update badge and default symbol when exchange changes
  if (exchangeSelect && exchangeBadge) {
    const symbolInput = document.getElementById("symbol-input");
    const assetSelect = document.getElementById("asset-type-select");
    const sessionSelect = document.getElementById("session-mode-select");
    const anchorSelect = document.getElementById("vwap-anchor-select");

    exchangeSelect.addEventListener("change", () => {
      const ex = exchangeSelect.value;
      if (ex === "gemini") {
        exchangeBadge.textContent = "GEMINI REST API";
        if (assetSelect) assetSelect.value = "crypto";
        if (sessionSelect) sessionSelect.value = "all";
        if (anchorSelect) anchorSelect.value = "D";
        if (symbolInput && (symbolInput.value === "AAPL" || symbolInput.value === "SPY")) {
          symbolInput.value = "BTC/USD";
        }
      } else if (ex === "kucoin") {
        exchangeBadge.textContent = "KUCOIN REST API";
        if (assetSelect) assetSelect.value = "crypto";
        if (sessionSelect) sessionSelect.value = "all";
        if (anchorSelect) anchorSelect.value = "D";
        if (symbolInput && (symbolInput.value === "AAPL" || symbolInput.value === "SPY")) {
          symbolInput.value = "BTC/USDT";
        }
      } else if (ex === "alpaca") {
        exchangeBadge.textContent = "ALPACA MARKETS API";
        if (assetSelect) assetSelect.value = "stock";
        if (sessionSelect) sessionSelect.value = "rth";
        if (anchorSelect) anchorSelect.value = "US_EQUITY";
        if (symbolInput && (symbolInput.value.includes("/") || symbolInput.value.startsWith("BTC"))) {
          symbolInput.value = "AAPL";
        }
      } else if (ex === "polygon") {
        exchangeBadge.textContent = "POLYGON.IO API";
        if (assetSelect) assetSelect.value = "stock";
        if (sessionSelect) sessionSelect.value = "rth";
        if (anchorSelect) anchorSelect.value = "US_EQUITY";
        if (symbolInput && (symbolInput.value.includes("/") || symbolInput.value.startsWith("BTC"))) {
          symbolInput.value = "SPY";
        }
      } else if (ex === "synthetic_stock") {
        exchangeBadge.textContent = "SYNTHETIC US STOCK";
        if (assetSelect) assetSelect.value = "stock";
        if (sessionSelect) sessionSelect.value = "rth";
        if (anchorSelect) anchorSelect.value = "US_EQUITY";
        if (symbolInput && (symbolInput.value.includes("/") || symbolInput.value.startsWith("BTC"))) {
          symbolInput.value = "AAPL";
        }
      } else {
        exchangeBadge.textContent = "SYNTHETIC CRYPTO";
        if (assetSelect) assetSelect.value = "synthetic";
        if (sessionSelect) sessionSelect.value = "all";
        if (anchorSelect) anchorSelect.value = "D";
        if (symbolInput && (symbolInput.value === "AAPL" || symbolInput.value === "SPY")) {
          symbolInput.value = "BTC/USD";
        }
      }
    });
  }

  runBtn.addEventListener("click", runBacktest);
  if (runOptBtn) runOptBtn.addEventListener("click", runOptunaOptimization);
  if (runWfoBtn) runWfoBtn.addEventListener("click", runWalkForwardAnalysis);
  if (runMcBtn) runMcBtn.addEventListener("click", runMonteCarloSimulation);
  if (applyOptBtn) applyOptBtn.addEventListener("click", applyBestOptunaParams);
  if (resetMainBtn) resetMainBtn.addEventListener("click", resetMainDefaults);
  syncBtn.addEventListener("click", syncDatabase);
  if (clearBtn) clearBtn.addEventListener("click", clearDatabase);
  const sync1mBtn = document.getElementById("sync-1m-btn");
  if (sync1mBtn) sync1mBtn.addEventListener("click", sync1m);
  exportBtn.addEventListener("click", exportTradesCSV);

  setupSettingsModalEvents();

  // Initialize and periodically update real-time US Market Status badge
  updateNyseStatusBadge();
  setInterval(updateNyseStatusBadge, 30000);

  // Initialize Global Portfolio NAV pill
  fetch("/api/portfolio/snapshot?include_synthetic=true", { headers: getAuthHeaders() })
    .then((r) => r.json())
    .then((d) => {
      if (d && d.snapshot) updateGlobalNavPill(d.snapshot.total_nav_usd);
    })
    .catch(() => { });

  // Run initial backtest on page load
  runBacktest();
});

function updateNyseStatusBadge() {
  const badge = document.getElementById("nyse-status-badge");
  if (!badge) return;

  try {
    const now = new Date();
    const nyTimeStr = now.toLocaleString("en-US", { timeZone: "America/New_York" });
    const nyDate = new Date(nyTimeStr);

    const day = nyDate.getDay(); // 0 = Sun, 6 = Sat
    const hour = nyDate.getHours();
    const minute = nyDate.getMinutes();
    const timeMinutes = hour * 60 + minute;

    const rthOpen = 9 * 60 + 30; // 09:30
    const rthClose = 16 * 60; // 16:00
    const preOpen = 4 * 60; // 04:00
    const postClose = 20 * 60; // 20:00

    if (day === 0 || day === 6) {
      badge.textContent = "NYSE: WEEKEND CLOSED";
      badge.style.color = "var(--text-muted)";
      badge.style.borderColor = "rgba(255, 255, 255, 0.2)";
    } else if (timeMinutes >= rthOpen && timeMinutes < rthClose) {
      badge.textContent = "NYSE: RTH OPEN";
      badge.style.color = "#00f5a0";
      badge.style.borderColor = "rgba(0, 245, 160, 0.4)";
    } else if (timeMinutes >= preOpen && timeMinutes < rthOpen) {
      badge.textContent = "NYSE: PRE-MARKET";
      badge.style.color = "var(--accent-amber)";
      badge.style.borderColor = "rgba(255, 170, 0, 0.4)";
    } else if (timeMinutes >= rthClose && timeMinutes < postClose) {
      badge.textContent = "NYSE: AFTER-HOURS";
      badge.style.color = "#b87cf8";
      badge.style.borderColor = "rgba(184, 124, 248, 0.4)";
    } else {
      badge.textContent = "NYSE: CLOSED";
      badge.style.color = "var(--text-muted)";
      badge.style.borderColor = "rgba(255, 255, 255, 0.2)";
    }
  } catch (e) {
    badge.textContent = "NYSE: ACTIVE";
  }
}

function resetMainDefaults() {
  const setField = (id, val) => {
    const el = document.getElementById(id);
    if (el) el.value = val;
  };

  // Market & Data
  setField("exchange-select", "kucoin");
  setField("symbol-input", "BTC/USD");
  setField("timeframe-select", "5m");
  setField("data-range-select", "500");

  // Strategy Core
  setField("strategy-mode-select", "crossover");
  setField("trade-direction-select", "long_only");
  setField("fast-ema-input", 9);
  setField("slow-ema-input", 21);
  setField("trend-ema-input", 50);

  // Multi-Asset & Execution Rules
  setField("asset-type-select", "crypto");
  setField("session-mode-select", "all");
  setField("vwap-anchor-select", "D");
  setField("fractional-shares-select", "true");
  const marginCallsCheckbox = document.getElementById("margin-calls-checkbox");
  if (marginCallsCheckbox) marginCallsCheckbox.checked = false;
  setField("min-margin-input", 2000);

  // Filters & Gates
  setField("vwap-slope-input", 0.0001);
  setField("volume-filter-toggle", "true");
  setField("vol-mult-input", 1.2);

  // Risk & Stops
  setField("stop-loss-type-select", "vwap");
  setField("risk-per-trade-input", 1.0);
  setField("rr-ratio-input", 2.0);
  setField("capital-input", 10000);

  // Optuna & Robustness Controls
  setField("optuna-scope-select", "auto");
  setField("optuna-objective-mode-select", "single");
  setField("optuna-target-select", "sharpe_ratio");
  setField("optuna-multi-metric-select", "sharpe_ratio|max_drawdown_pct");
  setField("batch-objective-mode-select", "single");
  setField("batch-multi-metric-select", "sharpe_ratio|max_drawdown_pct");
  setField("optuna-trials-input", 100);
  setField("wfo-windows-input", 0);
  setField("mc-sims-input", 500);
  const objectiveModeEl = document.getElementById("optuna-objective-mode-select");
  if (objectiveModeEl) {
    objectiveModeEl.dispatchEvent(new Event("change"));
  }
  const batchObjectiveModeEl = document.getElementById("batch-objective-mode-select");
  if (batchObjectiveModeEl) {
    batchObjectiveModeEl.dispatchEvent(new Event("change"));
  }

  // Trigger exchange badge update
  const exchangeBadge = document.getElementById("exchange-badge");
  if (exchangeBadge) exchangeBadge.textContent = "KUCOIN REST API";

  // Flash highlight all reset fields
  const allInputIds = [
    "exchange-select",
    "symbol-input",
    "timeframe-select",
    "data-range-select",
    "strategy-mode-select",
    "trade-direction-select",
    "fast-ema-input",
    "slow-ema-input",
    "trend-ema-input",
    "asset-type-select",
    "session-mode-select",
    "vwap-anchor-select",
    "fractional-shares-select",
    "vwap-slope-input",
    "volume-filter-toggle",
    "vol-mult-input",
    "stop-loss-type-select",
    "risk-per-trade-input",
    "rr-ratio-input",
    "capital-input",
  ];

  allInputIds.forEach((id) => {
    const el = document.getElementById(id);
    if (el) {
      el.style.transition = "box-shadow 0.25s ease, border-color 0.25s ease";
      el.style.boxShadow = "0 0 12px rgba(0, 242, 254, 0.5)";
      el.style.borderColor = "#00f2fe";
      setTimeout(() => {
        el.style.boxShadow = "";
        el.style.borderColor = "";
      }, 1500);
    }
  });

  // Switch to Backtest tab and run backtest
  document.querySelector('[data-tab="tab-backtest"]').click();
  runBacktest();
}

async function clearDatabase() {
  if (!confirm("Are you sure you want to clear the database candle cache?")) return;
  const clearBtn = document.getElementById("clear-db-btn");
  clearBtn.disabled = true;

  try {
    const response = await fetch("/api/clear_cache", {
      method: "POST",
      headers: getAuthHeaders({ "Content-Type": "application/json" })
    });
    const data = await response.json();
    alert(data.message || "Database cache cleared.");
    runBacktest();
  } catch (error) {
    alert(`Clear Error: ${error.message}`);
  } finally {
    clearBtn.disabled = false;
  }
}

async function syncDatabase() {
  const syncBtn = document.getElementById("sync-db-btn");
  syncBtn.disabled = true;
  syncBtn.innerHTML = `<span class="btn-icon">⏳</span> Fetching adaptive lookback...`;

  const { days, limit, timeframe, barsPerDay } = getDataRangeParams();
  const warmWarnings = checkWarmupAndFetchLimits(limit, timeframe, barsPerDay);
  if (warmWarnings.length) {
    warmWarnings.forEach(w => showToast(w, "warn"));
  }

  const payload = {
    exchange: document.getElementById("exchange-select").value,
    symbol: document.getElementById("symbol-input").value.trim(),
    timeframe: document.getElementById("timeframe-select").value,
    days: days,
    limit: limit,
  };

  try {
    const response = await fetch("/api/sync_history", {
      method: "POST",
      headers: getAuthHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify(payload),
    });

    const data = await response.json();
    if (response.ok) {
      showToast(`Success: ${data.message}`, "success");
      runBacktest();
    } else {
      showToast(`Sync Error: ${data.detail || "Failed to sync historical candles."}`, "error");
    }
  } catch (error) {
    showToast(`Sync Error: ${error.message}`, "error");
  } finally {
    syncBtn.disabled = false;
    syncBtn.innerHTML = `<span class="btn-icon">📥</span> Sync DB`;
  }
}

async function sync1m() {
  const syncBtn = document.getElementById("sync-1m-btn");
  syncBtn.disabled = true;
  syncBtn.innerHTML = `<span class="btn-icon">⏳</span> Fetching 1m...`;

  const exchange = document.getElementById("exchange-select").value;
  const symbol = document.getElementById("symbol-input").value.trim();
  const timeframe = document.getElementById("timeframe-select").value;

  // For 1m sync, use a fixed 30-day window (covers ~43,200 candles)
  const days = 30;
  const limit = 50000;

  try {
    const response = await fetch("/api/sync_history", {
      method: "POST",
      headers: getAuthHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify({ exchange, symbol, timeframe: "1m", days, limit }),
    });

    const data = await response.json();
    if (response.ok) {
      showToast(`Success: ${data.message}`, "success");
    } else {
      showToast(`Sync Error: ${data.detail || "Failed to sync 1m candles."}`, "error");
    }
  } catch (error) {
    showToast(`Sync Error: ${error.message}`, "error");
  } finally {
    syncBtn.disabled = false;
    syncBtn.innerHTML = `<span class="btn-icon">⚡</span> Sync 1m`;
  }
}

async function runBacktest() {
  const runBtn = document.getElementById("run-backtest-btn");
  if (!runBtn) return;
  runBtn.disabled = true;
  runBtn.innerHTML = `<span class="btn-icon">⏳</span> Running Simulation...`;

  try {
    const settings = getSettings();
    const { days, limit, timeframe, barsPerDay } = getDataRangeParams();
    const warmWarnings = checkWarmupAndFetchLimits(limit, timeframe, barsPerDay);
    if (warmWarnings.length) {
      warmWarnings.forEach((w) => showToast(w, "warn"));
    }

    const payload = {
      exchange: document.getElementById("exchange-select").value,
      symbol: document.getElementById("symbol-input").value.trim(),
      timeframe: document.getElementById("timeframe-select").value,
      limit: limit,
      days: days,
      ...(lastAppliedParams || {}),
      ...(lastBatchParams || {}),
      strategy_mode: document.getElementById("strategy-mode-select").value,
      trade_direction: document.getElementById("trade-direction-select").value,
      fast_ema: parseInt(document.getElementById("fast-ema-input").value, 10),
      slow_ema: parseInt(document.getElementById("slow-ema-input").value, 10),
      trend_ema: parseInt(document.getElementById("trend-ema-input").value, 10),
      asset_type: document.getElementById("asset-type-select")?.value || "crypto",
      session_mode: document.getElementById("session-mode-select")?.value || "all",
      vwap_anchor: document.getElementById("vwap-anchor-select")?.value || "D",
      allow_fractional_shares: document.getElementById("fractional-shares-select")?.value === "true",
      enforce_margin_calls: document.getElementById("margin-calls-checkbox")?.checked || false,
      min_margin_equity: parseFloat(document.getElementById("min-margin-input")?.value || 2000),
      long_buying_power_ratio: 4.0,
      short_buying_power_ratio: 2.0,
      vwap_slope_min: parseFloat(document.getElementById("vwap-slope-input").value),
      volume_filter_enabled: document.getElementById("volume-filter-toggle").value === "true",
      volume_multiplier: parseFloat(document.getElementById("vol-mult-input").value),
      stop_loss_type: document.getElementById("stop-loss-type-select").value,
      trailing_stop_pct: parseFloat(document.getElementById("tsl-distance-input").value) || 1.5,
      risk_per_trade_pct: parseFloat(document.getElementById("risk-per-trade-input").value),
      risk_reward_ratio: parseFloat(document.getElementById("rr-ratio-input").value),
      initial_capital: parseFloat(document.getElementById("capital-input").value),
      maker_fee_pct: settings.maker_fee_pct,
      taker_fee_pct: settings.taker_fee_pct,
      slippage_pct: settings.slippage_pct,
    };

    runBtn.innerHTML = `<span class="btn-icon">⏳</span> Starting backtest...`;
    const response = await fetch("/api/backtest", {
      method: "POST",
      headers: getAuthHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify(payload),
    });

    if (!response.ok) {
      const err = await response.json();
      alert(`Backtest Error: ${err.detail || "Server error"}`);
      return;
    }

    const data = await response.json();

    // Background-mode: backend returns task_id for real exchanges; poll until done.
    if (data.status === "started" && data.task_id) {
      const taskId = data.task_id;
      let attempts = 0;
      const maxAttempts = 900; // ~15 min at 1s intervals
      let lastStep = "";
      while (attempts < maxAttempts) {
        await new Promise((r) => setTimeout(r, 1000));
        attempts += 1;
        const pollResp = await fetch(`/api/tasks/${taskId}`);
        if (!pollResp.ok) {
          const pollError = await pollResp.json();
          throw new Error(
            pollError.detail || `Unable to retrieve backtest task (${pollResp.status}).`
          );
        }
        const pollData = await pollResp.json();
        const task = pollData.task || {};
        if (task.status === "completed" || task.status === "success") {
          Object.assign(data, task.result || task.result_json || {});
          break;
        }
        if (task.status === "failed" || task.status === "error") {
          alert(
            `Backtest task failed: ${task.error || task.step_description || "Unknown error"}`
          );
          return;
        }
        if (task.status === "cancelled") {
          alert("Backtest task was cancelled.");
          return;
        }
        const step = task.step_description || "";
        if (step && step !== lastStep) {
          lastStep = step;
          runBtn.innerHTML = `<span class="btn-icon">⏳</span> ${step}…`;
        }
      }
      if (attempts >= maxAttempts) {
        runBtn.innerHTML = `<span class="btn-icon">⏳</span> Running Simulation...`;
        alert("Backtest is taking longer than 15 minutes. It may still be running — check the Tasks panel before starting another run.");
        return;
      }
    }

    if (!data.metrics) {
      alert("Backtest returned no results.");
      return;
    }

    currentTrades = data.metrics.trades || [];
    window._lastBacktestMetrics = data.metrics;
    updateKPIs(data.metrics);
    renderPriceChart(data.chart_data, payload.symbol, payload.fast_ema, payload.slow_ema, payload.trend_ema);
    renderEquityChart(data.metrics.equity_curve);
    renderDrawdownChart(data.metrics.equity_curve);
    renderMonthlyTable(data.metrics.monthly_breakdown);
    syncQuickTweakBar();
    tradesCurrentPage = 1;
    renderTradeTable(currentTrades);
  } catch (error) {
    console.error("Backtest request failed:", error);
    alert(`Backtest request error: ${error.message}`);
  } finally {
    runBtn.disabled = false;
    runBtn.innerHTML = `<span class="btn-icon">▶</span> Run Strategy Backtest`;
  }
}

function updateKPIs(metrics) {
  if (!metrics) return;

  // SQLite cache hit/coverage badge
  const kpiCache = document.getElementById("kpi-cache");
  const kpiCacheSource = document.getElementById("kpi-cache-source");
  if (kpiCache && kpiCacheSource && metrics.cache_info) {
    const ci = metrics.cache_info;
    const pct = ci.coverage_pct || 0;
    const requested = ci.requested_bars || 0;
    const cached = ci.cached_bars || 0;
    kpiCache.textContent = `${pct.toFixed(1)}%`;
    kpiCacheSource.textContent = `${cached.toLocaleString()} / ${requested.toLocaleString()} bars cached`;
    kpiCache.style.color = pct >= 70 ? "#00f5a0" : pct >= 30 ? "#ffaa00" : "#ff007a";
  }

  const kpiReturn = document.getElementById("kpi-return");
  const kpiProfit = document.getElementById("kpi-profit");
  const kpiWinrate = document.getElementById("kpi-winrate");
  const kpiTradesCount = document.getElementById("kpi-trades-count");
  const kpiPf = document.getElementById("kpi-pf");
  const kpiPayoff = document.getElementById("kpi-payoff");
  const kpiDrawdown = document.getElementById("kpi-drawdown");
  const kpiExpectancy = document.getElementById("kpi-expectancy");
  const kpiSharpe = document.getElementById("kpi-sharpe");

  const fmt = (val, dec = 2) => (typeof val === "number" && !isNaN(val) ? val.toFixed(dec) : (0).toFixed(dec));

  const totalReturn = metrics.total_return_pct || 0;
  const netProfit = metrics.net_profit || 0;
  const expectancy = metrics.expectancy || 0;

  kpiReturn.textContent = `${totalReturn >= 0 ? "+" : ""}${fmt(totalReturn)}%`;
  kpiReturn.style.color = totalReturn >= 0 ? "#00f5a0" : "#ff007a";

  kpiProfit.textContent = `$${netProfit >= 0 ? "+" : ""}${netProfit.toLocaleString()} Net`;
  kpiWinrate.textContent = `${fmt(metrics.win_rate || 0)}%`;
  kpiTradesCount.textContent = `${metrics.winning_trades || 0}W / ${metrics.losing_trades || 0}L (${metrics.total_trades || 0} total)`;

  kpiPf.textContent = fmt(metrics.profit_factor);
  kpiPayoff.textContent = `Payoff: ${fmt(metrics.payoff_ratio)}`;
  kpiDrawdown.textContent = `${fmt(metrics.max_drawdown_pct)}%`;

  kpiExpectancy.textContent = `$${expectancy >= 0 ? "+" : ""}${fmt(expectancy)}`;
  kpiExpectancy.style.color = expectancy >= 0 ? "#00f5a0" : "#ff007a";
  kpiSharpe.textContent = `Sharpe: ${fmt(metrics.sharpe_ratio)}`;
}

let lastCandleTimestamps = [];

function renderPriceChart(chartData, symbol, fastEma, slowEma, trendEma) {
  const timestamps = chartData.map((d) => d.timestamp);
  lastCandleTimestamps = timestamps;
  const opens = chartData.map((d) => d.open);
  const highs = chartData.map((d) => d.high);
  const lows = chartData.map((d) => d.low);
  const closes = chartData.map((d) => d.close);
  const volumes = chartData.map((d) => d.volume);

  const vwaps = chartData.map((d) => d.vwap);
  const emaFasts = chartData.map((d) => d.ema_fast);
  const emaSlows = chartData.map((d) => d.ema_slow);
  const emaTrends = chartData.map((d) => d.ema_trend);

  const buyX = [], buyY = [];
  const sellX = [], sellY = [];

  chartData.forEach((d) => {
    if (d.signal === 1) {
      buyX.push(d.timestamp);
      buyY.push(d.low * 0.998);
    } else if (d.signal === -1) {
      sellX.push(d.timestamp);
      sellY.push(d.high * 1.002);
    }
  });

  const candlestickTrace = {
    type: "candlestick",
    x: timestamps,
    open: opens,
    high: highs,
    low: lows,
    close: closes,
    name: symbol,
    increasing: { line: { color: "#00f5a0" } },
    decreasing: { line: { color: "#ff007a" } },
  };

  const vwapTrace = {
    type: "scatter",
    mode: "lines",
    x: timestamps,
    y: vwaps,
    name: "VWAP",
    line: { color: "#00f2fe", width: 2 },
  };

  const emaFastTrace = {
    type: "scatter",
    mode: "lines",
    x: timestamps,
    y: emaFasts,
    name: `EMA ${fastEma}`,
    line: { color: "#ffaa00", width: 1.5 },
  };

  const emaSlowTrace = {
    type: "scatter",
    mode: "lines",
    x: timestamps,
    y: emaSlows,
    name: `EMA ${slowEma}`,
    line: { color: "#7928ca", width: 1.5 },
  };

  const emaTrendTrace = {
    type: "scatter",
    mode: "lines",
    x: timestamps,
    y: emaTrends,
    name: `EMA ${trendEma}`,
    line: { color: "#ff007a", width: 1.5, dash: "dot" },
  };

  const buyMarkers = {
    type: "scatter",
    mode: "markers",
    x: buyX,
    y: buyY,
    name: "BUY Signal",
    marker: { symbol: "triangle-up", size: 12, color: "#00f5a0" },
  };

  const sellMarkers = {
    type: "scatter",
    mode: "markers",
    x: sellX,
    y: sellY,
    name: "SELL Signal",
    marker: { symbol: "triangle-down", size: 12, color: "#ff007a" },
  };

  const volumeTrace = {
    type: "bar",
    x: timestamps,
    y: volumes,
    name: "Volume",
    yaxis: "y2",
    marker: { color: "rgba(255, 255, 255, 0.15)" },
  };

  const layout = {
    paper_bgcolor: "transparent",
    plot_bgcolor: "transparent",
    font: { color: "#8a99ad", family: "Inter, sans-serif" },
    margin: { l: 40, r: 40, t: 20, b: 40 },
    showlegend: false,
    xaxis: {
      type: "date",
      gridcolor: "rgba(255, 255, 255, 0.05)",
      rangeslider: { visible: false },
    },
    yaxis: {
      gridcolor: "rgba(255, 255, 255, 0.05)",
      domain: [0.25, 1],
    },
    yaxis2: {
      gridcolor: "rgba(255, 255, 255, 0.05)",
      domain: [0, 0.2],
      showticklabels: false,
    },
  };

  Plotly.newPlot(
    "price-chart",
    [candlestickTrace, vwapTrace, emaFastTrace, emaSlowTrace, emaTrendTrace, buyMarkers, sellMarkers, volumeTrace],
    layout,
    { responsive: true, displayModeBar: false }
  );
}

function applyPriceChartZoom(range) {
  if (!lastCandleTimestamps || lastCandleTimestamps.length === 0) return;
  const n = lastCandleTimestamps.length;
  const lastTs = new Date(lastCandleTimestamps[n - 1]).getTime();

  let startTs = new Date(lastCandleTimestamps[0]).getTime();

  if (range === "1d") {
    startTs = lastTs - 1 * 24 * 3600 * 1000;
  } else if (range === "5d") {
    startTs = lastTs - 5 * 24 * 3600 * 1000;
  } else if (range === "1m") {
    startTs = lastTs - 30 * 24 * 3600 * 1000;
  } else if (range === "3m") {
    startTs = lastTs - 90 * 24 * 3600 * 1000;
  }

  const startStr = new Date(Math.max(new Date(lastCandleTimestamps[0]).getTime(), startTs)).toISOString();
  const endStr = new Date(lastTs).toISOString();

  Plotly.relayout("price-chart", {
    "xaxis.range": range === "all" || range === "reset" ? null : [startStr, endStr],
    "xaxis.autorange": range === "all" || range === "reset",
  });
}

function renderEquityChart(equityCurve) {
  if (!equityCurve || equityCurve.length === 0) return;

  const timestamps = equityCurve.map((d) => d.timestamp);
  const equities = equityCurve.map((d) => d.equity);

  let minEq = Infinity;
  let maxEq = -Infinity;
  for (let i = 0; i < equities.length; i++) {
    const val = equities[i];
    if (val < minEq) minEq = val;
    if (val > maxEq) maxEq = val;
  }
  if (!isFinite(minEq)) minEq = 10000;
  if (!isFinite(maxEq)) maxEq = 10000;

  const swing = maxEq - minEq || maxEq * 0.05;
  const padding = swing * 0.12;
  const yMin = minEq - padding;
  const yMax = maxEq + padding;

  const baselineTrace = {
    type: "scatter",
    mode: "lines",
    x: timestamps,
    y: timestamps.map(() => yMin),
    showlegend: false,
    hoverinfo: "skip",
    line: { width: 0, color: "transparent" },
  };

  const equityTrace = {
    type: "scatter",
    mode: "lines",
    x: timestamps,
    y: equities,
    name: "Equity ($)",
    line: { color: "#00f2fe", width: 2 },
    fill: "tonexty",
    fillcolor: "rgba(0, 242, 254, 0.07)",
  };

  const layout = {
    paper_bgcolor: "transparent",
    plot_bgcolor: "transparent",
    font: { color: "#8a99ad", family: "Inter, sans-serif" },
    margin: { l: 40, r: 40, t: 10, b: 30 },
    showlegend: false,
    xaxis: { type: "date", gridcolor: "rgba(255, 255, 255, 0.05)" },
    yaxis: {
      gridcolor: "rgba(255, 255, 255, 0.05)",
      range: [yMin, yMax],
    },
  };

  Plotly.newPlot("equity-chart", [baselineTrace, equityTrace], layout, { responsive: true, displayModeBar: false });
}

function renderDrawdownChart(equityCurve) {
  const chartDiv = document.getElementById("drawdown-chart");
  if (!chartDiv || !equityCurve || equityCurve.length === 0 || typeof Plotly === "undefined") return;

  const timestamps = equityCurve.map((d) => d.timestamp);
  const equities = equityCurve.map((d) => d.equity);

  let peak = -Infinity;
  const drawdowns = [];
  for (let i = 0; i < equities.length; i++) {
    const eq = equities[i];
    if (eq > peak) peak = eq;
    const dd = peak > 0 ? ((eq - peak) / peak) * 100.0 : 0.0;
    drawdowns.push(roundDec(dd, 2));
  }

  let minDd = 0;
  for (let i = 0; i < drawdowns.length; i++) {
    if (drawdowns[i] < minDd) minDd = drawdowns[i];
  }
  const yMin = Math.min(-5.0, minDd * 1.15);

  const baselineTrace = {
    type: "scatter",
    mode: "lines",
    x: timestamps,
    y: timestamps.map(() => 0),
    showlegend: false,
    hoverinfo: "skip",
    line: { width: 1, color: "rgba(255, 255, 255, 0.2)", dash: "dash" },
  };

  const ddTrace = {
    type: "scatter",
    mode: "lines",
    x: timestamps,
    y: drawdowns,
    name: "Drawdown (%)",
    line: { color: "#ff007a", width: 1.5 },
    fill: "tozeroy",
    fillcolor: "rgba(255, 0, 122, 0.15)",
    hoverinfo: "x+y",
  };

  const layout = {
    paper_bgcolor: "transparent",
    plot_bgcolor: "transparent",
    font: { color: "#8a99ad", family: "Inter, sans-serif" },
    margin: { l: 40, r: 40, t: 10, b: 30 },
    showlegend: false,
    xaxis: { type: "date", gridcolor: "rgba(255, 255, 255, 0.05)" },
    yaxis: {
      gridcolor: "rgba(255, 255, 255, 0.05)",
      range: [yMin, 0.5],
      ticksuffix: "%",
    },
  };

  Plotly.newPlot(chartDiv, [baselineTrace, ddTrace], layout, { responsive: true, displayModeBar: false });
}

function roundDec(val, dec = 2) {
  return Number(Math.round(Number(val + "e" + dec)) + "e-" + dec);
}

function renderMonthlyTable(monthlyBreakdown) {
  const tbody = document.getElementById("monthly-table-body");
  if (!monthlyBreakdown || monthlyBreakdown.length === 0) {
    tbody.innerHTML = `<tr><td colspan="3" class="empty-msg">No monthly breakdown data.</td></tr>`;
    return;
  }

  tbody.innerHTML = monthlyBreakdown
    .map((m) => {
      const color = m.net_pnl >= 0 ? "#00f5a0" : "#ff007a";
      return `
      <tr>
        <td style="font-weight: 600;">${m.month}</td>
        <td style="color: ${color}; font-weight: 600;">$${m.net_pnl >= 0 ? "+" : ""}${m.net_pnl.toLocaleString()}</td>
        <td style="color: ${color}; font-weight: 600;">${m.return_pct >= 0 ? "+" : ""}${m.return_pct}%</td>
      </tr>
    `;
    })
    .join("");
}

function formatPrice(val) {
  if (typeof val !== "number" || isNaN(val)) return "-";
  if (val === 0) return "$0";
  const absVal = Math.abs(val);

  if (absVal >= 1000) {
    return "$" + val.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  } else if (absVal >= 1.0) {
    return "$" + val.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 4 });
  } else if (absVal >= 0.001) {
    return "$" + val.toLocaleString(undefined, { minimumFractionDigits: 4, maximumFractionDigits: 6 });
  } else {
    const str = val.toFixed(10);
    const trimmed = str.replace(/(\.\d*?[1-9])0+$|\.0+$/, "$1");
    return "$" + trimmed;
  }
}

let tradeFilterMode = "all";
let tradeSearchQuery = "";
let tradesCurrentPage = 1;
let tradesPerPage = 50;

function renderTradeTable(trades) {
  const tbody = document.getElementById("trade-table-body");
  const countBadge = document.getElementById("trade-log-count");
  const pageInfo = document.getElementById("trade-pagination-info");
  const pageNumEl = document.getElementById("trade-page-num");
  const prevBtn = document.getElementById("trade-prev-page-btn");
  const nextBtn = document.getElementById("trade-next-page-btn");

  if (!trades) trades = [];

  // Filter trades
  let filtered = trades.filter((t) => {
    if (tradeFilterMode === "wins" && t.pnl <= 0) return false;
    if (tradeFilterMode === "losses" && t.pnl >= 0) return false;
    if (tradeFilterMode === "gaps" && !((t.exit_reason || "").startsWith("gap_"))) return false;
    if (tradeFilterMode === "longs" && t.side !== 1) return false;
    if (tradeFilterMode === "shorts" && t.side !== -1) return false;

    if (tradeSearchQuery) {
      const q = tradeSearchQuery.toLowerCase();
      const matchId = String(t.trade_id).includes(q);
      const matchReason = (t.exit_reason || "").toLowerCase().includes(q);
      const matchEntry = (t.entry_time || "").toLowerCase().includes(q);
      const matchExit = (t.exit_time || "").toLowerCase().includes(q);
      if (!matchId && !matchReason && !matchEntry && !matchExit) return false;
    }
    return true;
  });

  countBadge.textContent = `${filtered.length} of ${trades.length} trades`;

  if (filtered.length === 0) {
    tbody.innerHTML = `<tr><td colspan="12" class="empty-msg">No trades match the selected filter.</td></tr>`;
    if (pageInfo) pageInfo.textContent = "Showing 0 of 0 trades";
    if (prevBtn) prevBtn.disabled = true;
    if (nextBtn) nextBtn.disabled = true;
    return;
  }

  // Pagination
  const totalPages = tradesPerPage === "all" ? 1 : Math.ceil(filtered.length / parseInt(tradesPerPage, 10));
  if (tradesCurrentPage > totalPages) tradesCurrentPage = totalPages;
  if (tradesCurrentPage < 1) tradesCurrentPage = 1;

  const startIdx = tradesPerPage === "all" ? 0 : (tradesCurrentPage - 1) * parseInt(tradesPerPage, 10);
  const endIdx = tradesPerPage === "all" ? filtered.length : Math.min(startIdx + parseInt(tradesPerPage, 10), filtered.length);
  const pageItems = filtered.slice(startIdx, endIdx);

  if (pageInfo) pageInfo.textContent = `Showing ${startIdx + 1}-${endIdx} of ${filtered.length} trades`;
  if (pageNumEl) pageNumEl.textContent = `${tradesCurrentPage} / ${totalPages}`;
  if (prevBtn) prevBtn.disabled = tradesCurrentPage <= 1;
  if (nextBtn) nextBtn.disabled = tradesCurrentPage >= totalPages;

  tbody.innerHTML = pageItems
    .map((t) => {
      const isLong = t.side === 1;
      const pnlColor = t.pnl >= 0 ? "#00f5a0" : "#ff007a";
      const sideBadge = isLong
        ? `<span class="badge badge-long">BUY</span>`
        : `<span class="badge badge-short">SELL</span>`;

      let exitBadge = `<span class="badge">${t.exit_reason || "-"}</span>`;
      if (t.exit_reason === "gap_stop_loss") {
        exitBadge = `<span class="badge" style="background:rgba(255, 170, 0, 0.2); color:var(--accent-amber); border:1px solid rgba(255,170,0,0.4);" title="Filled at opening gap down">⚡ Gap Stop</span>`;
      } else if (t.exit_reason === "gap_take_profit") {
        exitBadge = `<span class="badge" style="background:rgba(0, 242, 254, 0.2); color:var(--accent-cyan); border:1px solid rgba(0,242,254,0.4);" title="Filled at opening gap up">🚀 Gap Profit</span>`;
      } else if (t.exit_reason === "stop_loss") {
        exitBadge = `<span class="badge" style="background:rgba(255, 0, 122, 0.2); color:#ff007a; border:1px solid rgba(255,0,122,0.4);">🛑 Stop Loss</span>`;
      } else if (t.exit_reason === "take_profit") {
        exitBadge = `<span class="badge" style="background:rgba(0, 245, 160, 0.2); color:#00f5a0; border:1px solid rgba(0,245,160,0.4);">🎯 Take Profit</span>`;
      } else if (t.exit_reason === "signal_reversal") {
        exitBadge = `<span class="badge" style="background:rgba(121, 40, 202, 0.2); color:#b87cf8; border:1px solid rgba(121,40,202,0.4);">🔄 Reversal</span>`;
      }

      return `
      <tr>
        <td>#${t.trade_id}</td>
        <td>${sideBadge}</td>
        <td>${t.entry_time}</td>
        <td>${formatPrice(t.entry_price)}</td>
        <td>${t.units.toLocaleString()}</td>
        <td>${formatPrice(t.stop_loss)}</td>
        <td>${formatPrice(t.take_profit)}</td>
        <td>${t.exit_time || "OPEN"}</td>
        <td>${t.exit_price ? formatPrice(t.exit_price) : "-"}</td>
        <td>${exitBadge}</td>
        <td style="color: ${pnlColor}; font-weight: 600;">$${t.pnl >= 0 ? "+" : ""}${t.pnl.toLocaleString()}</td>
        <td style="color: ${pnlColor}; font-weight: 600;">${t.pnl_pct >= 0 ? "+" : ""}${t.pnl_pct}%</td>
      </tr>
    `;
    })
    .join("");
}

function syncQuickTweakBar() {
  const setVal = (id, targetId) => {
    const src = document.getElementById(targetId);
    const dest = document.getElementById(id);
    if (src && dest) dest.value = src.value;
  };
  setVal("tweak-fast-val", "fast-ema-input");
  setVal("tweak-slow-val", "slow-ema-input");
  setVal("tweak-trend-val", "trend-ema-input");
  setVal("tweak-rr-val", "rr-ratio-input");
}

function exportTradesCSV() {
  if (!currentTrades || currentTrades.length === 0) {
    alert("No trades to export.");
    return;
  }

  const headers = ["TradeID", "Side", "EntryTime", "EntryPrice", "Units", "StopLoss", "TakeProfit", "ExitTime", "ExitPrice", "ExitReason", "PnL_USD", "PnL_PCT", "Fee_USD"];
  const rows = currentTrades.map(t => [
    t.trade_id,
    t.side === 1 ? "BUY" : "SELL",
    `"${t.entry_time}"`,
    t.entry_price,
    t.units,
    t.stop_loss,
    t.take_profit,
    `"${t.exit_time || ''}"`,
    t.exit_price || '',
    `"${t.exit_reason || ''}"`,
    t.pnl,
    t.pnl_pct,
    t.fee
  ]);

  const csvContent = "data:text/csv;charset=utf-8," + [headers.join(","), ...rows.map(e => e.join(","))].join("\n");
  const encodedUri = encodeURI(csvContent);
  const link = document.createElement("a");
  link.setAttribute("href", encodedUri);
  link.setAttribute("download", `ema_vwap_backtest_trades.csv`);
  document.body.appendChild(link);
  link.click();
  link.remove();
}

function getBacktestDaysForTimeframe() {
  // Resolve "auto" to actual optimal lookback days per timeframe
  // Matches src/config.py get_optimal_lookback_days() defaults
  const tf = document.getElementById("timeframe-select")?.value || "5m";
  const daysMap = {
    "1m": 30,
    "3m": 60,
    "5m": 90,
    "15m": 180,
    "30m": 250,
    "1h": 900,
    "2h": 540,
    "4h": 350,
    "6h": 540,
    "8h": 540,
    "12h": 350,
    "1d": 1800,
  };
  return daysMap[tf.toLowerCase()] || 90;
}

function getBatchDataRangeDays() {
  const rangeVal = document.getElementById("batch-data-range-select")?.value || "365_days";

  // "auto" → let the backend decide (send days: null)
  if (rangeVal === "auto") {
    return null;
  }

  if (rangeVal === "30_days") return 30;
  if (rangeVal === "90_days") return 90;
  if (rangeVal === "180_days") return 180;
  if (rangeVal === "365_days") return 365;

  // Numeric candle counts (500/1500) have no clean per-TF day mapping in a
  // multi-timeframe sweep; fall back to backend default (days: null → limit 500).
  return null;
}

function getDataRangeParams() {
  const rangeVal = document.getElementById("data-range-select")?.value || "500";
  const timeframe = document.getElementById("timeframe-select")?.value || "5m";
  const barsPerDay =
    {
      "1m": 1440,
      "3m": 480,
      "5m": 288,
      "15m": 96,
      "30m": 48,
      "1h": 24,
      "2h": 12,
      "4h": 6,
      "6h": 4,
      "1d": 1,
    }[timeframe] || 288;

  // "auto" -> resolve to actual optimal lookback days (not null/500 limit)
  // This ensures sync pulls full backtest dataset (e.g., 3m=60d, 5m=90d)
  if (rangeVal === "auto") {
    const optDays = getBacktestDaysForTimeframe();
    return { limit: Math.round(optDays * barsPerDay), days: optDays, timeframe, barsPerDay };
  }

  let days = null;
  let limit = 500;

  if (rangeVal === "30_days") {
    days = 30;
    limit = Math.min(Math.round(30 * barsPerDay), 50000);
  } else if (rangeVal === "90_days") {
    days = 90;
    limit = Math.min(Math.round(90 * barsPerDay), 50000);
  } else if (rangeVal === "180_days") {
    days = 180;
    limit = Math.min(Math.round(180 * barsPerDay), 50000);
  } else if (rangeVal === "365_days") {
    days = 365;
    limit = Math.min(Math.round(365 * barsPerDay), 50000);
  } else {
    // Numeric value: treat as number of candles
    limit = Math.max(parseInt(rangeVal, 10) || 500, 50);
  }

  return { limit, days, timeframe, barsPerDay };
}

function checkWarmupAndFetchLimits(limit, timeframe, barsPerDay) {
  const warnings = [];
  const totalBars = limit || (barsPerDay ? Math.round(200 * barsPerDay) : 0);
  if (totalBars < 250) {
    warnings.push(`<strong>Warmup Alert:</strong> Only ${totalBars.toLocaleString()} bars selected (need ≥ 250 for reliable indicator warmup).`);
  }
  if (totalBars > 50000) {
    warnings.push(`<strong>Over-fetch Warning:</strong> ${totalBars.toLocaleString()} bars requested (> 50,000) — high-frequency load; consider reducing range.`);
  }
  return warnings;
}

function getStrategyParamsFromUI() {
  const s = getSettings();
  const maxHoldingBarsEl = document.getElementById("max-holding-bars-input");
  const maxHoldingBarsVal =
    maxHoldingBarsEl && maxHoldingBarsEl.value.trim() !== ""
      ? parseInt(maxHoldingBarsEl.value, 10)
      : null;

  const uiParams = {
    strategy_mode: document.getElementById("strategy-mode-select")?.value || "crossover",
    trade_direction: document.getElementById("trade-direction-select")?.value || "long_only",
    fast_ema: parseInt(document.getElementById("fast-ema-input")?.value || "9", 10),
    slow_ema: parseInt(document.getElementById("slow-ema-input")?.value || "21", 10),
    trend_ema: parseInt(document.getElementById("trend-ema-input")?.value || "50", 10),
    vwap_slope_min: parseFloat(document.getElementById("vwap-slope-input")?.value || "0.0001"),
    vwap_slope_lookback: parseInt(document.getElementById("vwap-lookback-input")?.value || "5", 10),
    volume_filter_enabled: document.getElementById("volume-filter-toggle")?.value === "true",
    volume_multiplier: parseFloat(document.getElementById("vol-mult-input")?.value || "1.2"),
    volume_sma_period: 20,
    pullback_tolerance_pct: parseFloat(document.getElementById("pullback-tol-input")?.value || "0.3"),
    stop_loss_type: document.getElementById("stop-loss-type-select")?.value || "vwap",
    vwap_stop_offset_pct: parseFloat(document.getElementById("vwap-stop-input")?.value || "0.1"),
    atr_period: 14,
    atr_multiplier: parseFloat(document.getElementById("atr-mult-input")?.value || "2.0"),
    trailing_stop_pct: parseFloat(document.getElementById("tsl-distance-input")?.value) || 1.5,
    risk_per_trade_pct: parseFloat(document.getElementById("risk-per-trade-input")?.value || "1.0"),
    risk_reward_ratio: parseFloat(document.getElementById("rr-ratio-input")?.value || "2.0"),
    max_holding_bars: maxHoldingBarsVal,
    initial_capital: parseFloat(document.getElementById("capital-input")?.value || "10000"),
    maker_fee_pct: s.maker_fee_pct,
    taker_fee_pct: s.taker_fee_pct,
    slippage_pct: s.slippage_pct,
    asset_type: document.getElementById("asset-type-select")?.value || "crypto",
    session_mode: document.getElementById("session-mode-select")?.value || "all",
    vwap_anchor: document.getElementById("vwap-anchor-select")?.value || "D",
    allow_fractional_shares: document.getElementById("fractional-shares-select")?.value === "true",
    enforce_margin_calls: document.getElementById("margin-calls-checkbox")?.checked || false,
    min_margin_equity: parseFloat(document.getElementById("min-margin-input")?.value || "2000"),
    long_buying_power_ratio: 4.0,
    short_buying_power_ratio: 2.0,
  };

  return Object.assign({}, lastAppliedParams || {}, uiParams);
}

function syncOptunaObjectiveModeUI() {
  const modeEl = document.getElementById("optuna-objective-mode-select");
  const targetGroup = document.getElementById("optuna-target-group");
  const multiGroup = document.getElementById("optuna-multi-metric-group");
  if (!modeEl) return;

  const isMulti = modeEl.value === "multi";
  if (targetGroup) targetGroup.style.display = isMulti ? "none" : "block";
  if (multiGroup) multiGroup.style.display = isMulti ? "block" : "none";
}

function syncBatchObjectiveModeUI() {
  const modeEl = document.getElementById("batch-objective-mode-select");
  const targetGroup = document.getElementById("batch-target-group");
  const multiGroup = document.getElementById("batch-multi-metric-group");
  if (!modeEl) return;

  const isMulti = modeEl.value === "multi";
  if (targetGroup) targetGroup.style.display = isMulti ? "none" : "block";
  if (multiGroup) multiGroup.style.display = isMulti ? "block" : "none";
  syncBatchMetricUI();
}

function syncBatchMetricUI() {
  const objectiveModeEl = document.getElementById("batch-objective-mode-select");
  const isSingle = !objectiveModeEl || objectiveModeEl.value === "single";
  const metricVal = document.getElementById("batch-metric-select")?.value;
  const multiVal = document.getElementById("batch-multi-metric-select")?.value;
  const tfChecked = [...document.querySelectorAll(".batch-tf-check:checked")].length;
  const singleHint = document.getElementById("batch-all-metrics-hint");
  const multiHint = document.getElementById("batch-all-multi-hint");
  const runBtn = document.getElementById("batch-run-btn");

  if (isSingle) {
    if (multiHint) multiHint.style.display = "none";
    if (metricVal === "all_metrics") {
      if (singleHint) singleHint.style.display = "block";
      if (tfChecked !== 1) {
        if (singleHint) {
          singleHint.textContent = "⛔ All Metrics requires exactly ONE timeframe. Please select only 1 timeframe.";
          singleHint.style.color = "var(--accent-red, #ff007a)";
        }
        if (runBtn) runBtn.disabled = true;
      } else {
        if (singleHint) {
          singleHint.textContent = "★ All Metrics: will run 10 sweeps (one per ranking metric) for the selected timeframe.";
          singleHint.style.color = "var(--accent-amber, #ffaa00)";
        }
        if (runBtn) runBtn.disabled = false;
      }
    } else {
      if (singleHint) singleHint.style.display = "none";
      if (runBtn) runBtn.disabled = false;
    }
  } else {
    // Multi-objective mode
    if (singleHint) singleHint.style.display = "none";
    if (multiVal === "all_combinations") {
      if (multiHint) multiHint.style.display = "block";
      if (tfChecked !== 1) {
        if (multiHint) {
          multiHint.textContent = "⛔ All Combinations requires exactly ONE timeframe. Please select only 1 timeframe.";
          multiHint.style.color = "var(--accent-red, #ff007a)";
        }
        if (runBtn) runBtn.disabled = true;
      } else {
        if (multiHint) {
          multiHint.textContent = "★ All Combinations: will run 12 curated Pareto sweeps for the selected timeframe.";
          multiHint.style.color = "var(--accent-amber, #ffaa00)";
        }
        if (runBtn) runBtn.disabled = false;
      }
    } else {
      if (multiHint) multiHint.style.display = "none";
      if (runBtn) runBtn.disabled = false;
    }
  }
}

async function runOptunaOptimization() {
  const btn = document.getElementById("run-optuna-btn");
  const deployBtn = document.getElementById("deploy-opt-paper-btn");
  if (deployBtn) deployBtn.disabled = true;
  btn.disabled = true;
  btn.innerHTML = `<span class="btn-icon">⏳</span> Optimizing Parameters...`;

  const { limit, days } = getDataRangeParams();
  const baseParams = getStrategyParamsFromUI();
  const s = getSettings();
  const scopeEl = document.getElementById("optuna-scope-select");
  const optScope = scopeEl ? scopeEl.value : "auto";
  const objectiveModeEl = document.getElementById("optuna-objective-mode-select");
  const objectiveMode = objectiveModeEl ? objectiveModeEl.value : "single";
  const targetMetric = document.getElementById("optuna-target-select").value;
  const multiMetricValue = document.getElementById("optuna-multi-metric-select")?.value || "sharpe_ratio|max_drawdown_pct";
  const multiMetricList = multiMetricValue.split("|").filter(Boolean);

  const payload = {
    exchange: document.getElementById("exchange-select").value,
    symbol: document.getElementById("symbol-input").value.trim(),
    timeframe: document.getElementById("timeframe-select").value,
    limit: limit,
    days: days,
    ...baseParams,
    strategy_mode: optScope === "auto" ? "auto" : baseParams.strategy_mode,
    target_metric: objectiveMode === "multi" ? (multiMetricList[0] || "sharpe_ratio") : targetMetric,
    enable_multi_objective: objectiveMode === "multi",
    multi_objective_metrics: objectiveMode === "multi" ? multiMetricList : undefined,
    enable_hard_drawdown_constraint: document.getElementById("optuna-enable-dd-constraint")?.checked || false,
    max_drawdown_constraint_pct: parseFloat(document.getElementById("optuna-dd-constraint-input")?.value || "25"),
    enable_max_holding_bars: document.getElementById("optuna-enable-max-hold")?.checked || false,
    max_holding_bars_min: parseInt(document.getElementById("optuna-max-hold-min")?.value || "4", 10),
    max_holding_bars_max: parseInt(document.getElementById("optuna-max-hold-max")?.value || "96", 10),
    n_trials: parseInt(document.getElementById("optuna-trials-input").value, 10),
    fast_ema_min: s.fast_ema_min,
    fast_ema_max: s.fast_ema_max,
    slow_ema_min: s.slow_ema_min,
    slow_ema_max: s.slow_ema_max,
    trend_ema_min: s.trend_ema_min,
    trend_ema_max: s.trend_ema_max,
    volume_multiplier_min: s.volume_multiplier_min,
    volume_multiplier_max: s.volume_multiplier_max,
    atr_multiplier_min: s.atr_multiplier_min,
    atr_multiplier_max: s.atr_multiplier_max,
    risk_reward_min: s.risk_reward_min,
    risk_reward_max: s.risk_reward_max,
    vwap_slope_max_bound: s.vwap_slope_max_bound,
    min_trades: s.min_trades,
    n_jobs: s.n_jobs !== undefined ? s.n_jobs : 1,
    n_startup_trials: s.n_startup_trials !== undefined ? s.n_startup_trials : null,
    seed_base_params: s.seed_base_params !== false,
  };

  try {
    const response = await fetch("/api/optimize", {
      method: "POST",
      headers: getAuthHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify(payload),
    });

    if (!response.ok) {
      const err = await response.json();
      alert(`Optuna Error: ${err.detail || "Server error"}`);
      return;
    }

    const data = await response.json();

    // Background-mode: backend returns task_id for real exchanges; poll until done.
    if (data.status === "started" && data.task_id) {
      const taskId = data.task_id;
      let attempts = 0;
      const maxAttempts = 600; // ~10 min at 1s intervals — real-exchange
      // optimizations with n_trials=90 can take several minutes.
      let progressShown = false;
      while (attempts < maxAttempts) {
        await new Promise((r) => setTimeout(r, 1000));
        const pollResp = await fetch(`/api/tasks/${taskId}`);
        const pollData = await pollResp.json();
        const task = pollData.task || {};
        if (task.status === "completed" || task.status === "success") {
          data.results = task.result || task.result_json || {};
          break;
        }
        if (task.status === "failed" || task.status === "error") {
          alert(`Optuna background task failed: ${task.error || "Unknown"}`);
          return;
        }
        // Non-blocking progress hint so the user knows it's still running.
        if (!progressShown && task.step_description) {
          showToast(`Optuna running: ${task.step_description}`, "info");
          progressShown = true;
        }
        attempts++;
      }
      if (!data.results) {
        alert("Optuna Request error: Background optimization did not complete in time.");
        return;
      }
    }

    const res = data.results;
    if (!res || typeof res !== "object") {
      alert("Optuna Request error: Unexpected server response (missing results).");
      return;
    }
    if (res.best_value_valid === false) {
      lastBestOptunaParams = null;
      window._lastOptunaValidationReport = null;
      window._lastOptunaValidatedParams = null;
      document.getElementById("opt-best-val").textContent = "N/A";
      document.getElementById("opt-status-badge").textContent = "No valid trial";
      document.getElementById("opt-summary-desc").textContent =
        "No valid in-sample candidate met the optimization requirements.";
      for (const id of [
        "apply-opt-params-btn",
        "deploy-opt-paper-btn",
        "validate-opt-params-btn",
      ]) {
        const element = document.getElementById(id);
        if (element) element.style.display = "none";
      }
      window._lastOptimizationResult = res;
      renderOptunaTrials(res.top_trials || []);
      return;
    }
    lastBestOptunaParams = res.best_params;
    window._lastOptunaValidationReport = null;
    window._lastOptunaValidatedParams = null;
    const bp = res.best_params;
    const winningMode = bp.strategy_mode || baseParams.strategy_mode;
    const slType = baseParams.stop_loss_type;

    // Display best value or best metric value (for multi-objective)
    const bestValDisplay = res.optimization_mode === "multi_objective"
      ? res.best_metric_value
      : res.best_value;
    document.getElementById("opt-best-val").textContent = bestValDisplay !== null ? bestValDisplay : "N/A";

    // Display target metric or pareto objectives
    let targetDisplay = res.target_metric.replaceAll("_", " ").toUpperCase();
    if (res.optimization_mode === "multi_objective" && res.pareto_objectives) {
      targetDisplay = res.pareto_objectives.map(m => m.replaceAll("_", " ")).join(" / ").toUpperCase();
    }
    document.getElementById("opt-target-sub").textContent = targetDisplay;

    // Mode-Aware Core Parameters KPI Card
    const coreLabel = document.getElementById("opt-kpi-core-label");
    const coreVal = document.getElementById("opt-best-emas");
    const coreSub = document.getElementById("opt-trend-ema-sub");

    const modeLabels = {
      crossover: "Setup 1 (EMA x VWAP Crossover)",
      multi_ema: "Setup 2 (Multi-EMA 8/21 x 50)",
      pullback: "Setup 3 (VWAP Pullback Touch)",
    };

    if (winningMode === "multi_ema") {
      if (coreLabel) coreLabel.textContent = "Best EMA Hierarchy";
      coreVal.textContent = `${bp.fast_ema} / ${bp.slow_ema}`;
      coreSub.textContent = `Trend EMA: ${bp.trend_ema}`;
    } else if (winningMode === "pullback") {
      if (coreLabel) coreLabel.textContent = "Pullback Touch & Fast EMA";
      coreVal.textContent = `EMA ${bp.fast_ema} / ${bp.pullback_tolerance_pct || 0.3}%`;
      coreSub.textContent = `VWAP Touch Band: ${bp.pullback_tolerance_pct || 0.3}%`;
    } else {
      if (coreLabel) coreLabel.textContent = "Crossover Momentum Core";
      coreVal.textContent = `Fast EMA: ${bp.fast_ema}`;
      coreSub.textContent = `Setup 1: Fast EMA x VWAP`;
    }

    // Risk & Stop Sizing KPI Card
    const riskLabel = document.getElementById("opt-kpi-risk-label");
    const riskVal = document.getElementById("opt-best-risk");
    const riskSub = document.getElementById("opt-risk-sub");

    if (slType === "vwap") {
      if (riskLabel) riskLabel.textContent = "VWAP Structural Stop Loss";
      riskVal.textContent = `${bp.vwap_stop_offset_pct || 0.1}% Offset`;
      if (riskSub) riskSub.textContent = `Target R:R: ${bp.risk_reward_ratio}x`;
    } else {
      if (riskLabel) riskLabel.textContent = "ATR Dynamic Trailing Stop";
      riskVal.textContent = `${bp.atr_multiplier}x ATR`;
      if (riskSub) riskSub.textContent = `Target R:R: ${bp.risk_reward_ratio}x`;
    }

    // Filters & Gate KPI Card
    const filtersVal = document.getElementById("opt-best-filters");
    const filtersSub = document.getElementById("opt-filters-sub");
    filtersVal.textContent = `${bp.volume_multiplier}x / ${bp.vwap_slope_min}`;
    if (filtersSub) filtersSub.textContent = bp.volume_filter_enabled ? "Volume & Slope Gates Active" : "Slope Gate Active";

    // Update Action Bar
    const applyBtn = document.getElementById("apply-opt-params-btn");
    const deployBtn = document.getElementById("deploy-opt-paper-btn");
    const validateBtn = document.getElementById("validate-opt-params-btn");
    const statusBadge = document.getElementById("opt-status-badge");
    const summaryDesc = document.getElementById("opt-summary-desc");
    if (statusBadge) {
      statusBadge.textContent = optScope === "auto"
        ? `IN-SAMPLE ONLY | ${modeLabels[winningMode] || winningMode}`
        : "IN-SAMPLE ONLY | Optimal Setup";
      statusBadge.className = "badge badge-long";
    }
    if (summaryDesc) {
      if (res.optimization_mode === "multi_objective") {
        const objStr = res.pareto_objectives ? res.pareto_objectives.join(" / ") : "multi-objective";
        summaryDesc.textContent = `In-sample score: ${res.best_metric_value !== null ? res.best_metric_value : "N/A"} (${objStr})`;
      } else {
        summaryDesc.textContent = `In-sample score: ${res.best_value} (${res.target_metric.replace('_', ' ')})`;
      }
    }
    if (applyBtn) applyBtn.style.display = "inline-flex";
    if (deployBtn) {
      deployBtn.style.display = "inline-flex";
      deployBtn.disabled = true;
    }
    if (validateBtn) {
      validateBtn.style.display = "inline-flex";
      validateBtn.innerHTML = `🔬 Validate Best`;
      validateBtn.disabled = false;
      validateBtn.onclick = async () => {
        // Guard against concurrent validation calls (e.g. user clicking while auto-validate runs)
        if (_isValidating) return;
        _isValidating = true;
        validateBtn.innerHTML = `⏳ Validating...`;
        validateBtn.disabled = true;
        if (statusBadge) {
          statusBadge.innerHTML = `<span class="badge" style="background: rgba(0, 242, 254, 0.15); color: #00f2fe; border: 1px solid rgba(0, 242, 254, 0.3); font-size: 0.72rem;">⏳ Validating...</span>`;
        }
        const topTrialCell = document.getElementById("trial-val-cell-0");
        if (topTrialCell) {
          topTrialCell.innerHTML = `<span class="badge" style="background: rgba(0, 242, 254, 0.15); color: #00f2fe; border: 1px solid rgba(0, 242, 254, 0.3); font-size: 0.72rem;">⏳ Validating...</span>`;
        }
        try {
          const sym = document.getElementById("symbol-input").value.trim();
          const tf = document.getElementById("timeframe-select").value;
          const ex = document.getElementById("exchange-select").value;
          const isMulti = res.optimization_mode === "multi_objective";
          const multiMetrics = isMulti ? (res.pareto_objectives || multiMetricList) : null;
          const targetMet = isMulti ? (multiMetrics ? multiMetrics[0] : "sharpe_ratio") : (res.target_metric || targetMetric);
          const report = await validateStrategyConfig(sym, tf, ex, lastBestOptunaParams, targetMet, isMulti, multiMetrics);
          window._lastOptunaValidationReport = report;
          window._lastOptunaValidatedParams = JSON.stringify(lastBestOptunaParams);
          if (deployBtn) deployBtn.disabled = false;
          const cacheKey = "optuna_trial_0";
          window._validationReportCache[cacheKey] = report;
          window._validationContextCache[cacheKey] = {
            reportKey: cacheKey,
            trialIdx: 0,
            symbol: sym,
            timeframe: tf,
            exchange: ex,
            params: lastBestOptunaParams,
            targetMetric: targetMet,
            enableMulti: isMulti,
            multiMetrics: multiMetrics,
          };
          if (statusBadge) {
            statusBadge.innerHTML = getValidationBadgeHtml(report.status, report.overall_score, cacheKey);
          }
          validateBtn.innerHTML = `✅ Validated (${report.status})`;
          if (topTrialCell) {
            const rowDataAttr = encodeURIComponent(JSON.stringify(lastBestOptunaParams));
            topTrialCell.innerHTML = `<div style="display: inline-flex; align-items: center; justify-content: center; gap: 4px;">
              ${getValidationBadgeHtml(report.status, report.overall_score, cacheKey)}
              <button class="btn-secondary btn-xs revalidate-trial-row-btn"
                      data-trial-idx="0"
                      data-params="${rowDataAttr}"
                      style="padding: 1px 5px; font-size: 0.68rem; cursor: pointer; border-color: rgba(0, 242, 254, 0.3); color: var(--accent-cyan);"
                      title="Re-run 3-Gate Robustness Check">
                🔄
              </button>
            </div>`;
          }
        } catch (err) {
          alert(`Validation failed: ${err.message}`);
          validateBtn.innerHTML = `🔬 Validate Best`;
          if (statusBadge) statusBadge.innerHTML = ``;
        } finally {
          _isValidating = false;
          validateBtn.disabled = false;
        }
      };
    }

    window._lastOptimizationResult = res;
    renderOptunaImportance(res.param_importance);
    renderOptunaTrials(res.top_trials);

    // Auto-Validate Best Discovered Configuration if toggled
    const autoValChecked = document.getElementById("opt-auto-validate")?.checked;
    if (autoValChecked && bp && !_isValidating) {
      _isValidating = true;
      if (statusBadge) {
        statusBadge.innerHTML = `<span class="badge" style="background: rgba(0, 242, 254, 0.15); color: #00f2fe; border: 1px solid rgba(0, 242, 254, 0.3); font-size: 0.72rem;">⏳ Auto-Validating...</span>`;
      }
      if (validateBtn) {
        validateBtn.innerHTML = `⏳ Auto-Validating...`;
        validateBtn.disabled = true;
      }
      const topTrialCell = document.getElementById("trial-val-cell-0");
      if (topTrialCell) {
        topTrialCell.innerHTML = `<span class="badge" style="background: rgba(0, 242, 254, 0.15); color: #00f2fe; border: 1px solid rgba(0, 242, 254, 0.3); font-size: 0.72rem;">⏳ Auto-Validating...</span>`;
      }

      const sym = document.getElementById("symbol-input").value.trim();
      const tf = document.getElementById("timeframe-select").value;
      const ex = document.getElementById("exchange-select").value;
      const isMulti = res.optimization_mode === "multi_objective";
      const multiMetrics = isMulti ? (res.pareto_objectives || multiMetricList) : null;
      const targetMet = isMulti ? (multiMetrics ? multiMetrics[0] : "sharpe_ratio") : (res.target_metric || targetMetric);

      validateStrategyConfig(sym, tf, ex, bp, targetMet, isMulti, multiMetrics)
        .then((report) => {
          window._lastOptunaValidationReport = report;
          window._lastOptunaValidatedParams = JSON.stringify(bp);
          if (deployBtn) deployBtn.disabled = false;
          const cacheKey = "optuna_trial_0";
          window._validationReportCache[cacheKey] = report;
          window._validationContextCache[cacheKey] = {
            reportKey: cacheKey,
            trialIdx: 0,
            symbol: sym,
            timeframe: tf,
            exchange: ex,
            params: bp,
            targetMetric: targetMet,
            enableMulti: isMulti,
            multiMetrics: multiMetrics,
          };
          if (statusBadge) {
            statusBadge.innerHTML = getValidationBadgeHtml(report.status, report.overall_score, cacheKey);
          }
          if (validateBtn) {
            validateBtn.innerHTML = `✅ Validated (${report.status})`;
            validateBtn.disabled = false;
          }
          const topCell = document.getElementById("trial-val-cell-0");
          if (topCell) {
            const rowDataAttr = encodeURIComponent(JSON.stringify(bp));
            topCell.innerHTML = `<div style="display: inline-flex; align-items: center; justify-content: center; gap: 4px;">
              ${getValidationBadgeHtml(report.status, report.overall_score, cacheKey)}
              <button class="btn-secondary btn-xs revalidate-trial-row-btn"
                      data-trial-idx="0"
                      data-params="${rowDataAttr}"
                      style="padding: 1px 5px; font-size: 0.68rem; cursor: pointer; border-color: rgba(0, 242, 254, 0.3); color: var(--accent-cyan);"
                      title="Re-run 3-Gate Robustness Check">
                🔄
              </button>
            </div>`;
          }
        })
        .catch((e) => {
          console.warn("[Optuna] Auto-validation warning:", e);
          if (statusBadge) statusBadge.innerHTML = ``;
          if (validateBtn) {
            validateBtn.innerHTML = `🔬 Validate Best`;
            validateBtn.disabled = false;
          }
        })
        .finally(() => {
          _isValidating = false;
        });
    }

    // Switch tab to Optuna
    document.querySelector('[data-tab="tab-optuna"]').click();
  } catch (error) {
    alert(`Optuna Request error: ${error.message}`);
  } finally {
    btn.disabled = false;
    btn.innerHTML = `<span class="btn-icon">🎯</span> Run Optuna Optimization`;
  }
}

async function deployOptunaToPaper(params = null, score = null, validationReport = null) {
  const targetParams = params || lastBestOptunaParams;
  if (!targetParams) {
    alert("No Optuna optimization results available to deploy.");
    return;
  }

  // Safety Guardrail Check
  const valRep = validationReport || (targetParams === lastBestOptunaParams ? window._lastOptunaValidationReport : null);
  if (!valRep) {
    alert("Run out-of-sample robustness validation before deploying Optuna parameters.");
    return;
  }
  if (
    targetParams === lastBestOptunaParams &&
    window._lastOptunaValidatedParams !== JSON.stringify(targetParams)
  ) {
    alert("The validation report does not match the current Optuna parameters. Validate them again.");
    return;
  }
  if (valRep) {
    if (valRep.status === "FAIL") {
      const reasons = (valRep.reasons || []).map((r) => `• ${r}`).join("\n");
      const ok = confirm(
        `⚠️ DEPLOYMENT GUARDRAIL ALERT:\n\nThis strategy FAILED multi-stage robustness validation (Score: ${valRep.overall_score}/100).\n\nFailure Reasons:\n${reasons || "• Negative return or overfit detected"}\n\nDeploying an unprofitable or curve-fitted configuration into paper trading will risk capital.\n\nDo you still want to proceed with deployment?`
      );
      if (!ok) return;
    } else if (valRep.status === "CAUTION") {
      const reasons = (valRep.reasons || []).map((r) => `• ${r}`).join("\n");
      const ok = confirm(
        `⚠️ CAUTIONARY DEPLOYMENT:\n\nThis strategy passed validation with CAUTION (Score: ${valRep.overall_score}/100).\n\nObservations:\n${reasons || "• Low out-of-sample consistency"}\n\nProceed with deployment to paper trading?`
      );
      if (!ok) return;
    }
  }

  const exchange = document.getElementById("exchange-select").value;
  const symbol = document.getElementById("symbol-input").value.trim();
  const timeframe = document.getElementById("timeframe-select").value;
  const mode = targetParams.strategy_mode || document.getElementById("strategy-mode-select").value;
  const targetMetric = document.getElementById("optuna-metric-select") ? document.getElementById("optuna-metric-select").value : "sharpe_ratio";
  const optScore = score !== null ? parseFloat(score) : (parseFloat(document.getElementById("opt-best-val").textContent) || 0.0);

  const deployBtn = document.getElementById("deploy-opt-paper-btn");
  if (deployBtn && !params) {
    deployBtn.disabled = true;
    deployBtn.innerHTML = `⏳ Deploying...`;
  }

  try {
    const resp = await fetch("/api/paper/profiles/deploy", {
      method: "POST",
      headers: getAuthHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify({
        exchange: exchange,
        symbol: symbol,
        timeframe: timeframe,
        strategy_mode: mode,
        target_metric: targetMetric,
        params: targetParams,
        optuna_score: optScore,
        is_active: true,
        source: "optuna",
        validation_report: valRep,
      }),
    });

    if (!resp.ok) {
      const err = await resp.json();
      alert(`Deployment Error: ${err.detail || "Server error"}`);
      return;
    }

    if (deployBtn && !params) {
      deployBtn.innerHTML = `✅ Deployed to Paper!`;
      setTimeout(() => {
        deployBtn.disabled = false;
        deployBtn.innerHTML = `🚀 Deploy to Paper Profile`;
      }, 3000);
    }

    const summaryDesc = document.getElementById("opt-summary-desc");
    if (summaryDesc) {
      const origText = summaryDesc.textContent;
      summaryDesc.textContent = `🚀 Deployed active paper profile for ${symbol} (${exchange.toUpperCase()}, ${timeframe})!`;
      summaryDesc.style.color = "#00f5a0";
      setTimeout(() => {
        summaryDesc.textContent = origText;
        summaryDesc.style.color = "";
      }, 4000);
    }
  } catch (err) {
    alert(`Failed to deploy paper profile: ${err.message}`);
  } finally {
    if (deployBtn && !params && deployBtn.innerHTML === `⏳ Deploying...`) {
      deployBtn.disabled = false;
      deployBtn.innerHTML = `🚀 Deploy to Paper Profile`;
    }
  }
}

function applyBestOptunaParams() {
  if (!lastBestOptunaParams) {
    alert("No Optuna optimization results available to apply.");
    return;
  }

  const bp = lastBestOptunaParams;
  lastAppliedParams = bp;

  const fieldMap = {
    strategy_mode: "strategy-mode-select",
    trade_direction: "trade-direction-select",
    fast_ema: "fast-ema-input",
    slow_ema: "slow-ema-input",
    trend_ema: "trend-ema-input",
    vwap_slope_min: "vwap-slope-input",
    vwap_slope_lookback: "vwap-lookback-input",
    volume_filter_enabled: "volume-filter-toggle",
    volume_multiplier: "vol-mult-input",
    pullback_tolerance_pct: "pullback-tol-input",
    stop_loss_type: "stop-loss-type-select",
    vwap_stop_offset_pct: "vwap-stop-input",
    atr_multiplier: "atr-mult-input",
    trailing_stop_pct: "tsl-distance-input",
    risk_per_trade_pct: "risk-per-trade-input",
    risk_reward_ratio: "rr-ratio-input",
    max_holding_bars: "max-holding-bars-input",
    initial_capital: "capital-input",
    asset_type: "asset-type-select",
    session_mode: "session-mode-select",
    vwap_anchor: "vwap-anchor-select",
    allow_fractional_shares: "fractional-shares-select",
    enforce_margin_calls: "margin-calls-checkbox",
    min_margin_equity: "min-margin-input",
  };

  const updatedIds = [];
  Object.entries(fieldMap).forEach(([key, id]) => {
    if (bp[key] !== undefined && bp[key] !== null) {
      const el = document.getElementById(id);
      if (el) {
        if (key === "volume_filter_enabled") {
          el.value = bp[key] ? "true" : "false";
        } else {
          el.value = bp[key];
        }
        updatedIds.push(id);
      }
    }
  });

  const maxHoldEl = document.getElementById("max-holding-bars-input");
  if (maxHoldEl) {
    maxHoldEl.value =
      bp.max_holding_bars !== undefined && bp.max_holding_bars !== null
        ? bp.max_holding_bars
        : "";
    updatedIds.push("max-holding-bars-input");
  }

  // Flash highlight on updated sidebar inputs
  updatedIds.forEach((id) => {
    const el = document.getElementById(id);
    if (el) {
      el.style.transition = "box-shadow 0.25s ease, border-color 0.25s ease";
      el.style.boxShadow = "0 0 14px rgba(0, 245, 160, 0.6)";
      el.style.borderColor = "#00f5a0";
      setTimeout(() => {
        el.style.boxShadow = "";
        el.style.borderColor = "";
      }, 1800);
    }
  });

  // Switch to Backtest Dashboard and execute backtest
  document.querySelector('[data-tab="tab-backtest"]').click();
  runBacktest();
}

function renderOptunaImportance(importanceMap) {
  if (!importanceMap || Object.keys(importanceMap).length === 0) return;

  const params = Object.keys(importanceMap);
  const values = Object.values(importanceMap).map((v) => v * 100);

  const trace = {
    type: "bar",
    x: values,
    y: params,
    orientation: "h",
    marker: {
      color: "rgba(0, 242, 254, 0.7)",
      line: { color: "#00f2fe", width: 1 },
    },
  };

  const layout = {
    paper_bgcolor: "transparent",
    plot_bgcolor: "transparent",
    font: { color: "#8a99ad", family: "Inter, sans-serif" },
    margin: { l: 140, r: 40, t: 10, b: 30 },
    xaxis: { title: "Importance (%)", gridcolor: "rgba(255, 255, 255, 0.05)" },
    yaxis: { gridcolor: "rgba(255, 255, 255, 0.05)" },
  };

  Plotly.newPlot("optuna-importance-chart", [trace], layout, { responsive: true, displayModeBar: false });
}

function getValidationCriteriaFromUI() {
  const getNum = (id, def) => {
    const el = document.getElementById(id);
    if (!el) return def;
    const parsed = Number.parseFloat(el.value);
    return Number.isFinite(parsed) ? parsed : def;
  };
  const getInt = (id, def) => {
    const el = document.getElementById(id);
    if (!el) return def;
    const parsed = Number.parseInt(el.value, 10);
    return Number.isFinite(parsed) ? parsed : def;
  };
  return {
    min_backtest_sharpe: getNum("setting-val-min-sharpe", DEFAULT_SETTINGS.val_min_sharpe),
    max_backtest_drawdown_pct: getNum("setting-val-max-drawdown", DEFAULT_SETTINGS.val_max_drawdown),
    min_backtest_return_pct: getNum("setting-val-min-return", DEFAULT_SETTINGS.val_min_return),
    min_trade_count: getInt("setting-val-min-trades", DEFAULT_SETTINGS.val_min_trades),
    min_walk_forward_efficiency: getNum("setting-val-wf-efficiency", DEFAULT_SETTINGS.val_wf_efficiency),
    min_profitable_windows_pct: getNum("setting-val-profitable-windows", DEFAULT_SETTINGS.val_profitable_windows),
    min_oos_return_pct: getNum("setting-val-min-oos-return", DEFAULT_SETTINGS.val_min_oos_return),
    max_monte_carlo_ruin_pct: getNum("setting-val-max-ruin", DEFAULT_SETTINGS.val_max_ruin),
    max_monte_carlo_drawdown_pct: getNum("setting-val-max-mc-drawdown", DEFAULT_SETTINGS.val_max_mc_drawdown),
  };
}

function getValidationTrialsFromUI() {
  const valTrialsEl = document.getElementById("setting-val-trials");
  const batchTrialsEl = document.getElementById("batch-trials-input");
  const optunaTrialsEl = document.getElementById("optuna-trials-input");
  const valTrials = valTrialsEl && parseInt(valTrialsEl.value, 10) ? parseInt(valTrialsEl.value, 10) : null;
  const batchTrials = batchTrialsEl && parseInt(batchTrialsEl.value, 10) ? parseInt(batchTrialsEl.value, 10) : null;
  const optunaTrials = optunaTrialsEl && parseInt(optunaTrialsEl.value, 10) ? parseInt(optunaTrialsEl.value, 10) : null;
  return valTrials || batchTrials || optunaTrials || 250;
}

async function validateStrategyConfig(symbol, timeframe, exchange, params, targetMetric = null, enableMulti = false, multiMetrics = null, limitOverride = undefined, daysOverride = undefined) {
  const rangeParams = getDataRangeParams();
  const limit = limitOverride !== undefined ? limitOverride : rangeParams.limit;
  const days = daysOverride !== undefined ? daysOverride : rangeParams.days;
  const valTrialsEl = document.getElementById("setting-val-trials");
  const nTrialsEl = document.getElementById("optuna-trials-input");
  let nTrials = 250;
  if (valTrialsEl && parseInt(valTrialsEl.value, 10)) {
    nTrials = parseInt(valTrialsEl.value, 10);
  } else if (nTrialsEl && parseInt(nTrialsEl.value, 10)) {
    nTrials = parseInt(nTrialsEl.value, 10);
  }

  const getNum = (id, def) => {
    const el = document.getElementById(id);
    if (!el) return def;
    const parsed = Number.parseFloat(el.value);
    return Number.isFinite(parsed) ? parsed : def;
  };
  const getInt = (id, def) => {
    const el = document.getElementById(id);
    if (!el) return def;
    const parsed = Number.parseInt(el.value, 10);
    return Number.isFinite(parsed) ? parsed : def;
  };

  const criteria = {
    min_backtest_sharpe: getNum("setting-val-min-sharpe", DEFAULT_SETTINGS.val_min_sharpe),
    max_backtest_drawdown_pct: getNum("setting-val-max-drawdown", DEFAULT_SETTINGS.val_max_drawdown),
    min_backtest_return_pct: getNum("setting-val-min-return", DEFAULT_SETTINGS.val_min_return),
    min_trade_count: getInt("setting-val-min-trades", DEFAULT_SETTINGS.val_min_trades),
    min_walk_forward_efficiency: getNum("setting-val-wf-efficiency", DEFAULT_SETTINGS.val_wf_efficiency),
    min_profitable_windows_pct: getNum("setting-val-profitable-windows", DEFAULT_SETTINGS.val_profitable_windows),
    min_oos_return_pct: getNum("setting-val-min-oos-return", DEFAULT_SETTINGS.val_min_oos_return),
    max_monte_carlo_ruin_pct: getNum("setting-val-max-ruin", DEFAULT_SETTINGS.val_max_ruin),
    max_monte_carlo_drawdown_pct: getNum("setting-val-max-mc-drawdown", DEFAULT_SETTINGS.val_max_mc_drawdown),
  };

  // Resolve target_metric: use the explicit argument, fall back to the UI select, then sharpe_ratio.
  let resolvedMetric = targetMetric;
  if (!resolvedMetric) {
    const metricEl = document.getElementById("optuna-metric-select") || document.getElementById("optuna-target-select");
    resolvedMetric = metricEl ? metricEl.value : "sharpe_ratio";
  }

  const resp = await fetch("/api/strategy/validate", {
    method: "POST",
    headers: getAuthHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify({
      exchange: exchange || document.getElementById("exchange-select").value,
      symbol: symbol || document.getElementById("symbol-input").value.trim(),
      timeframe: timeframe || document.getElementById("timeframe-select").value,
      asset_type: document.getElementById("asset-type-select")?.value || "crypto",
      limit: limit,
      days: days,
      params: params,
      criteria: criteria,
      n_trials: nTrials,
      target_metric: resolvedMetric,
      enable_multi_objective: enableMulti,
      multi_objective_metrics: multiMetrics,
    }),
  });
  if (!resp.ok) {
    const err = await resp.json();
    throw new Error(err.detail || "Validation request failed");
  }
  const data = await resp.json();
  return data.report;
}

// Note: _validationReportCache, _validationContextCache, and _activeValidationContext
// are initialized at the top of this file with the other global state variables.

function showValidationDetailsModal(reportKey, title = "Strategy Robustness Audit", context = null) {
  const report = typeof reportKey === "object" ? reportKey : window._validationReportCache[reportKey];
  if (!report) {
    alert("No detailed validation report available.");
    return;
  }

  if (context) {
    window._activeValidationContext = context;
  } else if (typeof reportKey === "string" && window._validationContextCache[reportKey]) {
    window._activeValidationContext = window._validationContextCache[reportKey];
  }

  const modal = document.getElementById("validation-details-modal");
  if (!modal) return;

  const statusBadge = document.getElementById("val-modal-status-badge");
  const summaryBox = document.getElementById("val-modal-summary-box");
  const summaryText = document.getElementById("val-modal-summary-text");
  const recAction = document.getElementById("val-modal-rec-action");
  const scoreEl = document.getElementById("val-modal-score");

  const status = (report.status || "UNKNOWN").toUpperCase();
  const score = report.overall_score !== undefined ? report.overall_score : "--";

  if (statusBadge) {
    statusBadge.innerHTML = getValidationBadgeHtml(status, null);
  }
  if (summaryText) summaryText.textContent = report.summary || "Validation Analysis Complete";
  if (recAction) recAction.textContent = `Recommended Action: ${(report.recommended_action || "REVIEW").replace('_', ' ')}`;
  if (scoreEl) {
    scoreEl.textContent = `${score}/100`;
    scoreEl.style.color = (status === "ROBUST" || status === "PASS") ? "#00f5a0" : status === "CAUTION" ? "#ffaa00" : "#ff007a";
  }
  if (summaryBox) {
    summaryBox.style.borderLeftColor = (status === "ROBUST" || status === "PASS") ? "#00f5a0" : status === "CAUTION" ? "#ffaa00" : "#ff007a";
  }

  // Populate Gate 1
  const g1 = (report.gate_results && report.gate_results.gate1_backtest) || {};
  const g1Pill = document.getElementById("g1-status-pill");
  if (g1Pill) g1Pill.innerHTML = getValidationBadgeHtml(g1.status || "PASS", null);
  const g1Grid = document.getElementById("g1-metrics-grid");
  if (g1Grid && g1.metrics) {
    g1Grid.innerHTML = `
      <div class="kpi-card glow-cyan" style="padding: 6px 10px;"><div class="kpi-label" style="font-size:0.7rem;">Return</div><div class="kpi-value" style="font-size:0.95rem; color:${g1.metrics.total_return_pct >= 0 ? '#00f5a0' : '#ff007a'};">${g1.metrics.total_return_pct > 0 ? '+' : ''}${g1.metrics.total_return_pct}%</div></div>
      <div class="kpi-card glow-green" style="padding: 6px 10px;"><div class="kpi-label" style="font-size:0.7rem;">Sharpe</div><div class="kpi-value" style="font-size:0.95rem;">${g1.metrics.sharpe_ratio}</div></div>
      <div class="kpi-card glow-red" style="padding: 6px 10px;"><div class="kpi-label" style="font-size:0.7rem;">Max DD</div><div class="kpi-value" style="font-size:0.95rem; color:#ff007a;">${g1.metrics.max_drawdown_pct}%</div></div>
      <div class="kpi-card glow-purple" style="padding: 6px 10px;"><div class="kpi-label" style="font-size:0.7rem;">Win Rate</div><div class="kpi-value" style="font-size:0.95rem;">${g1.metrics.win_rate}%</div></div>
      <div class="kpi-card glow-blue" style="padding: 6px 10px;"><div class="kpi-label" style="font-size:0.7rem;">Trades</div><div class="kpi-value" style="font-size:0.95rem;">${g1.metrics.trade_count}</div></div>
      <div class="kpi-card glow-cyan" style="padding: 6px 10px;"><div class="kpi-label" style="font-size:0.7rem;">Profit Factor</div><div class="kpi-value" style="font-size:0.95rem;">${g1.metrics.profit_factor}</div></div>
    `;
  }
  const g1Details = document.getElementById("g1-details-text");
  if (g1Details) g1Details.textContent = g1.details || "";
  const g1Warn = document.getElementById("g1-warnings-list");
  if (g1Warn) g1Warn.innerHTML = (g1.warnings || []).map(w => `<div>⚠️ ${w}</div>`).join("");

  // Populate Gate 2
  const g2 = report.gate_results ? report.gate_results.gate2_wfo : null;
  const g2Pill = document.getElementById("g2-status-pill");
  if (g2Pill) g2Pill.innerHTML = getValidationBadgeHtml(g2 ? g2.status : "SKIPPED", null);
  const g2Grid = document.getElementById("g2-metrics-grid");
  if (g2Grid) {
    if (g2 && g2.metrics && Object.keys(g2.metrics).length > 0) {
      g2Grid.innerHTML = `
        <div class="kpi-card glow-cyan" style="padding: 6px 10px;"><div class="kpi-label" style="font-size:0.7rem;">WFE Efficiency</div><div class="kpi-value" style="font-size:0.95rem; color:#00f2fe;">${g2.metrics.walk_forward_efficiency_pct}%</div></div>
        <div class="kpi-card glow-green" style="padding: 6px 10px;"><div class="kpi-label" style="font-size:0.7rem;">Profitable Windows</div><div class="kpi-value" style="font-size:0.95rem;">${g2.metrics.profitable_windows_pct}%</div></div>
        <div class="kpi-card glow-purple" style="padding: 6px 10px;"><div class="kpi-label" style="font-size:0.7rem;">OOS Return</div><div class="kpi-value" style="font-size:0.95rem; color:${g2.metrics.oos_total_return_pct >= 0 ? '#00f5a0' : '#ff007a'};">${g2.metrics.oos_total_return_pct > 0 ? '+' : ''}${g2.metrics.oos_total_return_pct}%</div></div>
        <div class="kpi-card glow-blue" style="padding: 6px 10px;"><div class="kpi-label" style="font-size:0.7rem;">Consistency</div><div class="kpi-value" style="font-size:0.95rem;">${g2.metrics.consistency_score}</div></div>
      `;
    } else {
      g2Grid.innerHTML = `<div style="font-size: 0.82rem; color: var(--text-muted); padding: 4px 8px;">Gate 2 was skipped because Gate 1 did not meet viability criteria.</div>`;
    }
  }
  const g2Details = document.getElementById("g2-details-text");
  if (g2Details) g2Details.textContent = g2 ? (g2.details || "") : "";
  const g2Warn = document.getElementById("g2-warnings-list");
  if (g2Warn) g2Warn.innerHTML = ((g2 && g2.warnings) || []).map(w => `<div>⚠️ ${w}</div>`).join("");

  // Populate Gate 3
  const g3 = report.gate_results ? report.gate_results.gate3_monte_carlo : null;
  const g3Pill = document.getElementById("g3-status-pill");
  if (g3Pill) g3Pill.innerHTML = getValidationBadgeHtml(g3 ? g3.status : "SKIPPED", null);
  const g3Grid = document.getElementById("g3-metrics-grid");
  if (g3Grid) {
    if (g3 && g3.metrics && Object.keys(g3.metrics).length > 0) {
      g3Grid.innerHTML = `
        <div class="kpi-card glow-red" style="padding: 6px 10px;"><div class="kpi-label" style="font-size:0.7rem;">Risk of Ruin</div><div class="kpi-value" style="font-size:0.95rem; color:${g3.metrics.risk_of_ruin_pct > 5 ? '#ff007a' : '#00f5a0'};">${g3.metrics.risk_of_ruin_pct}%</div></div>
        <div class="kpi-card glow-amber" style="padding: 6px 10px;"><div class="kpi-label" style="font-size:0.7rem;">95% Max DD</div><div class="kpi-value" style="font-size:0.95rem; color:#f87171;">${g3.metrics.p95_max_drawdown_pct}%</div></div>
        <div class="kpi-card glow-cyan" style="padding: 6px 10px;"><div class="kpi-label" style="font-size:0.7rem;">Median Net Profit</div><div class="kpi-value" style="font-size:0.95rem;">$${g3.metrics.median_return}</div></div>
        <div class="kpi-card glow-blue" style="padding: 6px 10px;"><div class="kpi-label" style="font-size:0.7rem;">Simulations</div><div class="kpi-value" style="font-size:0.95rem;">${g3.metrics.simulations_count}</div></div>
      `;
    } else {
      g3Grid.innerHTML = `<div style="font-size: 0.82rem; color: var(--text-muted); padding: 4px 8px;">Gate 3 was skipped because Gate 1 did not meet viability criteria.</div>`;
    }
  }
  const g3Details = document.getElementById("g3-details-text");
  if (g3Details) g3Details.textContent = g3 ? (g3.details || "") : "";
  const g3Warn = document.getElementById("g3-warnings-list");
  if (g3Warn) g3Warn.innerHTML = ((g3 && g3.warnings) || []).map(w => `<div>⚠️ ${w}</div>`).join("");

  // Re-run validation button in modal
  const revalBtn = document.getElementById("val-modal-revalidate-btn");
  if (revalBtn) {
    revalBtn.onclick = async () => {
      if (!window._activeValidationContext) {
        alert("No strategy configuration context linked for re-validation.");
        return;
      }
      const ctx = window._activeValidationContext;
      revalBtn.disabled = true;
      revalBtn.innerHTML = `⏳ Running Audit...`;
      try {
        const newReport = await validateStrategyConfig(
          ctx.symbol,
          ctx.timeframe,
          ctx.exchange,
          ctx.params,
          ctx.targetMetric,
          ctx.enableMulti || false,
          ctx.multiMetrics || null,
          null, // limitOverride: null -> backend resolves full timeframe lookback
          null  // daysOverride: null
        );
        window._validationReportCache[ctx.reportKey] = newReport;

        // If batch result ID exists, save to SQLite
        if (ctx.batchId) {
          const patchResp = await fetch(`/api/batch_results/${ctx.batchId}/validation`, {
            method: "PATCH",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              status: newReport.status,
              report_json: JSON.stringify(newReport),
            }),
          });
          if (!patchResp.ok) {
            const err = await patchResp.json().catch(() => ({ detail: "Unknown error" }));
            throw new Error(`Failed to save validation: ${err.detail || patchResp.status}`);
          }
          // Update the DOM cell
          const cell = document.getElementById(`batch-val-cell-${ctx.batchId}`);
          if (cell) {
            const rowDataAttr = encodeURIComponent(JSON.stringify(ctx.params));
            cell.innerHTML = `<div style="display: inline-flex; align-items: center; justify-content: center; gap: 4px;">
              ${getValidationBadgeHtml(newReport.status, newReport.overall_score, ctx.reportKey)}
              <button class="btn-secondary btn-xs"
                      style="padding: 1px 5px; font-size: 0.68rem; cursor: pointer; border-color: rgba(0, 242, 254, 0.3); color: var(--accent-cyan);"
                      onclick="validateBatchLeaderboardRow(${ctx.batchId}, '${ctx.symbol}', '${ctx.timeframe}', '${ctx.exchange}', this.dataset.params, this)"
                      data-params="${rowDataAttr}"
                      title="Re-run 3-Gate Robustness Check">
                🔄
              </button>
            </div>`;
          }
          // Also update cached rows so sorting preserves validation status
          const table = document.getElementById("batch-leaderboard-table");
          if (table && table._cachedRows) {
            const rowIdx = table._cachedRows.findIndex(r => r.id === ctx.batchId);
            if (rowIdx >= 0) {
              table._cachedRows[rowIdx].validation_status = newReport.status;
              table._cachedRows[rowIdx].validation_json = JSON.stringify(newReport);
              table._cachedRows[rowIdx].validation_report = JSON.stringify(newReport);
            }
          }
        } else if (ctx.trialIdx !== undefined) {
          const cell = document.getElementById(`trial-val-cell-${ctx.trialIdx}`);
          if (cell) {
            const rowDataAttr = encodeURIComponent(JSON.stringify(ctx.params));
            cell.innerHTML = `<div style="display: inline-flex; align-items: center; justify-content: center; gap: 4px;">
              ${getValidationBadgeHtml(newReport.status, newReport.overall_score, ctx.reportKey)}
              <button class="btn-secondary btn-xs revalidate-trial-row-btn"
                      data-trial-idx="${ctx.trialIdx}"
                      data-params="${rowDataAttr}"
                      style="padding: 1px 5px; font-size: 0.68rem; cursor: pointer; border-color: rgba(0, 242, 254, 0.3); color: var(--accent-cyan);"
                      title="Re-run 3-Gate Robustness Check">
                🔄
              </button>
            </div>`;
          }
          // Also update cached rows for optuna trials table if present
          const trialTable = document.getElementById("optuna-trials-table");
          if (trialTable && trialTable._cachedRows) {
            const rowIdx = trialTable._cachedRows.findIndex((r, i) => i === ctx.trialIdx);
            if (rowIdx >= 0) {
              trialTable._cachedRows[rowIdx].validation_status = newReport.status;
              trialTable._cachedRows[rowIdx].validation_json = JSON.stringify(newReport);
            }
          }
        }

        // Refresh modal view
        showValidationDetailsModal(ctx.reportKey, title, ctx);
      } catch (err) {
        alert(`Re-validation error: ${err.message}`);
      } finally {
        revalBtn.disabled = false;
        revalBtn.innerHTML = `🔄 Re-Run Validation`;
      }
    };
  }

  modal.style.display = "flex";
}

function closeValidationDetailsModal() {
  const modal = document.getElementById("validation-details-modal");
  if (modal) modal.style.display = "none";
}

function getValidationBadgeHtml(status, score = null, reportKey = null) {
  const s = (status || "").toUpperCase();
  const scoreText = score !== null ? ` (${score})` : "";
  const clickAttr = reportKey ? `onclick="showValidationDetailsModal('${reportKey}')" style="cursor: pointer;" title="Click to inspect 3-Gate breakdown"` : "";
  if (s === "ROBUST" || s === "PASS") {
    const label = s === "PASS" ? "PASS" : "ROBUST";
    return `<span class="badge" ${clickAttr} style="background: rgba(0, 245, 160, 0.15); color: #00f5a0; border: 1px solid rgba(0, 245, 160, 0.3); font-size: 0.72rem; cursor: pointer;">🟢 ${label}${scoreText}</span>`;
  } else if (s === "CAUTION") {
    return `<span class="badge" ${clickAttr} style="background: rgba(255, 170, 0, 0.15); color: #ffaa00; border: 1px solid rgba(255, 170, 0, 0.3); font-size: 0.72rem; cursor: pointer;">🟡 CAUTION${scoreText}</span>`;
  } else if (s === "FAIL") {
    return `<span class="badge" ${clickAttr} style="background: rgba(255, 0, 122, 0.15); color: #ff007a; border: 1px solid rgba(255, 0, 122, 0.3); font-size: 0.72rem; cursor: pointer;">🔴 FAIL${scoreText}</span>`;
  } else if (s === "SKIPPED" || s === "NOT_RUN") {
    return `<span class="badge" style="background: rgba(138, 153, 173, 0.15); color: #8a99ad; border: 1px solid rgba(138, 153, 173, 0.3); font-size: 0.72rem;">⚪ SKIPPED</span>`;
  }
  return `<span class="badge" style="color: var(--text-muted); font-size: 0.72rem;">—</span>`;
}

// ---------------------------------------------------------------------------
// Load an arbitrary trial into the top KPI panel (makes it the "active" trial
// for Apply to Strategy / Deploy to Paper).
// ---------------------------------------------------------------------------
function loadTrialIntoPanel(trial, trialIdx) {
  const p = trial.params || {};
  const res = window._lastOptimizationResult || {};
  const slType = res.stop_loss_type || (p.stop_loss_type) || "vwap";
  const winningMode = p.strategy_mode || "crossover";

  // Update global so Apply / Deploy use this trial
  lastBestOptunaParams = p;

  // --- KPI Card: Target Score ---
  const scoreEl = document.getElementById("opt-best-val");
  if (scoreEl) {
    let valDisplay = "N/A";
    if (typeof trial.value === "object" && trial.value !== null) {
      valDisplay = Object.values(trial.value).map(v => (typeof v === "number" ? v.toFixed(4) : v)).join(" / ");
    } else if (typeof trial.value === "number") {
      valDisplay = trial.value.toFixed(4);
    } else if (trial.value !== null && trial.value !== undefined) {
      valDisplay = String(trial.value);
    }
    scoreEl.textContent = valDisplay;
  }

  // --- KPI Card: Core Parameters ---
  const coreLabel = document.getElementById("opt-kpi-core-label");
  const coreVal = document.getElementById("opt-best-emas");
  const coreSub = document.getElementById("opt-trend-ema-sub");
  if (winningMode === "multi_ema") {
    if (coreLabel) coreLabel.textContent = "Best EMA Hierarchy";
    if (coreVal) coreVal.textContent = `${p.fast_ema ?? "–"} / ${p.slow_ema ?? "–"}`;
    if (coreSub) coreSub.textContent = `Trend EMA: ${p.trend_ema ?? "–"}`;
  } else if (winningMode === "pullback") {
    if (coreLabel) coreLabel.textContent = "Pullback Touch & Fast EMA";
    if (coreVal) coreVal.textContent = `EMA ${p.fast_ema ?? "–"} / ${p.pullback_tolerance_pct ?? 0.3}%`;
    if (coreSub) coreSub.textContent = `VWAP Touch Band: ${p.pullback_tolerance_pct ?? 0.3}%`;
  } else {
    if (coreLabel) coreLabel.textContent = "Crossover Momentum Core";
    if (coreVal) coreVal.textContent = `Fast EMA: ${p.fast_ema ?? "–"}`;
    if (coreSub) coreSub.textContent = `Setup 1: Fast EMA x VWAP`;
  }

  // --- KPI Card: Risk & Stop ---
  const riskLabel = document.getElementById("opt-kpi-risk-label");
  const riskVal = document.getElementById("opt-best-risk");
  const riskSub = document.getElementById("opt-risk-sub");
  if (slType === "vwap") {
    if (riskLabel) riskLabel.textContent = "VWAP Structural Stop Loss";
    if (riskVal) riskVal.textContent = `${p.vwap_stop_offset_pct ?? 0.1}% Offset`;
    if (riskSub) riskSub.textContent = `Target R:R: ${p.risk_reward_ratio ?? "–"}x`;
  } else {
    if (riskLabel) riskLabel.textContent = "ATR Dynamic Trailing Stop";
    if (riskVal) riskVal.textContent = `${p.atr_multiplier ?? "–"}x ATR`;
    if (riskSub) riskSub.textContent = `Target R:R: ${p.risk_reward_ratio ?? "–"}x`;
  }

  // --- KPI Card: Filters & Gates ---
  const filtersVal = document.getElementById("opt-best-filters");
  const filtersSub = document.getElementById("opt-filters-sub");
  if (filtersVal) filtersVal.textContent = `${p.volume_multiplier ?? "–"}x / ${p.vwap_slope_min ?? "–"}`;
  if (filtersSub) filtersSub.textContent = p.volume_filter_enabled ? "Volume & Slope Gates Active" : "Slope Gate Active";

  // --- Action bar: update badge to show selected trial number ---
  const statusBadge = document.getElementById("opt-status-badge");
  if (statusBadge) {
    statusBadge.textContent = `Trial #${trial.trial_number} Selected`;
    statusBadge.className = "badge badge-long";
  }
  const summaryDesc = document.getElementById("opt-summary-desc");
  if (summaryDesc) {
    const valStr = (typeof trial.value === "number") ? trial.value.toFixed(4) : "N/A";
    summaryDesc.textContent = `Trial #${trial.trial_number} — Score: ${valStr}. Click ⚡ Apply to Strategy to run backtest.`;
  }

  // Ensure action buttons are visible
  const applyBtn = document.getElementById("apply-opt-params-btn");
  const deployBtn = document.getElementById("deploy-opt-paper-btn");
  const validateBtn = document.getElementById("validate-opt-params-btn");
  if (applyBtn) applyBtn.style.display = "inline-flex";
  if (deployBtn) deployBtn.style.display = "inline-flex";
  if (validateBtn) { validateBtn.style.display = "inline-flex"; validateBtn.innerHTML = "🔬 Validate Best"; validateBtn.disabled = false; }

  // Highlight selected row in the trial table
  document.querySelectorAll(".optuna-trial-row").forEach((r) => r.classList.remove("optuna-trial-row--selected"));
  const selRow = document.getElementById(`optuna-trial-row-${trialIdx}`);
  if (selRow) selRow.classList.add("optuna-trial-row--selected");

  // Scroll the panel header into view smoothly
  const panel = document.querySelector("#tab-optuna .optuna-action-bar");
  if (panel) panel.scrollIntoView({ behavior: "smooth", block: "nearest" });
}

// ---------------------------------------------------------------------------
// Optuna trials table sort state
// ---------------------------------------------------------------------------
let _optunaSortKey = "value";
let _optunaSortDir = "desc";

function sortOptunaTrials(key) {
  if (_optunaSortKey === key) {
    _optunaSortDir = _optunaSortDir === "asc" ? "desc" : "asc";
  } else {
    _optunaSortKey = key;
    // trial_number sorts asc by default; everything else desc
    _optunaSortDir = key === "trial_number" ? "asc" : "desc";
  }
  const table = document.querySelector("#optuna-trials-body")?.closest("table");
  if (table && table._cachedTrials) {
    renderOptunaTrials(table._cachedTrials);
  }
}

function _updateOptunaSortHeaders() {
  document.querySelectorAll(".optuna-sortable-th").forEach((th) => {
    const key = th.dataset.sort;
    const indicator = th.querySelector(".sort-indicator");
    if (!indicator) return;
    if (key === _optunaSortKey) {
      indicator.textContent = _optunaSortDir === "asc" ? " ↑" : " ↓";
      th.style.color = "#00f2fe";
    } else {
      indicator.textContent = " ↕";
      th.style.color = "";
    }
  });
}

function renderOptunaTrials(trials) {
  const tbody = document.getElementById("optuna-trials-body");
  const thead = document.getElementById("optuna-trials-head");

  // Cache on table element for re-sort without re-fetch
  const table = tbody?.closest("table");
  if (table) table._cachedTrials = trials;

  if (!trials || trials.length === 0) {
    tbody.innerHTML = `<tr><td colspan="12" class="empty-msg">No trial results available.</td></tr>`;
    return;
  }

  // Check which parameters are present in top trials
  const hasMode = trials.some((t) => t.params && "strategy_mode" in t.params);
  const hasSlow = trials.some((t) => t.params && "slow_ema" in t.params);
  const hasTrend = trials.some((t) => t.params && "trend_ema" in t.params);
  const hasPullback = trials.some((t) => t.params && "pullback_tolerance_pct" in t.params);
  const hasVwapOffset = trials.some((t) => t.params && "vwap_stop_offset_pct" in t.params);
  const hasAtr = trials.some((t) => t.params && "atr_multiplier" in t.params);

  // Sort before render — extract numeric value for comparison
  const _trialNumericValue = (t) => {
    if (typeof t.value === "number") return t.value;
    if (typeof t.value === "object" && t.value !== null) return Object.values(t.value)[0] ?? 0;
    return 0;
  };
  const sorted = [...trials].sort((a, b) => {
    let aVal, bVal;
    if (_optunaSortKey === "value") {
      aVal = _trialNumericValue(a); bVal = _trialNumericValue(b);
    } else if (_optunaSortKey === "trial_number") {
      aVal = a.trial_number ?? 0; bVal = b.trial_number ?? 0;
    } else {
      // param key
      aVal = (a.params || {})[_optunaSortKey] ?? "";
      bVal = (b.params || {})[_optunaSortKey] ?? "";
    }
    const cmp = (typeof aVal === "number" && typeof bVal === "number")
      ? aVal - bVal
      : String(aVal).localeCompare(String(bVal), undefined, { numeric: true });
    return _optunaSortDir === "asc" ? cmp : -cmp;
  });

  // Helper for sortable th
  const sth = (label, sortKey, extra = "") =>
    `<th class="optuna-sortable-th" data-sort="${sortKey}" style="cursor:pointer;user-select:none;white-space:nowrap;${extra}">${label} <span class="sort-indicator"></span></th>`;

  let headCols = sth("Trial #", "trial_number") + sth("Target Score", "value");
  if (hasMode) headCols += `<th>Setup Mode</th>`;
  headCols += sth("Fast EMA", "fast_ema");
  if (hasSlow) headCols += sth("Slow EMA", "slow_ema");
  if (hasTrend) headCols += sth("Trend EMA", "trend_ema");
  if (hasPullback) headCols += sth("Pullback Tol", "pullback_tolerance_pct");
  headCols += sth("Vol Mult", "volume_multiplier") + sth("VWAP Slope", "vwap_slope_min");
  if (hasAtr) headCols += sth("ATR Mult", "atr_multiplier");
  if (hasVwapOffset) headCols += sth("VWAP Offset", "vwap_stop_offset_pct");
  headCols += sth("R:R Ratio", "risk_reward_ratio") + `<th>Validation</th><th>Actions</th>`;

  if (thead) thead.innerHTML = `<tr>${headCols}</tr>`;

  tbody.innerHTML = sorted
    .map((t, idx) => {
      const p = t.params || {};
      let rowHtml = `<td style="font-weight: 600;">#${t.trial_number}</td>`;

      // Format trial value: handle both single-objective (number) and multi-objective (object)
      let valueDisplay = "N/A";
      if (typeof t.value === "object" && t.value !== null) {
        // Multi-objective: display as comma-separated values
        const values = Object.values(t.value).map(v => (typeof v === 'number' ? v.toFixed(2) : v));
        valueDisplay = values.join(" / ");
      } else if (typeof t.value === "number") {
        // Single-objective: display as-is
        valueDisplay = t.value.toFixed(4);
      } else if (t.value !== null && t.value !== undefined) {
        valueDisplay = String(t.value);
      }
      rowHtml += `<td style="color: #00f5a0; font-weight: 700;">${valueDisplay}</td>`;
      if (hasMode) {
        const modeBadge =
          p.strategy_mode === "multi_ema"
            ? `<span class="badge" style="color: #b87cf8;">Multi-EMA</span>`
            : p.strategy_mode === "pullback"
              ? `<span class="badge" style="color: #ffaa00;">Pullback</span>`
              : `<span class="badge" style="color: #00f2fe;">Crossover</span>`;
        rowHtml += `<td>${modeBadge}</td>`;
      }
      rowHtml += `<td>${p.fast_ema !== undefined ? p.fast_ema : "-"}</td>`;
      if (hasSlow) rowHtml += `<td>${p.slow_ema !== undefined ? p.slow_ema : "-"}</td>`;
      if (hasTrend) rowHtml += `<td>${p.trend_ema !== undefined ? p.trend_ema : "-"}</td>`;
      if (hasPullback) rowHtml += `<td>${p.pullback_tolerance_pct !== undefined ? p.pullback_tolerance_pct + "%" : "-"}</td>`;
      rowHtml += `<td>${p.volume_multiplier !== undefined ? p.volume_multiplier + "x" : "-"}</td>`;
      rowHtml += `<td>${p.vwap_slope_min !== undefined ? p.vwap_slope_min : "-"}</td>`;
      if (hasAtr) rowHtml += `<td>${p.atr_multiplier !== undefined ? p.atr_multiplier + "x" : "-"}</td>`;
      if (hasVwapOffset) rowHtml += `<td>${p.vwap_stop_offset_pct !== undefined ? p.vwap_stop_offset_pct + "%" : "-"}</td>`;
      rowHtml += `<td>${p.risk_reward_ratio !== undefined ? p.risk_reward_ratio + "x" : "-"}</td>`;

      const lastRes = window._lastOptimizationResult;
      const isMulti = lastRes ? lastRes.optimization_mode === "multi_objective" : false;
      const multiMetrics = isMulti ? lastRes.pareto_objectives : null;
      const targetMet = isMulti ? (multiMetrics ? multiMetrics[0] : "sharpe_ratio") : (lastRes?.target_metric || null);

      const fullTrialParams = { ...(window._lastOptimizationResult?.base_params || getStrategyParamsFromUI()), ...p };
      const rowDataAttr = encodeURIComponent(JSON.stringify(fullTrialParams));
      const trialNum = t.trial_number ?? idx;
      const cacheKey = `optuna_trial_${trialNum}`;
      window._validationContextCache[cacheKey] = {
        reportKey: cacheKey,
        trialIdx: idx,
        symbol: document.getElementById("symbol-input")?.value?.trim() || "BTC/USD",
        timeframe: document.getElementById("timeframe-select")?.value || "5m",
        exchange: document.getElementById("exchange-select")?.value || "kucoin",
        params: fullTrialParams,
        targetMetric: targetMet,
        enableMulti: isMulti,
        multiMetrics: multiMetrics,
      };

      const cachedReport = window._validationReportCache[cacheKey];
      if (cachedReport) {
        rowHtml += `<td id="trial-val-cell-${trialNum}" style="text-align:center;">
          <div style="display: inline-flex; align-items: center; justify-content: center; gap: 4px;">
            ${getValidationBadgeHtml(cachedReport.status, cachedReport.overall_score, cacheKey)}
            <button class="btn-secondary btn-xs revalidate-trial-row-btn"
                    data-trial-idx="${trialNum}"
                    data-params="${rowDataAttr}"
                    style="padding: 1px 5px; font-size: 0.68rem; cursor: pointer; border-color: rgba(0, 242, 254, 0.3); color: var(--accent-cyan);"
                    title="Re-run 3-Gate Robustness Check">
              🔄
            </button>
          </div>
        </td>`;
      } else {
        rowHtml += `<td id="trial-val-cell-${trialNum}" style="text-align:center;">
          <button class="btn-secondary btn-sm validate-trial-row-btn" data-trial-idx="${trialNum}" data-params="${rowDataAttr}" style="padding: 3px 8px; font-size: 0.72rem; cursor: pointer; border-color: rgba(0, 242, 254, 0.4); color: var(--accent-cyan);">🔬 Validate</button>
        </td>`;
      }
      // Actions cell: Apply + Deploy buttons
      rowHtml += `<td style="white-space:nowrap;">
        <button class="btn-primary btn-sm apply-trial-row-btn" data-trial-idx="${trialNum}" style="padding: 3px 8px; font-size: 0.72rem; cursor: pointer; margin-right: 4px;" title="Load into top panel and Apply to Strategy">⚡ Apply</button>
        <button class="btn-success btn-sm deploy-trial-row-btn" data-trial-idx="${trialNum}" style="padding: 3px 8px; font-size: 0.72rem; cursor: pointer;">🚀 Deploy</button>
      </td>`;
      return `<tr id="optuna-trial-row-${trialNum}" class="optuna-trial-row" style="cursor:pointer;" title="Click row to load Trial #${t.trial_number} into the panel">${rowHtml}</tr>`;
    })
    .join("");

  // Attach event handlers to per-trial validate & revalidate buttons
  const attachTrialValListeners = () => {
    const valBtns = tbody.querySelectorAll(".validate-trial-row-btn, .revalidate-trial-row-btn");
    valBtns.forEach((b) => {
      b.onclick = async (e) => {
        const idx = parseInt(e.currentTarget.getAttribute("data-trial-idx"), 10);
        const rawParams = decodeURIComponent(e.currentTarget.getAttribute("data-params") || "{}");
        const p = JSON.parse(rawParams);
        const cell = document.getElementById(`trial-val-cell-${idx}`);
        if (!cell) return;
        e.currentTarget.innerHTML = "⏳";
        e.currentTarget.disabled = true;
        try {
          const sym = document.getElementById("symbol-input")?.value?.trim() || "BTC/USD";
          const tf = document.getElementById("timeframe-select")?.value || "5m";
          const ex = document.getElementById("exchange-select")?.value || "kucoin";
          const lastRes = window._lastOptimizationResult;
          const isMulti = lastRes ? lastRes.optimization_mode === "multi_objective" : false;
          const multiMetrics = isMulti ? lastRes.pareto_objectives : null;
          const targetMet = isMulti ? (multiMetrics ? multiMetrics[0] : "sharpe_ratio") : (lastRes?.target_metric || null);
          const fullParams = { ...(window._lastOptimizationResult?.base_params || getStrategyParamsFromUI()), ...p };
          const report = await validateStrategyConfig(sym, tf, ex, fullParams, targetMet, isMulti, multiMetrics);
          const cacheKey = `optuna_trial_${idx}`;
          window._validationReportCache[cacheKey] = report;
          window._validationContextCache[cacheKey] = {
            reportKey: cacheKey,
            trialIdx: idx,
            symbol: sym,
            timeframe: tf,
            exchange: ex,
            params: fullParams,
            targetMetric: targetMet,
            enableMulti: isMulti,
            multiMetrics: multiMetrics,
          };
          const rowDataAttr = encodeURIComponent(JSON.stringify(fullParams));
          cell.innerHTML = `<div style="display: inline-flex; align-items: center; justify-content: center; gap: 4px;">
            ${getValidationBadgeHtml(report.status, report.overall_score, cacheKey)}
            <button class="btn-secondary btn-xs revalidate-trial-row-btn"
                    data-trial-idx="${idx}"
                    data-params="${rowDataAttr}"
                    style="padding: 1px 5px; font-size: 0.68rem; cursor: pointer; border-color: rgba(0, 242, 254, 0.3); color: var(--accent-cyan);"
                    title="Re-run 3-Gate Robustness Check">
              🔄
            </button>
          </div>`;
          attachTrialValListeners();
        } catch (err) {
          console.error("[Optuna Trial Validation Error]", err);
          cell.innerHTML = `<span style="color: #ff007a; font-size: 0.72rem; cursor: pointer;" title="${err.message || err}">Error</span>`;
        }
      };
    });
  };
  attachTrialValListeners();

  // Attach event handlers to per-trial deploy buttons
  const rowBtns = tbody.querySelectorAll(".deploy-trial-row-btn");
  rowBtns.forEach((b) => {
    b.addEventListener("click", (e) => {
      const trialNum = parseInt(e.target.getAttribute("data-trial-idx"), 10);
      const trial = trials.find(t => t.trial_number === trialNum);
      const cacheKey = `optuna_trial_${trialNum}`;
      const report = window._validationReportCache[cacheKey];
      if (trial && trial.params) {
        e.target.innerHTML = "⏳";
        e.target.disabled = true;
        // Extract numeric value for deployment (use first objective for multi-objective)
        let deployValue = trial.value;
        if (typeof trial.value === "object" && trial.value !== null) {
          deployValue = Object.values(trial.value)[0];
        }
        deployOptunaToPaper(trial.params, deployValue, report).then(() => {
          e.target.innerHTML = "✅";
          setTimeout(() => {
            e.target.innerHTML = "🚀 Deploy";
            e.target.disabled = false;
          }, 2500);
        });
      }
    });
  });

  // --- Per-trial Apply buttons: load into panel then run backtest ---
  const applyBtns = tbody.querySelectorAll(".apply-trial-row-btn");
  applyBtns.forEach((b) => {
    b.addEventListener("click", (e) => {
      e.stopPropagation(); // don't also fire row-click
      const trialNum = parseInt(e.currentTarget.getAttribute("data-trial-idx"), 10);
      const trial = trials.find(t => t.trial_number === trialNum);
      if (!trial) return;
      loadTrialIntoPanel(trial, idx);
      // Brief flash on button then trigger Apply to Strategy
      b.innerHTML = "⏳";
      b.disabled = true;
      setTimeout(() => {
        b.innerHTML = "⚡ Apply";
        b.disabled = false;
        applyBestOptunaParams();
      }, 150);
    });
  });

  // --- Row-click: load trial into panel (ignore clicks on buttons) ---
  tbody.querySelectorAll(".optuna-trial-row").forEach((row) => {
    row.addEventListener("click", (e) => {
      if (e.target.closest("button")) return; // let button handlers fire instead
      const trialNum = parseInt(row.id.replace("optuna-trial-row-", ""), 10);
      const trial = trials.find(t => t.trial_number === trialNum);
      if (trial) loadTrialIntoPanel(trial, trialNum);
    });
  });

  // Auto-highlight first row as the current "best" selection
  const firstRow = document.getElementById("optuna-trial-row-0");
  if (firstRow) firstRow.classList.add("optuna-trial-row--selected");

  // Wire sort click handlers and update indicators
  document.querySelectorAll(".optuna-sortable-th").forEach((th) => {
    th.onclick = () => sortOptunaTrials(th.dataset.sort);
  });
  _updateOptunaSortHeaders();
}

async function runWalkForwardAnalysis() {
  const btn = document.getElementById("run-wfo-btn");
  if (!btn) return;
  btn.disabled = true;
  btn.innerHTML = `<span class="btn-icon">⏳</span> Analyzing Windows...`;

  try {
    const { limit, days } = getDataRangeParams();
    const baseParams = getStrategyParamsFromUI();
    const wfoDaysRaw = document.getElementById("wfo-days-input")?.value?.trim();
    const wfoDays = wfoDaysRaw ? parseInt(wfoDaysRaw, 10) : null;
    const optunaTrials = parseInt(document.getElementById("optuna-trials-input")?.value, 10);
    const valTrials = parseInt(document.getElementById("setting-val-trials")?.value, 10);
    // val_trials (Settings → Validation Trials) is the authoritative WFO budget;
    // fall back to the Optuna trials input, then 100 as a last resort.
    let nTrialsTotal = valTrials || optunaTrials || 100;
    const numWindows = parseInt(document.getElementById("wfo-windows-input")?.value, 10) || 0;

    const payload = {
      exchange: document.getElementById("exchange-select").value,
      symbol: document.getElementById("symbol-input").value.trim(),
      timeframe: document.getElementById("timeframe-select").value,
      limit: limit,
      days: wfoDays && wfoDays > 0 ? wfoDays : days,
      ...baseParams,
      num_windows: numWindows,
      trials_per_window: 0, // 0 = auto-derive from n_trials_total / num_windows
      n_trials_total: nTrialsTotal,
    };

    const response = await fetch("/api/walk_forward", {
      method: "POST",
      headers: getAuthHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify(payload),
    });

    if (!response.ok) {
      const err = await response.json();
      alert(`WFO Error: ${err.detail || "Server error"}`);
      return;
    }

    const data = await response.json();
    const res = data.results;

    document.getElementById("wfo-wfe-val").textContent = `${res.walk_forward_efficiency_pct}%`;
    document.getElementById("wfo-is-ret").textContent = `${res.avg_in_sample_return_pct}%`;
    document.getElementById("wfo-oos-ret").textContent = `${res.avg_out_of_sample_return_pct}%`;
    document.getElementById("wfo-windows-count").textContent = res.window_count;
    // Show effective auto-scaled parameters if the server computed them
    const effWindows = res.effective_num_windows ?? res.window_count;
    const effTrials = res.effective_trials_per_window ?? "--";
    const autoTag = res.auto_scaled_params ? " (auto)" : "";
    const wfoSubtitle = document.getElementById("wfo-config-subtitle");
    if (wfoSubtitle) {
      wfoSubtitle.textContent = `${effWindows} windows × ${effTrials} trials/window${autoTag}`;
    }

    renderWfoChart(res.window_details);
    renderWfoTable(res.window_details);

    // Switch tab to WFO
    document.querySelector('[data-tab="tab-wfo"]').click();
  } catch (error) {
    alert(`WFO Request error: ${error.message}`);
  } finally {
    btn.disabled = false;
    btn.innerHTML = `<span class="btn-icon">🔄</span> Run Walk-Forward Analysis`;
  }
}

function renderWfoChart(windowDetails) {
  if (!windowDetails || windowDetails.length === 0) return;

  const winLabels = windowDetails.map((w) => `Win ${w.window_index}`);
  const isReturns = windowDetails.map((w) => w.is_return_pct);
  const oosReturns = windowDetails.map((w) => w.oos_return_pct);

  const traceIS = {
    x: winLabels,
    y: isReturns,
    name: "In-Sample (Train)",
    type: "bar",
    marker: { color: "rgba(0, 242, 254, 0.7)" },
  };

  const traceOOS = {
    x: winLabels,
    y: oosReturns,
    name: "Out-of-Sample (Test)",
    type: "bar",
    marker: { color: "rgba(121, 40, 202, 0.8)" },
  };

  const layout = {
    barmode: "group",
    paper_bgcolor: "transparent",
    plot_bgcolor: "transparent",
    font: { color: "#8a99ad", family: "Inter, sans-serif" },
    margin: { l: 40, r: 40, t: 20, b: 30 },
    legend: { orientation: "h", y: 1.1 },
    xaxis: { gridcolor: "rgba(255, 255, 255, 0.05)" },
    yaxis: { title: "Return (%)", gridcolor: "rgba(255, 255, 255, 0.05)" },
  };

  Plotly.newPlot("wfo-chart", [traceIS, traceOOS], layout, { responsive: true, displayModeBar: false });
}

function renderWfoTable(windowDetails) {
  const tbody = document.getElementById("wfo-windows-body");
  if (!windowDetails || windowDetails.length === 0) {
    tbody.innerHTML = `<tr><td colspan="8" class="empty-msg">No Walk-Forward windows data.</td></tr>`;
    return;
  }

  tbody.innerHTML = windowDetails
    .map((w) => {
      const oosColor = w.oos_return_pct >= 0 ? "#00f5a0" : "#ff007a";
      return `
      <tr>
        <td style="font-weight: 600;">Win #${w.window_index}</td>
        <td style="font-size: 11px;">${w.is_period}</td>
        <td style="font-size: 11px;">${w.oos_period}</td>
        <td style="color: #00f2fe; font-weight: 600;">${w.is_return_pct}%</td>
        <td style="color: ${oosColor}; font-weight: 700;">${w.oos_return_pct}%</td>
        <td>${w.oos_win_rate}%</td>
        <td>${w.oos_max_drawdown_pct}%</td>
        <td>${w.oos_sharpe}</td>
      </tr>
    `;
    })
    .join("");
}

async function runMonteCarloSimulation() {
  const btn = document.getElementById("run-mc-btn");
  if (!btn) return;
  btn.disabled = true;
  btn.innerHTML = `<span class="btn-icon">⏳</span> Simulating Paths...`;

  try {
    const { limit, days } = getDataRangeParams();
    const baseParams = getStrategyParamsFromUI();
    const payload = {
      exchange: document.getElementById("exchange-select").value,
      symbol: document.getElementById("symbol-input").value.trim(),
      timeframe: document.getElementById("timeframe-select").value,
      limit: limit,
      days: days,
      ...baseParams,
      num_simulations: parseInt(document.getElementById("mc-sims-input").value, 10),
    };

    const response = await fetch("/api/monte_carlo", {
      method: "POST",
      headers: getAuthHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify(payload),
    });

    if (!response.ok) {
      const err = await response.json();
      alert(`Monte Carlo Error: ${err.detail || "Server error"}`);
      return;
    }

    const data = await response.json();
    const res = data.results;

    document.getElementById("mc-ruin-val").textContent = `${res.risk_of_ruin_pct}%`;
    document.getElementById("mc-p50-profit").textContent = `$${res.net_profit_percentiles.p50.toLocaleString()}`;
    document.getElementById("mc-p50-equity").textContent = `Equity: $${res.final_equity_percentiles.p50.toLocaleString()}`;
    document.getElementById("mc-p5-dd").textContent = `${res.max_drawdown_percentiles.p5}%`;
    document.getElementById("mc-p95-profit").textContent = `$${res.net_profit_percentiles.p95.toLocaleString()}`;

    renderMonteCarloFanChart(res.equity_curves_percentiles);
    renderMonteCarloHistogram(res.drawdown_histogram);

    // Switch tab to Monte Carlo
    document.querySelector('[data-tab="tab-mc"]').click();
  } catch (error) {
    alert(`Monte Carlo Request error: ${error.message}`);
  } finally {
    btn.disabled = false;
    btn.innerHTML = `<span class="btn-icon">🎲</span> Run Monte Carlo Simulator`;
  }
}

function renderMonteCarloFanChart(curves) {
  if (!curves || !curves.p50) return;

  const steps = curves.p50.map((d) => d.step);
  const p5 = curves.p5.map((d) => d.equity);
  const p50 = curves.p50.map((d) => d.equity);
  const p95 = curves.p95.map((d) => d.equity);

  const traceP95 = {
    x: steps,
    y: p95,
    name: "95th Percentile (Best 5%)",
    type: "scatter",
    mode: "lines",
    line: { color: "#00f5a0", width: 1.5, dash: "dot" },
  };

  const traceP50 = {
    x: steps,
    y: p50,
    name: "50th Percentile (Median)",
    type: "scatter",
    mode: "lines",
    line: { color: "#00f2fe", width: 2.5 },
  };

  const traceP5 = {
    x: steps,
    y: p5,
    name: "5th Percentile (Worst 5%)",
    type: "scatter",
    mode: "lines",
    line: { color: "#ff007a", width: 1.5, dash: "dot" },
  };

  const layout = {
    paper_bgcolor: "transparent",
    plot_bgcolor: "transparent",
    font: { color: "#8a99ad", family: "Inter, sans-serif" },
    margin: { l: 50, r: 40, t: 20, b: 30 },
    legend: { orientation: "h", y: 1.15 },
    xaxis: { title: "Simulation Step %", gridcolor: "rgba(255, 255, 255, 0.05)" },
    yaxis: { title: "Equity ($)", gridcolor: "rgba(255, 255, 255, 0.05)" },
  };

  Plotly.newPlot("mc-fan-chart", [traceP95, traceP50, traceP5], layout, { responsive: true, displayModeBar: false });
}

function renderMonteCarloHistogram(histogram) {
  if (!histogram || histogram.length === 0) return;

  const binLabels = histogram.map((h) => `${h.bin_start}-${h.bin_end}%`);
  const counts = histogram.map((h) => h.count);

  const trace = {
    x: binLabels,
    y: counts,
    type: "bar",
    marker: {
      color: "rgba(255, 0, 122, 0.6)",
      line: { color: "#ff007a", width: 1 },
    },
  };

  const layout = {
    paper_bgcolor: "transparent",
    plot_bgcolor: "transparent",
    font: { color: "#8a99ad", family: "Inter, sans-serif" },
    margin: { l: 40, r: 40, t: 20, b: 50 },
    xaxis: { title: "Max Drawdown % Range", tickangle: -45, gridcolor: "rgba(255, 255, 255, 0.05)" },
    yaxis: { title: "Frequency Count", gridcolor: "rgba(255, 255, 255, 0.05)" },
  };

  Plotly.newPlot("mc-histogram-chart", [trace], layout, { responsive: true, displayModeBar: false });
}

// ---------------------------------------------------------------------------
// Batch Sweep
// ---------------------------------------------------------------------------

let _batchPollTimer = null;

async function startBatchSweep() {
  const symbols = document.getElementById("batch-symbols-input").value
    .split(",").map(s => s.trim()).filter(Boolean);
  const timeframes = [...document.querySelectorAll(".batch-tf-check:checked")]
    .map(el => el.value);
  const nTrials = parseInt(document.getElementById("batch-trials-input").value, 10);
  const objectiveModeEl = document.getElementById("batch-objective-mode-select");
  const objectiveMode = objectiveModeEl ? objectiveModeEl.value : "single";
  const targetMetric = document.getElementById("batch-metric-select").value;
  const multiMetricValue = document.getElementById("batch-multi-metric-select")?.value || "sharpe_ratio|max_drawdown_pct";
  const multiMetricList = multiMetricValue.split("|").filter(Boolean);
  const exchange = document.getElementById("exchange-select").value;

  if (!symbols.length || !timeframes.length) {
    alert("Please enter at least one symbol and select at least one timeframe.");
    return;
  }

  if (objectiveMode === "single" && targetMetric === "all_metrics" && timeframes.length !== 1) {
    alert("All Metrics mode requires exactly one timeframe. Please select only 1 timeframe.");
    return;
  }

  if (objectiveMode === "multi" && multiMetricValue === "all_combinations" && timeframes.length !== 1) {
    alert("All Combinations mode requires exactly one timeframe. Please select only 1 timeframe.");
    return;
  }

  const isAllMulti = objectiveMode === "multi" && multiMetricValue === "all_combinations";
  const payloadTargetMetric = objectiveMode === "multi" ? (isAllMulti ? "all_combinations" : multiMetricValue) : targetMetric;
  const payloadMultiMetrics = objectiveMode === "multi" ? (isAllMulti ? ["all_combinations"] : multiMetricList) : undefined;

  const btn = document.getElementById("batch-run-btn");
  btn.disabled = true;
  btn.innerHTML = `<span class="btn-icon">⏳</span> Running...`;

  document.getElementById("batch-progress-wrap").style.display = "block";
  document.getElementById("batch-progress-bar").style.width = "0%";
  document.getElementById("batch-progress-label").textContent = "Starting sweep...";
  document.getElementById("batch-progress-pct").textContent = "0%";

  const tradeDirection = document.getElementById("batch-trade-direction-select")?.value || "long_only";

  try {
    const resp = await fetch("/api/batch_optimize", {
      method: "POST",
      headers: getAuthHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify({
        symbols, timeframes, exchange, n_trials: nTrials,
        target_metric: payloadTargetMetric,
        enable_multi_objective: objectiveMode === "multi",
        multi_objective_metrics: payloadMultiMetrics,
        enable_hard_drawdown_constraint: document.getElementById("batch-enable-dd-constraint")?.checked || false,
        max_drawdown_constraint_pct: parseFloat(document.getElementById("batch-dd-constraint-input")?.value || "25"),
        enable_max_holding_bars: document.getElementById("batch-enable-max-hold")?.checked || false,
        max_holding_bars_min: parseInt(document.getElementById("batch-max-hold-min")?.value || "4", 10),
        max_holding_bars_max: parseInt(document.getElementById("batch-max-hold-max")?.value || "96", 10),
        days: getBatchDataRangeDays(),
        strategy_mode: document.getElementById("batch-strategy-mode-select")?.value || "auto",
        min_trades: getSettings().min_trades,
        fast_ema_min: getSettings().fast_ema_min,
        fast_ema_max: getSettings().fast_ema_max,
        slow_ema_min: getSettings().slow_ema_min,
        slow_ema_max: getSettings().slow_ema_max,
        trend_ema_min: getSettings().trend_ema_min,
        trend_ema_max: getSettings().trend_ema_max,
        volume_multiplier_min: getSettings().volume_multiplier_min,
        volume_multiplier_max: getSettings().volume_multiplier_max,
        atr_multiplier_min: getSettings().atr_multiplier_min,
        atr_multiplier_max: getSettings().atr_multiplier_max,
        trailing_stop_min: getSettings().trailing_stop_min,
        trailing_stop_max: getSettings().trailing_stop_max,
        risk_reward_min: getSettings().risk_reward_min,
        risk_reward_max: getSettings().risk_reward_max,
        vwap_slope_max_bound: getSettings().vwap_slope_max_bound,
        n_jobs: getSettings().n_jobs !== undefined ? getSettings().n_jobs : 1,
        n_startup_trials: getSettings().n_startup_trials,
        trade_direction: tradeDirection
      }),
    });
    if (!resp.ok) {
      const err = await resp.json();
      alert(`Batch Sweep Error: ${err.detail || "Server error"}`);
      btn.disabled = false;
      btn.innerHTML = `<span class="btn-icon">&#x1f3c6;</span> Start Batch Sweep`;
      return;
    }

    // Start polling progress
    if (_batchPollTimer) clearInterval(_batchPollTimer);
    _batchPollTimer = setInterval(async () => {
      try {
        const statusResp = await fetch("/api/batch_status", { headers: getAuthHeaders() });
        const state = await statusResp.json();
        const pct = state.total > 0
          ? Math.round((state.done / state.total) * 100)
          : 0;
        document.getElementById("batch-progress-bar").style.width = `${pct}%`;
        document.getElementById("batch-progress-pct").textContent = `${pct}%`;
        document.getElementById("batch-progress-label").textContent =
          state.current_combo || "Working...";

        // Only treat as complete if not running AND (done >= total OR there's an error)
        // This prevents showing "Complete!" from stale state when a new sweep starts
        const isTrulyComplete = !state.running && (state.done >= state.total || state.error);
        if (isTrulyComplete) {
          clearInterval(_batchPollTimer);
          _batchPollTimer = null;
          btn.disabled = false;
          btn.innerHTML = `<span class="btn-icon">&#x1f3c6;</span> Start Batch Sweep`;
          if (state.error) {
            document.getElementById("batch-progress-label").textContent = `Failed: ${state.error}`;
            alert(`Batch Sweep Error: ${state.error}`);
          } else {
            document.getElementById("batch-progress-label").textContent = "Complete!";
          }
        }
        // Auto-validate only the recent sweep results if the checkbox is checked
        // Only trigger on true completion (done >= total)
        const autoValChecked = document.getElementById("batch-auto-validate")?.checked;
        if (autoValChecked && state.done >= state.total) {
          const autoValLabel = document.getElementById("batch-progress-label");
          if (autoValLabel) autoValLabel.textContent = "Validating results...";
          autoValidateBatchResults({
            targetMetric: payloadTargetMetric,
            symbols: symbols,
            timeframes: timeframes,
          })
            .then(() => {
              if (autoValLabel) autoValLabel.textContent = "Complete!";
            })
            .catch((err) => {
              console.warn("[Batch] Auto-Validate warning:", err);
              if (autoValLabel) autoValLabel.textContent = "Complete (validation skipped)";
            });
        }
        // Auto-load results
        await loadBatchResults();
        // Switch to batch tab
        document.querySelector('[data-tab="tab-batch"]').click();
      } catch (_) { }
    }, 2000);

  } catch (err) {
    alert(`Batch Sweep Error: ${err.message}`);
    btn.disabled = false;
    btn.innerHTML = `<span class="btn-icon">&#x1f3c6;</span> Start Batch Sweep`;
  }
}

async function autoValidateBatchResults(sweepContext = {}) {
  try {
    // For multi-objective, the database stores only the first metric as target_metric
    let targetMetricForQuery = sweepContext.targetMetric;
    if (targetMetricForQuery && targetMetricForQuery.includes("|")) {
      targetMetricForQuery = targetMetricForQuery.split("|")[0];
    }
    const valTrials = getValidationTrialsFromUI();
    const valCriteria = getValidationCriteriaFromUI();
    const resp = await fetch("/api/batch_results/auto-validate", {
      method: "POST",
      headers: getAuthHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify({
        target_metric: targetMetricForQuery,
        symbols: sweepContext.symbols,
        timeframes: sweepContext.timeframes,
        n_trials: valTrials,
        criteria: valCriteria,
      }),
    });
    if (!resp.ok) {
      const err = await resp.json();
      console.warn("[Auto-Validate] Server error:", err.detail || resp.status);
      return;
    }
    const data = await resp.json();
    console.log("[Auto-Validate] Validated", data.validated_count, "results.");
    // Refresh leaderboard to show updated badges
    await loadBatchResults();
  } catch (e) {
    console.warn("[Auto-Validate] Client error:", e);
  }
}

async function refreshAllBatchValidations() {
  const btn = document.getElementById("batch-refresh-val-btn");
  const label = document.getElementById("batch-progress-label");
  if (btn) {
    btn.disabled = true;
    btn.innerHTML = `<span class="btn-icon">&#x23f3;</span> Refreshing...`;
  }
  if (label) label.textContent = "Refreshing validation on all saved results...";

  try {
    // Call auto-validate without filters to process ALL saved results with current user settings
    const valTrials = getValidationTrialsFromUI();
    const valCriteria = getValidationCriteriaFromUI();
    const resp = await fetch("/api/batch_results/auto-validate", {
      method: "POST",
      headers: getAuthHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify({
        n_trials: valTrials,
        criteria: valCriteria,
      }),  // No filters = all results
    });
    if (!resp.ok) {
      const err = await resp.json();
      throw new Error(err.detail || `Server error: ${resp.status}`);
    }
    const data = await resp.json();
    console.log("[Refresh Validation] Validated", data.validated_count, "of", data.total_results, "results.");
    if (label) label.textContent = `Complete! Validated ${data.validated_count} results.`;
    // Refresh leaderboard to show updated badges
    await loadBatchResults();
  } catch (e) {
    console.warn("[Refresh Validation] Error:", e);
    if (label) label.textContent = `Error: ${e.message}`;
    alert(`Refresh Validation failed: ${e.message}`);
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.innerHTML = `<span class="btn-icon">&#x1f501;</span> Refresh Validation`;
    }
  }
}

async function loadBatchResults() {
  try {
    // Loading saved results should not depend on the metric currently selected
    // for the next sweep. Persisted rows identify their own target metric.
    const resp = await fetch("/api/batch_results?limit=100", { headers: getAuthHeaders() });
    const data = await resp.json();
    renderBatchLeaderboard(data.results || []);
    const countEl = document.getElementById("batch-result-count");
    if (countEl) countEl.textContent = `${data.count || 0} result${data.count !== 1 ? "s" : ""}`;
  } catch (err) {
    alert(`Could not load batch results: ${err.message}`);
  }
}

function getBatchValidationCellHtml(r) {
  const cacheKey = `batch_${r.id}`;
  const rowDataAttr = encodeURIComponent(r.best_params || "{}");
  const targetMetric = r.target_metric || null;
  const isMulti = Boolean(targetMetric && targetMetric.includes("|"));
  const multiMetrics = isMulti ? targetMetric.split("|").filter(Boolean) : null;
  const resolvedTarget = isMulti ? multiMetrics[0] : targetMetric;
  window._validationContextCache[cacheKey] = {
    reportKey: cacheKey,
    batchId: r.id,
    symbol: r.symbol,
    timeframe: r.timeframe,
    exchange: r.exchange,
    params: typeof r.best_params === "string" ? JSON.parse(r.best_params || "{}") : (r.best_params || {}),
    targetMetric: resolvedTarget,
    enableMulti: isMulti,
    multiMetrics: multiMetrics,
  };

  if (r.validation_json) {
    try {
      const rep = typeof r.validation_json === "string" ? JSON.parse(r.validation_json) : r.validation_json;
      window._validationReportCache[cacheKey] = rep;
    } catch (_) { }
  }
  if (r.validation_status) {
    let score = null;
    const rep = window._validationReportCache[cacheKey];
    if (rep && rep.overall_score !== undefined) score = rep.overall_score;
    return `<div style="display: inline-flex; align-items: center; justify-content: center; gap: 4px;">
      ${getValidationBadgeHtml(r.validation_status, score, cacheKey)}
      <button class="btn-secondary btn-xs"
              style="padding: 1px 5px; font-size: 0.68rem; cursor: pointer; border-color: rgba(0, 242, 254, 0.3); color: var(--accent-cyan);"
              onclick="validateBatchLeaderboardRow(${r.id}, '${r.symbol}', '${r.timeframe}', '${r.exchange}', this.dataset.params, this, '${r.target_metric || ''}')"
              data-params="${rowDataAttr}"
              title="Re-run 3-Gate Robustness Check">
        🔄
      </button>
    </div>`;
  }
  return `<button class="btn-secondary btn-sm"
                  style="padding: 3px 8px; font-size: 0.72rem; cursor: pointer; border-color: rgba(0, 242, 254, 0.4); color: var(--accent-cyan);"
                  onclick="validateBatchLeaderboardRow(${r.id}, '${r.symbol}', '${r.timeframe}', '${r.exchange}', this.dataset.params, this, '${r.target_metric || ''}')"
                  data-params="${rowDataAttr}"
                  title="Run 3-Gate Robustness Check">
            🔬 Validate
          </button>`;
}

async function validateBatchLeaderboardRow(resultId, symbol, timeframe, exchange, encodedParams, btn, targetMetric = null) {
  console.log("[DEBUG] validateBatchLeaderboardRow called:", { resultId, symbol, timeframe, exchange, targetMetric });
  if (btn) {
    btn.innerHTML = "⏳";
    btn.disabled = true;
  }
  const cell = document.getElementById(`batch-val-cell-${resultId}`);
  // Hoisted out of try: the catch-block error UI references these (re-render
  // buttons need resolvedTarget), so they must survive a validation failure.
  const isMulti = Boolean(targetMetric && targetMetric.includes("|"));
  const multiMetrics = isMulti ? targetMetric.split("|").filter(Boolean) : null;
  const resolvedTarget = isMulti ? multiMetrics[0] : (targetMetric || null);
  try {
    const rawParams = decodeURIComponent(encodedParams || "{}");
    const p = JSON.parse(rawParams);
    const report = await validateStrategyConfig(symbol, timeframe, exchange, p, resolvedTarget, isMulti, multiMetrics);
    const cacheKey = `batch_${resultId}`;
    window._validationReportCache[cacheKey] = report;
    window._validationContextCache[cacheKey] = {
      reportKey: cacheKey,
      batchId: resultId,
      symbol: symbol,
      timeframe: timeframe,
      exchange: exchange,
      params: p,
      targetMetric: resolvedTarget,
      enableMulti: isMulti,
      multiMetrics: multiMetrics,
    };

    // Save to SQLite
    await fetch(`/api/batch_results/${resultId}/validation`, {
      method: "PATCH",
      headers: getAuthHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify({
        status: report.status,
        report_json: JSON.stringify(report),
      }),
    });

    if (cell) {
      cell.innerHTML = `<div style="display: inline-flex; align-items: center; justify-content: center; gap: 4px;">
        ${getValidationBadgeHtml(report.status, report.overall_score, cacheKey)}
        <button class="btn-secondary btn-xs"
                style="padding: 1px 5px; font-size: 0.68rem; cursor: pointer; border-color: rgba(0, 242, 254, 0.3); color: var(--accent-cyan);"
                onclick="validateBatchLeaderboardRow(${resultId}, '${symbol}', '${timeframe}', '${exchange}', this.dataset.params, this, '${resolvedTarget || ''}')"
                data-params="${encodedParams}"
                title="Re-run 3-Gate Robustness Check">
          🔄
        </button>
      </div>`;
    }
  } catch (err) {
    console.error("[validateBatchLeaderboardRow Error]", err);
    if (cell) {
      const errMsg = String(err.message || err || "Validation failed").replace(/"/g, "&quot;");
      cell.innerHTML = `<div style="display: inline-flex; align-items: center; justify-content: center; gap: 4px;">
        <span style="color: #ff007a; font-size: 0.72rem; cursor: pointer;" title="${errMsg}" onclick="alert('Validation Error:\\n\\n' + this.title)">Error ⚠️</span>
        <button class="btn-secondary btn-xs"
                style="padding: 1px 5px; font-size: 0.68rem; cursor: pointer; border-color: rgba(255, 0, 122, 0.4); color: #ff007a;"
                onclick="validateBatchLeaderboardRow(${resultId}, '${symbol}', '${timeframe}', '${exchange}', this.dataset.params, this, '${resolvedTarget || ''}')"
                data-params="${encodedParams}"
                title="Retry Validation (${errMsg})">
          🔄
        </button>
      </div>`;
    }
  }
}

window.validateBatchLeaderboardRow = validateBatchLeaderboardRow;

// ---------------------------------------------------------------------------
// Leaderboard column sort state
// ---------------------------------------------------------------------------
let _batchSortKey = "best_value";
let _batchSortDir = "desc"; // "asc" | "desc"

function sortBatchLeaderboard(key) {
  if (_batchSortKey === key) {
    _batchSortDir = _batchSortDir === "asc" ? "desc" : "asc";
  } else {
    _batchSortKey = key;
    // Numeric columns default desc (higher = better), text columns default asc
    const textCols = new Set(["symbol", "timeframe", "target_metric", "run_timestamp"]);
    _batchSortDir = textCols.has(key) ? "asc" : "desc";
  }
  // Re-render using the cached rows (stored on the table element)
  const table = document.getElementById("batch-leaderboard-table");
  if (table && table._cachedRows) {
    renderBatchLeaderboard(table._cachedRows);
  }
}

function _updateLeaderboardSortHeaders() {
  document.querySelectorAll(".sortable-th").forEach((th) => {
    const key = th.dataset.sort;
    const indicator = th.querySelector(".sort-indicator");
    if (!indicator) return;
    if (key === _batchSortKey) {
      indicator.textContent = _batchSortDir === "asc" ? " ↑" : " ↓";
      th.style.color = "#00f2fe";
    } else {
      indicator.textContent = " ↕";
      th.style.color = "";
    }
  });
}

function renderBatchLeaderboard(rows) {

  const tbody = document.getElementById("batch-leaderboard-body");
  // Cache rows on the table element so re-sorting doesn't need a new fetch
  const table = document.getElementById("batch-leaderboard-table");
  if (table) table._cachedRows = rows;

  if (!rows || !rows.length) {
    tbody.innerHTML = `<tr><td colspan="15" style="padding:24px;text-align:center;color:var(--text-muted);">
      No results yet — run a Batch Sweep or click Load Saved Results.</td></tr>`;
    updateBatchSelectionState();
    return;
  }

  // Sort rows according to current sort state
  const sorted = [...rows].sort((a, b) => {
    const isNumericKey = ["best_value", "validation_score", "sharpe_ratio", "total_return_pct", "win_rate", "max_drawdown_pct", "trade_count"].includes(_batchSortKey);
    // For validation_score: fall back to parsing overall_score from validation_json if DB column is null
    const getNumericVal = (r, key) => {
      if (key === "validation_score") {
        if (r.validation_score !== null && r.validation_score !== undefined) return r.validation_score;
        try {
          const rep = typeof r.validation_json === "string" ? JSON.parse(r.validation_json) : r.validation_json;
          if (rep && typeof rep.overall_score === "number") return rep.overall_score;
        } catch (_) { }
        return null;
      }
      if (["win_rate", "total_return_pct", "max_drawdown_pct", "sharpe_ratio", "trade_count"].includes(key)) {
        if (r[key] !== null && r[key] !== undefined) return r[key];
        try {
          const rep = typeof r.validation_json === "string" ? JSON.parse(r.validation_json) : r.validation_json;
          const g1 = rep?.gate_results?.gate1?.metrics;
          if (g1 && g1[key] !== undefined && g1[key] !== null) return g1[key];
        } catch (_) { }
      }
      return r[key] ?? null;
    };
    const aVal = getNumericVal(a, _batchSortKey);
    const bVal = getNumericVal(b, _batchSortKey);
    let cmp = 0;
    if (isNumericKey) {
      const aN = (aVal === null || aVal === undefined || isNaN(aVal)) ? -Infinity : Number(aVal);
      const bN = (bVal === null || bVal === undefined || isNaN(bVal)) ? -Infinity : Number(bVal);
      cmp = aN - bN;
    } else if (typeof aVal === "number" && typeof bVal === "number") {
      cmp = aVal - bVal;
    } else {
      cmp = String(aVal).localeCompare(String(bVal), undefined, { numeric: true });
    }
    return _batchSortDir === "asc" ? cmp : -cmp;
  });

  const formatBatchMetricLabel = (m) => {
    if (!m) return "--";
    const singleLabels = {
      sharpe_ratio: "Sharpe",
      sortino_ratio: "Sortino",
      drawdown_penalized_sharpe: "DD-Penalized Sharpe",
      drawdown_penalized_sortino: "DD-Penalized Sortino",
      ulcer_index: "Ulcer Index",
      calmar_ratio: "Calmar",
      profit_factor: "Profit Factor",
      net_profit: "Net Profit ($)",
      total_return_pct: "Return %",
      win_rate: "Win %",
      max_drawdown_pct: "Max Drawdown",
    };
    if (m.includes("|")) {
      return m.split("|").map((part) => singleLabels[part] || part).join(" + ");
    }
    return singleLabels[m] || m;
  };

  tbody.innerHTML = sorted.map((r, i) => {
    const ts = r.run_timestamp
      ? new Date(r.run_timestamp).toLocaleString(undefined, { dateStyle: "short", timeStyle: "short" })
      : "--";
    // Handle best_value safely (could be null for multi-objective results)
    const bestValueNum = r.best_value !== null && r.best_value !== undefined ? r.best_value : 0;
    const valColor = bestValueNum >= 0 ? "#34d399" : "#f87171";
    const bestValueDisplay = r.best_value !== null && r.best_value !== undefined ? r.best_value.toFixed(3) : "N/A";
    const rowDataAttr = encodeURIComponent(r.best_params || "{}");

    // Extract metrics with fallback to validation_json Gate 1 metrics if null/missing
    let winRate = r.win_rate;
    let totalReturnPct = r.total_return_pct;
    let maxDrawdownPct = r.max_drawdown_pct;
    let sharpeRatio = r.sharpe_ratio;
    let tradeCount = r.trade_count;

    if (r.validation_json && (winRate === null || winRate === undefined || totalReturnPct === null || totalReturnPct === undefined)) {
      try {
        const rep = typeof r.validation_json === "string" ? JSON.parse(r.validation_json) : r.validation_json;
        const g1 = rep?.gate_results?.gate1?.metrics;
        if (g1) {
          if (winRate === null || winRate === undefined) winRate = g1.win_rate;
          if (totalReturnPct === null || totalReturnPct === undefined) totalReturnPct = g1.total_return_pct;
          if (maxDrawdownPct === null || maxDrawdownPct === undefined) maxDrawdownPct = g1.max_drawdown_pct;
          if (sharpeRatio === null || sharpeRatio === undefined) sharpeRatio = g1.sharpe_ratio;
          if (!tradeCount && g1.trade_count) tradeCount = g1.trade_count;
        }
      } catch (_) { }
    }

    return `
      <tr style="border-bottom:1px solid rgba(255,255,255,0.05);transition:background 0.15s;"
          onmouseover="this.style.background='rgba(255,255,255,0.04)'"
          onmouseout="this.style.background=''"
      >
        <td style="padding:9px 10px;"><input class="batch-result-check" type="checkbox" value="${r.id}" aria-label="Select ${r.symbol} ${r.timeframe} result" onchange="updateBatchSelectionState()"></td>
        <td style="padding:9px 10px;">
          <button class="batch-favorite-btn" type="button" title="${r.favorite ? "Remove from favorites" : "Save as favorite"}"
                  aria-label="${r.favorite ? "Remove from favorites" : "Save as favorite"}"
                  aria-pressed="${Boolean(r.favorite)}" onclick="toggleBatchFavorite(${r.id}, ${!Boolean(r.favorite)}, this)"
                  style="border:0;background:transparent;color:${r.favorite ? "#fbbf24" : "var(--text-muted)"};font-size:1.2em;cursor:pointer;padding:2px 6px;">
            ${r.favorite ? "&#9733;" : "&#9734;"}
          </button>
        </td>
        <td style="padding:9px 10px;color:var(--text-muted);">${i + 1}</td>
        <td style="padding:9px 10px;font-weight:600;">${r.symbol}</td>
        <td style="padding:9px 10px;color:var(--accent-cyan);">${r.timeframe}</td>
        <td style="padding:9px 10px;color:var(--text-muted);font-size:0.8em;" title="${r.target_metric || ''}">${formatBatchMetricLabel(r.target_metric)}</td>
        <td style="padding:9px 10px;font-weight:700;color:${valColor};">${bestValueDisplay}</td>
        <td style="padding:9px 10px;">${tradeCount !== null && tradeCount !== undefined ? tradeCount : "--"}</td>
        <td style="padding:9px 10px;">${sharpeRatio !== null && sharpeRatio !== undefined ? Number(sharpeRatio).toFixed(2) : "--"}</td>
        <td style="padding:9px 10px;">${totalReturnPct !== null && totalReturnPct !== undefined ? Number(totalReturnPct).toFixed(1) + "%" : "--"}</td>
        <td style="padding:9px 10px;">${winRate !== null && winRate !== undefined ? Number(winRate).toFixed(1) + "%" : "--"}</td>
        <td style="padding:9px 10px;color:#f87171;">${maxDrawdownPct !== null && maxDrawdownPct !== undefined ? Number(maxDrawdownPct).toFixed(1) + "%" : "--"}</td>
        <td style="padding:9px 10px;color:var(--text-muted);font-size:0.78em;">${ts}</td>
        <td style="padding:9px 10px; text-align: center;" id="batch-val-cell-${r.id}">
          ${getBatchValidationCellHtml(r)}
        </td>
        <td style="padding:9px 10px; white-space: nowrap;">
          <button class="btn-secondary"
                  style="padding:4px 8px;font-size:0.78em; margin-right: 4px;"
                  onclick="applyBatchRow('${r.symbol}','${r.timeframe}','${r.exchange}',this.dataset.params)"
                  data-params="${rowDataAttr}"
                  title="Apply parameters to Strategy Configuration">
            Apply
          </button>
          <button class="btn-success btn-sm glow-green"
                  style="padding:4px 8px;font-size:0.78em; cursor:pointer; margin-right: 4px;"
                  onclick="deployBatchRowToPaper('${r.symbol}','${r.timeframe}','${r.exchange}','${r.target_metric}',${bestValueNum},this.dataset.params,this)"
                  data-params="${rowDataAttr}"
                  title="Deploy directly to active Paper Trading Profile">
            🚀 Deploy
          </button>
          <button class="btn-secondary btn-sm"
                  style="padding:4px 8px;font-size:0.78em; cursor:pointer; color:#f87171; border-color:rgba(248,113,113,0.3);"
                  onclick="deleteSingleBatchResult(${r.id}, '${r.symbol}', '${r.timeframe}')"
                  title="Delete this result from database">
            🗑
          </button>
        </td>
      </tr>`;
  }).join("");
  updateBatchSelectionState();

  // Wire sort click handlers and update indicators
  document.querySelectorAll(".sortable-th").forEach((th) => {
    th.onclick = () => sortBatchLeaderboard(th.dataset.sort);
  });
  _updateLeaderboardSortHeaders();
}

async function toggleBatchFavorite(resultId, favorite, button) {
  button.disabled = true;
  try {
    const resp = await fetch(`/api/batch_results/${resultId}/favorite`, {
      method: "PATCH",
      headers: getAuthHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify({ favorite }),
    });
    const data = await resp.json();
    if (!resp.ok) throw new Error(data.detail || "Server error");
    button.setAttribute("aria-pressed", String(data.favorite));
    button.title = data.favorite ? "Remove from favorites" : "Save as favorite";
    button.setAttribute("aria-label", button.title);
    button.style.color = data.favorite ? "#fbbf24" : "var(--text-muted)";
    button.innerHTML = data.favorite ? "&#9733;" : "&#9734;";
    button.onclick = () => toggleBatchFavorite(resultId, !data.favorite, button);
  } catch (err) {
    alert(`Could not update favorite: ${err.message}`);
  } finally {
    button.disabled = false;
  }
}

function updateBatchSelectionState() {
  const checks = [...document.querySelectorAll(".batch-result-check")];
  const selected = checks.filter((check) => check.checked);
  const deleteBtn = document.getElementById("batch-delete-btn");
  const exportBtn = document.getElementById("batch-export-portfolio-btn");
  const selectAll = document.getElementById("batch-select-all");
  if (deleteBtn) deleteBtn.disabled = selected.length === 0;
  if (exportBtn) exportBtn.disabled = selected.length === 0;
  if (selectAll) {
    selectAll.checked = checks.length > 0 && selected.length === checks.length;
    selectAll.indeterminate = selected.length > 0 && selected.length < checks.length;
  }
}

function toggleAllBatchResults(checked) {
  document.querySelectorAll(".batch-result-check").forEach((check) => {
    check.checked = checked;
  });
  updateBatchSelectionState();
}

async function deleteSingleBatchResult(resultId, symbol, timeframe) {
  console.log("[DEBUG] deleteSingleBatchResult called:", { resultId, symbol, timeframe });
  const label = symbol && timeframe ? `${symbol} (${timeframe})` : `result #${resultId}`;
  if (!confirm(`Delete batch result for ${label}? This cannot be undone.`)) return;

  try {
    const resp = await fetch("/api/batch_results", {
      method: "DELETE",
      headers: getAuthHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify({ ids: [Number(resultId)] }),
    });
    const data = await resp.json();
    if (!resp.ok) throw new Error(data.detail || "Server error");
    await loadBatchResults();
  } catch (err) {
    alert(`Could not delete batch result: ${err.message}`);
  }
}
window.deleteSingleBatchResult = deleteSingleBatchResult;

async function deleteSelectedBatchResults() {
  const ids = [...document.querySelectorAll(".batch-result-check:checked")]
    .map((check) => Number(check.value))
    .filter((id) => Number.isInteger(id) && id > 0);
  if (!ids.length) {
    alert("Please select at least one entry to delete.");
    return;
  }
  if (!confirm(`Delete ${ids.length} selected batch result${ids.length === 1 ? "" : "s"}? This cannot be undone.`)) return;

  try {
    const resp = await fetch("/api/batch_results", {
      method: "DELETE",
      headers: getAuthHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify({ ids }),
    });
    const data = await resp.json();
    if (!resp.ok) throw new Error(data.detail || "Server error");
    await loadBatchResults();
  } catch (err) {
    alert(`Could not delete batch results: ${err.message}`);
  }
}
window.deleteSelectedBatchResults = deleteSelectedBatchResults;

async function exportBatchToPortfolio() {
  const ids = [...document.querySelectorAll(".batch-result-check:checked")]
    .map((check) => Number(check.value));
  if (!ids.length || !confirm(`Export ${ids.length} selected batch result${ids.length === 1 ? "" : "s"} to Portfolio Rebalancer?`)) return;

  const weightingMethod = document.getElementById("batch-export-weighting")?.value || "sharpe_weighted";
  const maxAssets = parseInt(document.getElementById("batch-export-max-assets")?.value || "10", 10);
  const minSharpe = parseFloat(document.getElementById("batch-export-min-sharpe")?.value || "0.5");
  const minTrades = parseInt(document.getElementById("batch-export-min-trades")?.value || "5", 10);

  try {
    const resp = await fetch("/api/batch_results/export_to_portfolio", {
      method: "POST",
      headers: getAuthHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify({
        result_ids: ids,
        weighting_method: weightingMethod,
        max_assets: maxAssets,
        min_sharpe: minSharpe,
        min_trades: minTrades,
      }),
    });
    const data = await resp.json();
    if (!resp.ok) throw new Error(data.detail || "Server error");

    // Show the result in a modal or navigate to portfolio tab
    const targetWeights = data.target_weights;
    const assetCount = data.count;
    alert(`Exported ${assetCount} assets to Portfolio Rebalancer with ${weightingMethod} weighting!`);

    // Auto-populate the portfolio rebalancer tab
    if (typeof populateRebalancerTargets === "function") {
      populateRebalancerTargets(targetWeights);
    }
  } catch (err) {
    alert(`Could not export to portfolio: ${err.message}`);
  }
}



function openBatchExportModal() {
  document.getElementById("batch-export-modal").style.display = "flex";
}

function closeBatchExportModal() {
  document.getElementById("batch-export-modal").style.display = "none";
}

async function confirmBatchExport() {
  closeBatchExportModal();
  await exportBatchToPortfolio();
}


async function purgeBatchResults() {
  const days = Number(document.getElementById("batch-purge-days").value);
  const metric = document.getElementById("batch-metric-select").value;
  if (!Number.isInteger(days) || days < 1) {
    alert("Enter a purge age of at least 1 day.");
    return;
  }
  const metricLabels = {
    sharpe_ratio: "Sharpe Ratio",
    sortino_ratio: "Sortino Ratio",
    drawdown_penalized_sharpe: "DD-Penalized Sharpe",
    drawdown_penalized_sortino: "DD-Penalized Sortino",
    ulcer_index: "Ulcer Index",
    calmar_ratio: "Calmar Ratio",
    profit_factor: "Profit Factor",
    net_profit: "Net Profit",
    total_return_pct: "Total Return %",
    win_rate: "Win Rate %",
  };
  const metricName = metricLabels[metric] || metric;
  if (!confirm(`Purge all "${metricName}" batch results older than ${days} day${days === 1 ? "" : "s"}?\n\nThis cannot be undone.`)) return;

  try {
    const resp = await fetch("/api/batch_results/purge", {
      method: "POST",
      headers: getAuthHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify({ older_than_days: days, target_metric: metric }),
    });
    const data = await resp.json();
    if (!resp.ok) throw new Error(data.detail || "Server error");
    await loadBatchResults();
  } catch (err) {
    alert(`Could not purge batch results: ${err.message}`);
  }
}

function applyBatchRow(symbol, timeframe, exchange, encodedParams) {
  console.log("[DEBUG] applyBatchRow called:", { symbol, timeframe, exchange });
  // Set the symbol, timeframe, and exchange in the sidebar
  const symEl = document.getElementById("symbol-input");
  const tfEl = document.getElementById("timeframe-select");
  const exEl = document.getElementById("exchange-select");
  if (symEl) symEl.value = symbol;
  if (tfEl) tfEl.value = timeframe;
  if (exEl) {
    exEl.value = exchange;
    exEl.dispatchEvent(new Event("change"));
  }

  // Apply best params to strategy controls
  try {
    const params = JSON.parse(decodeURIComponent(encodedParams));
    lastBatchParams = params;
    lastAppliedParams = params;
    const fieldMap = {
      strategy_mode: "strategy-mode-select",
      trade_direction: "trade-direction-select",
      fast_ema: "fast-ema-input",
      slow_ema: "slow-ema-input",
      trend_ema: "trend-ema-input",
      vwap_slope_min: "vwap-slope-input",
      vwap_slope_lookback: "vwap-lookback-input",
      volume_filter_enabled: "volume-filter-toggle",
      volume_multiplier: "vol-mult-input",
      pullback_tolerance_pct: "pullback-tol-input",
      stop_loss_type: "stop-loss-type-select",
      vwap_stop_offset_pct: "vwap-stop-input",
      atr_multiplier: "atr-mult-input",
      trailing_stop_pct: "tsl-distance-input",
      risk_per_trade_pct: "risk-per-trade-input",
      risk_reward_ratio: "rr-ratio-input",
    };
    const updatedIds = [];
    Object.entries(fieldMap).forEach(([key, id]) => {
      if (params[key] !== undefined && params[key] !== null) {
        const el = document.getElementById(id);
        if (el) {
          if (key === "volume_filter_enabled") {
            el.value = params[key] ? "true" : "false";
          } else {
            el.value = params[key];
          }
          updatedIds.push(id);
        }
      }
    });
    const maxHoldEl = document.getElementById("max-holding-bars-input");
    if (maxHoldEl) {
      maxHoldEl.value =
        params.max_holding_bars !== undefined && params.max_holding_bars !== null
          ? params.max_holding_bars
          : "";
      updatedIds.push("max-holding-bars-input");
    }
    // Sync the main backtest data-range to "auto" so the backend uses
    // get_optimal_lookback_days(timeframe) — the same lookback the batch
    // sweep used — ensuring trade counts are comparable across all three
    // contexts (sweep leaderboard, validation, main backtest).
    const rangeEl = document.getElementById("data-range-select");
    if (rangeEl) rangeEl.value = "auto";

    updatedIds.forEach((id) => {
      const el = document.getElementById(id);
      if (el) {
        el.style.transition = "box-shadow 0.25s ease, border-color 0.25s ease";
        el.style.boxShadow = "0 0 14px rgba(0, 245, 160, 0.6)";
        el.style.borderColor = "#00f5a0";
        setTimeout(() => {
          el.style.boxShadow = "";
          el.style.borderColor = "";
        }, 1800);
      }
    });

    alert(`Applied ${symbol} / ${timeframe} best params to strategy config.`);
  } catch (e) {
    alert("Could not parse params: " + e.message);
  }
}
window.applyBatchRow = applyBatchRow;

async function deployBatchRowToPaper(symbol, timeframe, exchange, metric, score, encodedParams, button) {
  console.log("[DEBUG] deployBatchRowToPaper called:", { symbol, timeframe, exchange, metric, score });
  try {
    const params = JSON.parse(decodeURIComponent(encodedParams));

    // Check validation guardrail if row has a cached/loaded report
    const tr = button ? button.closest("tr") : null;
    const check = tr ? tr.querySelector(".batch-result-check") : null;
    const resId = check ? check.value : null;
    const report = resId ? window._validationReportCache[`batch_${resId}`] : null;

    if (report) {
      if (report.status === "FAIL") {
        const reasons = (report.reasons || []).map((r) => `• ${r}`).join("\n");
        const ok = confirm(
          `⚠️ DEPLOYMENT GUARDRAIL ALERT:\n\nThis strategy FAILED multi-stage robustness validation (Score: ${report.overall_score}/100).\n\nFailure Reasons:\n${reasons || "• Negative return or overfit detected"}\n\nDeploying an unprofitable or curve-fitted configuration into paper trading will risk capital.\n\nDo you still want to proceed with deployment?`
        );
        if (!ok) return;
      } else if (report.status === "CAUTION") {
        const reasons = (report.reasons || []).map((r) => `• ${r}`).join("\n");
        const ok = confirm(
          `⚠️ CAUTIONARY DEPLOYMENT:\n\nThis strategy passed validation with CAUTION (Score: ${report.overall_score}/100).\n\nObservations:\n${reasons || "• Low out-of-sample consistency"}\n\nProceed with deployment to paper trading?`
        );
        if (!ok) return;
      }
    }

    if (button) {
      button.disabled = true;
      button.innerHTML = "⏳";
    }

    const resp = await fetch("/api/paper/profiles/deploy", {
      method: "POST",
      headers: getAuthHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify({
        exchange: exchange,
        symbol: symbol,
        timeframe: timeframe,
        strategy_mode: params.strategy_mode || "crossover",
        target_metric: metric,
        params: params,
        optuna_score: Number(score) || 0.0,
        is_active: true,
      }),
    });

    if (!resp.ok) {
      const err = await resp.json();
      alert(`Deployment Error: ${err.detail || "Server error"}`);
      return;
    }

    if (button) {
      button.innerHTML = "✅ Deployed";
    }
    alert(`🚀 Successfully deployed Optuna sweep parameters to active Paper Profile for ${symbol} (${exchange.toUpperCase()}, ${timeframe})!`);
    if (button) {
      setTimeout(() => {
        button.disabled = false;
        button.innerHTML = "🚀 Deploy";
      }, 2500);
    }
  } catch (err) {
    alert(`Failed to deploy paper profile: ${err.message}`);
  } finally {
    if (button && button.innerHTML === "⏳") {
      button.disabled = false;
      button.innerHTML = "🚀 Deploy";
    }
  }
}

async function deployCurrentBacktestToPaper() {
  const exchange = document.getElementById("exchange-select").value;
  const symbol = document.getElementById("symbol-input").value.trim();
  const timeframe = document.getElementById("timeframe-select").value;
  const baseParams = getStrategyParamsFromUI();
  const mergedParams = Object.assign({}, lastAppliedParams || {}, baseParams);

  // Use the target metric from the last optimization result if available
  const lastRes = window._lastOptimizationResult;
  const isMulti = lastRes ? lastRes.optimization_mode === "multi_objective" : false;
  const multiMetrics = isMulti ? lastRes.pareto_objectives : null;
  const targetMetric = isMulti ? (multiMetrics ? multiMetrics[0] : "sharpe_ratio") : (lastRes?.target_metric || "total_return");

  // Use the actual target metric value from the last backtest results
  // instead of total_return_pct (which is not the optimization metric)
  const metrics = window._lastBacktestMetrics || {};
  let score = 0.0;
  if (metrics && metrics[targetMetric] !== undefined) {
    score = metrics[targetMetric];
  } else {
    // Fallback to total_return_pct if target metric not available
    const returnText = document.getElementById("kpi-return") ? document.getElementById("kpi-return").textContent : "0.00%";
    score = parseFloat(returnText.replace("%", "").replace("+", "")) || 0.0;
  }

  // Validation guardrail check (same as deployOptunaToPaper)
  const valRep = window._lastBacktestValidationReport;
  if (!valRep) {
    alert("Run out-of-sample robustness validation before deploying backtest parameters.");
    return;
  }
  if (
    window._lastBacktestValidatedParams !== JSON.stringify(mergedParams)
  ) {
    alert("The validation report does not match the current backtest parameters. Validate them again.");
    return;
  }
  if (valRep) {
    if (valRep.status === "FAIL") {
      const reasons = (valRep.reasons || []).map((r) => `• ${r}`).join("\n");
      const ok = confirm(
        `⚠️ DEPLOYMENT GUARDRAIL ALERT:\n\nThis strategy FAILED multi-stage robustness validation (Score: ${valRep.overall_score}/100).\n\nFailure Reasons:\n${reasons || "• Negative return or overfit detected"}\n\nDeploying an unprofitable or curve-fitted configuration into paper trading will risk capital.\n\nDo you still want to proceed with deployment?`
      );
      if (!ok) return;
    } else if (valRep.status === "CAUTION") {
      const reasons = (valRep.reasons || []).map((r) => `• ${r}`).join("\n");
      const ok = confirm(
        `⚠️ CAUTIONARY DEPLOYMENT:\n\nThis strategy passed validation with CAUTION (Score: ${valRep.overall_score}/100).\n\nObservations:\n${reasons || "• Low out-of-sample consistency"}\n\nProceed with deployment to paper trading?`
      );
      if (!ok) return;
    }
  }

  const btn = document.getElementById("deploy-backtest-paper-btn");
  if (btn) {
    btn.disabled = true;
    btn.innerHTML = `⏳ Deploying...`;
  }

  try {
    const resp = await fetch("/api/paper/profiles/deploy", {
      method: "POST",
      headers: getAuthHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify({
        exchange: exchange,
        symbol: symbol,
        timeframe: timeframe,
        strategy_mode: mergedParams.strategy_mode || "crossover",
        target_metric: targetMetric,
        params: mergedParams,
        optuna_score: score,
        is_active: true,
        deployed: true,
        validation_report: valRep,
      }),
    });

    if (!resp.ok) {
      const err = await resp.json();
      alert(`Deployment Error: ${err.detail || "Server error"}`);
      return;
    }

    if (btn) {
      btn.innerHTML = `✅ Deployed to Paper!`;
      setTimeout(() => {
        btn.disabled = false;
        btn.innerHTML = `🚀 Deploy to Paper Profile`;
      }, 3000);
    }

    const desc = document.getElementById("backtest-summary-desc");
    if (desc) {
      const orig = desc.textContent;
      desc.textContent = `🚀 Active strategy parameters deployed to Paper Profile for ${symbol} (${exchange.toUpperCase()}, ${timeframe})!`;
      desc.style.color = "#00f5a0";
      setTimeout(() => {
        desc.textContent = orig;
        desc.style.color = "";
      }, 4000);
    }
  } catch (err) {
    alert(`Failed to deploy paper profile: ${err.message}`);
  } finally {
    if (btn && btn.innerHTML === `⏳ Deploying...`) {
      btn.disabled = false;
      btn.innerHTML = `🚀 Deploy to Paper Profile`;
    }
  }
}

window.deployBatchRowToPaper = deployBatchRowToPaper;

// Wire up batch buttons
document.addEventListener("DOMContentLoaded", () => {
  const runBtn = document.getElementById("batch-run-btn");
  const loadBtn = document.getElementById("batch-load-btn");
  const deleteBtn = document.getElementById("batch-delete-btn");
  const purgeBtn = document.getElementById("batch-purge-btn");
  const selectAll = document.getElementById("batch-select-all");
  const sideBtn = document.getElementById("run-batch-btn");

  if (runBtn) runBtn.addEventListener("click", startBatchSweep);
  if (loadBtn) loadBtn.addEventListener("click", loadBatchResults);
  if (deleteBtn) deleteBtn.addEventListener("click", deleteSelectedBatchResults);
  if (purgeBtn) purgeBtn.addEventListener("click", purgeBatchResults);
  const refreshValBtn = document.getElementById("batch-refresh-val-btn");
  if (refreshValBtn) refreshValBtn.addEventListener("click", refreshAllBatchValidations);
  const exportBtn = document.getElementById("batch-export-portfolio-btn");
  if (exportBtn) exportBtn.addEventListener("click", openBatchExportModal);
  if (selectAll) selectAll.addEventListener("change", (event) => toggleAllBatchResults(event.target.checked));
  if (sideBtn) sideBtn.addEventListener("click", () => {
    document.querySelector('[data-tab="tab-batch"]').click();
    startBatchSweep();
  });

  const batchMetricSelect = document.getElementById("batch-metric-select");
  if (batchMetricSelect) batchMetricSelect.addEventListener("change", syncBatchMetricUI);
  const batchMultiMetricSelect = document.getElementById("batch-multi-metric-select");
  if (batchMultiMetricSelect) batchMultiMetricSelect.addEventListener("change", syncBatchMetricUI);
  document.querySelectorAll(".batch-tf-check").forEach((el) => {
    el.addEventListener("change", syncBatchMetricUI);
  });
  syncBatchMetricUI();
});

// =========================================================================
// Paper Trading Dashboard Logic & State Management
// =========================================================================

let paperPollingInterval = null;
let isPaperRunnerActive = false;

async function loadPaperTradingData() {
  try {
    const [statusRes, posRes, profRes, tradesRes, txRes, perfRes] = await Promise.all([
      fetch("/api/paper/engine/status", { headers: getAuthHeaders() }).then((r) => r.json()),
      fetch("/api/paper/positions", { headers: getAuthHeaders() }).then((r) => r.json()),
      fetch("/api/paper/profiles").then((r) => r.json()),
      fetch("/api/paper/trades/history?limit=50", { headers: getAuthHeaders() }).then((r) => r.json()),
      fetch("/api/paper/ledger/transactions?limit=50", { headers: getAuthHeaders() }).then((r) => r.json()),
      fetch("/api/paper/performance", { headers: getAuthHeaders() }).then((r) => r.json()),
    ]);

    isPaperRunnerActive = statusRes.is_running;

    // 1. Update Runner Status Badge & Button
    const runnerBadge = document.getElementById("paper-runner-badge");
    const toggleBtn = document.getElementById("paper-toggle-runner-btn");
    if (runnerBadge) {
      if (statusRes.is_running) {
        runnerBadge.textContent = "Polling Runner: ACTIVE (15s)";
        runnerBadge.className = "badge badge-pulse-green";
      } else {
        runnerBadge.textContent = "Polling Runner: STOPPED";
        runnerBadge.className = "badge badge-amber";
      }
    }
    if (toggleBtn) {
      if (statusRes.is_running) {
        toggleBtn.innerHTML = `⏹ Stop Polling`;
        toggleBtn.className = "btn-secondary btn-sm";
        toggleBtn.style.borderColor = "rgba(255, 0, 122, 0.4)";
        toggleBtn.style.color = "var(--accent-red)";
      } else {
        toggleBtn.innerHTML = `▶ Start Live Polling`;
        toggleBtn.className = "btn-primary btn-sm glow-green";
        toggleBtn.style.borderColor = "";
        toggleBtn.style.color = "";
      }
    }

    // 2. Render KPIs
    renderPaperKPIs(statusRes, perfRes);

    // 3. Render Positions
    renderPaperPositions(posRes.positions || []);

    // 4. Render Profiles
    renderPaperProfiles(profRes.profiles || []);

    // 5. Render Trade History
    renderPaperTrades(tradesRes.trades || []);

    // 6. Render Ledger Transactions
    renderPaperTransactions(txRes.transactions || []);

    // 7. Render Equity Progression Chart
    renderPaperEquityChart(tradesRes.trades || [], perfRes);

  } catch (err) {
    console.error("[PaperTrading] Error loading paper data:", err);
  }
}

function renderPaperKPIs(status, perf) {
  const equityEl = document.getElementById("paper-kpi-equity");
  const cashEl = document.getElementById("paper-kpi-cash");
  const unrealizedEl = document.getElementById("paper-kpi-unrealized");
  const unrealizedPctEl = document.getElementById("paper-kpi-unrealized-pct");
  const realizedEl = document.getElementById("paper-kpi-realized");
  const winRateEl = document.getElementById("paper-kpi-winrate");
  const posCountEl = document.getElementById("paper-kpi-positions");
  const profCountEl = document.getElementById("paper-kpi-profiles-count");

  const cash = status.cash_balance !== undefined ? status.cash_balance : 10000;
  const equity = status.total_equity !== undefined ? status.total_equity : cash;
  const unrealized = status.unrealized_pnl !== undefined ? status.unrealized_pnl : 0;
  const unrealizedPct = cash > 0 ? (unrealized / cash) * 100 : 0;

  const stats = perf.statistics || {};
  const realizedPnl = stats.total_realized_pnl || 0;
  const winRate = stats.win_rate_pct || 0;
  const totalTrades = stats.total_trades || 0;

  if (equityEl) equityEl.textContent = `$${equity.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
  if (cashEl) cashEl.textContent = `$${cash.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;

  if (unrealizedEl) {
    unrealizedEl.textContent = `${unrealized >= 0 ? "+" : ""}$${unrealized.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
    unrealizedEl.style.color = unrealized >= 0 ? "#00f5a0" : "#ff007a";
  }
  if (unrealizedPctEl) {
    unrealizedPctEl.textContent = `${unrealizedPct >= 0 ? "+" : ""}${unrealizedPct.toFixed(2)}% Mark-to-Market`;
  }

  if (realizedEl) {
    realizedEl.textContent = `${realizedPnl >= 0 ? "+" : ""}$${realizedPnl.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
    realizedEl.style.color = realizedPnl >= 0 ? "#00f5a0" : "#ff007a";
  }
  if (winRateEl) {
    winRateEl.textContent = `Win Rate: ${winRate.toFixed(1)}% (${totalTrades} Trades)`;
  }

  if (posCountEl) posCountEl.textContent = `${status.open_positions_count || 0} Open`;
  if (profCountEl) profCountEl.textContent = `${status.active_profiles_count || 0} Active Profiles`;
}

function renderPaperPositions(positions) {
  const tbody = document.getElementById("paper-positions-body");
  const countTag = document.getElementById("paper-positions-count-tag");
  if (countTag) countTag.textContent = `${positions.length} open`;
  if (!tbody) return;

  if (positions.length === 0) {
    tbody.innerHTML = `<tr><td colspan="12" class="empty-msg">No active paper positions. Click "Step Engine" or deploy an Optuna profile to enter.</td></tr>`;
    return;
  }

  tbody.innerHTML = positions
    .map((p) => {
      const isLong = p.side === "LONG";
      const pnlColor = p.unrealized_pnl >= 0 ? "#00f5a0" : "#ff007a";
      const sideBadge = isLong
        ? `<span class="badge badge-long">LONG</span>`
        : `<span class="badge badge-short">SHORT</span>`;

      return `
        <tr>
          <td><span class="badge">${p.exchange.toUpperCase()}</span></td>
          <td style="font-weight: 600;">${p.symbol}</td>
          <td>${sideBadge}</td>
          <td>$${Number(p.entry_price).toFixed(2)}</td>
          <td style="color: var(--accent-cyan); font-weight: 600;">$${Number(p.current_price).toFixed(2)}</td>
          <td>${Number(p.quantity).toFixed(4)}</td>
          <td>$${Number(p.cost_basis).toFixed(2)}</td>
          <td style="color: #ffaa00; font-weight: 600;">${p.stop_loss ? "$" + Number(p.stop_loss).toFixed(2) : "-"}</td>
          <td style="color: #00f5a0; font-weight: 600;">${p.take_profit ? "$" + Number(p.take_profit).toFixed(2) : "-"}</td>
          <td style="color: ${pnlColor}; font-weight: 700;">
            ${p.unrealized_pnl >= 0 ? "+" : ""}$${Number(p.unrealized_pnl).toFixed(2)} (${p.unrealized_pnl_pct >= 0 ? "+" : ""}${Number(p.unrealized_pnl_pct).toFixed(2)}%)
          </td>
          <td style="font-size: 0.8em; color: var(--text-muted);">${(p.entry_time || "").replace("T", " ").split(".")[0]}</td>
          <td>
            <div style="display: flex; gap: 6px;">
              <button class="btn-sm" style="background: rgba(0, 242, 254, 0.12); color: var(--accent-cyan); border: 1px solid rgba(0, 242, 254, 0.35); padding: 4px 8px; font-size: 0.75rem; border-radius: var(--radius-md); cursor: pointer;" onclick="openEditSLTPModal('${p.exchange}', '${p.symbol}', ${p.stop_loss !== null && p.stop_loss !== undefined ? p.stop_loss : 'null'}, ${p.take_profit !== null && p.take_profit !== undefined ? p.take_profit : 'null'})">
                ✏️ Edit
              </button>
              <button class="btn-close-pos" onclick="closePaperPosition('${p.exchange}', '${p.symbol}')">
                ❌ Close
              </button>
            </div>
          </td>
        </tr>
      `;
    })
    .join("");
}

function openEditSLTPModal(exchange, symbol, sl, tp) {
  const modal = document.getElementById("paper-sltp-modal");
  const title = document.getElementById("modal-sltp-pos-title");
  const slInput = document.getElementById("modal-sltp-sl-input");
  const tpInput = document.getElementById("modal-sltp-tp-input");
  const exInput = document.getElementById("modal-sltp-exchange");
  const symInput = document.getElementById("modal-sltp-symbol");

  if (!modal) return;
  if (title) title.textContent = `${symbol} (${exchange.toUpperCase()})`;
  if (slInput) slInput.value = sl !== null && sl !== undefined ? sl : "";
  if (tpInput) tpInput.value = tp !== null && tp !== undefined ? tp : "";
  if (exInput) exInput.value = exchange;
  if (symInput) symInput.value = symbol;

  modal.style.display = "flex";
}

function closeEditSLTPModal() {
  const modal = document.getElementById("paper-sltp-modal");
  if (modal) modal.style.display = "none";
}

async function saveEditSLTP() {
  const ex = document.getElementById("modal-sltp-exchange")?.value;
  const sym = document.getElementById("modal-sltp-symbol")?.value;
  const slVal = document.getElementById("modal-sltp-sl-input")?.value;
  const tpVal = document.getElementById("modal-sltp-tp-input")?.value;

  if (!ex || !sym) return;

  const payload = {
    stop_loss: slVal ? parseFloat(slVal) : null,
    take_profit: tpVal ? parseFloat(tpVal) : null,
  };

  try {
    const resp = await fetch(`/api/paper/positions/${ex}/${encodeURIComponent(sym)}/sl_tp`, {
      method: "POST",
      headers: getAuthHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify(payload),
    });

    if (!resp.ok) {
      const err = await resp.json();
      alert(`Error updating SL/TP: ${err.detail || "Server error"}`);
      return;
    }

    closeEditSLTPModal();
    await loadPaperTradingData();
  } catch (err) {
    alert(`Failed to update SL/TP: ${err.message}`);
  }
}

async function closePaperPosition(exchange, symbol) {
  if (!confirm(`Are you sure you want to market close position for ${symbol} on ${exchange}?`)) return;
  try {
    const resp = await fetch(`/api/paper/positions/${exchange}/${encodeURIComponent(symbol)}/close`, {
      method: "POST",
      headers: getAuthHeaders(),
    });
    if (!resp.ok) {
      const err = await resp.json();
      alert(`Error closing position: ${err.detail || "Server error"}`);
      return;
    }
    await loadPaperTradingData();
  } catch (err) {
    alert(`Failed to close position: ${err.message}`);
  }
}

function renderPaperProfiles(profiles) {
  const tbody = document.getElementById("paper-profiles-body");
  const countTag = document.getElementById("paper-profiles-count-tag");
  if (countTag) countTag.textContent = `${profiles.filter((p) => p.is_active).length} active`;
  if (!tbody) return;

  if (profiles.length === 0) {
    tbody.innerHTML = `<tr><td colspan="9" class="empty-msg">No deployed Optuna profiles. Run Optuna Optimization and click "🚀 Deploy to Paper Profile" to add.</td></tr>`;
    return;
  }

  tbody.innerHTML = profiles
    .map((p) => {
      const activeChecked = p.is_active ? "checked" : "";
      const scoreColor = p.optuna_score > 0 ? "#00f5a0" : "#8a99ad";
      const paramsSummary = Object.entries(p.params || {})
        .slice(0, 3)
        .map(([k, v]) => `${k}:${v}`)
        .join(", ");

      const tdVal = (p.params && p.params.trade_direction) || "long_only";
      const tdColor = tdVal === "long_only" ? "#00f5a0" : tdVal === "long_short" ? "#b46eff" : "#ff5252";
      const tdSelectHtml = `
        <select onchange="updateProfileTradeDirection(${p.id}, this.value)"
                title="Account Mode (Spot / Margin)"
                style="font-size:0.78em; font-weight:600; padding:2px 6px; background:rgba(0,0,0,0.4); color:${tdColor}; border:1px solid rgba(255,255,255,0.15); border-radius:4px; cursor:pointer;">
          <option value="long_only" ${tdVal === "long_only" ? "selected" : ""}>🟢 Spot (Long)</option>
          <option value="long_short" ${tdVal === "long_short" ? "selected" : ""}>🟣 Margin (L&S)</option>
          <option value="short_only" ${tdVal === "short_only" ? "selected" : ""}>🔴 Short Only</option>
        </select>
      `;

      return `
        <tr>
          <td><span class="badge">${p.exchange.toUpperCase()}</span></td>
          <td style="font-weight: 600;">${p.symbol}</td>
          <td>${p.timeframe}</td>
          <td><span class="badge" style="color: #00f2fe;">${p.strategy_mode}</span></td>
          <td>${tdSelectHtml}</td>
          <td>${p.target_metric.replace("_", " ")}</td>
          <td style="color: ${scoreColor}; font-weight: 700;">${Number(p.optuna_score).toFixed(2)}</td>
          <td style="font-size: 0.8em; color: var(--text-muted);">${paramsSummary}</td>
          <td>
            <label style="cursor: pointer; display: flex; align-items: center; gap: 6px;">
              <input type="checkbox" ${activeChecked} onchange="togglePaperProfileActive(${p.id}, this.checked)" style="cursor: pointer;">
              <span style="font-size: 0.8em; color: ${p.is_active ? '#00f5a0' : 'var(--text-muted)'};">${p.is_active ? 'Active' : 'Paused'}</span>
            </label>
          </td>
          <td>
            <button class="btn-secondary btn-sm" onclick="deletePaperProfile(${p.id})" style="padding: 3px 8px; border-color: rgba(255,0,122,0.3); color: var(--accent-red);" title="Delete Profile">
              🗑️
            </button>
          </td>
        </tr>
      `;
    })
    .join("");
}

async function togglePaperProfileActive(profileId, isActive) {
  try {
    await fetch(`/api/paper/profiles/${profileId}/toggle-active`, {
      method: "POST",
      headers: getAuthHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify({ is_active: isActive }),
    });
    await loadPaperTradingData();
  } catch (err) {
    console.error("Failed to toggle profile active:", err);
  }
}

async function updateProfileTradeDirection(profileId, tradeDirection) {
  try {
    const resp = await fetch(`/api/paper/profiles/${profileId}/trade-direction`, {
      method: "PATCH",
      headers: getAuthHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify({ trade_direction: tradeDirection }),
    });
    if (!resp.ok) {
      const err = await resp.json();
      alert(`Failed to update account mode: ${err.detail || "Server error"}`);
    }
    await loadPaperTradingData();
  } catch (err) {
    console.error("Failed to update profile trade direction:", err);
  }
}

async function deletePaperProfile(profileId) {
  if (!confirm("Are you sure you want to delete this deployed paper profile?")) return;
  try {
    await fetch(`/api/paper/profiles/${profileId}`, {
      method: "DELETE",
      headers: getAuthHeaders(),
    });
    await loadPaperTradingData();
  } catch (err) {
    alert("Failed to delete profile: " + err.message);
  }
}

function renderPaperTrades(trades) {
  const tbody = document.getElementById("paper-trades-body");
  const countTag = document.getElementById("paper-trades-count-tag");
  if (countTag) countTag.textContent = `${trades.length} trades`;
  if (!tbody) return;

  if (trades.length === 0) {
    tbody.innerHTML = `<tr><td colspan="12" class="empty-msg">No completed paper trades yet.</td></tr>`;
    return;
  }

  tbody.innerHTML = trades
    .map((t) => {
      const isWin = t.realized_pnl > 0;
      const pnlColor = isWin ? "#00f5a0" : "#ff007a";
      const reasonBadge =
        t.exit_reason === "STOP_LOSS"
          ? `<span class="badge badge-stoploss">STOP LOSS</span>`
          : t.exit_reason === "TAKE_PROFIT"
            ? `<span class="badge badge-takeprofit">TAKE PROFIT</span>`
            : `<span class="badge badge-manual">MANUAL</span>`;

      return `
        <tr>
          <td style="font-size: 0.8em; font-family: monospace;">${t.trade_id}</td>
          <td><span class="badge">${t.exchange.toUpperCase()}</span></td>
          <td style="font-weight: 600;">${t.symbol}</td>
          <td><span class="badge ${t.side === 'LONG' ? 'badge-long' : 'badge-short'}">${t.side}</span></td>
          <td>$${Number(t.entry_price).toFixed(2)}</td>
          <td>$${Number(t.exit_price).toFixed(2)}</td>
          <td>${Number(t.quantity).toFixed(4)}</td>
          <td style="color: ${pnlColor}; font-weight: 700;">${t.realized_pnl >= 0 ? "+" : ""}$${Number(t.realized_pnl).toFixed(2)}</td>
          <td style="color: ${pnlColor};">${t.realized_pnl_pct >= 0 ? "+" : ""}${Number(t.realized_pnl_pct).toFixed(2)}%</td>
          <td style="color: var(--text-muted);">$${Number(t.fees).toFixed(2)}</td>
          <td>${reasonBadge}</td>
          <td style="font-size: 0.8em; color: var(--text-muted);">${(t.exit_time || "").replace("T", " ").split(".")[0]}</td>
        </tr>
      `;
    })
    .join("");
}

function renderPaperTransactions(txs) {
  const tbody = document.getElementById("paper-transactions-body");
  const countTag = document.getElementById("paper-transactions-count-tag");
  if (countTag) countTag.textContent = `${txs.length} transactions`;
  if (!tbody) return;

  if (txs.length === 0) {
    tbody.innerHTML = `<tr><td colspan="11" class="empty-msg">No ledger transactions recorded yet.</td></tr>`;
    return;
  }

  tbody.innerHTML = txs
    .map((t) => {
      const typeBadge =
        t.type === "DEPOSIT"
          ? `<span class="badge" style="color: #00f5a0;">DEPOSIT</span>`
          : t.type === "BUY_FILL"
            ? `<span class="badge badge-long">BUY FILL</span>`
            : t.type === "SELL_FILL"
              ? `<span class="badge badge-short">SELL FILL</span>`
              : `<span class="badge">${t.type}</span>`;

      return `
        <tr>
          <td style="font-size: 0.8em; font-family: monospace;">${t.transaction_id.slice(0, 8)}...</td>
          <td>${typeBadge}</td>
          <td><span class="badge">${t.exchange ? t.exchange.toUpperCase() : '-'}</span></td>
          <td style="font-weight: 600;">${t.symbol || '-'}</td>
          <td style="font-weight: 600;">$${Number(t.amount).toFixed(2)}</td>
          <td>${t.asset_qty ? Number(t.asset_qty).toFixed(4) : '-'}</td>
          <td>${t.price ? '$' + Number(t.price).toFixed(2) : '-'}</td>
          <td style="color: var(--text-muted);">${t.fee ? '$' + Number(t.fee).toFixed(2) : '$0.00'}</td>
          <td style="color: var(--accent-cyan); font-weight: 600;">$${Number(t.balance_after).toFixed(2)}</td>
          <td style="font-size: 0.8em; color: var(--text-muted);">${(t.timestamp || "").replace("T", " ").split(".")[0]}</td>
          <td style="font-size: 0.8em; color: var(--text-muted);">${t.notes || ''}</td>
        </tr>
      `;
    })
    .join("");
}

function renderPaperEquityChart(trades, perf) {
  const chartEl = document.getElementById("paper-equity-chart");
  if (!chartEl) return;

  // Read initial capital from performance stats or status, fallback to default
  const initialCapital =
    (perf && perf.statistics && perf.statistics.initial_capital) ||
    (perf && perf.initial_capital) ||
    10000;
  let cumulativeEquity = [initialCapital];
  let xLabels = ["Start"];

  let runningBalance = initialCapital;
  // Sort chronological (API returns newest first, reverse to get oldest first)
  const sorted = [...trades].reverse();
  sorted.forEach((t, i) => {
    runningBalance += Number(t.realized_pnl);
    cumulativeEquity.push(runningBalance);
    xLabels.push(`Trade #${i + 1}`);
  });

  const trace = {
    x: xLabels,
    y: cumulativeEquity,
    type: "scatter",
    mode: "lines+markers",
    line: {
      color: "#00f5a0",
      width: 2.5,
      shape: "spline",
    },
    marker: {
      color: "#00f2fe",
      size: 5,
    },
    fill: "tozeroy",
    fillcolor: "rgba(0, 245, 160, 0.08)",
  };

  const layout = {
    paper_bgcolor: "transparent",
    plot_bgcolor: "transparent",
    font: { color: "#8a99ad", family: "Inter, sans-serif" },
    margin: { l: 60, r: 20, t: 20, b: 30 },
    xaxis: { gridcolor: "rgba(255, 255, 255, 0.05)" },
    yaxis: {
      title: "Equity ($)",
      gridcolor: "rgba(255, 255, 255, 0.05)",
      tickprefix: "$",
    },
  };

  Plotly.newPlot("paper-equity-chart", [trace], layout, {
    responsive: true,
    displayModeBar: false,
  });
}

// Wire up Paper Trading buttons & tab listener
document.addEventListener("DOMContentLoaded", () => {
  const paperTabBtn = document.querySelector('[data-tab="tab-paper"]');
  const toggleRunnerBtn = document.getElementById("paper-toggle-runner-btn");
  const stepBtn = document.getElementById("paper-step-btn");
  const refreshBtn = document.getElementById("paper-refresh-btn");
  const depositBtn = document.getElementById("paper-deposit-btn");
  const autoRefreshSel = document.getElementById("paper-auto-refresh-select");

  let paperRefreshSecondsLeft = 5;
  let paperAutoRefreshTimer = null;

  function resetPaperAutoRefresh() {
    if (paperAutoRefreshTimer) {
      clearInterval(paperAutoRefreshTimer);
      paperAutoRefreshTimer = null;
    }

    const sel = document.getElementById("paper-auto-refresh-select");
    const countdownBadge = document.getElementById("paper-refresh-countdown");
    const val = sel ? sel.value : "5";

    if (val === "off") {
      if (countdownBadge) {
        countdownBadge.textContent = "OFF";
        countdownBadge.className = "badge";
      }
      return;
    }

    const intervalSec = parseInt(val, 10) || 5;
    paperRefreshSecondsLeft = intervalSec;

    if (countdownBadge) {
      countdownBadge.textContent = `⏱ ${paperRefreshSecondsLeft}s`;
      countdownBadge.className = "badge badge-pulse-green";
    }

    paperAutoRefreshTimer = setInterval(async () => {
      const tab = document.getElementById("tab-paper");
      if (!tab || !tab.classList.contains("active")) {
        clearInterval(paperAutoRefreshTimer);
        paperAutoRefreshTimer = null;
        return;
      }

      paperRefreshSecondsLeft--;
      if (paperRefreshSecondsLeft <= 0) {
        paperRefreshSecondsLeft = intervalSec;
        if (countdownBadge) countdownBadge.textContent = `⏳...`;
        await loadPaperTradingData();
      }
      if (countdownBadge && sel.value !== "off") {
        countdownBadge.textContent = `⏱ ${paperRefreshSecondsLeft}s`;
      }
    }, 1000);
  }

  if (paperTabBtn) {
    paperTabBtn.addEventListener("click", () => {
      loadPaperTradingData();
      resetPaperAutoRefresh();
    });
  }

  if (autoRefreshSel) {
    autoRefreshSel.addEventListener("change", () => resetPaperAutoRefresh());
  }

  // Edit SL/TP Modal Listeners
  const closeSLTPBtn = document.getElementById("close-sltp-modal-btn");
  const cancelSLTPBtn = document.getElementById("cancel-sltp-modal-btn");
  const saveSLTPBtn = document.getElementById("save-sltp-modal-btn");

  if (closeSLTPBtn) closeSLTPBtn.addEventListener("click", closeEditSLTPModal);
  if (cancelSLTPBtn) cancelSLTPBtn.addEventListener("click", closeEditSLTPModal);
  if (saveSLTPBtn) saveSLTPBtn.addEventListener("click", saveEditSLTP);

  if (toggleRunnerBtn) {
    toggleRunnerBtn.addEventListener("click", async () => {
      toggleRunnerBtn.disabled = true;
      try {
        if (isPaperRunnerActive) {
          await fetch("/api/paper/engine/stop", { method: "POST", headers: getAuthHeaders() });
        } else {
          await fetch("/api/paper/engine/start", {
            method: "POST",
            headers: getAuthHeaders({ "Content-Type": "application/json" }),
            body: JSON.stringify({ interval_seconds: 15 }),
          });
        }
        await loadPaperTradingData();
      } catch (err) {
        alert("Runner error: " + err.message);
      } finally {
        toggleRunnerBtn.disabled = false;
      }
    });
  }

  if (stepBtn) {
    stepBtn.addEventListener("click", async () => {
      stepBtn.disabled = true;
      stepBtn.innerHTML = `⏳ Stepping...`;
      try {
        const resp = await fetch("/api/paper/engine/step", {
          method: "POST",
          headers: getAuthHeaders({ "Content-Type": "application/json" }),
          body: JSON.stringify({}),
        });
        const data = await resp.json();
        await loadPaperTradingData();
        const desc = document.getElementById("paper-summary-desc");
        if (desc) {
          desc.textContent = `⚡ Engine step completed. Evaluated ${(data.results || []).length} profile(s).`;
          setTimeout(() => {
            desc.textContent = "Virtual live execution engine with real-time mark-to-market and SL/TP management.";
          }, 3500);
        }
      } catch (err) {
        alert("Step error: " + err.message);
      } finally {
        stepBtn.disabled = false;
        stepBtn.innerHTML = `⚡ Step Engine Once`;
      }
    });
  }

  if (refreshBtn) {
    refreshBtn.addEventListener("click", () => loadPaperTradingData());
  }

  if (depositBtn) {
    depositBtn.addEventListener("click", () => {
      // Build the deposit/reinitialize modal
      const existingModal = document.getElementById("paper-deposit-modal");
      if (existingModal) existingModal.remove();

      const modal = document.createElement("div");
      modal.id = "paper-deposit-modal";
      modal.style.cssText = `
        position: fixed; inset: 0; z-index: 9999;
        background: rgba(0,0,0,0.72); backdrop-filter: blur(4px);
        display: flex; align-items: center; justify-content: center;
      `;
      modal.innerHTML = `
        <div style="
          background: var(--bg-card, #1a1a2e);
          border: 1px solid rgba(255,170,0,0.35);
          border-radius: 12px; padding: 28px 32px;
          min-width: 360px; max-width: 440px;
          box-shadow: 0 0 40px rgba(255,170,0,0.12);
        ">
          <h3 style="margin: 0 0 6px; color: var(--accent-amber, #ffaa00); font-size: 1.1rem;">
            💵 Deposit Cash
          </h3>
          <p style="margin: 0 0 20px; color: var(--text-muted, #888); font-size: 0.85rem;">
            Add funds to your paper account, or reset and reinitialize it to a clean state.
          </p>

          <label style="display: block; font-size: 0.82rem; color: var(--text-muted, #888); margin-bottom: 4px;">
            Amount (USD)
          </label>
          <input id="deposit-amount-input" type="number" min="1" step="1000" value="10000"
            style="
              width: 100%; box-sizing: border-box;
              background: rgba(255,255,255,0.06); border: 1px solid rgba(255,255,255,0.15);
              color: #fff; border-radius: 6px; padding: 9px 12px; font-size: 1rem;
              margin-bottom: 16px; outline: none;
            " />

          <label style="display: flex; align-items: center; gap: 10px; cursor: pointer; margin-bottom: 20px; font-size: 0.88rem; color: var(--text-muted, #888);">
            <input type="checkbox" id="deposit-reset-chk"
              style="width: 16px; height: 16px; accent-color: #ff007a; cursor: pointer;" />
            <span>
              <strong style="color: #ff007a;">Reset account first</strong> — clears all ledger transactions,
              open positions &amp; trade history, then deposits the amount.
            </span>
          </label>

          <div id="deposit-error-msg" style="color: #ff007a; font-size: 0.82rem; margin-bottom: 12px; display: none;"></div>

          <div style="display: flex; gap: 10px; justify-content: flex-end;">
            <button id="deposit-cancel-btn" style="
              background: transparent; border: 1px solid rgba(255,255,255,0.15);
              color: var(--text-muted, #888); border-radius: 6px;
              padding: 8px 18px; cursor: pointer; font-size: 0.9rem;
            ">Cancel</button>
            <button id="deposit-confirm-btn" style="
              background: linear-gradient(135deg, rgba(255,170,0,0.18), rgba(255,170,0,0.08));
              border: 1px solid rgba(255,170,0,0.5);
              color: var(--accent-amber, #ffaa00); border-radius: 6px;
              padding: 8px 22px; cursor: pointer; font-size: 0.9rem; font-weight: 600;
            ">💵 Deposit</button>
          </div>
        </div>
      `;

      document.body.appendChild(modal);

      // Close on backdrop click
      modal.addEventListener("click", (e) => { if (e.target === modal) modal.remove(); });
      document.getElementById("deposit-cancel-btn").addEventListener("click", () => modal.remove());

      document.getElementById("deposit-confirm-btn").addEventListener("click", async () => {
        const amountInput = document.getElementById("deposit-amount-input");
        const resetChk = document.getElementById("deposit-reset-chk");
        const errMsg = document.getElementById("deposit-error-msg");
        const confirmBtn = document.getElementById("deposit-confirm-btn");

        const amount = parseFloat(amountInput.value);
        if (!isFinite(amount) || amount <= 0) {
          errMsg.textContent = "Please enter a valid positive amount.";
          errMsg.style.display = "block";
          return;
        }

        const doReset = resetChk.checked;
        const confirmText = doReset
          ? `Reset account and deposit $${amount.toLocaleString()}? This will permanently clear all ledger history, positions, and trade records.`
          : `Deposit $${amount.toLocaleString()} into the paper account?`;

        if (!confirm(confirmText)) return;

        confirmBtn.disabled = true;
        confirmBtn.textContent = "⏳ Processing...";
        errMsg.style.display = "none";

        try {
          const resp = await fetch("/api/paper/ledger/deposit", {
            method: "POST",
            headers: getAuthHeaders({ "Content-Type": "application/json" }),
            body: JSON.stringify({ amount, notes: doReset ? "Account reset + deposit" : "Manual deposit", reset: doReset }),
          });
          const data = await resp.json();
          if (!resp.ok) {
            errMsg.textContent = data.detail || "Deposit failed.";
            errMsg.style.display = "block";
            confirmBtn.disabled = false;
            confirmBtn.textContent = "💵 Deposit";
            return;
          }
          modal.remove();
          await loadPaperTradingData();
          // Brief success banner
          const desc = document.getElementById("paper-summary-desc");
          if (desc) {
            const msg = doReset
              ? `✅ Account reset. New balance: $${data.balance_after.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
              : `✅ Deposited $${amount.toLocaleString("en-US", { minimumFractionDigits: 2 })}. New balance: $${data.balance_after.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
            desc.textContent = msg;
            setTimeout(() => { desc.textContent = "Virtual live execution engine with real-time mark-to-market and SL/TP management."; }, 5000);
          }
        } catch (err) {
          errMsg.textContent = "Network error: " + err.message;
          errMsg.style.display = "block";
          confirmBtn.disabled = false;
          confirmBtn.textContent = "💵 Deposit";
        }
      });
    });
  }

  // Validation Details Modal close listeners
  const closeValModalBtn = document.getElementById("close-val-modal-btn");
  const closeValModalBottomBtn = document.getElementById("close-val-modal-bottom-btn");
  if (closeValModalBtn) closeValModalBtn.addEventListener("click", closeValidationDetailsModal);
  if (closeValModalBottomBtn) closeValModalBottomBtn.addEventListener("click", closeValidationDetailsModal);

  // Portfolio Dashboard Listeners
  const portfolioTabBtn = document.getElementById("tab-btn-portfolio");
  if (portfolioTabBtn) {
    portfolioTabBtn.addEventListener("click", () => loadPortfolioDashboard());
  }

  const refreshPortBtn = document.getElementById("refresh-portfolio-btn");
  if (refreshPortBtn) {
    refreshPortBtn.addEventListener("click", async () => {
      refreshPortBtn.disabled = true;
      refreshPortBtn.innerHTML = `⏳ Refreshing...`;
      try {
        await fetch("/api/portfolio/refresh?include_synthetic=true", { method: "POST", headers: getAuthHeaders() });
        await loadPortfolioDashboard();
      } catch (err) {
        console.warn("Portfolio refresh error:", err);
      } finally {
        refreshPortBtn.disabled = false;
        refreshPortBtn.innerHTML = `🔄 Refresh Venues & Risk`;
      }
    });
  }

  const calcRebalBtn = document.getElementById("calc-rebalance-plan-btn");
  if (calcRebalBtn) {
    calcRebalBtn.addEventListener("click", () => calculateRebalancePlan());
  }

  const execDryBtn = document.getElementById("exec-rebalance-dryrun-btn");
  if (execDryBtn) {
    execDryBtn.addEventListener("click", () => executePortfolioRebalance(true));
  }

  const execLiveBtn = document.getElementById("exec-rebalance-live-btn");
  if (execLiveBtn) {
    execLiveBtn.addEventListener("click", () => {
      if (confirm("Execute real rebalancing orders across active venues/paper ledger?")) {
        executePortfolioRebalance(false);
      }
    });
  }

  // =========================================================================
  // Backtest Enhancements Listeners (Zoom, Quick-Tweak, Filtering, Hotkeys)
  // =========================================================================

  // 1. Chart Zoom Range Presets
  const zoomBtns = [
    { id: "zoom-1d-btn", range: "1d" },
    { id: "zoom-5d-btn", range: "5d" },
    { id: "zoom-1m-btn", range: "1m" },
    { id: "zoom-3m-btn", range: "3m" },
    { id: "zoom-all-btn", range: "all" },
    { id: "zoom-reset-btn", range: "reset" },
  ];

  zoomBtns.forEach(({ id, range }) => {
    const btn = document.getElementById(id);
    if (btn) {
      btn.addEventListener("click", () => {
        zoomBtns.forEach((b) => {
          const el = document.getElementById(b.id);
          if (el) el.classList.remove("active");
        });
        btn.classList.add("active");
        applyPriceChartZoom(range);
      });
    }
  });

  // 2. Trade Log Filter Buttons
  document.querySelectorAll(".btn-filter").forEach((btn) => {
    btn.addEventListener("click", (e) => {
      document.querySelectorAll(".btn-filter").forEach((b) => b.classList.remove("active"));
      e.currentTarget.classList.add("active");
      tradeFilterMode = e.currentTarget.getAttribute("data-filter") || "all";
      tradesCurrentPage = 1;
      renderTradeTable(currentTrades);
    });
  });

  // 3. Trade Search Input
  const searchInp = document.getElementById("trade-search-input");
  if (searchInp) {
    searchInp.addEventListener("input", (e) => {
      tradeSearchQuery = e.target.value.trim();
      tradesCurrentPage = 1;
      renderTradeTable(currentTrades);
    });
  }

  // 4. Trade Pagination Controls
  const perPageSel = document.getElementById("trades-per-page");
  if (perPageSel) {
    perPageSel.addEventListener("change", (e) => {
      tradesPerPage = e.target.value;
      tradesCurrentPage = 1;
      renderTradeTable(currentTrades);
    });
  }

  const prevPageBtn = document.getElementById("trade-prev-page-btn");
  if (prevPageBtn) {
    prevPageBtn.addEventListener("click", () => {
      if (tradesCurrentPage > 1) {
        tradesCurrentPage--;
        renderTradeTable(currentTrades);
      }
    });
  }

  const nextPageBtn = document.getElementById("trade-next-page-btn");
  if (nextPageBtn) {
    nextPageBtn.addEventListener("click", () => {
      tradesCurrentPage++;
      renderTradeTable(currentTrades);
    });
  }

  // 5. Quick-Tweak Bar Adjustments & Sync
  const wireTweak = (incId, decId, valId, targetId, step = 1, min = 1, max = 500) => {
    const inc = document.getElementById(incId);
    const dec = document.getElementById(decId);
    const val = document.getElementById(valId);
    const target = document.getElementById(targetId);

    const update = (newVal) => {
      const clamped = Math.max(min, Math.min(max, newVal));
      if (val) val.value = step < 1 ? clamped.toFixed(1) : clamped;
      if (target) target.value = val.value;
    };

    if (inc) {
      inc.addEventListener("click", () => {
        const cur = parseFloat(val?.value || target?.value || 0);
        update(cur + step);
      });
    }

    if (dec) {
      dec.addEventListener("click", () => {
        const cur = parseFloat(val?.value || target?.value || 0);
        update(cur - step);
      });
    }

    if (val) {
      val.addEventListener("change", (e) => {
        update(parseFloat(e.target.value) || min);
      });
    }
  };

  wireTweak("tweak-fast-inc", "tweak-fast-dec", "tweak-fast-val", "fast-ema-input", 1, 2, 50);
  wireTweak("tweak-slow-inc", "tweak-slow-dec", "tweak-slow-val", "slow-ema-input", 1, 5, 150);
  wireTweak("tweak-trend-inc", "tweak-trend-dec", "tweak-trend-val", "trend-ema-input", 5, 20, 300);
  wireTweak("tweak-rr-inc", "tweak-rr-dec", "tweak-rr-val", "rr-ratio-input", 0.2, 0.5, 10.0);

  const tweakRerunBtn = document.getElementById("tweak-rerun-btn");
  if (tweakRerunBtn) {
    tweakRerunBtn.addEventListener("click", () => runBacktest());
  }

  // 6. Global Navigation & Keyboard Shortcuts
  window.addEventListener("keydown", (e) => {
    // Ctrl+Enter / Cmd+Enter: Run Strategy Backtest
    if ((e.ctrlKey || e.metaKey) && e.key === "Enter") {
      e.preventDefault();
      runBacktest();
      return;
    }

    // Ctrl+, / Cmd+, : Open Settings Modal
    if ((e.ctrlKey || e.metaKey) && e.key === ",") {
      e.preventDefault();
      const settingsBtn = document.getElementById("open-settings-btn");
      if (settingsBtn) settingsBtn.click();
      return;
    }

    // Escape: Close any open modal
    if (e.key === "Escape") {
      const settingsModal = document.getElementById("settings-modal");
      if (settingsModal && settingsModal.style.display === "flex") settingsModal.style.display = "none";
      if (typeof closeValidationDetailsModal === "function") closeValidationDetailsModal();
      if (typeof closeEditSLTPModal === "function") closeEditSLTPModal();
      return;
    }

    // Ctrl+1..7 / Cmd+1..7: Switch Navigation Tabs
    if (e.ctrlKey || e.metaKey) {
      const tabMap = {
        "1": "tab-backtest",
        "2": "tab-optuna",
        "3": "tab-wfo",
        "4": "tab-mc",
        "5": "tab-batch",
        "6": "tab-paper",
        "7": "tab-portfolio",
      };
      if (tabMap[e.key]) {
        e.preventDefault();
        switchToTab(tabMap[e.key]);
      }
    }
  });

  // Global NAV Pill click to view Portfolio tab
  const globalNavPill = document.getElementById("global-nav-pill");
  if (globalNavPill) {
    globalNavPill.addEventListener("click", () => {
      switchToTab("tab-portfolio");
    });
  }
});

function switchToTab(tabId) {
  const tabBtns = document.querySelectorAll(".tab-btn");
  tabBtns.forEach((b) => {
    if (b.getAttribute("data-tab") === tabId) {
      b.click();
    }
  });
}

function toggleSidebarSection(headerEl) {
  if (!headerEl) return;
  headerEl.classList.toggle("collapsed");
  const content = headerEl.nextElementSibling;
  if (content && content.classList.contains("sidebar-section-content")) {
    content.classList.toggle("collapsed");
  }
}

function updateGlobalNavPill(totalNav) {
  const pillVal = document.getElementById("global-nav-val");
  if (pillVal && typeof totalNav === "number") {
    pillVal.textContent = `$${totalNav.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
  }
}

// =========================================================================
// Multi-Asset Portfolio Dashboard & Analytics Suite
// =========================================================================


let portfolioTargetWeights = {
  "BTC/USD": 30,
  "ETH/USD": 20,
  "AAPL": 25,
  "SPY": 25,
};

// Called from batch export to auto-populate rebalancer targets
function populateRebalancerTargets(targetWeights) {
  // targetWeights is an object like {"BTC/USD": 0.3, "ETH/USD": 0.2, ...}
  // Convert to percentages and update portfolioTargetWeights
  portfolioTargetWeights = {};
  for (const [sym, weight] of Object.entries(targetWeights)) {
    portfolioTargetWeights[sym.toUpperCase()] = (weight * 100).toFixed(2);
  }

  // Switch to portfolio tab
  switchToTab("tab-portfolio");

  // Re-render the target inputs
  renderRebalanceTargetInputs();

  // Optionally auto-calculate the rebalance plan
  setTimeout(() => calculateRebalancePlan(), 100);
}



let currentHistoryLookbackDays = 30;

async function loadPortfolioDashboard() {
  try {
    const [snapResp, corrResp, varResp] = await Promise.all([
      fetch("/api/portfolio/snapshot?include_synthetic=true", { headers: getAuthHeaders() }).then((r) => r.json()),
      fetch("/api/portfolio/correlation", { headers: getAuthHeaders() }).then((r) => r.json()),
      fetch("/api/portfolio/var?include_synthetic=true", { headers: getAuthHeaders() }).then((r) => r.json()),
    ]);

    if (snapResp && snapResp.snapshot) {
      const s = snapResp.snapshot;

      // Update Global Top Navbar NAV Pill
      updateGlobalNavPill(s.total_nav_usd);

      // 1. Update KPIs
      const setTxt = (id, val) => {
        const el = document.getElementById(id);
        if (el) el.textContent = val;
      };

      setTxt("kpi-port-nav", `$${s.total_nav_usd.toLocaleString()}`);
      setTxt("kpi-port-buying-power", `$${s.total_buying_power_usd.toLocaleString()} Purchasing Power`);
      setTxt("kpi-port-crypto-val", `$${s.total_crypto_usd.toLocaleString()}`);
      setTxt("kpi-port-crypto-pct", `${s.crypto_weight_pct}% Weight`);
      setTxt("kpi-port-stock-val", `$${s.total_stock_usd.toLocaleString()}`);
      setTxt("kpi-port-stock-pct", `${s.stock_weight_pct}% Weight`);
      setTxt("kpi-port-cash-val", `$${(s.total_cash_usd + s.total_stablecoin_usd).toLocaleString()}`);
      setTxt("kpi-port-cash-pct", `${s.cash_weight_pct}% Weight`);

      const tsEl = document.getElementById("portfolio-timestamp-text");
      if (tsEl) tsEl.textContent = `Last synchronized at: ${s.timestamp} UTC`;

      // 2. Render Allocation Donut Chart
      renderPortfolioAllocationChart(s);

      // 3. Render Venues Breakdown Grid
      renderPortfolioVenues(s.venues || {});

      // 4. Render Active Positions Table
      renderPortfolioPositions(s.positions || []);

      // 5. Render Dynamic Rebalance Target Inputs
      renderRebalanceTargetInputs();
    }

    if (varResp && varResp.risk_report) {
      const r = varResp.risk_report;
      const setTxt = (id, val) => {
        const el = document.getElementById(id);
        if (el) el.textContent = val;
      };

      setTxt("kpi-port-var-val", `$${r.param_var_usd.toLocaleString()}`);
      setTxt("kpi-port-var-pct", `${r.param_var_pct}% 1-Day Risk (95%)`);
      setTxt("kpi-port-drawdown", `${r.unrealized_drawdown_pct}% Unrealized DD`);

      const cbEl = document.getElementById("kpi-port-circuit-status");
      if (cbEl) {
        cbEl.textContent = r.circuit_breaker_status;
        if (r.circuit_breaker_status === "EMERGENCY_HALT") {
          cbEl.style.color = "#ff007a";
        } else if (r.circuit_breaker_status === "DE_RISKING") {
          cbEl.style.color = "var(--accent-amber)";
        } else if (r.circuit_breaker_status === "WARNING") {
          cbEl.style.color = "#b87cf8";
        } else {
          cbEl.style.color = "#00f5a0";
        }
      }

      // Render Risk Decomposition
      renderPortfolioRiskDecomposition(r);
    }

    if (corrResp && corrResp.metrics) {
      renderPortfolioCorrelationHeatmap(corrResp.metrics);
      const divBadge = document.getElementById("diversification-score-badge");
      if (divBadge) {
        divBadge.textContent = `Div Ratio: ${corrResp.metrics.diversification_ratio}`;
      }
    }

    // 6. Render Historical NAV Chart
    await renderPortfolioHistoricalChart(currentHistoryLookbackDays);
  } catch (err) {
    console.warn("Could not load portfolio dashboard:", err);
  }
}

async function renderPortfolioHistoricalChart(days = 30) {
  currentHistoryLookbackDays = days;
  const chartDiv = document.getElementById("portfolio-history-chart");
  const gainBadge = document.getElementById("port-history-gain-badge");
  if (!chartDiv || typeof Plotly === "undefined") return;

  try {
    const resp = await fetch(`/api/portfolio/history?days=${days}&include_synthetic=true`);
    const data = await resp.json();
    if (!data || !data.history || data.history.length === 0) return;

    const hist = data.history;
    const timestamps = hist.map((h) => h.timestamp);
    const totalNavs = hist.map((h) => h.total_nav);
    const cryptoVals = hist.map((h) => h.crypto_value);
    const stockVals = hist.map((h) => h.stock_value);
    const cashVals = hist.map((h) => h.cash_value);

    // Compute period return
    const startNav = totalNavs[0] || 1;
    const endNav = totalNavs[totalNavs.length - 1] || 1;
    const gainPct = ((endNav - startNav) / startNav) * 100.0;

    if (gainBadge) {
      gainBadge.textContent = `${gainPct >= 0 ? "+" : ""}${gainPct.toFixed(2)}% (${days}D)`;
      gainBadge.className = gainPct >= 0 ? "badge badge-cyan" : "badge badge-short";
    }

    const navTrace = {
      type: "scatter",
      mode: "lines",
      x: timestamps,
      y: totalNavs,
      name: "Total NAV ($)",
      line: { color: "#00f2fe", width: 2.5 },
      hoverinfo: "x+y+name",
    };

    const cryptoTrace = {
      type: "scatter",
      mode: "lines",
      x: timestamps,
      y: cryptoVals,
      name: "Crypto ($)",
      line: { color: "#b87cf8", width: 1.5 },
      stackgroup: "one",
      fillcolor: "rgba(184, 124, 248, 0.15)",
    };

    const stockTrace = {
      type: "scatter",
      mode: "lines",
      x: timestamps,
      y: stockVals,
      name: "Equities ($)",
      line: { color: "#00f5a0", width: 1.5 },
      stackgroup: "one",
      fillcolor: "rgba(0, 245, 160, 0.15)",
    };

    const cashTrace = {
      type: "scatter",
      mode: "lines",
      x: timestamps,
      y: cashVals,
      name: "Cash ($)",
      line: { color: "#ffaa00", width: 1.5 },
      stackgroup: "one",
      fillcolor: "rgba(255, 170, 0, 0.1)",
    };

    const layout = {
      paper_bgcolor: "transparent",
      plot_bgcolor: "transparent",
      font: { color: "#8a99ad", family: "Inter, sans-serif" },
      margin: { l: 50, r: 20, t: 20, b: 30 },
      xaxis: { type: "date", gridcolor: "rgba(255, 255, 255, 0.05)" },
      yaxis: {
        gridcolor: "rgba(255, 255, 255, 0.05)",
        tickprefix: "$",
      },
      legend: { orientation: "h", y: 1.15, font: { color: "#fff" } },
    };

    Plotly.newPlot(chartDiv, [cryptoTrace, stockTrace, cashTrace, navTrace], layout, {
      responsive: true,
      displayModeBar: false,
    });
  } catch (err) {
    console.warn("Could not render portfolio historical chart:", err);
  }
}

function renderRebalanceTargetInputs() {
  const grid = document.getElementById("rebalance-targets-grid");
  if (!grid) return;

  const symbols = Object.keys(portfolioTargetWeights);
  grid.innerHTML = symbols
    .map((sym) => {
      const w = portfolioTargetWeights[sym];
      return `
      <div class="form-group" style="margin-bottom: 0; background: rgba(255,255,255,0.03); border: 1px solid rgba(255,255,255,0.08); border-radius: 6px; padding: 8px 10px;">
        <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 4px;">
          <label style="font-size: 0.78rem; font-weight: 600; color: #fff; text-transform: uppercase;">${sym}</label>
          <button class="btn-micro" style="width: 18px; height: 18px; font-size: 9px; background: rgba(255,0,122,0.15); color: #ff007a; border: none;" onclick="removeTargetAsset('${sym}')" title="Remove asset">✕</button>
        </div>
        <div style="display: flex; align-items: center; gap: 6px;">
          <input type="number" value="${w}" min="0" max="100" class="form-control" style="font-size: 0.85rem; padding: 4px 6px;" onchange="updateTargetWeight('${sym}', this.value)">
          <span style="font-size: 0.8rem; color: var(--text-muted);">%</span>
        </div>
      </div>
    `;
    })
    .join("");
}

function updateTargetWeight(sym, val) {
  portfolioTargetWeights[sym] = parseFloat(val) || 0;
}

function removeTargetAsset(sym) {
  delete portfolioTargetWeights[sym];
  renderRebalanceTargetInputs();
  calculateRebalancePlan();
}

function addCustomTargetAsset(sym, initialWeight = 10) {
  if (!sym) return;
  sym = sym.toUpperCase().trim();
  portfolioTargetWeights[sym] = initialWeight;
  renderRebalanceTargetInputs();
  calculateRebalancePlan();
}

function applyRebalancePreset(presetName) {
  if (presetName === "60-40") {
    portfolioTargetWeights = {
      "AAPL": 30,
      "SPY": 30,
      "BTC/USD": 25,
      "ETH/USD": 15,
    };
  } else if (presetName === "risk-parity") {
    // Risk parity: allocate inversely proportional to annualized volatility
    portfolioTargetWeights = {
      "SPY": 40,
      "AAPL": 25,
      "BTC/USD": 20,
      "ETH/USD": 15,
    };
  } else if (presetName === "equal-weight") {
    const keys = Object.keys(portfolioTargetWeights);
    const n = keys.length || 4;
    const eqW = Math.round(100.0 / n);
    keys.forEach((k) => {
      portfolioTargetWeights[k] = eqW;
    });
  } else if (presetName === "crypto-heavy") {
    portfolioTargetWeights = {
      "BTC/USD": 45,
      "ETH/USD": 25,
      "AAPL": 15,
      "SPY": 15,
    };
  } else if (presetName === "equities-core") {
    portfolioTargetWeights = {
      "SPY": 50,
      "AAPL": 30,
      "BTC/USD": 15,
      "ETH/USD": 5,
    };
  }

  renderRebalanceTargetInputs();
  calculateRebalancePlan();
}

function renderPortfolioAllocationChart(s) {
  const chartDiv = document.getElementById("portfolio-allocation-chart");
  if (!chartDiv || typeof Plotly === "undefined") return;

  const labels = ["Crypto", "US Equities", "Cash & Stablecoins"];
  const values = [s.total_crypto_usd, s.total_stock_usd, s.total_cash_usd + s.total_stablecoin_usd];
  const colors = ["#b87cf8", "#00f5a0", "#00f2fe"];

  const data = [
    {
      type: "pie",
      hole: 0.55,
      labels: labels,
      values: values,
      marker: { colors: colors },
      textinfo: "label+percent",
      hoverinfo: "label+value+percent",
      textposition: "outside",
    },
  ];

  const layout = {
    paper_bgcolor: "transparent",
    plot_bgcolor: "transparent",
    font: { color: "#8a99ad", family: "Inter, sans-serif" },
    margin: { l: 20, r: 20, t: 20, b: 20 },
    showlegend: false,
  };

  Plotly.newPlot(chartDiv, data, layout, { responsive: true, displayModeBar: false });
}

function renderPortfolioCorrelationHeatmap(metrics) {
  const chartDiv = document.getElementById("portfolio-correlation-chart");
  if (!chartDiv || typeof Plotly === "undefined" || !metrics) return;

  const data = [
    {
      type: "heatmap",
      z: metrics.correlation_matrix,
      x: metrics.symbols,
      y: metrics.symbols,
      colorscale: [
        [0.0, "#00f2fe"],
        [0.5, "#1a1f2c"],
        [1.0, "#ff007a"],
      ],
      zmin: -1.0,
      zmax: 1.0,
      showscale: true,
    },
  ];

  const layout = {
    paper_bgcolor: "transparent",
    plot_bgcolor: "transparent",
    font: { color: "#8a99ad", family: "Inter, sans-serif" },
    margin: { l: 60, r: 20, t: 20, b: 40 },
    xaxis: { gridcolor: "rgba(255,255,255,0.05)" },
    yaxis: { gridcolor: "rgba(255,255,255,0.05)" },
  };

  Plotly.newPlot(chartDiv, data, layout, { responsive: true, displayModeBar: false });
}

function renderPortfolioRiskDecomposition(riskReport) {
  const chartDiv = document.getElementById("portfolio-risk-decomp-chart");
  if (!chartDiv || typeof Plotly === "undefined" || !riskReport) return;

  const compVar = riskReport.component_var || {};
  const symbols = Object.keys(compVar);
  if (symbols.length === 0) return;

  const weights = symbols.map((s) => compVar[s].weight_pct);
  const riskContribs = symbols.map((s) => compVar[s].risk_contribution_pct);

  const data = [
    {
      name: "Weight (%)",
      type: "bar",
      x: symbols,
      y: weights,
      marker: { color: "#00f2fe" },
    },
    {
      name: "Risk Contribution (%)",
      type: "bar",
      x: symbols,
      y: riskContribs,
      marker: { color: "#ff007a" },
    },
  ];

  const layout = {
    paper_bgcolor: "transparent",
    plot_bgcolor: "transparent",
    barmode: "group",
    font: { color: "#8a99ad", family: "Inter, sans-serif" },
    margin: { l: 40, r: 20, t: 20, b: 40 },
    xaxis: { gridcolor: "rgba(255,255,255,0.05)" },
    yaxis: { gridcolor: "rgba(255,255,255,0.05)", title: "Percentage (%)" },
    legend: { orientation: "h", y: 1.15, font: { color: "#fff" } },
  };

  Plotly.newPlot(chartDiv, data, layout, { responsive: true, displayModeBar: false });
}

function renderPortfolioVenues(venues) {
  const grid = document.getElementById("portfolio-venues-grid");
  if (!grid) return;

  const keys = Object.keys(venues);
  if (keys.length === 0) {
    grid.innerHTML = `<div class="empty-msg">No venue balances found.</div>`;
    return;
  }

  grid.innerHTML = keys
    .map((k) => {
      const v = venues[k];
      const isOnline = v.is_connected;
      const statusBadge = isOnline
        ? `<span class="badge badge-green" style="font-size:0.75rem;">CONNECTED</span>`
        : `<span class="badge" style="background:rgba(255,255,255,0.1); font-size:0.75rem; color:var(--text-muted);">DISCONNECTED</span>`;

      const typeBadge = v.venue_type === "stock"
        ? `<span class="badge badge-blue" style="font-size:0.7rem;">STOCK</span>`
        : (v.venue_type === "crypto"
          ? `<span class="badge badge-purple" style="font-size:0.7rem;">CRYPTO</span>`
          : `<span class="badge badge-cyan" style="font-size:0.7rem;">PAPER</span>`);

      return `
      <div class="kpi-card" style="background: rgba(255, 255, 255, 0.03); border: 1px solid rgba(255, 255, 255, 0.08); padding: 14px;">
        <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px;">
          <strong style="text-transform: capitalize; color: #fff; font-size: 0.95rem;">${v.venue}</strong>
          <div>${typeBadge} ${statusBadge}</div>
        </div>
        <div style="font-size: 1.2rem; font-weight: 700; color: #00f5a0;">$${v.total_nav_usd.toLocaleString()}</div>
        <div style="font-size: 0.78rem; color: var(--text-muted); margin-top: 4px;">
          Cash: $${v.cash_usd.toLocaleString()} | Power: $${v.buying_power_usd.toLocaleString()}
        </div>
      </div>
    `;
    })
    .join("");
}

function renderPortfolioPositions(positions) {
  const tbody = document.getElementById("portfolio-positions-body");
  const countEl = document.getElementById("portfolio-positions-count");
  if (!tbody) return;

  if (countEl) countEl.textContent = `${positions.length} Holdings`;

  if (positions.length === 0) {
    tbody.innerHTML = `<tr><td colspan="11" class="empty-msg">No active cross-asset holdings.</td></tr>`;
    return;
  }

  tbody.innerHTML = positions
    .map((p) => {
      const pnlColor = p.unrealized_pnl_usd >= 0 ? "#00f5a0" : "#ff007a";
      const typeBadge = p.asset_type === "stock"
        ? `<span class="badge badge-blue">STOCK</span>`
        : `<span class="badge badge-purple">CRYPTO</span>`;

      return `
      <tr>
        <td style="font-weight: 600; color: #fff;">${p.symbol}</td>
        <td style="text-transform: capitalize;">${p.venue}</td>
        <td>${typeBadge}</td>
        <td><span class="badge badge-long">${p.side === 1 ? "LONG" : "SHORT"}</span></td>
        <td>${p.quantity.toLocaleString()}</td>
        <td>${formatPrice(p.entry_price)}</td>
        <td>${formatPrice(p.current_price)}</td>
        <td style="font-weight: 600;">$${p.market_value_usd.toLocaleString()}</td>
        <td style="color: ${pnlColor}; font-weight: 600;">$${p.unrealized_pnl_usd >= 0 ? "+" : ""}${p.unrealized_pnl_usd.toLocaleString()}</td>
        <td style="color: ${pnlColor}; font-weight: 600;">${p.unrealized_pnl_pct >= 0 ? "+" : ""}${p.unrealized_pnl_pct.toFixed(2)}%</td>
        <td style="font-weight: 600; color: var(--accent-cyan);">${p.allocation_pct}%</td>
      </tr>
    `;
    })
    .join("");
}

async function calculateRebalancePlan() {
  const driftThresh = parseFloat(document.getElementById("rebalance-drift-thresh")?.value) || 2.0;

  const targetWeightsNormalized = {};
  for (const [sym, w] of Object.entries(portfolioTargetWeights)) {
    targetWeightsNormalized[sym] = (parseFloat(w) || 0) / 100.0;
  }

  const tbody = document.getElementById("rebalance-plan-body");
  const statusBadge = document.getElementById("rebalance-status-badge");
  if (tbody) tbody.innerHTML = `<tr><td colspan="11" class="empty-msg">Calculating plan...</td></tr>`;

  try {
    const resp = await fetch("/api/portfolio/rebalance/plan", {
      headers: getAuthHeaders(),
      method: "POST",
      headers: getAuthHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify({
        target_weights: targetWeightsNormalized,
        drift_threshold_pct: driftThresh,
        include_synthetic: true,
      }),
    });

    const data = await resp.json();
    if (!resp.ok || !data.plan) {
      alert("Rebalance error: " + (data.detail || "Could not generate plan"));
      return;
    }

    const plan = data.plan;
    if (statusBadge) {
      statusBadge.textContent = plan.requires_rebalancing
        ? `Drift: Rebalancing Required ($${plan.total_turnover_usd.toLocaleString()} turnover)`
        : `Drift: Portfolio Balanced (No trades needed)`;
      statusBadge.className = plan.requires_rebalancing ? "badge badge-amber" : "badge badge-green";
    }

    if (tbody) {
      tbody.innerHTML = (plan.items || [])
        .map((item) => {
          const actionBadge = item.trade_side === 1
            ? `<span class="badge badge-long">BUY</span>`
            : (item.trade_side === -1
              ? `<span class="badge badge-short">SELL</span>`
              : `<span class="badge">HOLD</span>`);

          const deltaColor = item.trade_value_usd > 0 ? "#00f5a0" : (item.trade_value_usd < 0 ? "#ff007a" : "var(--text-muted)");

          return `
          <tr>
            <td style="font-weight: 600; color: #fff;">${item.symbol}</td>
            <td style="text-transform: capitalize;">${item.venue}</td>
            <td>${item.current_weight_pct}%</td>
            <td>${item.target_weight_pct}%</td>
            <td style="font-weight: 600;">${item.drift_pct > 0 ? "+" : ""}${item.drift_pct}%</td>
            <td>$${item.current_value_usd.toLocaleString()}</td>
            <td>$${item.target_value_usd.toLocaleString()}</td>
            <td style="color: ${deltaColor}; font-weight: 600;">$${item.trade_value_usd > 0 ? "+" : ""}${item.trade_value_usd.toLocaleString()}</td>
            <td>${actionBadge}</td>
            <td>${item.estimated_units.toLocaleString()}</td>
            <td><span class="badge">${item.status}</span></td>
          </tr>
        `;
        })
        .join("");
    }
  } catch (err) {
    alert("Rebalance Plan error: " + err.message);
  }
}

async function executePortfolioRebalance(dryRun) {
  const driftThresh = parseFloat(document.getElementById("rebalance-drift-thresh")?.value) || 2.0;

  const targetWeightsNormalized = {};
  for (const [sym, w] of Object.entries(portfolioTargetWeights)) {
    targetWeightsNormalized[sym] = (parseFloat(w) || 0) / 100.0;
  }

  try {
    const resp = await fetch("/api/portfolio/rebalance/execute", {
      headers: getAuthHeaders(),
      method: "POST",
      headers: getAuthHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify({
        target_weights: targetWeightsNormalized,
        drift_threshold_pct: driftThresh,
        dry_run: dryRun,
        include_synthetic: true,
      }),
    });

    const data = await resp.json();
    if (!resp.ok) {
      alert("Execute error: " + (data.detail || "Could not execute rebalance"));
      return;
    }

    alert(
      dryRun
        ? `✅ Dry-Run Rebalance Simulated! Dispatched ${data.executed_orders_count} orders.`
        : `🚀 Live Rebalance Executed! Dispatched ${data.executed_orders_count} orders.`
    );
    await loadPortfolioDashboard();
    await calculateRebalancePlan();
  } catch (err) {
    alert("Execution error: " + err.message);
  }
}

// Wire up lookback buttons & preset button listeners on page load
document.addEventListener("DOMContentLoaded", () => {
  const lookbackBtns = [
    { id: "port-lookback-30d", days: 30 },
    { id: "port-lookback-90d", days: 90 },
    { id: "port-lookback-180d", days: 180 },
  ];

  lookbackBtns.forEach(({ id, days }) => {
    const btn = document.getElementById(id);
    if (btn) {
      btn.addEventListener("click", () => {
        lookbackBtns.forEach((b) => {
          const el = document.getElementById(b.id);
          if (el) el.classList.remove("active");
        });
        btn.classList.add("active");
        renderPortfolioHistoricalChart(days);
      });
    }
  });

  const presetBtns = [
    { id: "preset-60-40-btn", preset: "60-40" },
    { id: "preset-risk-parity-btn", preset: "risk-parity" },
    { id: "preset-equal-weight-btn", preset: "equal-weight" },
    { id: "preset-crypto-heavy-btn", preset: "crypto-heavy" },
    { id: "preset-equities-core-btn", preset: "equities-core" },
  ];

  presetBtns.forEach(({ id, preset }) => {
    const btn = document.getElementById(id);
    if (btn) {
      btn.addEventListener("click", () => applyRebalancePreset(preset));
    }
  });

  const addAssetBtn = document.getElementById("add-target-asset-btn");
  const addAssetInput = document.getElementById("add-target-asset-input");
  if (addAssetBtn && addAssetInput) {
    addAssetBtn.addEventListener("click", () => {
      const sym = addAssetInput.value.trim();
      if (sym) {
        addCustomTargetAsset(sym, 10);
        addAssetInput.value = "";
      }
    });
  }

  const rigorPresetSelect = document.getElementById("setting-val-rigor-preset");
  const valTrialsInput = document.getElementById("setting-val-trials");
  if (rigorPresetSelect && valTrialsInput) {
    rigorPresetSelect.addEventListener("change", () => {
      const val = rigorPresetSelect.value;
      if (val === "fast") valTrialsInput.value = 150;
      else if (val === "standard") valTrialsInput.value = 250;
      else if (val === "thorough") valTrialsInput.value = 500;
    });
  }
});
