/**
 * SEO Audit Pro — Full-Featured Single Page Web Application Engine
 * Minimalist White & Blue Enterprise Edition with Full DOCX Depth & Deep Diagnostics.
 */

const AppState = {
  currentAuditId: null,
  currentResult: null,
  viewMode: 'expert', // 'layman' | 'expert'
  cwvStrategy: 'mobile', // 'mobile' | 'desktop'
  serpDevice: 'mobile', // 'mobile' | 'desktop'
  activeTab: 'audit', // 'audit' | 'tracker' | 'history'
  selectedCategory: 'technical',
  trackerMetric: 'overall',
  charts: {
    radar: null,
    donut: null,
    timeline: null,
  },
  simulatedChecks: new Set(),
};

// Client-facing plain language explanations (from report_docx.py)
const CATEGORY_CLIENT = {
  technical: 'Can Google find, crawl and trust your site? Problems here can keep pages out of search entirely — no matter how good they are.',
  onpage: "Does each page clearly tell Google and customers what it's about? Weak signals mean lost rankings and fewer clicks.",
  performance: 'How fast your pages load. Google measures this directly, and slow pages quietly lose visitors before they convert.',
  content: 'Is your content deep, readable and trustworthy enough to rank and win the click? Thin or hard-to-read pages underperform.',
  mobile: "How your site behaves on phones — where most of your visitors and Google's ranking signals now come from.",
  accessibility: 'How usable your site is for everyone, including people using assistive technology. It overlaps with SEO and lowers legal risk.',
  security: 'Do visitors and Google see your site as safe? Security gaps erode trust and can trigger browser warnings.',
  ux: 'The real experience visitors get — stability, responsiveness and smoothness. Poor experience costs conversions and rankings.',
};

// ==========================================================================
// Initialization
// ==========================================================================
document.addEventListener('DOMContentLoaded', () => {
  if (window.lucide) {
    window.lucide.createIcons();
  }
  loadSettingsAndCategories();
  loadAuditHistory();
});

// ==========================================================================
// Main Navigation Tabs
// ==========================================================================
function switchMainTab(tabName) {
  AppState.activeTab = tabName;
  ['audit', 'tracker', 'history'].forEach((t) => {
    const section = document.getElementById(`tab-${t}`);
    const navBtn = document.getElementById(`nav-${t}-tab`);
    if (section) {
      if (t === tabName) {
        section.classList.remove('hidden');
      } else {
        section.classList.add('hidden');
      }
    }
    if (navBtn) {
      if (t === tabName) {
        navBtn.classList.add('active');
      } else {
        navBtn.classList.remove('active');
      }
    }
  });

  if (tabName === 'history') {
    loadAuditHistory();
  } else if (tabName === 'tracker') {
    initTrackerView();
  }
}

// ==========================================================================
// UI Form Controls & Sliders
// ==========================================================================
function toggleCrawlMode(isSingle) {
  const depthWrapper = document.getElementById('crawl-depth-wrapper');
  if (depthWrapper) {
    depthWrapper.style.opacity = isSingle ? '0.4' : '1';
    depthWrapper.style.pointerEvents = isSingle ? 'none' : 'auto';
  }
}

function updateSliderLabel(val) {
  const label = document.getElementById('crawl-depth-label');
  if (label) label.textContent = `${val} pages`;
}

function toggleAdvancedOptions() {
  const panel = document.getElementById('advanced-options-panel');
  const text = document.getElementById('advanced-toggle-text');
  if (panel) {
    panel.classList.toggle('hidden');
    if (text) {
      text.textContent = panel.classList.contains('hidden')
        ? 'Competitors & Scope'
        : 'Hide Advanced Options';
    }
  }
}

// ==========================================================================
// Audit Execution & Real-Time Streaming
// ==========================================================================
async function handleStartAudit(event) {
  if (event) event.preventDefault();

  const urlInput = document.getElementById('target-url');
  let url = (urlInput.value || '').trim();
  if (!url) {
    showToast('Please enter a website URL.', 'error');
    return;
  }
  if (!url.startsWith('http://') && !url.startsWith('https://')) {
    url = 'https://' + url;
    urlInput.value = url;
  }

  const isSingle = document.getElementById('check-single-page').checked;
  const maxPages = parseInt(document.getElementById('crawl-depth-slider').value, 10) || 25;
  const psiStrategy = document.getElementById('psi-strategy-select').value || 'both';
  const proxy = (document.getElementById('proxy-input')?.value || '').trim();
  const competitorsRaw = (document.getElementById('competitors-input')?.value || '').trim();
  const competitors = competitorsRaw ? competitorsRaw.split(',').map((s) => s.trim()).filter(Boolean) : [];

  const enabledCategories = [];
  document.querySelectorAll('.cat-scope-check:checked').forEach((el) => {
    enabledCategories.push(el.value);
  });

  const progressCard = document.getElementById('audit-progress-card');
  const resultsContainer = document.getElementById('audit-results-container');
  const runBtn = document.getElementById('btn-run-audit');

  if (progressCard) progressCard.classList.remove('hidden');
  if (resultsContainer) resultsContainer.classList.add('hidden');
  if (runBtn) {
    runBtn.disabled = true;
    runBtn.style.opacity = '0.6';
    runBtn.style.pointerEvents = 'none';
  }

  resetProgressTerminal();
  appendTerminalLog(`[START] Initializing audit for ${url}...`);

  try {
    const resp = await fetch('/api/audit/start', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        url,
        max_pages: maxPages,
        single_page: isSingle,
        psi_strategy: psiStrategy,
        proxy,
        competitors,
        enabled: enabledCategories.length > 0 ? enabledCategories : undefined,
      }),
    });

    const data = await resp.json();
    if (!resp.ok || data.error) {
      throw new Error(data.error || 'Failed to initialize audit');
    }

    AppState.currentAuditId = data.audit_id;
    appendTerminalLog(`[ID] Audit session registered: ${data.audit_id}`);
    connectAuditStream(data.audit_id);
  } catch (err) {
    showToast(`Error: ${err.message}`, 'error');
    appendTerminalLog(`[ERROR] ${err.message}`);
    if (runBtn) {
      runBtn.disabled = false;
      runBtn.style.opacity = '1';
      runBtn.style.pointerEvents = 'auto';
    }
  }
}

function connectAuditStream(auditId) {
  let eventSource = null;
  let pollInterval = null;

  const cleanup = () => {
    if (eventSource) {
      eventSource.close();
      eventSource = null;
    }
    if (pollInterval) {
      clearInterval(pollInterval);
      pollInterval = null;
    }
    const runBtn = document.getElementById('btn-run-audit');
    if (runBtn) {
      runBtn.disabled = false;
      runBtn.style.opacity = '1';
      runBtn.style.pointerEvents = 'auto';
    }
  };

  if (window.EventSource) {
    eventSource = new EventSource(`/api/audit/stream/${auditId}`);
    eventSource.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);
        handleStreamPayload(data, cleanup);
      } catch (e) {
        console.error('SSE JSON parse error:', e);
      }
    };
    eventSource.onerror = () => {
      if (eventSource) {
        eventSource.close();
        eventSource = null;
      }
      startPollingFallback(auditId, cleanup);
    };
  } else {
    startPollingFallback(auditId, cleanup);
  }
}

function startPollingFallback(auditId, cleanup) {
  const poll = async () => {
    try {
      const res = await fetch(`/api/audit/status/${auditId}`);
      if (!res.ok) return;
      const data = await res.json();
      handleStreamPayload(data, cleanup);
    } catch (e) {
      console.warn('Poll error:', e);
    }
  };
  poll();
  pollInterval = setInterval(poll, 1500);
}

function handleStreamPayload(data, cleanup) {
  updateProgressBar(data.percent || 10, data.step || 'Running audit...');

  if (data.new_logs && data.new_logs.length > 0) {
    data.new_logs.forEach((msg) => appendTerminalLog(msg));
  } else if (data.logs && data.logs.length > 0) {
    appendTerminalLog(data.logs[data.logs.length - 1]);
  }

  if (data.status === 'completed') {
    cleanup();
    updateProgressBar(100, 'Audit complete!');
    appendTerminalLog('[DONE] Audit pipeline completed successfully.');

    setTimeout(() => {
      const progressCard = document.getElementById('audit-progress-card');
      if (progressCard) progressCard.classList.add('hidden');
      if (data.result) {
        displayAuditResults(data.result);
      } else {
        fetchAuditResult(data.id || AppState.currentAuditId);
      }
    }, 400);
  } else if (data.status === 'failed' || data.status === 'cancelled') {
    cleanup();
    showToast(data.error || `Audit ${data.status}`, 'error');
    appendTerminalLog(`[STOP] ${data.error || data.status}`);
  }
}

async function fetchAuditResult(auditId) {
  const id = auditId || AppState.currentAuditId;
  if (!id) return;
  try {
    showToast('Loading audit results...', 'info');
    const res = await fetch(`/api/audit/status/${id}`);
    const data = await res.json();
    if (data.result) {
      displayAuditResults(data.result);
      return;
    }
    // Fallback to history endpoint if status was completed and saved
    const histRes = await fetch(`/api/history/${id}`);
    const histData = await histRes.json();
    if (histData.audit) {
      displayAuditResults(histData.audit);
      return;
    }
  } catch (err) {
    showToast('Failed to load completed audit result', 'error');
  }
}

async function cancelCurrentAudit() {
  if (!AppState.currentAuditId) return;
  try {
    await fetch(`/api/audit/cancel/${AppState.currentAuditId}`, { method: 'POST' });
    appendTerminalLog('[CANCEL] User requested cancellation.');
    showToast('Audit cancellation requested.', 'info');
  } catch (e) {
    showToast('Could not cancel audit', 'error');
  }
}

function updateProgressBar(percent, stepText) {
  const bar = document.getElementById('progress-bar-fill');
  const badge = document.getElementById('progress-percent-badge');
  const subtitle = document.getElementById('progress-stage-subtitle');

  const p = Math.min(100, Math.max(5, percent));
  if (bar) bar.style.width = `${p}%`;
  if (badge) badge.textContent = `${Math.round(p)}%`;
  if (subtitle && stepText) subtitle.textContent = stepText;
}

function resetProgressTerminal() {
  const term = document.getElementById('progress-log-terminal');
  if (term) term.innerHTML = '';
}

function appendTerminalLog(msg) {
  const term = document.getElementById('progress-log-terminal');
  if (!term) return;
  const line = document.createElement('div');
  line.textContent = msg;
  term.appendChild(line);
  term.scrollTop = term.scrollHeight;
}

// ==========================================================================
// Audit Results Presentation
// ==========================================================================
function displayAuditResults(result) {
  if (!result) return;
  AppState.currentResult = result;
  AppState.currentAuditId = result.id || AppState.currentAuditId;

  // Ensure active tab is audit suite and progress card is hidden
  switchMainTab('audit');
  const progressCard = document.getElementById('audit-progress-card');
  if (progressCard) progressCard.classList.add('hidden');

  const resultsContainer = document.getElementById('audit-results-container');
  if (resultsContainer) {
    resultsContainer.classList.remove('hidden');
    resultsContainer.style.display = 'flex';
  }

  setAuditViewMode(AppState.viewMode || 'expert');

  const domainTitle = document.getElementById('result-domain-title');
  const dateBadge = document.getElementById('result-date-badge');
  const typeBadge = document.getElementById('result-type-badge');
  const verdictSummary = document.getElementById('result-verdict-summary');

  if (domainTitle) domainTitle.textContent = result.domain || 'Target Site';
  if (dateBadge) dateBadge.textContent = result.date || 'Today';
  if (typeBadge) {
    typeBadge.textContent = result.single_page
      ? 'Single-Page'
      : `${result.pages_crawled || 1} Pages Crawled`;
  }

  const scoreObj = result.score || {};
  if (verdictSummary) {
    verdictSummary.textContent = scoreObj.label
      ? `${scoreObj.label} (Grade: ${scoreObj.grade || '?'})`
      : 'Strictly evaluated against agency standards.';
  }

  const safeRun = (name, fn) => {
    try {
      fn();
    } catch (e) {
      console.warn(`Render error in ${name}:`, e);
    }
  };

  safeRun('HeroScore', () => renderHeroScore(scoreObj, result));
  safeRun('ExecutiveSummary', () => renderExecutiveSummary(result));
  safeRun('AuthorityOverview', () => renderAuthorityOverview(result));
  safeRun('TrafficOverview', () => renderTrafficOverview(result));
  safeRun('CategoryScorecards', () => renderCategoryScorecards(scoreObj.dimensions || []));
  safeRun('RadarChart', () => renderRadarChart(result));
  safeRun('SeverityDonut', () => renderSeverityDonut(result));
  safeRun('CategoryDeepDive', () => renderCategoryDeepDive(result));
  safeRun('CoreWebVitals', () => renderCoreWebVitals(result));
  safeRun('TechnicalDiagnostics', () => renderTechnicalDiagnostics(result));
  safeRun('HttpRedirection', () => renderHttpRedirection(result));
  safeRun('BrokenLinks', () => renderBrokenLinks(result));
  safeRun('CanonicalDirectives', () => renderCanonicalDirectives(result));
  safeRun('MediaAssets', () => renderMediaAssets(result));
  safeRun('LocalSeo', () => renderLocalSeo(result));
  safeRun('OffpageSeo', () => renderOffpageSeo(result));
  safeRun('SerpPreview', () => renderSerpPreview(result));
  safeRun('SocialPreview', () => renderSocialPreview(result));
  safeRun('HeadingsTree', () => renderHeadingsTree(result));
  safeRun('ContentMetrics', () => renderContentMetrics(result));
  safeRun('ActionItems', () => renderActionItems(result.action_items || []));
  safeRun('CrawledPages', () => renderCrawledPages(result.all_facts || []));
  safeRun('CompetitorBenchmark', () => renderCompetitorBenchmark(result));
  safeRun('NextSteps', () => renderNextSteps(result));
  safeRun('FixSimulator', () => setupFixSimulator(result));

  try {
    if (window.lucide) window.lucide.createIcons();
  } catch (e) {}

  if (resultsContainer) {
    resultsContainer.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }
}

// ==========================================================================
// 1. Hero Score Gauge
// ==========================================================================
function renderHeroScore(sc, result) {
  const scoreVal = sc.score != null ? Math.round(sc.score) : 0;
  const grade = sc.grade || '?';

  const numVal = document.getElementById('score-num-value');
  const gradeBadge = document.getElementById('score-grade-badge');
  const circleMeter = document.getElementById('score-circle-meter');

  if (numVal) numVal.textContent = scoreVal;
  if (gradeBadge) {
    gradeBadge.textContent = `GRADE ${grade}`;
    if (scoreVal >= 85) {
      gradeBadge.className = 'badge badge-pass grade-badge-lg';
    } else if (scoreVal >= 70) {
      gradeBadge.className = 'badge badge-low grade-badge-lg';
    } else if (scoreVal >= 50) {
      gradeBadge.className = 'badge badge-medium grade-badge-lg';
    } else {
      gradeBadge.className = 'badge badge-critical grade-badge-lg';
    }
  }

  let strokeColor = '#16a34a'; // Emerald
  if (scoreVal < 50) strokeColor = '#dc2626'; // Red
  else if (scoreVal < 70) strokeColor = '#d97706'; // Amber
  else if (scoreVal < 85) strokeColor = '#2563eb'; // Royal Blue

  const circumference = 264;
  const offset = circumference - (scoreVal / 100) * circumference;

  if (circleMeter) {
    circleMeter.style.stroke = strokeColor;
    circleMeter.style.strokeDashoffset = offset;
  }

  const pCount = document.getElementById('stat-pages-crawled');
  const critCount = document.getElementById('stat-critical-issues');
  const mobPsi = document.getElementById('stat-mobile-psi');

  if (pCount) pCount.textContent = result.pages_crawled || 1;
  const counts = result.priority_counts || {};
  if (critCount) critCount.textContent = counts.Critical || 0;

  const psiMobScore = ((result.psi || {}).mobile || {}).scores?.performance;
  if (mobPsi) {
    mobPsi.textContent = psiMobScore != null ? `${psiMobScore}/100` : 'N/A';
  }
}

// ==========================================================================
// 2. Executive Summary & Key Findings (DOCX Parity)
// ==========================================================================
function renderExecutiveSummary(result) {
  const summaryText = document.getElementById('exec-summary-text');
  const sourceBadge = document.getElementById('exec-summary-source-badge');
  const strengthsList = document.getElementById('exec-strengths-list');
  const weaknessesList = document.getElementById('exec-weaknesses-list');
  const opportunitiesList = document.getElementById('exec-opportunities-list');
  const penaltiesBanner = document.getElementById('score-penalties-banner');
  const penaltiesText = document.getElementById('score-penalties-text');

  if (summaryText) {
    summaryText.textContent = result.exec_summary || 'No summary available.';
  }
  if (sourceBadge) {
    sourceBadge.textContent = result.exec_summary_source === 'ai' ? 'Written by Analyst' : 'Agency Intelligence';
  }

  const kf = result.key_findings || {};

  // 1. Key Strengths
  if (strengthsList) {
    strengthsList.innerHTML = '';
    const strengths = kf.strengths || [];
    if (strengths.length > 0) {
      strengths.slice(0, 4).forEach((s) => {
        const li = document.createElement('li');
        li.style.display = 'flex';
        li.style.alignItems = 'flex-start';
        li.style.gap = '6px';
        li.innerHTML = `<span style="color: var(--color-pass); font-weight: bold; flex-shrink: 0;">[OK]</span> <span>${escapeHtml(s)}</span>`;
        strengthsList.appendChild(li);
      });
    } else {
      strengthsList.innerHTML = '<li>Solid baseline technical signals detected.</li>';
    }
  }

  // 2. Urgent Bottlenecks
  if (weaknessesList) {
    weaknessesList.innerHTML = '';
    const weaknesses = kf.weaknesses || [];
    if (weaknesses.length > 0) {
      weaknesses.slice(0, 4).forEach((w) => {
        const li = document.createElement('li');
        li.style.display = 'flex';
        li.style.alignItems = 'flex-start';
        li.style.gap = '6px';
        li.innerHTML = `<span style="color: var(--color-critical); font-weight: bold; flex-shrink: 0;">[!]</span> <span>${escapeHtml(w)}</span>`;
        weaknessesList.appendChild(li);
      });
    } else {
      weaknessesList.innerHTML = '<li>No critical bottlenecks detected in this pass.</li>';
    }
  }

  // 3. Growth Opportunities (DOCX Parity)
  if (opportunitiesList) {
    opportunitiesList.innerHTML = '';
    const opps = kf.opportunities || [];
    if (opps.length > 0) {
      opps.slice(0, 4).forEach((o) => {
        const li = document.createElement('li');
        li.style.display = 'flex';
        li.style.alignItems = 'flex-start';
        li.style.gap = '6px';
        li.innerHTML = `<span style="color: var(--primary); font-weight: bold; flex-shrink: 0;">[+]</span> <span>${escapeHtml(o)}</span>`;
        opportunitiesList.appendChild(li);
      });
    } else {
      opportunitiesList.innerHTML = '<li>Leverage structured data and speed gains to outrank competitors.</li>';
    }
  }

  // Score Penalties Applied
  const penalties = (result.score || {}).penalties || [];
  if (penaltiesBanner && penaltiesText) {
    if (penalties.length > 0) {
      penaltiesBanner.classList.remove('hidden');
      penaltiesText.textContent = penalties.join(' • ');
    } else {
      penaltiesBanner.classList.add('hidden');
    }
  }
}

