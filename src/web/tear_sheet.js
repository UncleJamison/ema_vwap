/* =========================================================================
   Tear Sheet Export Functionality
   ========================================================================= */

let _tearSheetGenerationInProgress = false;
let _lastTearSheetBlob = null;
let _lastTearSheetFilename = null;

async function updateTearSheetStatus() {
  // Check if there's a cached validation report available
  const cachedKeys = Object.keys(window._validationReportCache || {});
  const statusBadge = document.getElementById('tear-sheet-status-badge');
  const statusDesc = document.getElementById('tear-sheet-status-desc');
  const generateBtn = document.getElementById('tear-sheet-generate-btn');

  if (cachedKeys.length > 0) {
    // Use the most recent validation report
    const latestKey = cachedKeys[cachedKeys.length - 1];
    const report = window._validationReportCache[latestKey];
    const context = window._validationContextCache[latestKey];

    if (statusBadge) {
      const status = (report?.status || 'UNKNOWN').toUpperCase();
      statusBadge.textContent = `Validation: ${status} (Score: ${report?.overall_score || '--'}/100)`;
      statusBadge.className = 'badge';
      if (status === 'ROBUST') statusBadge.style.background = 'rgba(0, 245, 160, 0.15)';
      else if (status === 'CAUTION') statusBadge.style.background = 'rgba(255, 170, 0, 0.15)';
      else statusBadge.style.background = 'rgba(255, 0, 122, 0.15)';
    }
    if (statusDesc) {
      const sym = context?.symbol || 'Unknown';
      const tf = context?.timeframe || 'Unknown';
      const ex = context?.exchange || 'Unknown';
      statusDesc.textContent = `Ready to generate tear sheet for ${sym} (${tf}, ${ex})`;
    }
    if (generateBtn) {
      generateBtn.disabled = false;
      generateBtn.innerHTML = `<span class="btn-icon">📄</span> Generate`;
    }
  } else {
    if (statusBadge) {
      statusBadge.textContent = 'No validation report available';
      statusBadge.className = 'badge badge-cyan';
    }
    if (statusDesc) {
      statusDesc.textContent = 'Run a backtest, optimization, or validation first to enable tear sheet generation.';
    }
    if (generateBtn) {
      generateBtn.disabled = true;
      generateBtn.innerHTML = `<span class="btn-icon">⏳</span> Generate`;
    }
  }
}

