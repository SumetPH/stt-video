// JavaScript logic for STT Video Pipeline UI

let currentJobId = null;
let logEventSource = null;
let filesCache = {};
let pipelineInputMode = 'url';

function switchInputMode(mode) {
    pipelineInputMode = mode;
    
    const btnUrl = document.getElementById('btn-input-url');
    const btnLocal = document.getElementById('btn-input-local');
    const secUrl = document.getElementById('section-url-input');
    const secLocal = document.getElementById('section-local-input');
    const secDlSettings = document.getElementById('section-download-settings');
    
    const inputUrl = document.getElementById('pipe-url');
    const selectLocal = document.getElementById('pipe-local-video');
    
    const groupQuality = document.getElementById('group-pipe-quality');
    const groupThreads = document.getElementById('group-pipe-threads');
    
    if (mode === 'url') {
        btnUrl.classList.add('active');
        btnLocal.classList.remove('active');
        secUrl.classList.remove('hidden');
        secLocal.classList.add('hidden');
        secDlSettings.classList.remove('hidden');
        
        if (groupQuality) groupQuality.classList.remove('hidden');
        if (groupThreads) groupThreads.classList.remove('hidden');
        
        inputUrl.setAttribute('required', 'required');
        if (selectLocal) selectLocal.removeAttribute('required');
    } else {
        btnUrl.classList.remove('active');
        btnLocal.classList.add('active');
        secUrl.classList.add('hidden');
        secLocal.classList.remove('hidden');
        secDlSettings.classList.add('hidden');
        
        if (groupQuality) groupQuality.classList.add('hidden');
        if (groupThreads) groupThreads.classList.add('hidden');
        
        inputUrl.removeAttribute('required');
        if (selectLocal) selectLocal.setAttribute('required', 'required');
    }
}
window.switchInputMode = switchInputMode;

document.addEventListener('DOMContentLoaded', () => {
    // Initialize Lucide Icons
    lucide.createIcons();

    // Tab Navigation
    initNavigation();

    // Load initial system status
    fetchSystemStatus();

    // Fetch files and populate dropdowns
    refreshFiles();

    // Attach form submit listeners
    initForms();

    // Refresh action
    document.getElementById('btn-refresh-status').addEventListener('click', () => {
        fetchSystemStatus();
        refreshFiles();
    });

    // Cancel job
    document.getElementById('btn-cancel-job').addEventListener('click', () => {
        if (currentJobId) {
            cancelJob(currentJobId);
        }
    });

    // Sub-tab navigation in File Manager
    const subTabBtns = document.querySelectorAll('.tab-sub-btn');
    subTabBtns.forEach(btn => {
        btn.addEventListener('click', () => {
            subTabBtns.forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
            renderFileTable(btn.dataset.subdir);
        });
    });
});

// Advanced settings toggle
function toggleAdvanced(panelId) {
    const panel = document.getElementById(panelId);
    const icon = document.getElementById(panelId + '-icon');
    if (panel.classList.contains('hidden')) {
        panel.classList.remove('hidden');
        icon.setAttribute('data-lucide', 'chevron-up');
    } else {
        panel.classList.add('hidden');
        icon.setAttribute('data-lucide', 'chevron-down');
    }
    lucide.createIcons();
}

// Setup Navigation tabs
function initNavigation() {
    const navItems = document.querySelectorAll('.nav-item');
    const tabContents = document.querySelectorAll('.tab-content');
    
    const tabInfo = {
        'pipeline': {
            title: 'Full Pipeline Run',
            desc: 'สั่งดาวน์โหลดวิดีโอและทำซับไตเติลภาษาไทยเสร็จสิ้นในขั้นตอนเดียว'
        },
        'steps': {
            title: 'Step by Step Execution',
            desc: 'รันคำสั่งแยกส่วนทีละขั้นตอนเพื่อตรวจสอบหรือแก้ไขผลลัพธ์ระหว่างทาง'
        },
        'files': {
            title: 'File Manager',
            desc: 'จัดการและดาวน์โหลดไฟล์วิดีโอ ไฟล์เสียง และไฟล์ซับไตเติลในเครื่อง'
        },
        'settings': {
            title: 'System Environment',
            desc: 'ข้อมูลการกำหนดค่าและตัวแปรเสริมจากไฟล์ .env ของระบบ'
        }
    };

    navItems.forEach(item => {
        item.addEventListener('click', () => {
            const tabName = item.dataset.tab;
            
            navItems.forEach(nav => nav.classList.remove('active'));
            tabContents.forEach(tab => tab.classList.remove('active'));
            
            item.classList.add('active');
            document.getElementById(`tab-${tabName}`).classList.add('active');
            
            // Update title
            document.getElementById('current-tab-title').textContent = tabInfo[tabName].title;
            document.getElementById('current-tab-desc').textContent = tabInfo[tabName].desc;

            // Specific tab initializations
            if (tabName === 'files') {
                refreshFiles();
            }
        });
    });
}