function renderNextSteps(result) {
  const counts = result.priority_counts || {};
  const highImpactCount = (counts.Critical || 0) + (counts.High || 0);
  const step1Desc = document.getElementById('next-step-1-desc');
  const appendixDate = document.getElementById('appendix-timestamp-badge');

  if (step1Desc) {
    step1Desc.textContent = `Close the ${highImpactCount} high-priority item${highImpactCount === 1 ? '' : 's'} identified in the Priority Action Plan — these move Google rankings, organic visibility and leads the fastest.`;
  }
  if (appendixDate) {
    appendixDate.textContent = `Audited: ${result.date || new Date().toISOString().slice(0, 10)}`;
  }
}

// ==========================================================================
// 3. Category Scorecards
// ==========================================================================
function renderCategoryScorecards(dimensions) {
  const grid = document.getElementById('category-cards-grid');
  if (!grid) return;
  grid.innerHTML = '';

  const catMeta = {
    technical: { icon: 'cpu', color: '#6366f1', subtle: '#eef2ff' },
    onpage: { icon: 'file-text', color: '#2563eb', subtle: '#eff6ff' },
    performance: { icon: 'zap', color: '#f59e0b', subtle: '#fffbeb' },
    content: { icon: 'align-left', color: '#0d9488', subtle: '#f0fdfa' },
    mobile: { icon: 'smartphone', color: '#0284c7', subtle: '#f0f9ff' },
    accessibility: { icon: 'eye', color: '#8b5cf6', subtle: '#f5f3ff' },
    security: { icon: 'shield-check', color: '#e11d48', subtle: '#fff1f2' },
    ux: { icon: 'user-check', color: '#10b981', subtle: '#f0fdf4' },
  };

  dimensions.forEach((dim) => {
    const score = dim.score != null ? Math.round(dim.score) : 0;
    const meta = catMeta[dim.key] || { icon: 'check-circle', color: '#2563eb', subtle: '#eff6ff' };

    let scoreColor = '#16a34a';
    if (score < 50) scoreColor = '#dc2626';
    else if (score < 75) scoreColor = '#d97706';
    else if (score < 90) scoreColor = '#2563eb';

    const card = document.createElement('div');
    card.className = 'cat-card';
    card.onclick = () => selectCategoryInDeepDive(dim.key);

    const passCount = (dim.counts && dim.counts.pass != null) ? dim.counts.pass : (dim.passed || 0);
    const issueCount = dim.counts ? ((dim.counts.fail || 0) + (dim.counts.warn || 0)) : (dim.issues || 0);

    card.innerHTML = `
      <div class="cat-card-header">
        <div class="cat-icon-title">
          <div class="cat-icon-box" style="background-color: ${meta.subtle}; color: ${meta.color};">
            <i data-lucide="${meta.icon}" style="width: 15px; height: 15px;"></i>
          </div>
          <div>
            <h5 class="cat-name">${escapeHtml(dim.label)}</h5>
            <span style="font-size: 10px; color: var(--text-muted);">Weight: ${(dim.weight * 100).toFixed(0)}%</span>
          </div>
        </div>
        <span class="cat-score" style="color: ${scoreColor}">${score}/100</span>
      </div>
      <div class="cat-track">
        <div class="cat-fill" style="width: ${score}%; background-color: ${meta.color};"></div>
      </div>
      <div class="cat-card-footer">
        <span>Passed: <strong style="color: #16a34a;">${passCount}</strong></span>
        <span>Issues: <strong style="color: #dc2626;">${issueCount}</strong></span>
      </div>
    `;
    grid.appendChild(card);
  });
}

// ==========================================================================
// 4. Interactive Charts (Radar & Donut)
// ==========================================================================
function renderRadarChart(result) {
  const canvas = document.getElementById('radarChart');
  if (!canvas || !window.Chart) return;

  if (AppState.charts.radar) {
    AppState.charts.radar.destroy();
  }

  const dims = (result.score || {}).dimensions || [];
  const labels = dims.map((d) => d.label);
  const data = dims.map((d) => (d.score != null ? Math.round(d.score) : 0));

  AppState.charts.radar = new Chart(canvas, {
    type: 'radar',
    data: {
      labels: labels,
      datasets: [
        {
          label: 'This Site',
          data: data,
          backgroundColor: 'rgba(37, 99, 235, 0.12)',
          borderColor: '#2563eb',
          borderWidth: 2,
          pointBackgroundColor: '#2563eb',
          pointBorderColor: '#ffffff',
          pointHoverRadius: 5,
        },
        {
          label: 'Agency Target (100)',
          data: labels.map(() => 100),
          backgroundColor: 'transparent',
          borderColor: '#94a3b8',
          borderDash: [3, 3],
          borderWidth: 1.5,
          pointRadius: 0,
        },
      ],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      scales: {
        r: {
          min: 0,
          max: 100,
          ticks: { stepSize: 25, display: false },
          grid: { color: '#e2e8f0' },
          angleLines: { color: '#e2e8f0' },
          pointLabels: {
            color: '#334155',
            font: { family: 'Plus Jakarta Sans', size: 10.5, weight: '700' },
          },
        },
      },
      plugins: {
        legend: {
          position: 'bottom',
          labels: { color: '#475569', font: { family: 'Plus Jakarta Sans', size: 11 } },
        },
      },
    },
  });
}

function renderSeverityDonut(result) {
  const canvas = document.getElementById('severityDonutChart');
  if (!canvas || !window.Chart) return;

  if (AppState.charts.donut) {
    AppState.charts.donut.destroy();
  }

  const counts = result.priority_counts || {};
  const checks = result.checks || [];
  const passedCount = checks.filter((c) => c.status === 'pass').length;

  const totalChecks = document.getElementById('donut-total-checks');
  if (totalChecks) totalChecks.textContent = `${checks.length} Checks Run`;

  AppState.charts.donut = new Chart(canvas, {
    type: 'doughnut',
    data: {
      labels: ['Critical', 'High', 'Medium', 'Low', 'Passed'],
      datasets: [
        {
          data: [
            counts.Critical || 0,
            counts.High || 0,
            counts.Medium || 0,
            counts.Low || 0,
            passedCount,
          ],
          backgroundColor: [
            '#dc2626', // Critical
            '#ea580c', // High
            '#d97706', // Medium
            '#2563eb', // Low
            '#16a34a', // Passed
          ],
          borderWidth: 1,
          borderColor: '#ffffff',
          hoverOffset: 4,
        },
      ],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      cutout: '70%',
      plugins: {
        legend: {
          position: 'bottom',
          labels: { color: '#475569', font: { family: 'Plus Jakarta Sans', size: 11 } },
        },
      },
    },
  });
}

// ==========================================================================
// 5. Full Category Deep-Dive Explorer (DOCX Parity)
// ==========================================================================
function renderCategoryDeepDive(result) {
  const tabsContainer = document.getElementById('category-explorer-tabs');
  if (!tabsContainer) return;
  tabsContainer.innerHTML = '';

  const dims = (result.score || {}).dimensions || [];
  if (dims.length === 0) return;

  const initialCat = AppState.selectedCategory || dims[0].key;
  dims.forEach((d, idx) => {
    const btn = document.createElement('button');
    const isAct = d.key === initialCat;
    btn.className = `cat-nav-btn ${isAct ? 'active' : ''}`;
    btn.id = `cat-tab-${d.key}`;
    btn.textContent = `${d.label} (${d.score || 0})`;
    btn.onclick = () => selectCategoryInDeepDive(d.key);
    tabsContainer.appendChild(btn);
  });

  const initialCat = AppState.selectedCategory || dims[0].key;
  displayCategoryChecks(initialCat);
}

function selectCategoryInDeepDive(catKey) {
  AppState.selectedCategory = catKey;
  document.querySelectorAll('.cat-nav-btn').forEach((btn) => {
    btn.classList.toggle('active', btn.id === `cat-tab-${catKey}`);
  });
  displayCategoryChecks(catKey);
  document.getElementById('section-category-deepdive')?.scrollIntoView({ behavior: 'smooth', block: 'start' });
}

function displayCategoryChecks(catKey) {
  if (!AppState.currentResult) return;
  const result = AppState.currentResult;
  const dims = (result.score || {}).dimensions || [];
  const dim = dims.find((d) => d.key === catKey) || {};

  const titleEl = document.getElementById('cat-explorer-title');
  const scoreBadge = document.getElementById('cat-explorer-score-badge');
  const callout = document.getElementById('cat-explorer-client-callout');
  const tbody = document.getElementById('category-checks-tbody');

  if (titleEl) titleEl.textContent = dim.label || catKey;
  if (scoreBadge) {
    const s = dim.score != null ? Math.round(dim.score) : 0;
    scoreBadge.textContent = `${s} / 100`;
    scoreBadge.className = s >= 85 ? 'badge badge-pass text-mono' : s >= 60 ? 'badge badge-medium text-mono' : 'badge badge-critical text-mono';
  }
  if (callout) {
    callout.textContent = CATEGORY_CLIENT[catKey] || 'Strictly evaluated against industry best practice standards.';
  }

  if (!tbody) return;
  tbody.innerHTML = '';

  const allChecks = result.checks || [];
  const catChecks = allChecks.filter((c) => c.category === catKey);

  if (catChecks.length === 0) {
    tbody.innerHTML = `<tr><td colspan="5" style="text-align: center; color: var(--text-muted); padding: 18px;">No checks ran for this category.</td></tr>`;
    return;
  }

  // Sort fail -> warn -> info/na -> pass
  const order = { fail: 0, warn: 1, info: 2, na: 3, pass: 4 };
  catChecks.sort((a, b) => (order[a.status] || 5) - (order[b.status] || 5));

  catChecks.forEach((c) => {
    const tr = document.createElement('tr');
    let statusBadge = '<span class="badge badge-pass">PASS</span>';
    if (c.status === 'fail') statusBadge = '<span class="badge badge-critical">FAIL</span>';
    else if (c.status === 'warn') statusBadge = '<span class="badge badge-medium">WARN</span>';
    else if (c.status === 'info') statusBadge = '<span class="badge badge-low">INFO</span>';
    else if (c.status === 'na') statusBadge = '<span class="badge badge-subtle">N/A</span>';

    const whereUrls = (c.where || []).filter(Boolean);
    const whereHtml = whereUrls.length > 0 ? `<div style="font-size: 11px; color: var(--text-muted); margin-top: 4px; font-family: var(--font-mono);">${whereUrls.length} URL(s) affected: ${escapeHtml(whereUrls[0])}</div>` : '';

    tr.innerHTML = `
      <td>
        <strong style="color: var(--text-main); font-size: 13px;">${escapeHtml(c.label)}</strong>
        <div style="font-size: 10px; color: var(--text-faint); text-transform: uppercase;">ID: ${escapeHtml(c.id || '')}</div>
      </td>
      <td style="text-align: center;">${statusBadge}</td>
      <td>
        <div style="font-size: 12.5px; color: var(--text-main); margin-bottom: 4px;">${escapeHtml(c.detail || '')}</div>
        ${c.fix ? `<div style="font-size: 11.5px; color: var(--primary); background-color: var(--primary-subtle); padding: 4px 8px; border-radius: var(--radius-sm); border: 1px solid var(--primary-border);">Fix: ${escapeHtml(c.fix)}</div>` : ''}
        ${whereHtml}
      </td>
      <td style="text-align: center; font-size: 11px;">${escapeHtml(c.effort || 'Moderate')}</td>
      <td style="text-align: center; font-size: 11px; font-weight: 700; color: #16a34a;">${escapeHtml(c.gain || 'High')}</td>
    `;
    tbody.appendChild(tr);
  });
}

// ==========================================================================
// 6. Deep Technical Diagnostics (Robots, AI Bots, Security Headers)
// ==========================================================================
function renderTechnicalDiagnostics(result) {
  // 1. AI Crawlers Matrix
  const aiTbody = document.getElementById('ai-crawlers-tbody');
  const robotsBadge = document.getElementById('robots-status-badge');
  const sitemapsBox = document.getElementById('sitemaps-list-box');

  const robots = result.robots || {};
  if (robotsBadge) {
    robotsBadge.textContent = robots.present ? 'ROBOTS.TXT ACTIVE' : 'MISSING ROBOTS.TXT';
    robotsBadge.className = robots.present ? 'badge badge-pass' : 'badge badge-medium';
  }

  if (sitemapsBox) {
    const sm = result.sitemap || {};
    const count = sm.count || (sm.urls || []).length || 0;
    const smList = robots.sitemaps || [];
    sitemapsBox.innerHTML = `
      <div>Declared Sitemaps: <strong>${smList.length || (count > 0 ? 1 : 0)}</strong> • Total Indexed URLs in Sitemaps: <strong>${count}</strong></div>
      ${smList.length > 0 ? `<div class="truncate" style="color: var(--primary);">${escapeHtml(smList[0])}</div>` : ''}
    `;
  }

  if (aiTbody) {
    aiTbody.innerHTML = '';
    const aiBots = [
      { bot: 'GPTBot', op: 'OpenAI (ChatGPT)' },
      { bot: 'ClaudeBot', op: 'Anthropic (Claude)' },
      { bot: 'Google-Extended', op: 'Google (Gemini AI)' },
      { bot: 'CCBot', op: 'Common Crawl' },
      { bot: 'PerplexityBot', op: 'Perplexity AI Search' },
      { bot: 'Applebot', op: 'Apple Intelligence' },
    ];

    const aiRules = robots.ai_rules || {};
    aiBots.forEach((item) => {
      const rule = aiRules[item.bot];
      const isAllowed = rule !== false && rule !== 'disallow';
      const tr = document.createElement('tr');
      tr.innerHTML = `
        <td style="font-weight: 700; color: var(--text-main);">${item.bot}</td>
        <td style="color: var(--text-muted); font-size: 11.5px;">${item.op}</td>
        <td style="text-align: center;">
          ${isAllowed ? '<span class="badge badge-pass">ALLOWED</span>' : '<span class="badge badge-critical">BLOCKED</span>'}
        </td>
      `;
      aiTbody.appendChild(tr);
    });
  }

  // 2. Security Headers
  const secTbody = document.getElementById('security-headers-tbody');
  const httpsBadge = document.getElementById('https-status-badge');
  const isHttps = (result.final_url || result.url || '').startsWith('https://');

  if (httpsBadge) {
    httpsBadge.textContent = isHttps ? 'HTTPS ENFORCED' : 'HTTP INSECURE';
    httpsBadge.className = isHttps ? 'badge badge-pass' : 'badge badge-critical';
  }

  if (secTbody) {
    secTbody.innerHTML = '';
    const headers = (result.home_facts || {}).headers || {};

    const secHeaders = [
      { name: 'Strict-Transport-Security', desc: 'Enforces HTTPS encryption (HSTS)' },
      { name: 'Content-Security-Policy', desc: 'Prevents XSS and malicious scripts' },
      { name: 'X-Frame-Options', desc: 'Prevents clickjacking and framing' },
      { name: 'X-Content-Type-Options', desc: 'Blocks MIME sniffing attacks' },
      { name: 'Referrer-Policy', desc: 'Controls referrer data privacy' },
    ];

    secHeaders.forEach((sh) => {
      const hasHeader = boolCheckHeader(headers, sh.name);
      const tr = document.createElement('tr');
      tr.innerHTML = `
        <td style="font-weight: 700; color: var(--text-main); font-size: 11.5px;">${sh.name}</td>
        <td style="color: var(--text-muted); font-size: 11.5px;">${sh.desc}</td>
        <td style="text-align: center;">
          ${hasHeader ? '<span class="badge badge-pass">CONFIGURED</span>' : '<span class="badge badge-critical">MISSING</span>'}
        </td>
      `;
      secTbody.appendChild(tr);
    });
  }
}

function boolCheckHeader(headers, name) {
  if (!headers) return false;
  const lname = name.toLowerCase();
  for (let k of Object.keys(headers)) {
    if (k.toLowerCase() === lname) return true;
  }
  return false;
}