async function generateTearSheet() {
  if (_tearSheetGenerationInProgress) return;

  const format = document.getElementById('tear-sheet-format')?.value || 'pdf';
  const mcSims = parseInt(document.getElementById('tear-sheet-mc-sims')?.value) || 500;

  // Get the latest validation report and context
  const cachedKeys = Object.keys(window._validationReportCache || {});
  if (cachedKeys.length === 0) {
    alert('No validation report available. Please run a validation first.');
    return;
  }

  const latestKey = cachedKeys[cachedKeys.length - 1];
  const validationReport = window._validationReportCache[latestKey];
  const context = window._validationContextCache[latestKey];

  if (!context || !context.params) {
    alert('No strategy parameters available for tear sheet generation.');
    return;
  }

  const generateBtn = document.getElementById('tear-sheet-generate-btn');
  const downloadBtn = document.getElementById('tear-sheet-download-btn');
  const progressDiv = document.getElementById('tear-sheet-progress');
  const progressBar = document.getElementById('tear-sheet-progress-bar');
  const progressText = document.getElementById('tear-sheet-progress-text');
  const previewDiv = document.getElementById('tear-sheet-preview');
  const previewContent = document.getElementById('tear-sheet-preview-content');

  _tearSheetGenerationInProgress = true;

  // Show progress
  if (generateBtn) {
    generateBtn.disabled = true;
    generateBtn.innerHTML = `<span class="btn-icon">⏳</span> Generating...`;
  }
  if (progressDiv) progressDiv.style.display = 'flex';
  if (downloadBtn) downloadBtn.style.display = 'none';
  if (previewDiv) previewDiv.style.display = 'none';

  // Animate progress bar
  let progress = 0;
  const progressInterval = setInterval(() => {
    progress = Math.min(progress + Math.random() * 15, 90);
    if (progressBar) progressBar.style.width = `${progress}%`;
    if (progressText) progressText.textContent = `Processing... ${Math.round(progress)}%`;
  }, 200);

  try {
    const payload = {
      exchange: context.exchange,
      symbol: context.symbol,
      timeframe: context.timeframe,
      params: context.params,
      validation_report: validationReport,
      mc_simulations: mcSims,
      target_metric: context.targetMetric,
      enable_multi_objective: context.enableMulti,
      multi_objective_metrics: context.multiMetrics,
    };

    let response;
    let blob;
    let filename;

    if (format === 'pdf') {
      response = await fetch('/api/tear-sheet/pdf', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      if (!response.ok) {
        const err = await response.json();
        throw new Error(err.detail || 'PDF generation failed');
      }
      blob = await response.blob();
      filename = `ema_vwap_tear_sheet_${context.symbol.replace('/', '_')}_${context.timeframe}_${new Date().toISOString().slice(0,19).replace(/:/g, '-')}.pdf`;
    } else if (format === 'html') {
      response = await fetch('/api/tear-sheet/html', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      if (!response.ok) {
        const err = await response.json();
        throw new Error(err.detail || 'HTML generation failed');
      }
      const html = await response.text();
      blob = new Blob([html], { type: 'text/html' });
      filename = `ema_vwap_tear_sheet_${context.symbol.replace('/', '_')}_${context.timeframe}_${new Date().toISOString().slice(0,19).replace(/:/g, '-')}.html`;
    } else if (format === 'json') {
      response = await fetch('/api/tear-sheet/json', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      if (!response.ok) {
        const err = await response.json();
        throw new Error(err.detail || 'JSON generation failed');
      }
      const data = await response.json();
      blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
      filename = `ema_vwap_report_${context.symbol.replace('/', '_')}_${context.timeframe}_${new Date().toISOString().slice(0,19).replace(/:/g, '-')}.json`;
    }

    // Store blob and filename for download
    _lastTearSheetBlob = blob;
    _lastTearSheetFilename = filename;

    // Show preview info
    if (previewDiv && previewContent) {
      const sizeKB = (blob.size / 1024).toFixed(1);
      previewContent.innerHTML = `
        <strong>Format:</strong> ${format.toUpperCase()}<br>
        <strong>Size:</strong> ${sizeKB} KB<br>
        <strong>Symbol:</strong> ${context.symbol}<br>
        <strong>Timeframe:</strong> ${context.timeframe}<br>
        <strong>Exchange:</strong> ${context.exchange}<br>
        <strong>Status:</strong> ${validationReport?.status || 'N/A'} (Score: ${validationReport?.overall_score || 'N/A'}/100)
      `;
      previewDiv.style.display = 'block';
    }

    // Enable download button
    if (downloadBtn) {
      downloadBtn.style.display = 'inline-flex';
      downloadBtn.onclick = () => {
        const url = URL.createObjectURL(_lastTearSheetBlob);
        const a = document.createElement('a');
        a.href = url;
        a.download = _lastTearSheetFilename;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(url);
      };
    }

    clearInterval(progressInterval);
    if (progressBar) progressBar.style.width = '100%';
    if (progressText) progressText.textContent = 'Complete!';

    setTimeout(() => {
      if (progressDiv) progressDiv.style.display = 'none';
    }, 1500);

  } catch (err) {
    clearInterval(progressInterval);
    alert(`Tear sheet generation failed: ${err.message}`);
    if (progressDiv) progressDiv.style.display = 'none';
  } finally {
    _tearSheetGenerationInProgress = false;
    if (generateBtn) {
      generateBtn.disabled = false;
      generateBtn.innerHTML = `<span class="btn-icon">📄</span> Generate`;
    }
  }
}

// Wire up tear sheet tab when it becomes visible
function initTearSheetTab() {
  const generateBtn = document.getElementById('tear-sheet-generate-btn');
  if (generateBtn && !generateBtn.dataset.listenerAdded) {
    generateBtn.addEventListener('click', generateTearSheet);
    generateBtn.dataset.listenerAdded = 'true';
  }

  // Update status when tab is shown
  const tearSheetTab = document.getElementById('tab-tear-sheet');
  if (tearSheetTab) {
    // Use MutationObserver to detect when tab becomes active
    const observer = new MutationObserver((mutations) => {
      for (const mutation of mutations) {
        if (mutation.attributeName === 'class' && tearSheetTab.classList.contains('active')) {
          updateTearSheetStatus();
        }
      }
    });
    observer.observe(tearSheetTab, { attributes: true });

    // Also check immediately if already active
    if (tearSheetTab.classList.contains('active')) {
      updateTearSheetStatus();
    }
  }
}

// Initialize tear sheet tab on DOMContentLoaded
document.addEventListener('DOMContentLoaded', () => {
  // Small delay to ensure DOM is fully ready
  setTimeout(initTearSheetTab, 100);
});

// Also update tear sheet status when validation completes
const originalShowValidationDetailsModal = window.showValidationDetailsModal;
if (originalShowValidationDetailsModal) {
  window.showValidationDetailsModal = function(...args) {
    const result = originalShowValidationDetailsModal.apply(this, args);
    // Update tear sheet status after validation
    setTimeout(updateTearSheetStatus, 100);
    return result;
  };
}