// Fetch general system status
async function fetchSystemStatus() {
    try {
        const res = await fetch('/api/status');
        const data = await res.json();
        
        // Update FFmpeg
        const ffmpegEl = document.getElementById('status-ffmpeg');
        if (data.ffmpeg) {
            ffmpegEl.className = 'status-badge online';
            ffmpegEl.textContent = 'Online';
        } else {
            ffmpegEl.className = 'status-badge offline';
            ffmpegEl.textContent = 'Missing';
        }

        // Update Streamlink
        const streamlinkEl = document.getElementById('status-streamlink');
        if (data.streamlink) {
            streamlinkEl.className = 'status-badge online';
            streamlinkEl.textContent = 'Online';
        } else {
            streamlinkEl.className = 'status-badge offline';
            streamlinkEl.textContent = 'Missing';
        }

        // Update Whisper Device
        const whisperEl = document.getElementById('status-whisper');
        const env = data.environment;
        const device = env.WHISPER_DEVICE || 'cpu (auto)';
        const model = env.WHISPER_MODEL || 'large-v3';
        whisperEl.textContent = `${device} [${model}]`;

        // Populate Environment settings tab
        const settingsContainer = document.getElementById('settings-container');
        if (settingsContainer) {
            settingsContainer.innerHTML = '';
            for (const [key, val] of Object.entries(env)) {
                const settingDiv = document.createElement('div');
                settingDiv.className = 'setting-item';
                settingDiv.innerHTML = `
                    <div class="setting-key">${key}</div>
                    <div class="setting-val">${val || '<em>(ค่าว่าง)</em>'}</div>
                `;
                settingsContainer.appendChild(settingDiv);
            }
        }
    } catch (e) {
        console.error("Failed to fetch system status", e);
    }
}

// Refresh files in directories and populate lists
async function refreshFiles() {
    try {
        const res = await fetch('/api/files');
        const data = await res.json();
        filesCache = data;

        // Populate Step 2 dropdown (Videos to transcribe - can use downloaded videos or main videos)
        const step2Select = document.getElementById('step2-video');
        const step4SelectV = document.getElementById('step4-video');
        
        const allVideos = [...data.video_downloads, ...data.videos];
        
        updateDropdown(step2Select, allVideos, 'rel_path', 'name', '-- เลือกไฟล์วิดีโอ --');
        updateDropdown(step4SelectV, allVideos, 'rel_path', 'name', '-- เลือกไฟล์วิดีโอ --');
        updateDropdown(document.getElementById('pipe-local-video'), allVideos, 'rel_path', 'name', '-- เลือกไฟล์วิดีโอ --');

        // Populate Step 3 dropdown (SRT transcript files)
        const step3Select = document.getElementById('step3-srt');
        updateDropdown(step3Select, data.transcripts, 'rel_path', 'name', '-- เลือกไฟล์ SRT --');

        // Populate Step 4 Subtitle dropdown (srt translated files)
        const step4SelectS = document.getElementById('step4-srt');
        const allSrts = [...data.translations, ...data.transcripts]; // Allow raw srt too
        updateDropdown(step4SelectS, allSrts, 'rel_path', 'name', '-- เลือกไฟล์ซับไตเติล --');

        // Update current file table view if in File Manager tab
        const activeSubTab = document.querySelector('.tab-sub-btn.active');
        if (activeSubTab) {
            renderFileTable(activeSubTab.dataset.subdir);
        }
    } catch (e) {
        console.error("Failed to refresh files list", e);
    }
}

function updateDropdown(selectEl, items, valKey, labelKey, defaultLabel) {
    if (!selectEl) return;
    const currentVal = selectEl.value;
    selectEl.innerHTML = `<option value="">${defaultLabel}</option>`;
    
    items.forEach(item => {
        const opt = document.createElement('option');
        opt.value = item[valKey];
        opt.textContent = item[labelKey];
        selectEl.appendChild(opt);
    });
    
    // Restore value if still exists
    if (items.some(item => item[valKey] === currentVal)) {
        selectEl.value = currentVal;
    }
}