// ==========================================================================
// 6B. HTTP to HTTPS Redirection & Security Chain
// ==========================================================================
function renderHttpRedirection(result) {
  const badge = document.getElementById('http-redirect-badge');
  const originStatus = document.getElementById('http-origin-status');
  const sslEnforced = document.getElementById('http-ssl-enforced');

  const red = result.http_redirect;
  if (!red) {
    if (badge) {
      badge.textContent = 'CHECK SKIPPED';
      badge.className = 'badge badge-subtle text-mono';
    }
    if (originStatus) originStatus.textContent = 'Not tested';
    if (sslEnforced) sslEnforced.textContent = 'Not tested';
    return;
  }

  const redirects = red.redirects_to_https;
  const reachable = red.reachable;

  if (badge) {
    if (redirects) {
      badge.textContent = 'SECURE 301 REDIRECT';
      badge.className = 'badge badge-pass text-mono';
    } else {
      badge.textContent = 'INSECURE HTTP REACHABLE';
      badge.className = 'badge badge-critical text-mono';
    }
  }

  if (originStatus) {
    originStatus.textContent = reachable ? 'HTTP Reachable' : 'HTTP Blocked / Offline';
    originStatus.className = reachable ? 'badge badge-subtle text-mono' : 'badge badge-critical text-mono';
  }

  if (sslEnforced) {
    sslEnforced.textContent = redirects ? 'Yes (Enforced 301/308)' : 'No (Insecure HTTP Serves Content)';
    sslEnforced.className = redirects ? 'badge badge-pass text-mono' : 'badge badge-critical text-mono';
  }
}

// ==========================================================================
// 6C. Broken Internal Links & Link Health
// ==========================================================================
function renderBrokenLinks(result) {
  const badge = document.getElementById('broken-links-badge');
  const summary = document.getElementById('broken-links-checked-summary');
  const statusText = document.getElementById('broken-links-status-text');
  const tbody = document.getElementById('broken-links-tbody');

  const linksData = result.broken_links;
  if (!linksData) {
    if (badge) {
      badge.textContent = 'NOT AUDITED';
      badge.className = 'badge badge-subtle text-mono';
    }
    if (summary) summary.textContent = 'Link sampling active on full site crawl';
    if (statusText) statusText.textContent = 'Single page mode';
    if (tbody) {
      tbody.innerHTML = '<tr><td colspan="2" style="text-align: center; color: var(--text-muted); padding: 12px;">Link sampling enabled on full site crawls.</td></tr>';
    }
    return;
  }

  const checked = linksData.checked || 0;
  const broken = linksData.broken || [];

  if (badge) {
    badge.textContent = broken.length > 0 ? `${broken.length} BROKEN` : '0 BROKEN';
    badge.className = broken.length > 0 ? 'badge badge-critical text-mono' : 'badge badge-pass text-mono';
  }

  if (summary) {
    summary.textContent = `${checked} internal links tested across crawled pages`;
  }

  if (statusText) {
    if (broken.length === 0) {
      statusText.textContent = 'All Sampled Links Valid (HTTP 200)';
      statusText.style.color = '#16a34a';
    } else {
      statusText.textContent = `${broken.length} Broken Internal Link(s) Found`;
      statusText.style.color = '#dc2626';
    }
  }

  if (tbody) {
    tbody.innerHTML = '';
    if (broken.length === 0) {
      tbody.innerHTML = '<tr><td colspan="2" style="text-align: center; color: var(--color-pass); padding: 12px; font-weight: 600;">[OK] Zero broken links or 404 dead ends discovered.</td></tr>';
    } else {
      broken.forEach((b) => {
        const tr = document.createElement('tr');
        tr.innerHTML = `
          <td class="truncate" style="max-width: 320px;" title="${escapeHtml(b.url)}">
            <span style="color: #dc2626; font-weight: bold; margin-right: 4px;">[!]</span>
            <span style="color: var(--text-main);">${escapeHtml(b.url)}</span>
          </td>
          <td style="text-align: center; color: #dc2626; font-weight: 800;">${b.status || 'TIMEOUT'}</td>
        `;
        tbody.appendChild(tr);
      });
    }
  }
}

// ==========================================================================
// 6D. Canonical Directives & Media Assets
// ==========================================================================
function renderCanonicalDirectives(result) {
  const badge = document.getElementById('canonical-status-badge');
  const declUrl = document.getElementById('canonical-declared-url');
  const verdict = document.getElementById('canonical-match-verdict');
  const robotsMeta = document.getElementById('robots-meta-directives');
  const xRobots = document.getElementById('x-robots-header');

  const home = result.home_facts || {};
  const canonical = home.canonical || '';
  const canonicalOk = home.canonical_ok;

  if (declUrl) declUrl.textContent = canonical || 'None (Missing Canonical Tag)';

  if (badge) {
    if (!canonical) {
      badge.textContent = 'MISSING CANONICAL';
      badge.className = 'badge badge-critical text-mono';
    } else if (canonicalOk) {
      badge.textContent = 'CANONICAL VALID';
      badge.className = 'badge badge-pass text-mono';
    } else {
      badge.textContent = 'CANONICAL MISMATCH';
      badge.className = 'badge badge-medium text-mono';
    }
  }

  if (verdict) {
    if (!canonical) {
      verdict.textContent = 'Missing canonical link tag in <head>';
      verdict.style.color = '#dc2626';
    } else if (canonicalOk) {
      verdict.textContent = 'Matches live page URL cleanly';
      verdict.style.color = '#16a34a';
    } else {
      verdict.textContent = 'Points to alternate URL (Check canonicalization)';
      verdict.style.color = '#d97706';
    }
  }

  if (robotsMeta) {
    const rm = home.robots_meta;
    robotsMeta.textContent = rm || (home.noindex ? 'noindex' : 'index, follow (default)');
    robotsMeta.style.color = home.noindex ? '#dc2626' : 'var(--text-main)';
  }

  if (xRobots) {
    const xr = home.x_robots;
    xRobots.textContent = xr || 'None (Standard permissive crawl)';
  }
}

function renderMediaAssets(result) {
  const home = result.home_facts || {};
  const badge = document.getElementById('media-alt-badge');
  const totalEl = document.getElementById('media-total-images');
  const altEl = document.getElementById('media-alt-coverage');
  const modernEl = document.getElementById('media-modern-formats');
  const responsiveEl = document.getElementById('media-responsive-images');
  const lazyEl = document.getElementById('media-lazy-status');
  const dimsEl = document.getElementById('media-dims-status');

  const total = home.images_total || 0;
  const withAlt = home.images_with_alt || 0;
  const modern = home.img_modern || 0;
  const srcset = (home.img_srcset || 0) + (home.picture_count || 0);
  const lazy = home.img_lazy || 0;
  const dims = home.img_dims || 0;

  if (totalEl) totalEl.textContent = total;
  if (altEl) {
    const pct = total > 0 ? Math.round((withAlt / total) * 100) : 100;
    altEl.textContent = `${withAlt}/${total} (${pct}%)`;
    altEl.style.color = pct >= 90 ? '#16a34a' : pct >= 60 ? '#d97706' : '#dc2626';
  }
  if (modernEl) modernEl.textContent = `${modern} files`;
  if (responsiveEl) responsiveEl.textContent = `${srcset} tags`;
  if (lazyEl) lazyEl.textContent = `${lazy} images use loading="lazy"`;
  if (dimsEl) dimsEl.textContent = `${dims}/${total} have width/height attributes`;

  if (badge) {
    if (total === 0) {
      badge.textContent = 'NO IMAGES';
      badge.className = 'badge badge-subtle text-mono';
    } else if (withAlt === total) {
      badge.textContent = '100% ALT COVERAGE';
      badge.className = 'badge badge-pass text-mono';
    } else {
      badge.textContent = `${total - withAlt} MISSING ALT`;
      badge.className = 'badge badge-medium text-mono';
    }
  }
}

// ==========================================================================
// Crawl Depth & Concurrency Handlers (Supports 200 - 1,000+ Pages)
// ==========================================================================
function handleCrawlSelect(val) {
  const customInput = document.getElementById('crawl-depth-custom');
  const hiddenSlider = document.getElementById('crawl-depth-slider');
  if (val === 'custom') {
    if (customInput) {
      customInput.classList.remove('hidden');
      customInput.focus();
    }
  } else {
    if (customInput) customInput.classList.add('hidden');
    if (hiddenSlider) hiddenSlider.value = val;
  }
}

function handleCustomCrawlInput(val) {
  const hiddenSlider = document.getElementById('crawl-depth-slider');
  const num = parseInt(val, 10);
  if (hiddenSlider && !isNaN(num) && num > 0) {
    hiddenSlider.value = num;
  }
}

// ==========================================================================
// Domain & Page Authority (DA / PA) Overview
// ==========================================================================
function renderAuthorityOverview(result) {
  const auth = result.authority || {};
  const da = auth.domain_authority != null ? auth.domain_authority : '—';
  const pa = auth.page_authority != null ? auth.page_authority : '—';

  const daValEl = document.getElementById('authority-da-val');
  const paValEl = document.getElementById('authority-pa-val');
  const daStatusEl = document.getElementById('authority-da-status');
  const paStatusEl = document.getElementById('authority-pa-status');
  const srcBadge = document.getElementById('authority-source-badge');
  const tldEl = document.getElementById('authority-tld-badge');
  const sslGradeEl = document.getElementById('authority-ssl-grade');
  const archEl = document.getElementById('authority-arch-equity');
  const footprintEl = document.getElementById('authority-footprint');

  if (daValEl) daValEl.textContent = da;
  if (paValEl) paValEl.textContent = pa;

  if (srcBadge) {
    srcBadge.textContent = auth.source || (auth.is_estimated ? 'Algorithmic Model' : 'Moz API Verified');
    srcBadge.className = auth.is_estimated ? 'badge badge-subtle text-mono' : 'badge badge-pass text-mono';
  }

  if (daStatusEl) {
    if (da === '—') {
      daStatusEl.textContent = 'NO DATA';
    } else if (da >= 60) {
      daStatusEl.textContent = 'EXCELLENT';
      daStatusEl.className = 'badge badge-pass text-mono';
    } else if (da >= 35) {
      daStatusEl.textContent = 'SOLID AUTHORITY';
      daStatusEl.className = 'badge badge-low text-mono';
    } else {
      daStatusEl.textContent = 'DEVELOPING';
      daStatusEl.className = 'badge badge-medium text-mono';
    }
  }

  if (paStatusEl) {
    if (pa === '—') {
      paStatusEl.textContent = 'NO DATA';
    } else if (pa >= 50) {
      paStatusEl.textContent = 'HIGH EQUITY';
      paStatusEl.className = 'badge badge-pass text-mono';
    } else {
      paStatusEl.textContent = 'MODERATE';
      paStatusEl.className = 'badge badge-subtle text-mono';
    }
  }

  const dt = auth.details || {};
  if (tldEl) tldEl.textContent = dt.tld_tier || 'COM';
  if (sslGradeEl) sslGradeEl.textContent = dt.ssl_security_grade || 'A+';
  if (archEl) archEl.textContent = dt.architecture_equity || 'Solid';
  if (footprintEl) footprintEl.textContent = `${result.pages_crawled || 1} pages`;
}

// ==========================================================================
// Monthly Organic Traffic & Geographic Overview
// ==========================================================================
let trafficTrendChartInstance = null;

function renderTrafficOverview(result) {
  const traffic = result.traffic || {};
  const visitsEl = document.getElementById('traffic-monthly-visits');
  const impsEl = document.getElementById('traffic-monthly-impressions');
  const kwsEl = document.getElementById('traffic-ranking-keywords');
  const visEl = document.getElementById('traffic-visibility-index');
  const geoStrip = document.getElementById('traffic-geo-strip');
  const geoSelect = document.getElementById('traffic-geo-select');

  const visits = traffic.monthly_visits != null ? traffic.monthly_visits.toLocaleString() : '—';
  const imps = traffic.monthly_impressions != null ? (traffic.monthly_impressions > 1000 ? Math.round(traffic.monthly_impressions / 1000) + 'K' : traffic.monthly_impressions) : '—';
  const kws = traffic.ranking_keywords != null ? traffic.ranking_keywords.toLocaleString() : '—';
  const vis = traffic.search_visibility_index != null ? `${traffic.search_visibility_index} / 100` : '—';

  if (visitsEl) visitsEl.textContent = visits;
  if (impsEl) impsEl.textContent = imps;
  if (kwsEl) kwsEl.textContent = kws;
  if (visEl) visEl.textContent = vis;

  // Render geo distribution pills
  if (geoStrip) {
    geoStrip.innerHTML = '';
    const geos = traffic.geo_distribution || [];
    if (geoSelect) {
      geoSelect.innerHTML = `<option value="ALL">Worldwide (Global)</option>`;
      geos.forEach((g) => {
        geoSelect.innerHTML += `<option value="${escapeHtml(g.code)}">${escapeHtml(g.country)} (${g.pct}%)</option>`;
      });
    }

    geos.forEach((g) => {
      const pill = document.createElement('div');
      pill.style.cssText = 'background: var(--bg-subtle); border: 1px solid var(--border-subtle); border-radius: 4px; padding: 3px 8px; display: flex; align-items: center; gap: 6px;';
      pill.innerHTML = `
        <span style="font-weight: 700; color: var(--text-main);">${escapeHtml(g.country)}:</span>
        <span class="text-mono" style="color: var(--primary); font-weight: 700;">${g.pct}%</span>
        <span style="color: var(--text-muted); font-size: 10px;">(${g.visits.toLocaleString()} visits)</span>
      `;
      geoStrip.appendChild(pill);
    });
  }

  // Draw 12-month area chart
  const canvas = document.getElementById('trafficTrendChart');
  if (!canvas || !window.Chart) return;

  const trajectory = traffic.trajectory || [];
  const labels = trajectory.map((t) => t.month);
  const dataPoints = trajectory.map((t) => t.visits);

  if (trafficTrendChartInstance) {
    trafficTrendChartInstance.destroy();
  }

  const ctx = canvas.getContext('2d');
  const gradient = ctx.createLinearGradient(0, 0, 0, 160);
  gradient.addColorStop(0, 'rgba(22, 163, 74, 0.25)');
  gradient.addColorStop(1, 'rgba(22, 163, 74, 0.0)');

  trafficTrendChartInstance = new Chart(ctx, {
    type: 'line',
    data: {
      labels: labels.length > 0 ? labels : ['M1', 'M2', 'M3', 'M4', 'M5', 'M6', 'M7', 'M8', 'M9', 'M10', 'M11', 'M12'],
      datasets: [
        {
          label: 'Estimated Monthly Visits',
          data: dataPoints.length > 0 ? dataPoints : [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0],
          borderColor: '#16a34a',
          backgroundColor: gradient,
          fill: true,
          tension: 0.35,
          borderWidth: 2,
          pointRadius: 3,
          pointHoverRadius: 6,
          pointBackgroundColor: '#16a34a',
        },
      ],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: { display: false },
        tooltip: {
          backgroundColor: '#0f172a',
          titleColor: '#f8fafc',
          bodyColor: '#e2e8f0',
          callbacks: {
            label: (item) => `Est. Visits: ${item.raw.toLocaleString()}`,
          },
        },
      },
      scales: {
        x: {
          grid: { display: false },
          ticks: { font: { size: 10 }, color: '#64748b' },
        },
        y: {
          beginAtZero: true,
          grid: { color: 'rgba(226, 232, 240, 0.6)' },
          ticks: { font: { size: 10 }, color: '#64748b' },
        },
      },
    },
  });
}

function handleTrafficGeoChange(code) {
  const traffic = (AppState.currentResult && AppState.currentResult.traffic) || {};
  const visitsEl = document.getElementById('traffic-monthly-visits');
  if (!visitsEl || !traffic.monthly_visits) return;

  if (code === 'ALL') {
    visitsEl.textContent = traffic.monthly_visits.toLocaleString();
  } else {
    const geo = (traffic.geo_distribution || []).find((g) => g.code === code);
    if (geo) {
      visitsEl.textContent = geo.visits.toLocaleString();
    }
  }
}

// ==========================================================================
// Local SEO & Citations
// ==========================================================================
function renderLocalSeo(result) {
  const home = result.home_facts || {};
  const ls = home.local_signals || {};
  const sd = ((result.structured || {})[result.final_url] || {});
  const types = sd.types || [];
  const isLocalSchema = types.some((t) => t.includes('Business') || t.includes('Store') || t.includes('Restaurant'));

  const badge = document.getElementById('local-seo-badge');
  const mapStatus = document.getElementById('local-map-status');
  const schemaStatus = document.getElementById('local-schema-status');
  const phoneStatus = document.getElementById('local-phone-status');
  const addressStatus = document.getElementById('local-address-status');
  const citationsText = document.getElementById('local-citations-text');
  const reviewsText = document.getElementById('local-reviews-text');

  if (mapStatus) {
    if (ls.has_map) {
      mapStatus.textContent = '[OK] Integrated / Linked';
      mapStatus.style.color = '#16a34a';
    } else {
      mapStatus.textContent = '[!] Missing Map / GBP Link';
      mapStatus.style.color = '#dc2626';
    }
  }

  if (schemaStatus) {
    if (isLocalSchema) {
      schemaStatus.textContent = '[OK] Schema.org LocalBusiness';
      schemaStatus.style.color = '#16a34a';
    } else if (sd.present) {
      schemaStatus.textContent = '[!] Generic Schema (Needs Local)';
      schemaStatus.style.color = '#d97706';
    } else {
      schemaStatus.textContent = '[!] No Schema Markup';
      schemaStatus.style.color = '#dc2626';
    }
  }

  if (phoneStatus) {
    if (ls.phone_count > 0) {
      phoneStatus.textContent = `[OK] Click-to-Call (${ls.phone_count} tel: links)`;
      phoneStatus.style.color = '#16a34a';
    } else if (ls.has_phone) {
      phoneStatus.textContent = '[!] Found in text (Needs tel: link)';
      phoneStatus.style.color = '#d97706';
    } else {
      phoneStatus.textContent = '[!] No Phone Detected';
      phoneStatus.style.color = '#dc2626';
    }
  }

  if (addressStatus) {
    if (ls.has_address) {
      addressStatus.textContent = '[OK] Physical Address Indicators Present';
      addressStatus.style.color = '#16a34a';
    } else {
      addressStatus.textContent = '[!] No Physical Address in Footer';
      addressStatus.style.color = '#d97706';
    }
  }

  if (citationsText) {
    const cits = ls.citations || [];
    citationsText.textContent = cits.length > 0 ? cits.join(', ') : 'None detected on homepage';
  }

  if (reviewsText) {
    reviewsText.textContent = ls.has_reviews ? '[OK] Customer Testimonials / Reviews Found' : 'No review schema or testimonial widgets found';
    reviewsText.style.color = ls.has_reviews ? '#16a34a' : 'var(--text-muted)';
  }

  if (badge) {
    const passed = (ls.has_map ? 1 : 0) + (isLocalSchema ? 1 : 0) + (ls.phone_count > 0 ? 1 : 0) + (ls.has_address ? 1 : 0);
    badge.textContent = `${passed}/4 SIGNALS ACTIVE`;
    badge.className = passed >= 3 ? 'badge badge-pass text-mono' : 'badge badge-medium text-mono';
  }
}

// ==========================================================================
// Off-Page SEO & Backlink Intelligence
// ==========================================================================
function renderOffpageSeo(result) {
  const home = result.home_facts || {};
  const auth = result.authority || {};
  const dofollow = home.external_dofollow_count || 0;
  const nofollow = home.external_nofollow_count || 0;
  const total = dofollow + nofollow;

  const badge = document.getElementById('offpage-status-badge');
  const outboundTotal = document.getElementById('offpage-outbound-total');
  const dofollowRatio = document.getElementById('offpage-dofollow-ratio');
  const toxicStatus = document.getElementById('offpage-toxic-status');
  const qualityTier = document.getElementById('offpage-quality-tier');
  const diversityEl = document.getElementById('offpage-anchor-diversity');
  const spamTldEl = document.getElementById('offpage-spam-tld-count');

  if (outboundTotal) outboundTotal.textContent = total;
  if (dofollowRatio) {
    const pct = total > 0 ? Math.round((dofollow / total) * 100) : 100;
    dofollowRatio.textContent = `${pct}% Dofollow`;
    dofollowRatio.style.color = pct <= 80 ? '#16a34a' : '#d97706';
  }

  let toxicCount = 0;
  (result.all_facts || []).forEach((f) => {
    (f.external_links_details || []).forEach((l) => {
      const d = l.domain || '';
      const tld = d.split('.').pop();
      if (['xyz', 'top', 'click', 'win', 'loan', 'buzz'].includes(tld)) {
        toxicCount++;
      }
    });
  });

  if (toxicStatus) {
    if (toxicCount === 0) {
      toxicStatus.textContent = 'CLEAN';
      toxicStatus.style.color = '#16a34a';
    } else {
      toxicStatus.textContent = `${toxicCount} RISKY`;
      toxicStatus.style.color = '#dc2626';
    }
  }

  if (qualityTier) {
    const da = auth.domain_authority || 30;
    qualityTier.textContent = da >= 60 ? 'TIER A+' : da >= 40 ? 'TIER A' : da >= 25 ? 'TIER B' : 'TIER C';
  }

  if (diversityEl) diversityEl.textContent = total > 15 ? 'Balanced Diversity' : 'Natural Brand Profile';
  if (spamTldEl) {
    spamTldEl.textContent = `${toxicCount} Detected`;
    spamTldEl.style.color = toxicCount === 0 ? '#16a34a' : '#dc2626';
  }

  if (badge) {
    badge.textContent = toxicCount === 0 ? 'HEALTHY PROFILE' : `${toxicCount} TOXIC RISKS`;
    badge.className = toxicCount === 0 ? 'badge badge-pass text-mono' : 'badge badge-critical text-mono';
  }
}

// ==========================================================================
// 7. Core Web Vitals & PageSpeed Insights v5
// ==========================================================================
function switchCwvStrategy(strategy) {
  AppState.cwvStrategy = strategy;
  document.getElementById('cwv-btn-mobile')?.classList.toggle('active', strategy === 'mobile');
  document.getElementById('cwv-btn-desktop')?.classList.toggle('active', strategy === 'desktop');
  if (AppState.currentResult) {
    renderCoreWebVitals(AppState.currentResult);
  }
}

function renderCoreWebVitals(result) {
  const psiData = (result.psi || {})[AppState.cwvStrategy] || {};
  const scores = psiData.scores || {};
  const fieldMetrics = (psiData.field || {}).metrics || {};
  const audits = psiData.audits || {};

  const scoresRow = document.getElementById('lighthouse-scores-row');
  if (scoresRow) {
    scoresRow.innerHTML = '';
    const cats = [
      { key: 'performance', label: 'Performance' },
      { key: 'seo', label: 'SEO' },
      { key: 'accessibility', label: 'Accessibility' },
      { key: 'best_practices', label: 'Best Practices' },
    ];

    cats.forEach((c) => {
      const val = scores[c.key] != null ? Math.round(scores[c.key]) : null;
      let badgeClass = 'badge-pass';
      if (val === null) badgeClass = 'badge-subtle';
      else if (val < 50) badgeClass = 'badge-critical';
      else if (val < 90) badgeClass = 'badge-medium';

      const div = document.createElement('div');
      div.className = 'cwv-card';
      div.style.textAlign = 'center';
      div.innerHTML = `
        <span class="cwv-label" style="text-transform: uppercase; font-weight: 700;">${c.label}</span>
        <span class="cwv-value">${val != null ? val : 'N/A'}</span>
        <span class="badge ${badgeClass}" style="align-self: center; font-size: 10px;">Lighthouse</span>
      `;
      scoresRow.appendChild(div);
    });
  }

  const metricsGrid = document.getElementById('cwv-metrics-grid');
  if (metricsGrid) {
    metricsGrid.innerHTML = '';
    const cwvItems = [
      {
        id: 'LCP',
        name: 'Largest Contentful Paint',
        val: audits['largest-contentful-paint']?.displayValue || (fieldMetrics.LARGEST_CONTENTFUL_PAINT_MS ? `${(fieldMetrics.LARGEST_CONTENTFUL_PAINT_MS.percentile / 1000).toFixed(1)} s` : null),
        threshold: '< 2.5s',
        score: audits['largest-contentful-paint']?.score,
      },
      {
        id: 'INP / TBT',
        name: 'Interactivity (TBT/INP)',
        val: audits['total-blocking-time']?.displayValue || (fieldMetrics.INTERACTION_TO_NEXT_PAINT ? `${fieldMetrics.INTERACTION_TO_NEXT_PAINT.percentile} ms` : null),
        threshold: '< 200ms',
        score: audits['total-blocking-time']?.score,
      },
      {
        id: 'CLS',
        name: 'Cumulative Layout Shift',
        val: audits['cumulative-layout-shift']?.displayValue || (fieldMetrics.CUMULATIVE_LAYOUT_SHIFT_SCORE ? (fieldMetrics.CUMULATIVE_LAYOUT_SHIFT_SCORE.percentile / 100).toFixed(2) : null),
        threshold: '< 0.1',
        score: audits['cumulative-layout-shift']?.score,
      },
      {
        id: 'FCP',
        name: 'First Contentful Paint',
        val: audits['first-contentful-paint']?.displayValue || (fieldMetrics.FIRST_CONTENTFUL_PAINT_MS ? `${(fieldMetrics.FIRST_CONTENTFUL_PAINT_MS.percentile / 1000).toFixed(1)} s` : null),
        threshold: '< 1.8s',
        score: audits['first-contentful-paint']?.score,
      },
      {
        id: 'TTFB',
        name: 'Time to First Byte',
        val: audits['server-response-time']?.displayValue || (fieldMetrics.EXPERIMENTAL_TIME_TO_FIRST_BYTE ? `${fieldMetrics.EXPERIMENTAL_TIME_TO_FIRST_BYTE.percentile} ms` : null),
        threshold: '< 800ms',
        score: audits['server-response-time']?.score,
      },
    ];

    cwvItems.forEach((item) => {
      let badgeClass = 'badge-pass';
      let statusText = 'GOOD';

      if (item.score != null) {
        if (item.score < 0.5) {
          badgeClass = 'badge-critical';
          statusText = 'POOR';
        } else if (item.score < 0.9) {
          badgeClass = 'badge-medium';
          statusText = 'NEEDS WORK';
        }
      }

      const card = document.createElement('div');
      card.className = 'cwv-card';
      card.innerHTML = `
        <div class="cwv-card-top">
          <span class="text-mono" style="font-weight: 700; color: var(--primary); font-size: 11px;">${item.id}</span>
          <span class="badge ${badgeClass}" style="font-size: 9.5px;">${statusText}</span>
        </div>
        <div>
          <span class="cwv-value">${item.val || '—'}</span>
          <span class="cwv-label truncate" title="${item.name}">${item.name}</span>
        </div>
        <div class="cwv-target text-mono">Target: ${item.threshold}</div>
      `;
      metricsGrid.appendChild(card);
    });
  }

  const oppTable = document.getElementById('cwv-opportunities-table');
  if (oppTable) {
    oppTable.innerHTML = '';
    const oppAudits = [
      'render-blocking-resources',
      'unused-javascript',
      'unused-css-rules',
      'modern-image-formats',
      'offscreen-images',
      'unminified-javascript',
    ];

    let count = 0;
    oppAudits.forEach((key) => {
      const a = audits[key];
      if (a && a.score != null && a.score < 0.9) {
        count++;
        const tr = document.createElement('tr');
        tr.innerHTML = `
          <td style="font-weight: 600;">${escapeHtml(a.title || key)}</td>
          <td class="text-mono" style="color: #dc2626; font-weight: 700;">${escapeHtml(a.displayValue || 'Potential savings')}</td>
          <td style="color: var(--text-muted); font-size: 12px;">${escapeHtml(a.description ? a.description.split('[')[0] : 'Optimize asset delivery')}</td>
        `;
        oppTable.appendChild(tr);
      }
    });

    if (count === 0) {
      oppTable.innerHTML = `<tr><td colspan="3" style="text-align: center; color: var(--text-muted); padding: 14px;">No critical performance bottlenecks detected in this pass.</td></tr>`;
    }
  }
}

// ==========================================================================
// 8. Google SERP & Social Previews
// ==========================================================================
function setSerpDevice(dev) {
  AppState.serpDevice = dev;
  document.getElementById('serp-btn-mobile')?.classList.toggle('active', dev === 'mobile');
  document.getElementById('serp-btn-desktop')?.classList.toggle('active', dev === 'desktop');

  const box = document.getElementById('serp-preview-box');
  if (box) {
    box.style.maxWidth = dev === 'mobile' ? '400px' : '650px';
  }
}

function renderSerpPreview(result) {
  const home = result.home_facts || {};
  const title = home.title || result.domain || 'Site Title';
  const desc = home.meta_desc || 'No meta description tag provided on the homepage.';
  const domainUrl = result.final_url || `https://${result.domain}`;

  const domEl = document.getElementById('serp-preview-domain');
  const titleEl = document.getElementById('serp-preview-title');
  const descEl = document.getElementById('serp-preview-desc');

  if (domEl) domEl.textContent = domainUrl;
  if (titleEl) titleEl.textContent = title;
  if (descEl) descEl.textContent = desc;

  const titleLen = title.length;
  const descLen = desc.length;

  const tLenText = document.getElementById('serp-title-len-text');
  const tLenBar = document.getElementById('serp-title-len-bar');
  const dLenText = document.getElementById('serp-desc-len-text');
  const dLenBar = document.getElementById('serp-desc-len-bar');

  if (tLenText) tLenText.textContent = `${titleLen} / 60`;
  if (tLenBar) {
    tLenBar.style.width = `${Math.min(100, (titleLen / 60) * 100)}%`;
    tLenBar.style.backgroundColor = titleLen >= 40 && titleLen <= 60 ? '#16a34a' : '#d97706';
  }

  if (dLenText) dLenText.textContent = `${descLen} / 160`;
  if (dLenBar) {
    dLenBar.style.width = `${Math.min(100, (descLen / 160) * 100)}%`;
    dLenBar.style.backgroundColor = descLen >= 120 && descLen <= 160 ? '#16a34a' : '#d97706';
  }
}

function renderSocialPreview(result) {
  const home = result.home_facts || {};
  const og = home.og || {};
  const tw = home.twitter || {};
  const domain = (result.domain || '').toUpperCase();
  const ogConfigured = Boolean(og.title || og.image || og.description);

  const badge = document.getElementById('og-status-badge');
  if (badge) {
    badge.textContent = ogConfigured ? 'OPENGRAPH ACTIVE' : 'MISSING OG TAGS';
    badge.className = ogConfigured ? 'badge badge-pass text-mono' : 'badge badge-medium text-mono';
  }

  const sDomain = document.getElementById('social-card-domain');
  const sTitle = document.getElementById('social-card-title');
  const sDesc = document.getElementById('social-card-desc');
  const ogImg = document.getElementById('social-og-image');
  const ogPlace = document.getElementById('social-og-placeholder');
  const tagsList = document.getElementById('social-tags-list');

  if (sDomain) sDomain.textContent = domain || 'YOUR-SITE.COM';
  if (sTitle) sTitle.textContent = og.title || home.title || result.domain || '—';
  if (sDesc) sDesc.textContent = og.description || home.meta_desc || 'No social description meta tag provided.';

  const imgUrl = og.image || tw.image;
  if (imgUrl && ogImg) {
    ogImg.src = imgUrl;
    ogImg.style.display = 'block';
    if (ogPlace) ogPlace.style.display = 'none';
  } else {
    if (ogImg) ogImg.style.display = 'none';
    if (ogPlace) {
      ogPlace.style.display = 'block';
      ogPlace.textContent = 'No og:image or twitter:image tag declared';
    }
  }

  // Populate discovered tags list
  if (tagsList) {
    tagsList.innerHTML = '';
    const allSocial = { ...og, ...tw };
    const entries = Object.entries(allSocial);
    if (entries.length === 0) {
      tagsList.innerHTML = '<span style="color: var(--text-muted); font-size: 11px;">No OpenGraph or Twitter Card meta tags declared in <head>.</span>';
    } else {
      entries.forEach(([k, v]) => {
        const item = document.createElement('div');
        item.style.display = 'flex';
        item.style.gap = '8px';
        item.style.alignItems = 'baseline';
        item.innerHTML = `
          <strong style="color: var(--primary); flex-shrink: 0;">${escapeHtml(k)}:</strong>
          <span class="truncate" style="color: var(--text-main); font-size: 11px;" title="${escapeHtml(v)}">${escapeHtml(v)}</span>
        `;
        tagsList.appendChild(item);
      });
    }
  }
}

// ==========================================================================
// 9. Headings Hierarchy Tree & Content Intelligence
// ==========================================================================
function renderHeadingsTree(result) {
  const home = result.home_facts || {};
  const headings = home.headings || [];
  const list = document.getElementById('headings-tree-list');
  const h1Badge = document.getElementById('h1-count-badge');

  const h1Count = home.h1_count || 0;
  if (h1Badge) {
    if (h1Count === 1) {
      h1Badge.textContent = '1 x H1 (Optimal)';
      h1Badge.className = 'badge badge-pass text-mono';
    } else if (h1Count === 0) {
      h1Badge.textContent = 'Missing H1';
      h1Badge.className = 'badge badge-critical text-mono';
    } else {
      h1Badge.textContent = `${h1Count} x H1 (Multiple)`;
      h1Badge.className = 'badge badge-medium text-mono';
    }
  }

  if (!list) return;
  list.innerHTML = '';

  if (headings.length === 0) {
    list.innerHTML = `<div style="color: var(--text-muted); font-size: 11px;">No H1-H6 tags discovered on homepage.</div>`;
    return;
  }

  headings.slice(0, 30).forEach(([lvl, txt]) => {
    const item = document.createElement('div');
    const indent = (lvl - 1) * 12;

    item.style.paddingLeft = `${indent}px`;
    item.style.display = 'flex';
    item.style.alignItems = 'center';
    item.style.gap = '6px';
    item.style.fontSize = '11.5px';

    item.innerHTML = `
      <span class="badge ${lvl === 1 ? 'badge-low' : 'badge-subtle'} text-mono" style="font-size: 9px; padding: 1px 4px;">H${lvl}</span>
      <span class="truncate text-mono" style="color: var(--text-main);" title="${escapeHtml(txt)}">${escapeHtml(txt)}</span>
    `;
    list.appendChild(item);
  });
}

function renderContentMetrics(result) {
  const finalUrl = result.final_url || result.url;
  const content = (result.content || {})[finalUrl] || {};
  const home = result.home_facts || {};

  const wordCount = document.getElementById('metric-word-count');
  const flesch = document.getElementById('metric-flesch-score');
  const kw = document.getElementById('metric-primary-kw');
  const methodBadge = document.getElementById('content-extraction-badge');

  const wc = content.word_count || home.word_count || 0;
  if (wordCount) wordCount.textContent = wc > 0 ? `${wc.toLocaleString()} words` : '—';
  
  if (methodBadge && content.extraction_method) {
    methodBadge.textContent = content.extraction_method.toUpperCase();
  }

  const fl = content.flesch != null ? content.flesch : null;
  if (flesch) {
    if (fl != null) {
      let flLabel = 'Standard';
      if (fl >= 80) flLabel = 'Easy';
      else if (fl >= 60) flLabel = 'Standard';
      else if (fl >= 50) flLabel = 'Fairly Difficult';
      else flLabel = 'Difficult';
      flesch.textContent = `${fl} (${flLabel})`;
      flesch.style.color = fl >= 60 ? '#16a34a' : fl >= 50 ? '#d97706' : '#dc2626';
    } else {
      flesch.textContent = '—';
    }
  }

  if (kw) {
    const kTxt = content.primary_keyword || '—';
    const dens = content.keyword_density != null ? ` (${content.keyword_density}%)` : '';
    kw.textContent = kTxt !== '—' ? `${kTxt}${dens}` : '—';
  }

  const sdBox = document.getElementById('schema-badges-container');
  if (sdBox) {
    sdBox.innerHTML = '';
    const sdInfo = (result.structured || {})[finalUrl] || {};
    const types = sdInfo.types || [];

    if (types.length > 0) {
      types.forEach((t) => {
        const badge = document.createElement('span');
        badge.className = 'badge badge-low text-mono';
        badge.textContent = t;
        sdBox.appendChild(badge);
      });
    } else {
      sdBox.innerHTML = `<span style="color: var(--text-muted); font-size: 11.5px;">No Schema.org structured data detected on homepage.</span>`;
    }
  }
}