// Format bytes to human readable format
function formatBytes(bytes) {
    if (bytes === 0) return '0 Bytes';
    const k = 1024;
    const sizes = ['Bytes', 'KB', 'MB', 'GB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i];
}

// Format Unix Timestamp
function formatTime(unixTime) {
    const date = new Date(unixTime * 1000);
    return date.toLocaleString('th-TH', { hour12: false });
}

// Render File Manager Table
function renderFileTable(subdir) {
    const tableBody = document.getElementById('file-table-body');
    if (!tableBody) return;
    
    let files = [];
    if (subdir === 'download') {
        files = filesCache.video_downloads || [];
    } else if (subdir === 'raw_srt') {
        files = filesCache.transcripts || [];
    } else if (subdir === 'translated_srt') {
        files = filesCache.translations || [];
    } else if (subdir === 'output_video') {
        // Output videos are MKV or MP4s that are processed (exist in root video folder)
        files = filesCache.videos || [];
    }

    if (files.length === 0) {
        tableBody.innerHTML = `<tr><td colspan="4" class="text-center text-muted">ไม่พบไฟล์ในโฟลเดอร์นี้</td></tr>`;
        return;
    }

    tableBody.innerHTML = '';
    files.forEach(file => {
        const tr = document.createElement('tr');
        
        // Actions mapping depending on file type
        let actionButtons = '';
        if (subdir === 'download') {
            actionButtons = `<button class="btn btn-secondary btn-xs" onclick="sendToStep(2, '${file.rel_path}')">Transcribe</button>`;
        } else if (subdir === 'raw_srt') {
            actionButtons = `<button class="btn btn-secondary btn-xs" onclick="sendToStep(3, '${file.rel_path}')">Translate</button>`;
        } else if (subdir === 'translated_srt') {
            actionButtons = `<button class="btn btn-secondary btn-xs" onclick="sendToStep(4, '${file.rel_path}')">Embed/Mux</button>`;
        }

        // Always add download button
        actionButtons += `
            <a href="/api/download-file?path=${encodeURIComponent(file.rel_path)}" class="btn btn-primary btn-xs btn-icon" title="ดาวน์โหลดลงเครื่อง" download>
                <i data-lucide="download" style="width:14px;height:14px;"></i>
            </a>
        `;

        tr.innerHTML = `
            <td><strong>${file.name}</strong></td>
            <td>${formatBytes(file.size_bytes)}</td>
            <td>${formatTime(file.modified)}</td>
            <td class="text-right">
                <div class="flex items-center justify-end gap-2">
                    ${actionButtons}
                </div>
            </td>
        `;
        tableBody.appendChild(tr);
    });
    
    lucide.createIcons();
}

// Helper to push actions from File Manager to Steps tab
function sendToStep(stepNum, filepath) {
    // Go to steps tab
    const stepsNavBtn = document.querySelector('.nav-item[data-tab="steps"]');
    if (stepsNavBtn) stepsNavBtn.click();
    
    if (stepNum === 2) {
        const select = document.getElementById('step2-video');
        if (select) select.value = filepath;
    } else if (stepNum === 3) {
        const select = document.getElementById('step3-srt');
        if (select) select.value = filepath;
    } else if (stepNum === 4) {
        const select = document.getElementById('step4-srt');
        if (select) select.value = filepath;
        // Auto fill video if possible (same filename stem)
        const videoSelect = document.getElementById('step4-video');
        if (videoSelect) {
            const baseName = filepath.split('/').pop().split('.')[0]; // e.g. 13469305
            const option = Array.from(videoSelect.options).find(opt => opt.value.includes(baseName));
            if (option) videoSelect.value = option.value;
        }
    }
}

// Initialize forms submit handlers
function initForms() {
    // 1. Full Pipeline Form
    document.getElementById('form-full-pipeline').addEventListener('submit', async (e) => {
        e.preventDefault();
        
        let body = {};
        if (pipelineInputMode === 'url') {
            body = {
                url: document.getElementById('pipe-url').value,
                duration: document.getElementById('pipe-duration').value || null,
                start_offset: document.getElementById('pipe-offset').value,
                quality: document.getElementById('pipe-quality').value,
                threads: parseInt(document.getElementById('pipe-threads').value),
                output_name: document.getElementById('pipe-output-name').value || null,
                font_name: document.getElementById('pipe-font').value || null,
                source_lang: document.getElementById('pipe-source-lang').value || "ko",
                timing_mode: document.getElementById('pipe-timing-mode').value || "auto",
                video_path: null
            };
        } else {
            body = {
                url: null,
                video_path: document.getElementById('pipe-local-video').value,
                font_name: document.getElementById('pipe-font').value || null,
                source_lang: document.getElementById('pipe-source-lang-local').value || "ko",
                timing_mode: document.getElementById('pipe-timing-mode').value || "auto"
            };
        }
        
        await startJob('/api/pipeline', body);
    });

    // 2. Step 1 Download Form
    document.getElementById('form-step-download').addEventListener('submit', async (e) => {
        e.preventDefault();
        const body = {
            url: document.getElementById('step1-url').value,
            quality: document.getElementById('step1-quality').value || "best",
            start_offset: document.getElementById('step1-offset').value || "00:00:00",
            duration: document.getElementById('step1-duration').value || null,
            output_name: document.getElementById('step1-output').value || null
        };
        await startJob('/api/download', body);
    });

    // 3. Step 2 Transcribe Form
    document.getElementById('form-step-transcribe').addEventListener('submit', async (e) => {
        e.preventDefault();
        const body = {
            video_path: document.getElementById('step2-video').value,
            start_time: document.getElementById('step2-start').value || null,
            duration: document.getElementById('step2-duration').value || null,
            source_lang: document.getElementById('step2-source-lang').value || "ko",
            timing_mode: document.getElementById('step2-timing-mode').value || "auto"
        };
        await startJob('/api/transcribe', body);
    });

    // 4. Step 3 Translate Form
    document.getElementById('form-step-translate').addEventListener('submit', async (e) => {
        e.preventDefault();
        const body = {
            input_srt: document.getElementById('step3-srt').value,
            model: document.getElementById('step3-model').value || null,
            source_lang: document.getElementById('step3-source-lang').value || "ko"
        };
        await startJob('/api/translate', body);
    });

    // 5. Step 4 Integrate Form
    document.getElementById('form-step-integrate').addEventListener('submit', async (e) => {
        e.preventDefault();
        const body = {
            video_path: document.getElementById('step4-video').value,
            srt_path: document.getElementById('step4-srt').value,
            mode: document.getElementById('step4-mode').value,
            font_name: document.getElementById('step4-font').value || null
        };
        await startJob('/api/integrate', body);
    });
}

// Start a job backend API call
async function startJob(endpoint, body) {
    if (currentJobId) {
        alert("กรุณารอหรือยกเลิกงานปัจจุบันก่อนเริ่มงานใหม่");
        return;
    }
    
    // Clear console
    clearTerminal();
    
    // Show spinner & terminal container
    document.getElementById('job-spinner').classList.remove('hidden');
    document.getElementById('terminal-wrapper').classList.remove('hidden');
    document.getElementById('btn-cancel-job').classList.remove('hidden');

    try {
        const res = await fetch(endpoint, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body)
        });
        
        if (!res.ok) {
            const err = await res.json();
            throw new Error(err.detail || "API Error");
        }

        const data = await res.json();
        currentJobId = data.job_id;
        
        // Show job detail in panel
        updateJobPanel(data.label, "Starting...");
        
        // Listen to logs stream
        connectLogStream(currentJobId);
    } catch (e) {
        appendLog(`[ERROR] Failed to start job: ${e.message}`, 'error');
        document.getElementById('job-spinner').classList.add('hidden');
        document.getElementById('btn-cancel-job').classList.add('hidden');
        updateJobPanel("Failed Task", "Error", true);
    }
}