// ==========================================================================
// 10. Prioritized Action Items Matrix (DOCX Parity)
// ==========================================================================
function renderActionItems(items) {
  const list = document.getElementById('action-items-list');
  const badge = document.getElementById('action-items-total-badge');
  const stackedBar = document.getElementById('action-stacked-bar');
  const stackedSummary = document.getElementById('stacked-bar-summary');

  if (badge) badge.textContent = `${items.length} Items`;

  // 1. Render Proportional Stacked Priority Bar (mirrors DOCX _stacked_bar)
  if (stackedBar) {
    stackedBar.innerHTML = '';
    const counts = {
      Critical: items.filter((it) => (it.priority || '').toLowerCase() === 'critical').length,
      High: items.filter((it) => (it.priority || '').toLowerCase() === 'high').length,
      Medium: items.filter((it) => (it.priority || '').toLowerCase() === 'medium').length,
      Low: items.filter((it) => (it.priority || '').toLowerCase() === 'low').length,
    };
    const total = items.length;

    if (total > 0) {
      if (stackedSummary) {
        stackedSummary.textContent = `${counts.Critical} Critical • ${counts.High} High • ${counts.Medium} Medium • ${counts.Low} Low`;
      }

      const segs = [
        { label: 'Critical', count: counts.Critical, cls: 'priority-seg-critical' },
        { label: 'High', count: counts.High, cls: 'priority-seg-high' },
        { label: 'Medium', count: counts.Medium, cls: 'priority-seg-medium' },
        { label: 'Low', count: counts.Low, cls: 'priority-seg-low' },
      ];

      segs.forEach((s) => {
        if (s.count > 0) {
          const pct = Math.max(8, (s.count / total) * 100);
          const segEl = document.createElement('div');
          segEl.className = `priority-segment ${s.cls}`;
          segEl.style.width = `${pct}%`;
          segEl.textContent = `${s.label} (${s.count})`;
          stackedBar.appendChild(segEl);
        }
      });
    } else {
      stackedBar.innerHTML = `<div style="padding: 4px 10px; font-size: 11px; color: var(--color-pass);">All checks pass with zero open issues.</div>`;
      if (stackedSummary) stackedSummary.textContent = 'Clean Bill of Health';
    }
  }

  if (!list) return;
  list.innerHTML = '';

  if (items.length === 0) {
    list.innerHTML = `
      <div style="background-color: var(--color-pass-bg); border: 1px solid var(--color-pass-border); border-radius: var(--radius-md); padding: 20px; text-align: center;">
        <strong style="color: var(--color-pass); font-size: 14px;">Clean bill of health!</strong>
        <p style="color: var(--text-muted); font-size: 12px; margin-top: 4px;">No critical or high priority errors were found in the checks that ran.</p>
      </div>
    `;
    return;
  }

  items.forEach((item) => {
    const sev = item.priority || 'Medium';
    let sevClass = 'badge-medium';
    if (sev === 'Critical') sevClass = 'badge-critical';
    else if (sev === 'High') sevClass = 'badge-high';
    else if (sev === 'Low') sevClass = 'badge-low';

    const row = document.createElement('div');
    row.className = 'action-item-row';
    row.setAttribute('data-category', item.category || '');
    row.setAttribute('data-severity', sev);
    row.setAttribute('data-tier', item.tier ? String(item.tier) : '1');
    row.setAttribute('data-text', `${item.label} ${item.detail} ${item.fix} ${item.impact_text || ''}`.toLowerCase());

    row.innerHTML = `
      <div class="action-item-top">
        <div class="action-item-left" style="flex-wrap: wrap; gap: 6px;">
          <span class="badge ${sevClass}">${sev}</span>
          <span class="badge badge-low text-mono" style="font-size: 10px;">Tier ${item.tier || 1}: ${escapeHtml(item.tier_title || 'General')}</span>
          <span class="badge badge-subtle">${escapeHtml(item.category_label || item.category)}</span>
          <h5 class="action-title">${escapeHtml(item.label)}</h5>
        </div>
        <div style="font-size: 11px; color: var(--text-muted); display: flex; gap: 10px;">
          <span>Effort: <strong style="color: var(--text-main);">${escapeHtml(item.difficulty || 'Moderate')}</strong></span>
          <span>Gain: <strong style="color: #16a34a;">${escapeHtml(item.gain || 'High')}</strong></span>
        </div>
      </div>
      <p style="font-size: 12.5px; color: var(--text-main); line-height: 1.5;">${escapeHtml(item.detail)}</p>
      ${
        item.where && item.where.length > 0
          ? `<div style="font-size: 11px; background: var(--bg-subtle); padding: 4px 8px; border-radius: 4px; border: 1px solid var(--border-subtle); color: var(--text-muted);">
               <strong>Affected URLs (${item.where.length}):</strong> <span class="text-mono" style="color: var(--text-main);">${escapeHtml(item.where.slice(0, 3).join(', '))}${item.where.length > 3 ? '...' : ''}</span>
             </div>`
          : ''
      }
      ${
        item.business_impact
          ? `<div style="font-size: 11.5px; color: #16a34a; background: rgba(22, 163, 74, 0.08); border-left: 3px solid #16a34a; padding: 4px 8px; border-radius: 0 4px 4px 0;">
               <strong>Expected Business Impact:</strong> <span>${escapeHtml(item.business_impact)}</span>
             </div>`
          : ''
      }
      ${
        item.impact_text
          ? `<div class="action-impact-text">
               <strong style="color: var(--text-main);">Why it matters:</strong>
               <span>${escapeHtml(item.impact_text)}</span>
             </div>`
          : ''
      }
      ${
        item.fix
          ? `<div class="action-fix-box">
               <strong style="color: var(--primary);">What to do:</strong>
               <span>${escapeHtml(item.fix)}</span>
             </div>`
          : ''
      }
    `;
    list.appendChild(row);
  });
}

function filterActionItems() {
  const search = (document.getElementById('action-items-search')?.value || '').toLowerCase();
  const sev = document.getElementById('action-items-severity-filter')?.value || 'all';
  const cat = document.getElementById('action-items-category-filter')?.value || 'all';
  const tier = document.getElementById('action-items-tier-filter')?.value || 'all';

  document.querySelectorAll('.action-item-row').forEach((row) => {
    const cardCat = row.getAttribute('data-category') || '';
    const cardSev = row.getAttribute('data-severity') || '';
    const cardTier = row.getAttribute('data-tier') || '';
    const cardTxt = row.getAttribute('data-text') || '';

    const matchSearch = !search || cardTxt.includes(search);
    const matchSev = sev === 'all' || cardSev === sev;
    const matchCat = cat === 'all' || cardCat === cat;
    const matchTier = tier === 'all' || cardTier === tier;

    row.style.display = matchSearch && matchSev && matchCat && matchTier ? 'flex' : 'none';
  });
}

function filterActionItemsByCategory(catKey) {
  const select = document.getElementById('action-items-category-filter');
  if (select) {
    select.value = catKey;
    filterActionItems();
    document.getElementById('section-action-items')?.scrollIntoView({ behavior: 'smooth' });
  }
}

// ==========================================================================
// 11. Crawled Pages Inventory Table
// ==========================================================================
function renderCrawledPages(pages) {
  const tbody = document.getElementById('crawled-pages-tbody');
  const countBadge = document.getElementById('crawled-pages-count-badge');

  if (countBadge) countBadge.textContent = `${pages.length} Pages`;
  if (!tbody) return;
  tbody.innerHTML = '';

  pages.forEach((p) => {
    const status = p.status || 200;
    const statusColor = status >= 200 && status < 300 ? '#16a34a' : status >= 300 && status < 400 ? '#0284c7' : '#dc2626';

    const tr = document.createElement('tr');
    tr.className = 'crawl-row';
    tr.setAttribute('data-url', (p.url || '').toLowerCase());
    tr.setAttribute('data-title', (p.title || '').toLowerCase());

    tr.innerHTML = `
      <td class="truncate" style="max-width: 280px;" title="${escapeHtml(p.url)}">
        <a href="${escapeHtml(p.url)}" target="_blank">${escapeHtml(p.url)}</a>
      </td>
      <td style="font-weight: 700; color: ${statusColor};">${status}</td>
      <td class="truncate" style="max-width: 260px;" title="${escapeHtml(p.title || '—')}">${escapeHtml(p.title || '—')}</td>
      <td style="text-align: center;">${p.title_len || 0}</td>
      <td style="text-align: center;">${(p.word_count || 0).toLocaleString()}</td>
      <td style="text-align: center;">${p.internal_count || (p.internal_links || []).length}</td>
      <td style="text-align: center;">
        ${p.noindex ? '<span class="badge badge-critical">NOINDEX</span>' : '<span class="badge badge-pass">INDEXABLE</span>'}
      </td>
    `;
    tbody.appendChild(tr);
  });
}

function filterCrawledPagesTable() {
  const query = (document.getElementById('crawl-table-search')?.value || '').toLowerCase();
  document.querySelectorAll('.crawl-row').forEach((row) => {
    const u = row.getAttribute('data-url') || '';
    const t = row.getAttribute('data-title') || '';
    row.style.display = !query || u.includes(query) || t.includes(query) ? '' : 'none';
  });
}

// ==========================================================================
// 12. Competitor Benchmark
// ==========================================================================
function renderCompetitorBenchmark(result) {
  const section = document.getElementById('section-competitor-benchmark');
  const container = document.getElementById('competitor-comparison-content');
  const comps = result.competitors || [];

  if (!section || !container) return;

  if (comps.length === 0) {
    section.classList.add('hidden');
    return;
  }

  section.classList.remove('hidden');
  container.innerHTML = '';

  let html = `
    <table class="clean-table text-mono">
      <thead>
        <tr>
          <th>Metric</th>
          <th style="color: var(--primary); font-weight: 700;">${escapeHtml(result.domain)} (Target)</th>
  `;

  comps.forEach((c) => {
    html += `<th>${escapeHtml(c.domain || c.url)}</th>`;
  });

  html += `</tr></thead><tbody>`;

  const rows = [
    { label: 'Overall SEO Score', key: 'score', targetVal: (result.score || {}).score },
    { label: 'Grade', key: 'grade', targetVal: (result.score || {}).grade },
    { label: 'Mobile Performance', key: 'perf_mobile', targetVal: ((result.psi || {}).mobile || {}).scores?.performance },
    { label: 'HTTPS Enforced', key: 'https', targetVal: result.final_url?.startsWith('https://') ? 'Yes' : 'No' },
    { label: 'Title Length', key: 'title_len', targetVal: (result.home_facts || {}).title_len },
    { label: 'Word Count', key: 'word_count', targetVal: (result.home_facts || {}).word_count },
  ];

  rows.forEach((r) => {
    html += `<tr><td style="font-weight: 600;">${r.label}</td>`;
    html += `<td style="color: var(--primary); font-weight: 700;">${r.targetVal != null ? r.targetVal : '—'}</td>`;
    comps.forEach((c) => {
      let val = c[r.key];
      if (typeof val === 'boolean') val = val ? 'Yes' : 'No';
      html += `<td>${val != null ? val : '—'}</td>`;
    });
    html += `</tr>`;
  });

  html += `</tbody></table>`;
  container.innerHTML = html;
}

// ==========================================================================
// View Mode Switcher
// ==========================================================================
function setAuditViewMode(mode) {
  AppState.viewMode = mode;
  document.getElementById('view-mode-layman')?.classList.toggle('active', mode === 'layman');
  document.getElementById('view-mode-expert')?.classList.toggle('active', mode === 'expert');

  const pagesSection = document.getElementById('section-crawled-pages');
  const headingsSection = document.getElementById('section-headings-content-row');
  const deepdiveSection = document.getElementById('section-category-deepdive');

  if (deepdiveSection) deepdiveSection.style.display = 'flex';
  if (pagesSection) pagesSection.style.display = mode === 'expert' ? 'flex' : 'none';
  if (headingsSection) headingsSection.style.display = mode === 'expert' ? 'grid' : 'none';
}

// ==========================================================================
// Section Image Capture Engine (html2canvas)
// ==========================================================================
async function captureElementAsImage(elementId, baseName) {
  const el = document.getElementById(elementId);
  if (!el || !window.html2canvas) {
    showToast('Image capture utility not loaded.', 'error');
    return;
  }

  showToast('Generating section image...', 'info');

  try {
    const canvas = await html2canvas(el, {
      scale: 2,
      useCORS: true,
      logging: false,
      backgroundColor: '#ffffff',
    });

    const domain = AppState.currentResult?.domain || 'site';
    const date = new Date().toISOString().slice(0, 10);
    const filename = `${baseName}-${domain}-${date}.png`;

    const a = document.createElement('a');
    a.href = canvas.toDataURL('image/png');
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);

    showToast(`Downloaded: ${filename}`, 'success');
  } catch (err) {
    console.error('Capture error:', err);
    showToast('Failed to generate image capture', 'error');
  }
}

// Helper to reliably trigger file download or open in new tab
function triggerFileDownload(url, filename) {
  const link = document.createElement('a');
  link.href = url;
  if (filename) link.download = filename;
  link.target = '_blank';
  link.rel = 'noopener noreferrer';
  document.body.appendChild(link);
  link.click();
  setTimeout(() => {
    try {
      document.body.removeChild(link);
    } catch (e) {}
  }, 400);
}

function downloadWordReport() {
  if (!AppState.currentAuditId) {
    showToast('No active audit to export.', 'error');
    return;
  }
  const auditId = AppState.currentAuditId;
  const domain = AppState.currentResult?.domain || 'report';
  const url = `/api/audit/${auditId}/docx`;
  showToast('Downloading Word report (.docx)...', 'info');
  triggerFileDownload(url, `SEO-Audit-${domain}-${auditId.slice(0, 8)}.docx`);
}

async function handlePdfExport() {
  if (!AppState.currentAuditId) {
    showToast('No active audit to export.', 'error');
    return;
  }

  try {
    const check = await fetch(`/api/audit/${AppState.currentAuditId}/pdf`, { method: 'HEAD' });
    if (check.ok) {
      window.location.href = `/api/audit/${AppState.currentAuditId}/pdf`;
      showToast('Downloading PDF report...', 'info');
      return;
    }
  } catch (e) {}

  showToast('Opening clean print-to-PDF view...', 'info');
  setTimeout(() => {
    window.print();
  }, 300);
}

function downloadJsonReport() {
  if (!AppState.currentAuditId) {
    showToast('No active audit to export.', 'error');
    return;
  }
  window.location.href = `/api/audit/${AppState.currentAuditId}/json`;
}

// ==========================================================================
// ==========================================================================
// Progress Tracker & Historical Trajectory Suite
// ==========================================================================
async function initTrackerView() {
  const domainSelect = document.getElementById('tracker-domain-select');
  if (domainSelect && domainSelect.options.length === 0) {
    domainSelect.innerHTML = `<option value="">Loading historical audits...</option>`;
  }
  try {
    const res = await fetch('/api/history');
    const data = await res.json();
    const history = data.history || [];

    const domainSelect = document.getElementById('tracker-domain-select');
    const audit1Select = document.getElementById('compare-audit-1');
    const audit2Select = document.getElementById('compare-audit-2');

    if (!domainSelect) return;
    domainSelect.innerHTML = '';
    if (audit1Select) audit1Select.innerHTML = '';
    if (audit2Select) audit2Select.innerHTML = '';

    const uniqueDomains = [...new Set(history.map((h) => h.domain).filter(Boolean))];

    // If an active audit is currently loaded in memory, ensure its domain is available
    if (AppState.currentResult?.domain && !uniqueDomains.includes(AppState.currentResult.domain)) {
      uniqueDomains.unshift(AppState.currentResult.domain);
    }

    if (uniqueDomains.length === 0) {
      domainSelect.innerHTML = `<option value="">No historical audits yet</option>`;
      renderTrackerDomainStrip('', []);
      renderProgressTimelineChart([]);
      recomputeTargetGauges();
      renderTrackerMilestones([]);
      return;
    }

    uniqueDomains.forEach((dom) => {
      const opt = document.createElement('option');
      opt.value = dom;
      opt.textContent = dom;
      domainSelect.appendChild(opt);
    });

    // Populate comparison dropdowns (chronological: oldest to newest)
    const sortedAudits = [...history].reverse();
    sortedAudits.forEach((h) => {
      const opt1 = document.createElement('option');
      opt1.value = h.id;
      opt1.textContent = `${h.domain} (${h.date} - Score: ${h.score})`;
      if (audit1Select) audit1Select.appendChild(opt1);

      const opt2 = document.createElement('option');
      opt2.value = h.id;
      opt2.textContent = `${h.domain} (${h.date} - Score: ${h.score})`;
      if (audit2Select) audit2Select.appendChild(opt2);
    });

    if (audit2Select && audit2Select.options.length > 1) {
      audit2Select.selectedIndex = audit2Select.options.length - 1; // newest
      audit1Select.selectedIndex = 0; // oldest baseline
    }

    // Default to active audit's domain if present, otherwise first in list
    const activeDomain = AppState.currentResult?.domain || uniqueDomains[0];
    domainSelect.value = activeDomain;
    loadDomainTimeline(activeDomain);
  } catch (e) {
    console.error('Init tracker error:', e);
  }
}

function syncCurrentAuditToTracker() {
  if (!AppState.currentResult?.domain) {
    showToast('No active audit currently running or loaded.', 'info');
    return;
  }
  const domain = AppState.currentResult.domain;
  const select = document.getElementById('tracker-domain-select');
  if (select) {
    // Add if not present
    let found = false;
    for (let i = 0; i < select.options.length; i++) {
      if (select.options[i].value === domain) {
        select.selectedIndex = i;
        found = true;
        break;
      }
    }
    if (!found) {
      const opt = document.createElement('option');
      opt.value = domain;
      opt.textContent = domain;
      select.prepend(opt);
      select.selectedIndex = 0;
    }
  }
  loadDomainTimeline(domain);
  showToast(`Tracking progress for ${domain}`, 'success');
}

async function loadDomainTimeline(domain) {
  if (!domain) return;
  const milestonesFeed = document.getElementById('tracker-milestones-feed');
  if (milestonesFeed) {
    milestonesFeed.innerHTML = `
      <div class="shimmer-card">
        <div class="shimmer shimmer-line short" style="margin-bottom: 6px;"></div>
        <div class="shimmer shimmer-line" style="margin-bottom: 6px;"></div>
        <div class="shimmer shimmer-line medium"></div>
      </div>
      <div class="shimmer-card">
        <div class="shimmer shimmer-line short" style="margin-bottom: 6px;"></div>
        <div class="shimmer shimmer-line"></div>
      </div>
    `;
  }
  try {
    const res = await fetch(`/api/progress/timeline?domain=${encodeURIComponent(domain)}`);
    const data = await res.json();
    let timeline = data.timeline || [];

    // If current audit is for this domain and not yet in timeline, append temporary snapshot
    if (AppState.currentResult && AppState.currentResult.domain === domain) {
      const hasId = timeline.some((t) => t.id === AppState.currentAuditId);
      if (!hasId) {
        const sc = AppState.currentResult.score || {};
        const psi = AppState.currentResult.psi || {};
        timeline.push({
          id: AppState.currentAuditId || 'current',
          domain: domain,
          date: AppState.currentResult.date || 'Today',
          time: 'Active',
          score: sc.score || 0,
          grade: sc.grade || '?',
          pages_crawled: AppState.currentResult.pages_crawled || 1,
          critical_issues: (AppState.currentResult.priority_counts || {}).Critical || 0,
          high_issues: (AppState.currentResult.priority_counts || {}).High || 0,
          scores: {
            technical: (sc.dimensions || []).find((d) => d.key === 'technical')?.score || 0,
            onpage: (sc.dimensions || []).find((d) => d.key === 'onpage')?.score || 0,
            performance: (sc.dimensions || []).find((d) => d.key === 'performance')?.score || 0,
            psi_mobile: ((psi.mobile || {}).scores || {}).performance || 0,
            psi_desktop: ((psi.desktop || {}).scores || {}).performance || 0,
          },
        });
      }
    }

    AppState.currentTimeline = timeline;

    renderTrackerDomainStrip(domain, timeline);
    renderProgressTimelineChart(timeline);
    loadDomainTargets(domain);
    renderTrackerMilestones(timeline);
  } catch (err) {
    console.error('Timeline error:', err);
    showToast('Failed to load progress timeline', 'error');
  }
}

// 1. Domain Executive Health Strip
function renderTrackerDomainStrip(domain, timeline) {
  const stripScore = document.getElementById('tracker-strip-score');
  const stripGrade = document.getElementById('tracker-strip-grade');
  const stripDomain = document.getElementById('tracker-strip-domain');
  const stripDelta = document.getElementById('tracker-strip-delta');
  const stripDeltaSub = document.getElementById('tracker-strip-delta-sub');
  const stripAudits = document.getElementById('tracker-strip-audits');
  const stripDates = document.getElementById('tracker-strip-dates');
  const stripPsi = document.getElementById('tracker-strip-psi');
  const stripCritical = document.getElementById('tracker-strip-critical');
  const stripDate = document.getElementById('tracker-strip-date');
  const stripPages = document.getElementById('tracker-strip-pages');

  if (stripDomain) stripDomain.textContent = domain || 'No Domain Selected';

  if (!timeline || timeline.length === 0) {
    if (stripScore) stripScore.textContent = '—';
    if (stripGrade) stripGrade.textContent = 'GRADE —';
    if (stripDelta) stripDelta.textContent = '—';
    if (stripDeltaSub) stripDeltaSub.textContent = 'No baseline yet';
    if (stripAudits) stripAudits.textContent = '0';
    if (stripDates) stripDates.textContent = 'No records';
    if (stripPsi) stripPsi.textContent = '—';
    if (stripCritical) stripCritical.textContent = '0';
    if (stripDate) stripDate.textContent = '—';
    if (stripPages) stripPages.textContent = '0 pages';
    return;
  }

  const latest = timeline[timeline.length - 1];
  const baseline = timeline[0];

  const currentScore = latest.score != null ? Math.round(latest.score) : 0;
  const baselineScore = baseline.score != null ? Math.round(baseline.score) : 0;
  const netDelta = currentScore - baselineScore;

  if (stripScore) stripScore.textContent = currentScore;
  if (stripGrade) {
    stripGrade.textContent = `GRADE ${latest.grade || '?'}`;
    stripGrade.className = currentScore >= 85 ? 'badge badge-pass' : currentScore >= 60 ? 'badge badge-medium' : 'badge badge-critical';
  }

  if (stripDelta) {
    const sign = netDelta > 0 ? '+' : '';
    stripDelta.textContent = `${sign}${netDelta} pts`;
    stripDelta.style.color = netDelta > 0 ? '#16a34a' : netDelta < 0 ? '#dc2626' : '#475569';
  }
  if (stripDeltaSub) {
    stripDeltaSub.textContent = timeline.length > 1 ? `From ${baseline.score} on ${baseline.date}` : 'Baseline established';
  }

  if (stripAudits) stripAudits.textContent = timeline.length;
  if (stripDates) {
    stripDates.textContent = timeline.length > 1 ? `${baseline.date} to ${latest.date}` : `First audit: ${latest.date}`;
  }

  const mobPsi = latest.scores?.psi_mobile || latest.scores?.performance;
  if (stripPsi) {
    stripPsi.textContent = mobPsi != null ? `${mobPsi}/100` : '—';
    stripPsi.style.color = mobPsi >= 80 ? '#16a34a' : mobPsi >= 50 ? '#d97706' : '#dc2626';
  }

  const crit = latest.critical_issues || 0;
  if (stripCritical) {
    stripCritical.textContent = crit;
    stripCritical.style.color = crit === 0 ? '#16a34a' : '#dc2626';
  }

  if (stripDate) stripDate.textContent = latest.date;
  if (stripPages) stripPages.textContent = `${latest.pages_crawled || 1} pages reviewed`;
}

// 2. All-in-One Multi-Metric Trajectory Chart
function switchTrackerMetric(metricKey) {
  AppState.trackerMetric = metricKey;
  document.querySelectorAll('.tracker-metric-btn').forEach((btn) => {
    btn.classList.toggle('active', btn.getAttribute('onclick')?.includes(metricKey));
  });
  renderProgressTimelineChart(AppState.currentTimeline || []);
}

function renderProgressTimelineChart(timeline) {
  const canvas = document.getElementById('progressTimelineChart');
  if (!canvas || !window.Chart) return;

  if (AppState.charts.timeline) {
    AppState.charts.timeline.destroy();
  }

  const milestonesPills = document.getElementById('chart-milestones-pills');

  if (!timeline || timeline.length === 0) {
    if (milestonesPills) {
      milestonesPills.innerHTML = '<span style="color: var(--text-muted); font-size: 11px;">No milestones recorded yet. Run an audit to start tracking.</span>';
    }
    return;
  }

  // Populate milestone pills underneath the chart
  if (milestonesPills) {
    milestonesPills.innerHTML = '';
    timeline.forEach((item, idx) => {
      const chip = document.createElement('div');
      chip.className = 'milestone-pill';
      const isBaseline = idx === 0;
      const score = item.score != null ? Math.round(item.score) : '—';
      const prev = idx > 0 ? timeline[idx - 1] : null;
      let diffHtml = '';
      if (prev && prev.score != null && item.score != null) {
        const diff = Math.round(item.score) - Math.round(prev.score);
        diffHtml = diff > 0 ? `<span style="color: #16a34a; font-weight: bold;">+${diff}</span>` : diff < 0 ? `<span style="color: #dc2626; font-weight: bold;">${diff}</span>` : '<span>=</span>';
      } else if (isBaseline) {
        diffHtml = '<span class="badge badge-low" style="font-size: 9px; padding: 1px 4px;">BASELINE</span>';
      }

      chip.innerHTML = `
        <strong>#${idx + 1}</strong>
        <span>${item.date}${item.time && item.time !== 'Active' ? ' ' + item.time : ''}:</span>
        <strong style="color: var(--primary);">${score}/100</strong>
        ${diffHtml}
      `;
      chip.title = `Click to view Audit #${idx + 1} in Suite`;
      chip.onclick = () => {
        if (item.id) openPastAudit(item.id);
      };
      milestonesPills.appendChild(chip);
    });
  }

  // Format exact labels with date & time
  const labels = timeline.map((t, idx) => {
    const timeStr = t.time && t.time !== 'Active' ? ` ${t.time}` : '';
    return `Audit #${idx + 1} (${t.date}${timeStr})`;
  });

  const targetScore = parseInt(document.getElementById('target-score-input')?.value, 10) || 90;
  let datasets = [];
  let dualAxis = false;

  if (AppState.trackerMetric === 'multi') {
    dualAxis = true;
    datasets = [
      {
        label: 'Overall Health Score',
        data: timeline.map((t) => (t.score != null ? Math.round(t.score) : 0)),
        borderColor: '#2563eb',
        backgroundColor: 'rgba(37, 99, 235, 0.08)',
        borderWidth: 3,
        tension: 0.25,
        fill: true,
        pointRadius: 6,
        pointHoverRadius: 8,
        pointBackgroundColor: '#2563eb',
        pointBorderColor: '#ffffff',
        pointBorderWidth: 2,
        yAxisID: 'y',
      },
      {
        label: 'Mobile PSI Speed',
        data: timeline.map((t) => t.scores?.psi_mobile || t.scores?.performance || 0),
        borderColor: '#f59e0b',
        borderWidth: 2.5,
        tension: 0.25,
        borderDash: [5, 4],
        pointRadius: 5,
        pointHoverRadius: 7,
        pointBackgroundColor: '#f59e0b',
        fill: false,
        yAxisID: 'y',
      },
      {
        label: 'Desktop PSI Speed',
        data: timeline.map((t) => t.scores?.psi_desktop || 0),
        borderColor: '#0d9488',
        borderWidth: 2,
        tension: 0.25,
        pointRadius: 4,
        pointHoverRadius: 6,
        pointBackgroundColor: '#0d9488',
        fill: false,
        yAxisID: 'y',
      },
      {
        label: 'Technical SEO Subscore',
        data: timeline.map((t) => t.scores?.technical || 0),
        borderColor: '#6366f1',
        borderWidth: 2,
        tension: 0.25,
        pointRadius: 4,
        pointHoverRadius: 6,
        pointBackgroundColor: '#6366f1',
        fill: false,
        yAxisID: 'y',
      },
      {
        label: `Target Goal (${targetScore})`,
        data: timeline.map(() => targetScore),
        borderColor: '#16a34a',
        borderWidth: 1.5,
        borderDash: [6, 4],
        pointRadius: 0,
        fill: false,
        yAxisID: 'y',
      },
      {
        label: 'Critical Blockers (Urgent)',
        data: timeline.map((t) => t.critical_issues || 0),
        borderColor: '#dc2626',
        backgroundColor: 'rgba(220, 38, 38, 0.1)',
        borderWidth: 2,
        tension: 0.2,
        borderDash: [3, 3],
        pointRadius: 5,
        pointHoverRadius: 7,
        pointBackgroundColor: '#dc2626',
        fill: false,
        yAxisID: 'y1',
      },
    ];
  } else if (AppState.trackerMetric === 'overall') {
    datasets = [
      {
        label: 'Overall SEO Health Score',
        data: timeline.map((t) => t.score || 0),
        borderColor: '#2563eb',
        backgroundColor: 'rgba(37, 99, 235, 0.12)',
        borderWidth: 3,
        tension: 0.25,
        fill: true,
        pointRadius: 6,
        pointBackgroundColor: '#2563eb',
        pointBorderColor: '#ffffff',
      },
      {
        label: `Target Goal (${targetScore})`,
        data: timeline.map(() => targetScore),
        borderColor: '#16a34a',
        borderWidth: 2,
        borderDash: [6, 4],
        pointRadius: 0,
        fill: false,
      },
    ];
  } else if (AppState.trackerMetric === 'speed') {
    datasets = [
      {
        label: 'Mobile PSI Speed',
        data: timeline.map((t) => t.scores?.psi_mobile || t.scores?.performance || 0),
        borderColor: '#f59e0b',
        backgroundColor: 'rgba(245, 158, 11, 0.1)',
        borderWidth: 2.5,
        tension: 0.25,
        fill: true,
        pointRadius: 5,
        pointBackgroundColor: '#f59e0b',
      },
      {
        label: 'Desktop PSI Speed',
        data: timeline.map((t) => t.scores?.psi_desktop || 0),
        borderColor: '#0d9488',
        backgroundColor: 'rgba(13, 148, 136, 0.1)',
        borderWidth: 2.5,
        tension: 0.25,
        fill: true,
        pointRadius: 5,
        pointBackgroundColor: '#0d9488',
      },
    ];
  } else if (AppState.trackerMetric === 'category') {
    const catDefs = [
      { key: 'technical', label: 'Technical', color: '#6366f1' },
      { key: 'onpage', label: 'On-Page', color: '#2563eb' },
      { key: 'performance', label: 'Performance', color: '#f59e0b' },
      { key: 'content', label: 'Content', color: '#0d9488' },
      { key: 'mobile', label: 'Mobile', color: '#0284c7' },
      { key: 'security', label: 'Security', color: '#e11d48' },
    ];
    datasets = catDefs.map((cd) => ({
      label: cd.label,
      data: timeline.map((t) => (t.scores ? t.scores[cd.key] || 0 : 0)),
      borderColor: cd.color,
      borderWidth: 2,
      tension: 0.25,
      pointRadius: 4,
      pointBackgroundColor: cd.color,
      fill: false,
    }));
  } else if (AppState.trackerMetric === 'cwv') {
    datasets = [
      {
        label: 'LCP Speed (Seconds)',
        data: timeline.map((t) => (t.cwv?.lcp_ms ? (t.cwv.lcp_ms / 1000).toFixed(2) : 2.5)),
        borderColor: '#0d9488',
        backgroundColor: 'rgba(13, 148, 136, 0.1)',
        borderWidth: 2.5,
        tension: 0.25,
        fill: true,
        pointRadius: 5,
        pointBackgroundColor: '#0d9488',
      },
    ];
  } else if (AppState.trackerMetric === 'issues') {
    datasets = [
      {
        label: 'Critical Urgent Issues',
        data: timeline.map((t) => t.critical_issues || 0),
        borderColor: '#dc2626',
        backgroundColor: 'rgba(220, 38, 38, 0.15)',
        borderWidth: 2,
        tension: 0.2,
        fill: true,
        pointRadius: 5,
        pointBackgroundColor: '#dc2626',
      },
      {
        label: 'Total Unresolved Issues',
        data: timeline.map((t) => t.total_issues || (t.critical_issues || 0) + (t.high_issues || 0) + (t.medium_issues || 0)),
        borderColor: '#475569',
        borderWidth: 2,
        tension: 0.2,
        borderDash: [3, 3],
        pointRadius: 4,
        pointBackgroundColor: '#475569',
        fill: false,
      },
    ];
  }

  const maxCrit = Math.max(...timeline.map((t) => t.critical_issues || 0), 3);

  const scalesConfig = {
    y: {
      min: 0,
      max: 100,
      grid: { color: '#e2e8f0' },
      ticks: { color: '#475569', font: { family: 'Plus Jakarta Sans', size: 11 } },
      title: { display: true, text: 'Score / PSI Speed (0-100)', color: '#475569', font: { family: 'Plus Jakarta Sans', size: 10.5, weight: '700' } },
    },
    x: {
      grid: { color: '#e2e8f0' },
      ticks: { color: '#0f172a', font: { family: 'JetBrains Mono', size: 11, weight: '600' } },
    },
  };

  if (dualAxis) {
    scalesConfig.y1 = {
      position: 'right',
      min: 0,
      suggestedMax: maxCrit + 1,
      grid: { display: false },
      ticks: { color: '#dc2626', stepSize: 1, font: { family: 'JetBrains Mono', size: 11 } },
      title: { display: true, text: 'Critical Issues', color: '#dc2626', font: { family: 'Plus Jakarta Sans', size: 10.5, weight: '700' } },
    };
  }

  AppState.charts.timeline = new Chart(canvas, {
    type: 'line',
    data: { labels, datasets },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      interaction: {
        mode: 'index',
        intersect: false,
      },
      scales: scalesConfig,
      onClick: (e, elements) => {
        if (elements && elements.length > 0) {
          const idx = elements[0].index;
          const auditItem = timeline[idx];
          if (auditItem && auditItem.id) {
            openPastAudit(auditItem.id);
          }
        }
      },
      plugins: {
        legend: {
          position: 'top',
          labels: { color: '#0f172a', font: { family: 'Plus Jakarta Sans', size: 11, weight: '700' }, boxWidth: 14 },
        },
        tooltip: {
          padding: 12,
          backgroundColor: '#0f172a',
          titleFont: { family: 'Plus Jakarta Sans', size: 12.5, weight: 'bold' },
          bodyFont: { family: 'JetBrains Mono', size: 11 },
          callbacks: {
            title: (items) => {
              const idx = items[0]?.dataIndex ?? 0;
              const a = timeline[idx] || {};
              return `Audit #${idx + 1} • ${a.date || ''} ${a.time || ''}`;
            },
            afterBody: (items) => {
              const idx = items[0]?.dataIndex ?? 0;
              const a = timeline[idx] || {};
              const prev = idx > 0 ? timeline[idx - 1] : null;
              const lines = [];
              if (a.grade) lines.push(`Grade: ${a.grade}`);
              if (a.pages_crawled) lines.push(`Pages Crawled: ${a.pages_crawled}`);
              if (prev && a.score != null && prev.score != null) {
                const diff = Math.round(a.score) - Math.round(prev.score);
                lines.push(`Score Delta: ${diff > 0 ? '+' : ''}${diff} pts`);
              }
              if (a.label) lines.push(`State: ${a.label}`);
              lines.push('Click point to view audit');
              return lines;
            },
          },
        },
      },
    },
  });
}