// Update Job monitoring panel layout
function updateJobPanel(label, statusText, isError = false) {
    const infoArea = document.getElementById('job-info-area');
    
    let statusClass = "status-running-text";
    let progressFillWidth = "50%";
    let animClass = "indeterminate";
    
    if (statusText.toLowerCase() === 'completed') {
        statusClass = "status-completed-text";
        progressFillWidth = "100%";
        animClass = "";
    } else if (statusText.toLowerCase() === 'failed' || isError) {
        statusClass = "status-failed-text";
        progressFillWidth = "100%";
        animClass = "";
    } else if (statusText.toLowerCase() === 'cancelled') {
        statusClass = "status-failed-text";
        progressFillWidth = "0%";
        animClass = "";
    }

    infoArea.innerHTML = `
        <div class="job-detail">
            <span><strong>งาน:</strong> ${label}</span>
            <span class="job-status-text ${statusClass}">${statusText}</span>
        </div>
        <div class="progress-bar-bg">
            <div class="progress-bar-fill ${animClass}" style="width: ${progressFillWidth}"></div>
        </div>
    `;
}

// Connect to SSE Log stream
function connectLogStream(jobId) {
    if (logEventSource) {
        logEventSource.close();
    }
    
    logEventSource = new EventSource(`/api/jobs/${jobId}/logs`);
    
    logEventSource.onmessage = (event) => {
        const logLine = event.data;
        
        if (logLine === '[SYSTEM] EOF') {
            // Completed
            logEventSource.close();
            currentJobId = null;
            document.getElementById('job-spinner').classList.add('hidden');
            document.getElementById('btn-cancel-job').classList.add('hidden');
            
            // Check latest status of jobs
            checkJobStatus(jobId);
            return;
        }
        
        // Re-construct newline and carriage return
        const lineText = logLine.replace(/\\n/g, '\n').replace(/\\r/g, '');
        const isCarriageReturn = logLine.includes('\\r');
        
        // Styling logs
        let type = 'output';
        if (lineText.startsWith('[SYSTEM]')) type = 'system';
        else if (lineText.startsWith('[WARNING]')) type = 'warning';
        else if (lineText.includes('[ERROR]') || lineText.startsWith('Error')) type = 'error';
        
        appendLog(lineText, type, isCarriageReturn);
    };

    logEventSource.onerror = (err) => {
        console.error("SSE connection error", err);
        appendLog("[SYSTEM] Lost connection to log stream. Trying to reconnect...\n", 'warning');
        
        // After timeout if job is done, close it
        setTimeout(() => {
            if (currentJobId === jobId) {
                checkJobStatus(jobId);
            }
        }, 5000);
    };
}