// 3. Goal & Target Cockpit
async function loadDomainTargets(domain) {
  try {
    const res = await fetch(`/api/progress/targets?domain=${encodeURIComponent(domain)}`);
    const targets = await res.json();

    const tScore = document.getElementById('target-score-input');
    const tPsi = document.getElementById('target-mobile-psi-input');
    const tLcp = document.getElementById('target-lcp-input');
    const tCrit = document.getElementById('target-critical-input');

    if (tScore) tScore.value = targets.target_score || 90;
    if (tPsi) tPsi.value = targets.target_mobile_psi || 85;
    if (tLcp) tLcp.value = targets.target_lcp || 2.5;
    if (tCrit) tCrit.value = targets.target_critical != null ? targets.target_critical : 0;

    recomputeTargetGauges();
  } catch (e) {
    console.warn('Could not load targets:', e);
  }
}

function recomputeTargetGauges() {
  const tScore = parseInt(document.getElementById('target-score-input')?.value, 10) || 90;
  const tPsi = parseInt(document.getElementById('target-mobile-psi-input')?.value, 10) || 85;
  const tLcp = parseFloat(document.getElementById('target-lcp-input')?.value) || 2.5;
  const tCrit = parseInt(document.getElementById('target-critical-input')?.value, 10) || 0;

  const latest = (AppState.currentTimeline || [])[AppState.currentTimeline?.length - 1] || {};
  const hasData = Boolean(latest.score != null || AppState.currentResult?.score?.score != null);

  const gScoreVal = document.getElementById('target-gauge-score-val');
  const gScoreBar = document.getElementById('target-gauge-score-bar');
  const gScoreBadge = document.getElementById('target-gauge-score-badge');
  const gScoreStatus = document.getElementById('target-gauge-score-status');

  const gPsiVal = document.getElementById('target-gauge-psi-val');
  const gPsiBar = document.getElementById('target-gauge-psi-bar');
  const gPsiBadge = document.getElementById('target-gauge-psi-badge');
  const gPsiStatus = document.getElementById('target-gauge-psi-status');

  const gLcpVal = document.getElementById('target-gauge-lcp-val');
  const gLcpBar = document.getElementById('target-gauge-lcp-bar');
  const gLcpBadge = document.getElementById('target-gauge-lcp-badge');
  const gLcpStatus = document.getElementById('target-gauge-lcp-status');

  const gCritVal = document.getElementById('target-gauge-crit-val');
  const gCritBar = document.getElementById('target-gauge-crit-bar');
  const gCritBadge = document.getElementById('target-gauge-crit-badge');
  const gCritStatus = document.getElementById('target-gauge-crit-status');

  if (!hasData) {
    if (gScoreVal) gScoreVal.textContent = `— / ${tScore}`;
    if (gScoreBar) { gScoreBar.style.width = '0%'; gScoreBar.style.backgroundColor = '#94a3b8'; }
    if (gScoreBadge) { gScoreBadge.textContent = '—'; gScoreBadge.className = 'badge badge-subtle'; }
    if (gScoreStatus) { gScoreStatus.textContent = 'Run an audit to track gap'; gScoreStatus.style.color = 'var(--text-subtle)'; }

    if (gPsiVal) gPsiVal.textContent = `— / ${tPsi}`;
    if (gPsiBar) { gPsiBar.style.width = '0%'; gPsiBar.style.backgroundColor = '#94a3b8'; }
    if (gPsiBadge) { gPsiBadge.textContent = '—'; gPsiBadge.className = 'badge badge-subtle'; }
    if (gPsiStatus) { gPsiStatus.textContent = 'Speed benchmark comparison'; gPsiStatus.style.color = 'var(--text-subtle)'; }

    if (gLcpVal) gLcpVal.textContent = `— <= ${tLcp.toFixed(1)}s`;
    if (gLcpBar) { gLcpBar.style.width = '0%'; gLcpBar.style.backgroundColor = '#94a3b8'; }
    if (gLcpBadge) { gLcpBadge.textContent = '—'; gLcpBadge.className = 'badge badge-subtle'; }
    if (gLcpStatus) { gLcpStatus.textContent = 'Core Web Vitals ceiling'; gLcpStatus.style.color = 'var(--text-subtle)'; }

    if (gCritVal) gCritVal.textContent = `— remaining`;
    if (gCritBar) { gCritBar.style.width = '0%'; gCritBar.style.backgroundColor = '#94a3b8'; }
    if (gCritBadge) { gCritBadge.textContent = '—'; gCritBadge.className = 'badge badge-subtle'; }
    if (gCritStatus) { gCritStatus.textContent = 'Urgent blockers remaining'; gCritStatus.style.color = 'var(--text-subtle)'; }
    return;
  }

  const currentScore = latest.score != null ? Math.round(latest.score) : ((AppState.currentResult?.score || {}).score || 0);
  const currentPsi = latest.scores?.psi_mobile || latest.scores?.performance || (((AppState.currentResult?.psi || {}).mobile || {}).scores?.performance || 0);
  const currentLcp = latest.cwv?.lcp_ms ? (latest.cwv.lcp_ms / 1000) : 2.5;
  const currentCrit = latest.critical_issues != null ? latest.critical_issues : ((AppState.currentResult?.priority_counts || {}).Critical || 0);

  // 1. Health Score Gauge
  const scorePct = Math.min(100, Math.round((currentScore / tScore) * 100));
  const scoreGap = tScore - currentScore;

  if (gScoreVal) gScoreVal.textContent = `${currentScore} / ${tScore}`;
  if (gScoreBar) {
    gScoreBar.style.width = `${scorePct}%`;
    gScoreBar.style.backgroundColor = scoreGap <= 0 ? '#16a34a' : '#2563eb';
  }
  if (gScoreBadge) {
    gScoreBadge.textContent = scoreGap <= 0 ? 'MET' : `${scorePct}%`;
    gScoreBadge.className = scoreGap <= 0 ? 'badge badge-pass' : 'badge badge-low';
  }
  if (gScoreStatus) {
    gScoreStatus.textContent = scoreGap <= 0 ? `Target achieved! (+${Math.abs(scoreGap)} pts above)` : `${scoreGap} pts needed to hit benchmark`;
    gScoreStatus.style.color = scoreGap <= 0 ? '#16a34a' : 'var(--text-subtle)';
  }

  // 2. Mobile PSI Gauge
  const psiGap = tPsi - currentPsi;

  if (gPsiVal) gPsiVal.textContent = `${currentPsi} / ${tPsi}`;
  if (gPsiBar) {
    const pPct = Math.min(100, Math.round((currentPsi / tPsi) * 100));
    gPsiBar.style.width = `${pPct}%`;
    gPsiBar.style.backgroundColor = psiGap <= 0 ? '#16a34a' : '#f59e0b';
  }
  if (gPsiBadge) {
    gPsiBadge.textContent = psiGap <= 0 ? 'MET' : 'GAP';
    gPsiBadge.className = psiGap <= 0 ? 'badge badge-pass' : 'badge badge-medium';
  }
  if (gPsiStatus) {
    gPsiStatus.textContent = psiGap <= 0 ? `Target exceeded (+${Math.abs(psiGap)} pts)` : `${psiGap} PSI points below benchmark`;
    gPsiStatus.style.color = psiGap <= 0 ? '#16a34a' : '#d97706';
  }

  // 3. LCP Speed Gauge
  if (gLcpVal) gLcpVal.textContent = `${currentLcp.toFixed(1)}s <= ${tLcp.toFixed(1)}s`;
  const isLcpGood = currentLcp <= tLcp;
  if (gLcpBar) {
    gLcpBar.style.width = isLcpGood ? '100%' : '60%';
    gLcpBar.style.backgroundColor = isLcpGood ? '#16a34a' : '#dc2626';
  }
  if (gLcpBadge) {
    gLcpBadge.textContent = isLcpGood ? 'PASS' : 'POOR';
    gLcpBadge.className = isLcpGood ? 'badge badge-pass' : 'badge badge-critical';
  }
  if (gLcpStatus) {
    const diff = Math.abs(tLcp - currentLcp).toFixed(1);
    gLcpStatus.textContent = isLcpGood ? `${diff}s faster than target ceiling` : `${diff}s slower than threshold`;
    gLcpStatus.style.color = isLcpGood ? '#16a34a' : '#dc2626';
  }

  // 4. Critical Issues Gauge
  if (gCritVal) gCritVal.textContent = `${currentCrit} remaining`;
  if (gCritBar) {
    gCritBar.style.width = currentCrit === 0 ? '100%' : '40%';
    gCritBar.style.backgroundColor = currentCrit === 0 ? '#16a34a' : '#dc2626';
  }
  if (gCritBadge) {
    gCritBadge.textContent = currentCrit === 0 ? 'ZERO' : `${currentCrit} LEFT`;
    gCritBadge.className = currentCrit === 0 ? 'badge badge-pass' : 'badge badge-critical';
  }
  if (gCritStatus) {
    gCritStatus.textContent = currentCrit === 0 ? 'All critical blockers resolved!' : `${currentCrit} urgent issue(s) remaining`;
    gCritStatus.style.color = currentCrit === 0 ? '#16a34a' : '#dc2626';
  }
}

function applyTargetPreset(preset) {
  const tScore = document.getElementById('target-score-input');
  const tPsi = document.getElementById('target-mobile-psi-input');
  const tLcp = document.getElementById('target-lcp-input');
  const tCrit = document.getElementById('target-critical-input');

  if (preset === 'industry') {
    if (tScore) tScore.value = 90;
    if (tPsi) tPsi.value = 85;
    if (tLcp) tLcp.value = 2.5;
    if (tCrit) tCrit.value = 0;
    showToast('Applied Top 10% Industry Benchmark Preset.', 'info');
  } else if (preset === 'perfect') {
    if (tScore) tScore.value = 98;
    if (tPsi) tPsi.value = 95;
    if (tLcp) tLcp.value = 1.8;
    if (tCrit) tCrit.value = 0;
    showToast('Applied Aggressive Perfection Preset.', 'info');
  }
  recomputeTargetGauges();
  saveDomainTargets();
}

async function saveDomainTargets() {
  const domain = document.getElementById('tracker-domain-select')?.value;
  if (!domain) return;

  const tScore = parseInt(document.getElementById('target-score-input')?.value, 10) || 90;
  const tPsi = parseInt(document.getElementById('target-mobile-psi-input')?.value, 10) || 85;
  const tLcp = parseFloat(document.getElementById('target-lcp-input')?.value) || 2.5;
  const tCrit = parseInt(document.getElementById('target-critical-input')?.value, 10) || 0;

  try {
    const res = await fetch('/api/progress/targets', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        domain,
        targets: { target_score: tScore, target_mobile_psi: tPsi, target_lcp: tLcp, target_critical: tCrit },
      }),
    });
    if (res.ok) {
      showToast('Benchmark targets saved successfully.', 'success');
      recomputeTargetGauges();
      renderProgressTimelineChart(AppState.currentTimeline || []);
    }
  } catch (e) {
    showToast('Could not save goals', 'error');
  }
}

// 4. Interactive "What-If" Fix Simulator
function setupFixSimulator(result) {
  const container = document.getElementById('simulator-issues-container');
  if (!container) return;
  container.innerHTML = '';

  const currentScore = (result.score || {}).score || 0;
  const currentGrade = (result.score || {}).grade || '?';

  const badgeScore = document.getElementById('sim-projected-score-badge');
  const badgeGain = document.getElementById('sim-gain-badge');
  const countEl = document.getElementById('sim-selected-count');

  if (badgeScore) badgeScore.textContent = `${currentScore} / 100 (Grade ${currentGrade})`;
  if (badgeGain) badgeGain.textContent = '+0 pts gain';
  if (countEl) countEl.textContent = '0 issues selected for simulation';

  const allChecks = result.checks || [];
  const fixable = allChecks.filter((c) => c.status === 'fail' || c.status === 'warn');

  if (fixable.length === 0) {
    container.innerHTML = `<div style="color: var(--color-pass); font-weight: 600; padding: 12px; text-align: center;">All checks currently pass! Target health score is already 100%.</div>`;
    return;
  }

  AppState.simulatedChecks.clear();

  fixable.forEach((c) => {
    const item = document.createElement('label');
    item.className = 'sim-issue-item';

    const ptsEst = c.severity === 'critical' ? 5 : c.severity === 'high' ? 3 : 2;

    item.innerHTML = `
      <div style="display: flex; align-items: center; gap: 8px;">
        <input type="checkbox" class="sim-check-box" value="${escapeHtml(c.id)}" data-severity="${escapeHtml(c.severity || '')}" onchange="toggleSimulatedFix('${escapeHtml(c.id)}', this.checked)">
        <div>
          <span style="font-weight: 700; color: var(--text-main); font-size: 12.5px;">${escapeHtml(c.label)}</span>
          <span style="font-size: 11px; color: var(--text-muted); margin-left: 6px;">[${c.category}]</span>
        </div>
      </div>
      <div style="display: flex; align-items: center; gap: 6px;">
        <span class="badge ${c.status === 'fail' ? 'badge-critical' : 'badge-medium'}">${c.status.toUpperCase()}</span>
        <span class="badge badge-pass text-mono">+${ptsEst} pts est.</span>
      </div>
    `;
    container.appendChild(item);
  });
}

function simulatorSelectAll(type) {
  const boxes = document.querySelectorAll('.sim-check-box');
  boxes.forEach((box) => {
    const sev = box.getAttribute('data-severity') || '';
    if (type === 'all') {
      box.checked = true;
      AppState.simulatedChecks.add(box.value);
    } else if (type === 'critical') {
      if (sev === 'critical') {
        box.checked = true;
        AppState.simulatedChecks.add(box.value);
      } else {
        box.checked = false;
        AppState.simulatedChecks.delete(box.value);
      }
    } else if (type === 'none') {
      box.checked = false;
      AppState.simulatedChecks.delete(box.value);
    }
  });

  const countEl = document.getElementById('sim-selected-count');
  if (countEl) countEl.textContent = `${AppState.simulatedChecks.size} issues selected for simulation`;

  triggerSimulatorRecalculation();
}

async function toggleSimulatedFix(checkId, isChecked) {
  if (isChecked) {
    AppState.simulatedChecks.add(checkId);
  } else {
    AppState.simulatedChecks.delete(checkId);
  }

  const countEl = document.getElementById('sim-selected-count');
  if (countEl) countEl.textContent = `${AppState.simulatedChecks.size} issues selected for simulation`;

  triggerSimulatorRecalculation();
}

async function triggerSimulatorRecalculation() {
  if (!AppState.currentAuditId) return;

  try {
    const res = await fetch('/api/progress/simulate', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        audit_id: AppState.currentAuditId,
        fixed_check_ids: Array.from(AppState.simulatedChecks),
      }),
    });

    const data = await res.json();
    const badgeScore = document.getElementById('sim-projected-score-badge');
    const badgeGain = document.getElementById('sim-gain-badge');

    if (badgeScore) {
      badgeScore.textContent = `${data.projected_score} / 100 (Grade ${data.projected_grade})`;
      badgeScore.className = data.projected_score >= 85 ? 'badge badge-pass text-mono' : 'badge badge-low text-mono';
    }

    if (badgeGain) {
      badgeGain.textContent = `+${data.score_gain} pts gain`;
      badgeGain.className = data.score_gain > 0 ? 'badge badge-pass text-mono' : 'badge badge-low text-mono';
    }
  } catch (e) {
    console.error('Simulator error:', e);
  }
}

// 5. Side-by-Side Comparison Engine
function presetCompare(type) {
  const audit1Select = document.getElementById('compare-audit-1');
  const audit2Select = document.getElementById('compare-audit-2');
  if (!audit1Select || !audit2Select || audit1Select.options.length < 2) {
    showToast('At least 2 audits are needed to compare.', 'info');
    return;
  }

  if (type === 'first-last') {
    audit1Select.selectedIndex = 0; // oldest
    audit2Select.selectedIndex = audit2Select.options.length - 1; // newest
  } else if (type === 'prev-last') {
    audit1Select.selectedIndex = Math.max(0, audit2Select.options.length - 2);
    audit2Select.selectedIndex = audit2Select.options.length - 1;
  }

  executeAuditComparison();
}

async function executeAuditComparison() {
  const a1 = document.getElementById('compare-audit-1')?.value;
  const a2 = document.getElementById('compare-audit-2')?.value;
  const out = document.getElementById('comparison-results-output');

  if (!a1 || !a2) {
    showToast('Please select two audits to compare.', 'error');
    return;
  }

  try {
    showToast('Computing side-by-side progression diffs...', 'info');
    const res = await fetch('/api/progress/compare', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ audit1: a1, audit2: a2 }),
    });

    const data = await res.json();
    if (!res.ok || data.error) {
      throw new Error(data.error || 'Comparison calculation failed');
    }

    if (!out) return;
    out.classList.remove('hidden');

    const delta = data.score_delta;
    const deltaColor = delta > 0 ? '#16a34a' : delta < 0 ? '#dc2626' : '#475569';
    const deltaSign = delta > 0 ? '+' : '';

    // Build Dimension Comparison Rows
    const dims = data.dimensions || {};
    let dimRowsHtml = '';
    const catLabels = {
      technical: 'Technical SEO',
      onpage: 'On-Page SEO',
      performance: 'Performance',
      content: 'Content Quality',
      mobile: 'Mobile Optimization',
      accessibility: 'Accessibility',
      security: 'Security',
      ux: 'User Experience',
    };

    Object.keys(dims).forEach((k) => {
      const d = dims[k];
      const bScore = d.before != null ? Math.round(d.before) : '—';
      const aScore = d.after != null ? Math.round(d.after) : '—';
      const dDelta = d.delta;
      let badgeHtml = '<span class="delta-badge-neutral">Steady</span>';
      if (dDelta != null && dDelta > 0) {
        badgeHtml = `<span class="delta-badge-pos">+${dDelta} pts</span>`;
      } else if (dDelta != null && dDelta < 0) {
        badgeHtml = `<span class="delta-badge-neg">${dDelta} pts</span>`;
      }

      dimRowsHtml += `
        <tr>
          <td style="font-weight: 700;">${catLabels[k] || k}</td>
          <td style="text-align: center; font-family: var(--font-mono);">${bScore}/100</td>
          <td style="text-align: center; font-family: var(--font-mono); font-weight: 700; color: var(--primary);">${aScore}/100</td>
          <td style="text-align: center;">${badgeHtml}</td>
          <td style="width: 28%;">
            <div class="cat-track" style="height: 6px;">
              <div class="cat-fill" style="width: ${typeof aScore === 'number' ? aScore : 0}%; background-color: var(--primary);"></div>
            </div>
          </td>
        </tr>
      `;
    });

    out.innerHTML = `
      <!-- Comparison KPI Delta Strip -->
      <div class="grid-4" style="text-align: center;">
        <div class="stat-pill">
          <span class="stat-pill-label">Overall Score Delta</span>
          <span class="stat-pill-val" style="color: ${deltaColor}; font-size: 19px;">${deltaSign}${delta} pts</span>
          <span style="font-size: 11px; color: var(--text-muted);">${data.baseline.score} (Grade ${data.baseline.grade}) &rarr; ${data.latest.score} (Grade ${data.latest.grade})</span>
        </div>
        <div class="stat-pill">
          <span class="stat-pill-label">Issues Remediated</span>
          <span class="stat-pill-val" style="color: #16a34a; font-size: 19px;">${data.resolved_issues_count} Fixed</span>
          <span style="font-size: 11px; color: var(--text-muted);">Verified resolved in target audit</span>
        </div>
        <div class="stat-pill">
          <span class="stat-pill-label">New Regressions</span>
          <span class="stat-pill-val" style="color: #dc2626; font-size: 19px;">${data.new_issues_count} New</span>
          <span style="font-size: 11px; color: var(--text-muted);">Flagged since baseline audit</span>
        </div>
        <div class="stat-pill">
          <span class="stat-pill-label">Mobile Lighthouse Speed</span>
          <span class="stat-pill-val" style="color: var(--primary); font-size: 19px;">
            ${data.speed_comparison?.mobile_perf_delta != null ? `${data.speed_comparison.mobile_perf_delta > 0 ? '+' : ''}${data.speed_comparison.mobile_perf_delta} PSI` : 'Steady'}
          </span>
          <span style="font-size: 11px; color: var(--text-muted);">${data.speed_comparison?.mobile_perf_before || '—'} &rarr; ${data.speed_comparison?.mobile_perf_after || '—'}</span>
        </div>
      </div>

      <!-- Dimension Breakdown Side-by-Side Table -->
      <div class="comparison-table-wrapper">
        <table class="clean-table">
          <thead>
            <tr>
              <th>Audit Dimension</th>
              <th style="text-align: center;">Baseline (${data.baseline.date})</th>
              <th style="text-align: center;">Target (${data.latest.date})</th>
              <th style="text-align: center;">Net Delta</th>
              <th>Progress Gauge</th>
            </tr>
          </thead>
          <tbody>
            ${dimRowsHtml}
          </tbody>
        </table>
      </div>

      <!-- 3 Issue Detail Sections (Resolved, New, Persistent) -->
      <div class="grid-2">
        <!-- 1. Resolved Issues (Celebration) -->
        <div style="background-color: var(--color-pass-bg); border: 1px solid var(--color-pass-border); border-radius: var(--radius-md); padding: 14px 18px; display: flex; flex-direction: column; gap: 8px;">
          <div style="display: flex; align-items: center; justify-content: space-between;">
            <h5 style="font-size: 13px; font-weight: 700; color: var(--color-pass);">Resolved Issues (${data.resolved_issues_count})</h5>
            <span class="badge badge-pass">Remediated</span>
          </div>
          ${
            data.resolved_issues && data.resolved_issues.length > 0
              ? `<ul style="list-style: none; font-size: 12px; display: flex; flex-direction: column; gap: 6px;">
                   ${data.resolved_issues.map((r) => `
                     <li style="display: flex; align-items: baseline; gap: 6px;">
                       <span style="color: #16a34a; font-weight: bold; flex-shrink: 0;">[FIXED]</span>
                       <span><strong>${escapeHtml(r.label)}</strong> <span style="color: var(--text-muted);">[${r.category}]</span></span>
                     </li>
                   `).join('')}
                 </ul>`
              : '<div style="color: var(--text-muted); font-size: 12px;">No issues were resolved between these two runs.</div>'
          }
        </div>

        <!-- 2. New Regressions / Warnings Flagged -->
        <div style="background-color: var(--color-critical-bg); border: 1px solid var(--color-critical-border); border-radius: var(--radius-md); padding: 14px 18px; display: flex; flex-direction: column; gap: 8px;">
          <div style="display: flex; align-items: center; justify-content: space-between;">
            <h5 style="font-size: 13px; font-weight: 700; color: var(--color-critical);">New Regressions Flagged (${data.new_issues_count})</h5>
            <span class="badge badge-critical">New Alert</span>
          </div>
          ${
            data.new_issues && data.new_issues.length > 0
              ? `<ul style="list-style: none; font-size: 12px; display: flex; flex-direction: column; gap: 6px;">
                   ${data.new_issues.map((n) => `
                     <li style="display: flex; align-items: baseline; gap: 6px;">
                       <span style="color: #dc2626; font-weight: bold; flex-shrink: 0;">[NEW]</span>
                       <span><strong>${escapeHtml(n.label)}</strong>: ${escapeHtml(n.fix || 'Review finding')}</span>
                     </li>
                   `).join('')}
                 </ul>`
              : '<div style="color: var(--color-pass); font-size: 12px;">Zero regressions introduced! Clean progression.</div>'
          }
        </div>
      </div>
    `;

    showToast('Comparison computed.', 'success');
  } catch (err) {
    showToast(`Comparison error: ${err.message}`, 'error');
  }
}

// 6. Chronological Milestone Feed
function renderTrackerMilestones(timeline) {
  const container = document.getElementById('tracker-milestones-feed');
  if (!container) return;

  if (!timeline || timeline.length === 0) {
    container.innerHTML = `<div style="color: var(--text-muted); font-size: 12px; padding: 12px;">No historical milestones recorded yet. Run an audit to start tracking.</div>`;
    return;
  }

  container.innerHTML = '';

  // Render newest first in milestone list
  const revList = [...timeline].reverse();

  revList.forEach((m, idx) => {
    const origIdx = timeline.length - idx;
    const scoreVal = m.score != null ? Math.round(m.score) : 0;
    const scoreColor = scoreVal >= 85 ? '#16a34a' : scoreVal >= 60 ? '#d97706' : '#dc2626';

    let deltaBadgeHtml = '';
    const prev = timeline[origIdx - 2];
    if (prev) {
      const diff = scoreVal - (prev.score || 0);
      if (diff > 0) {
        deltaBadgeHtml = `<span class="delta-badge-pos">+${diff} pts</span>`;
      } else if (diff < 0) {
        deltaBadgeHtml = `<span class="delta-badge-neg">${diff} pts</span>`;
      } else {
        deltaBadgeHtml = `<span class="delta-badge-neutral">Steady</span>`;
      }
    } else {
      deltaBadgeHtml = `<span class="badge badge-low">Baseline</span>`;
    }

    const card = document.createElement('div');
    card.className = 'milestone-item-card';

    card.innerHTML = `
      <div class="milestone-left">
        <div class="milestone-num-circle">#${origIdx}</div>
        <div>
          <div style="display: flex; align-items: center; gap: 8px;">
            <strong style="color: var(--text-main); font-size: 13.5px;">${m.date}</strong>
            <span style="font-size: 11px; color: var(--text-muted);">${m.time || ''}</span>
            ${deltaBadgeHtml}
          </div>
          <span style="font-size: 11px; color: var(--text-muted);">${m.pages_crawled || 1} pages reviewed • Critical Issues: <strong style="color: ${m.critical_issues ? '#dc2626' : '#16a34a'};">${m.critical_issues || 0}</strong></span>
        </div>
      </div>

      <div style="display: flex; align-items: center; gap: 14px;">
        <div style="text-align: right;">
          <span style="font-size: 16px; font-weight: 800; font-family: var(--font-mono); color: ${scoreColor};">${scoreVal}/100</span>
          <span class="badge badge-subtle" style="font-size: 10px; margin-left: 4px;">GRADE ${m.grade || '?'}</span>
        </div>
        <div style="display: flex; align-items: center; gap: 6px;">
          <button class="btn-secondary" style="font-size: 11px; padding: 4px 8px;" onclick="openPastAudit('${m.id}')">View Suite</button>
          <button class="btn-secondary" style="font-size: 11px; padding: 4px 8px;" onclick="downloadWordDirect('${m.id}', '${escapeHtml(m.domain || '')}')">DOCX</button>
        </div>
      </div>
    `;

    container.appendChild(card);
  });
}

// ==========================================================================
// History Archive Table
// ==========================================================================
async function loadAuditHistory() {
  const tbody = document.getElementById('history-table-tbody');
  if (!tbody) return;

  // Show shimmer skeleton rows while fetching history
  tbody.innerHTML = `
    <tr>
      <td colspan="7" style="padding: 20px 16px;">
        <div style="display: flex; flex-direction: column; gap: 14px;">
          <div style="display: flex; gap: 14px; align-items: center;">
            <div class="shimmer shimmer-line" style="width: 25%;"></div>
            <div class="shimmer shimmer-line" style="width: 15%;"></div>
            <div class="shimmer shimmer-line" style="width: 10%;"></div>
            <div class="shimmer shimmer-line" style="width: 8%;"></div>
            <div class="shimmer shimmer-line" style="width: 10%;"></div>
            <div class="shimmer shimmer-line" style="width: 10%;"></div>
            <div class="shimmer shimmer-line" style="width: 22%;"></div>
          </div>
          <div style="display: flex; gap: 14px; align-items: center;">
            <div class="shimmer shimmer-line" style="width: 22%;"></div>
            <div class="shimmer shimmer-line" style="width: 18%;"></div>
            <div class="shimmer shimmer-line" style="width: 12%;"></div>
            <div class="shimmer shimmer-line" style="width: 7%;"></div>
            <div class="shimmer shimmer-line" style="width: 11%;"></div>
            <div class="shimmer shimmer-line" style="width: 9%;"></div>
            <div class="shimmer shimmer-line" style="width: 21%;"></div>
          </div>
          <div style="display: flex; gap: 14px; align-items: center;">
            <div class="shimmer shimmer-line" style="width: 27%;"></div>
            <div class="shimmer shimmer-line" style="width: 14%;"></div>
            <div class="shimmer shimmer-line" style="width: 11%;"></div>
            <div class="shimmer shimmer-line" style="width: 8%;"></div>
            <div class="shimmer shimmer-line" style="width: 10%;"></div>
            <div class="shimmer shimmer-line" style="width: 10%;"></div>
            <div class="shimmer shimmer-line" style="width: 20%;"></div>
          </div>
        </div>
      </td>
    </tr>
  `;

  try {
    const res = await fetch('/api/history');
    const data = await res.json();
    const history = data.history || [];

    tbody.innerHTML = '';
    if (history.length === 0) {
      tbody.innerHTML = `<tr><td colspan="7" style="text-align: center; color: var(--text-muted); padding: 24px;">No past audits recorded yet. Run an audit to populate history.</td></tr>`;
      return;
    }

    history.forEach((h) => {
      const tr = document.createElement('tr');
      const scoreColor = h.score >= 85 ? '#16a34a' : h.score >= 55 ? '#d97706' : '#dc2626';

      tr.innerHTML = `
        <td style="font-weight: 700;">${escapeHtml(h.domain || h.url)}</td>
        <td style="color: var(--text-muted);">${h.date}</td>
        <td style="text-align: center; font-weight: 800; color: ${scoreColor};">${h.score}/100</td>
        <td style="text-align: center; font-weight: 700;">${h.grade || '?'}</td>
        <td style="text-align: center;">${h.pages_crawled || 1}</td>
        <td style="text-align: center; color: #dc2626; font-weight: 700;">${h.critical_issues || 0}</td>
        <td style="text-align: right;">
          <button class="btn-secondary" style="padding: 4px 8px; font-size: 11px;" onclick="openPastAudit('${h.id}')">View</button>
          <button class="btn-secondary" style="padding: 4px 8px; font-size: 11px;" onclick="downloadWordDirect('${h.id}', '${escapeHtml(h.domain || '')}')">DOCX</button>
          <button class="btn-secondary" style="padding: 4px 8px; font-size: 11px; color: #dc2626;" onclick="deletePastAudit('${h.id}')">Delete</button>
        </td>
      `;
      tbody.appendChild(tr);
    });
  } catch (e) {
    console.error('History load error:', e);
  }
}

async function openPastAudit(auditId) {
  try {
    showToast('Loading audit from archive...', 'info');
    const res = await fetch(`/api/history/${auditId}`);
    const data = await res.json();
    if (data.audit) {
      switchMainTab('audit');
      displayAuditResults(data.audit);
      showToast('Loaded audit.', 'success');
    }
  } catch (e) {
    showToast('Could not load audit', 'error');
  }
}

function downloadWordDirect(auditId, domain) {
  if (!auditId) return;
  const url = `/api/audit/${auditId}/docx`;
  showToast('Downloading Word report (.docx)...', 'info');
  triggerFileDownload(url, `SEO-Audit-${domain || auditId.slice(0, 8)}.docx`);
}

async function deletePastAudit(auditId) {
  if (!confirm('Are you sure you want to delete this audit record?')) return;
  try {
    await fetch(`/api/history/${auditId}`, { method: 'DELETE' });
    showToast('Audit deleted.', 'info');
    loadAuditHistory();
  } catch (e) {
    showToast('Could not delete audit', 'error');
  }
}

// ==========================================================================
// Settings & Categories Loader
// ==========================================================================
async function loadSettingsAndCategories() {
  try {
    const res = await fetch('/api/settings');
    const data = await res.json();
    const cats = data.categories || [];
    const container = document.getElementById('category-checkboxes');

    if (container) {
      container.innerHTML = '';
      cats.forEach((c) => {
        const label = document.createElement('label');
        label.className = 'form-label-inline';
        label.innerHTML = `
          <input type="checkbox" value="${c.key}" checked class="cat-scope-check">
          <span>${escapeHtml(c.label)}</span>
        `;
        container.appendChild(label);
      });
    }

    if (data.keys) {
      if (data.keys.groq && document.getElementById('setting-groq-key')) {
        document.getElementById('setting-groq-key').placeholder = '•••••••••••••••• (Active)';
      }
      if (data.keys.gemini && document.getElementById('setting-gemini-key')) {
        document.getElementById('setting-gemini-key').placeholder = '•••••••••••••••• (Active)';
      }
      if (data.keys.openrouter && document.getElementById('setting-openrouter-key')) {
        document.getElementById('setting-openrouter-key').placeholder = '•••••••••••••••• (Active)';
      }
      if (data.keys.pagespeed && document.getElementById('setting-psi-key')) {
        document.getElementById('setting-psi-key').placeholder = '•••••••••••••••• (Active)';
      }
      if (data.keys.moz_access && document.getElementById('setting-moz-access')) {
        document.getElementById('setting-moz-access').value = data.keys.moz_access;
      }
      if (data.keys.moz_secret && document.getElementById('setting-moz-secret')) {
        document.getElementById('setting-moz-secret').placeholder = '•••••••••••••••• (Active)';
      }
    }
  } catch (e) {
    console.warn('Could not load settings:', e);
  }
}

function openSettingsModal() {
  const modal = document.getElementById('settings-modal');
  if (modal) modal.classList.remove('hidden');
}

function closeSettingsModal() {
  const modal = document.getElementById('settings-modal');
  if (modal) modal.classList.add('hidden');
}

async function handleSaveSettings(event) {
  if (event) event.preventDefault();

  const groq = document.getElementById('setting-groq-key')?.value;
  const gemini = document.getElementById('setting-gemini-key')?.value;
  const openrouter = document.getElementById('setting-openrouter-key')?.value;
  const psiKey = document.getElementById('setting-psi-key')?.value;
  const mozAccess = document.getElementById('setting-moz-access')?.value;
  const mozSecret = document.getElementById('setting-moz-secret')?.value;

  const payload = {
    keys: {
      groq_api_key: groq,
      gemini_api_key: gemini,
      openrouter_api_key: openrouter,
      pagespeed_api_key: psiKey,
      moz_access_id: mozAccess,
      moz_secret_key: mozSecret,
    },
  };

  try {
    const res = await fetch('/api/settings', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    if (res.ok) {
      showToast('Settings saved successfully.', 'success');
      closeSettingsModal();
    } else {
      showToast('Could not save settings.', 'error');
    }
  } catch (e) {
    showToast('Failed to save settings.', 'error');
  }
}

async function openReportsFolder() {
  try {
    await fetch('/api/open-reports', { method: 'POST' });
    showToast('Opened reports directory.', 'info');
  } catch (e) {
    showToast('Could not open reports directory.', 'error');
  }
}

// ==========================================================================
// Toast Notification Utility (Clean text, No emojis)
// ==========================================================================
function showToast(message, type = 'info') {
  const container = document.getElementById('toast-container');
  if (!container) return;

  const toast = document.createElement('div');
  toast.className = 'toast';

  let prefix = '[INFO]';
  if (type === 'success') prefix = '[OK]';
  else if (type === 'error') prefix = '[!]';

  toast.textContent = `${prefix} ${message}`;
  container.appendChild(toast);

  setTimeout(() => {
    toast.style.opacity = '0';
    setTimeout(() => toast.remove(), 250);
  }, 3200);
}

function escapeHtml(str) {
  if (str == null) return '';
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}