// Fetch job status to finalize UI
async function checkJobStatus(jobId) {
    try {
        const res = await fetch('/api/jobs');
        const data = await res.json();
        const job = data[jobId];
        
        if (job) {
            updateJobPanel(job.label, job.status);
            if (job.status === 'completed') {
                appendLog("[SYSTEM] Job finished successfully.\n", 'system');
            } else {
                appendLog(`[SYSTEM] Job ended with status: ${job.status}\n`, 'error');
            }
        }
        
        currentJobId = null;
        document.getElementById('job-spinner').classList.add('hidden');
        document.getElementById('btn-cancel-job').classList.add('hidden');
        
        // Refresh explorer
        refreshFiles();
    } catch (e) {
        console.error("Failed to check final job status", e);
    }
}

// Cancel current running task
async function cancelJob(jobId) {
    if (!confirm("คุณต้องการยกเลิกคำสั่งที่กำลังทำงานอยู่ใช่หรือไม่?")) return;
    
    try {
        appendLog("[SYSTEM] Cancelling process...\n", 'warning');
        
        // Update UI immediately to stop progress bar and spinner
        updateJobPanel("Cancelling Task...", "cancelled");
        document.getElementById('job-spinner').classList.add('hidden');
        document.getElementById('btn-cancel-job').classList.add('hidden');
        
        if (logEventSource) {
            logEventSource.close();
            logEventSource = null;
        }
        currentJobId = null;
        
        const res = await fetch(`/api/jobs/${jobId}/cancel`, { method: 'POST' });
        if (res.ok) {
            appendLog("[SYSTEM] Cancellation request sent.\n", 'system');
        }
        
        setTimeout(refreshFiles, 1000);
    } catch (e) {
        console.error("Failed to cancel job", e);
    }
}

// Append line to terminal console
function appendLog(text, type = 'output', isCarriageReturn = false) {
    const consoleEl = document.getElementById('terminal-console');
    if (!consoleEl) return;
    
    if (isCarriageReturn && consoleEl.lastElementChild && consoleEl.lastElementChild.classList.contains('log-cr')) {
        consoleEl.lastElementChild.textContent = text;
        return;
    }
    
    const line = document.createElement('div');
    line.className = `terminal-line ${type}`;
    if (isCarriageReturn) line.classList.add('log-cr');
    line.textContent = text;
    
    consoleEl.appendChild(line);
    
    // Auto scroll to bottom
    consoleEl.scrollTop = consoleEl.scrollHeight;
}

// Clear terminal output
function clearTerminal() {
    const consoleEl = document.getElementById('terminal-console');
    if (consoleEl) consoleEl.innerHTML = '';
}
