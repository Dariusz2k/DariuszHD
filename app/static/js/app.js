// HomeRun Clone JavaScript
let socket;
let currentChannel = null;
let isStreaming = false;
let scanModal;
let scanStatusState = null;

// Initialize WebSocket connection
function initSocket() {
    socket = io();
    
    socket.on('connect', function() {
        updateStatus('Connected', 'status-connected');
    });
    
    socket.on('disconnect', function() {
        updateStatus('Disconnected', 'status-disconnected');
    });
    
    socket.on('status', function(data) {
        updateChannelInfo(data);
    });
    
    socket.on('scan_progress', function(data) {
        updateScanProgress(data);
        updateScanStatus({
            status: 'running',
            frequency_khz: data.frequency_khz,
            progress: data.progress,
            channels_found: data.channels_found
        });
    });
    
    socket.on('scan_complete', function(data) {
        completeScan(data);
        if (data.canceled) {
            updateScanStatus({status: 'canceled'});
        } else {
            updateScanStatus({status: 'complete'});
        }
    });
}

function updateStatus(status, className) {
    const statusEl = document.getElementById('status');
    statusEl.textContent = status;
    statusEl.className = `badge bg-secondary me-2 ${className}`;
}

function updateChannelInfo(data) {
    currentChannel = data.current_channel;
    isStreaming = data.is_streaming;
    
    document.getElementById('currentChannel').textContent = 
        currentChannel ? `Channel ${currentChannel}` : 'No Channel Selected';
    
    if (data.signal_strength !== undefined) {
        document.getElementById('signalStrength').textContent = data.signal_strength;
        document.getElementById('signalQuality').textContent = data.signal_quality;
    }
    
    // Update scan status if available
    if (data.scan_status) {
        updateScanStatus(data.scan_status);
    }
    
    // Update channel list if new channels are available
    if (data.channels) {
        updateChannelList(data.channels);
    }
    
    updateUI();
}

function updateScanStatus(status) {
    scanStatusState = status;
    const scanStatusEl = document.getElementById('scanStatus');
    const statusMap = {
        'idle': 'Idle',
        'running': 'Scanning...',
        'complete': 'Complete',
        'error': 'Error',
        'canceled': 'Canceled'
    };
    
    if (status.status === 'running') {
        const freqText = status.frequency_khz ? `${status.frequency_khz} kHz` : 'frequencies';
        const progressText = status.progress !== undefined ? `${status.progress}%` : '--%';
        scanStatusEl.textContent = `Scanning ${freqText} (${progressText})`;
    } else {
        scanStatusEl.textContent = statusMap[status.status] || status.status;
    }
    
    if (status.status === 'running') {
        scanStatusEl.className = 'text-primary fw-bold';
    } else if (status.status === 'complete') {
        scanStatusEl.className = 'text-success';
    } else if (status.status === 'canceled') {
        scanStatusEl.className = 'text-warning';
    } else if (status.status === 'error') {
        scanStatusEl.className = 'text-danger';
    } else {
        scanStatusEl.className = 'text-muted';
    }

    updateScanFloatingButton(status);
}

function updateChannelList(channels) {
    const channelList = document.getElementById('channelList');
    const channelCount = document.getElementById('channelCount');
    
    channelList.innerHTML = '';
    channelCount.textContent = Object.keys(channels).length;
    
    // Sort channels by channel number
    const sortedChannels = Object.entries(channels).sort((a, b) => {
        return parseFloat(a[0]) - parseFloat(b[0]);
    });
    
    sortedChannels.forEach(([channelId, channelInfo]) => {
        const button = document.createElement('button');
        button.className = 'list-group-item list-group-item-action d-flex justify-content-between align-items-center';
        button.onclick = () => tuneChannel(channelId);
        
        const signalStrength = channelInfo.signal_strength || 0;
        const badgeClass = signalStrength > 70 ? 'success' : signalStrength > 40 ? 'warning' : 'danger';
        
        button.innerHTML = `
            <div>
                <strong>${channelId}</strong><br>
                <small class="text-muted">${channelInfo.name}</small>
            </div>
            ${signalStrength > 0 ? `<span class="badge bg-${badgeClass}">${signalStrength}%</span>` : ''}
        `;
        
        channelList.appendChild(button);
    });
}

function updateUI() {
    const playBtn = document.getElementById('playBtn');
    const stopBtn = document.getElementById('stopBtn');
    
    playBtn.disabled = !currentChannel || isStreaming;
    stopBtn.disabled = !isStreaming;
    
    // Update active channel in list
    document.querySelectorAll('.list-group-item').forEach(item => {
        item.classList.remove('active');
    });
    
    if (currentChannel) {
        const activeItem = document.querySelector(`[onclick*="tuneChannel('${currentChannel}')"]`);
        if (activeItem) {
            activeItem.classList.add('active');
        }
    }
}

// Enhanced Scanner Functions
function showScanModal() {
    scanModal = new bootstrap.Modal(document.getElementById('scanModal'));
    scanModal.show();
    
    // Reset modal state
    document.getElementById('scanProgress').style.display = 'none';
    document.getElementById('startScanBtn').style.display = 'block';
    document.getElementById('finishScanBtn').style.display = 'none';
    document.getElementById('cancelScanBtn').style.display = 'none';
    const foundCount = document.getElementById('foundChannelsCount');
    if (foundCount) {
        foundCount.textContent = '0';
    }

    if (scanStatusState && scanStatusState.status === 'running') {
        document.getElementById('scanProgress').style.display = 'block';
        document.getElementById('startScanBtn').style.display = 'none';
        document.getElementById('cancelScanBtn').style.display = 'inline-block';
    }
}

function updateThresholdValue() {
    const threshold = document.getElementById('signalThreshold').value;
    document.getElementById('thresholdValue').textContent = threshold + '%';
}

function startFullScan() {
    const threshold = parseInt(document.getElementById('signalThreshold').value);
    const identifyNames = document.getElementById('identifyNames').checked;
    const selectedRegions = Array.from(document.querySelectorAll('input[name="scanRegions"]:checked'))
        .map((input) => input.value);

    // Show progress UI
    document.getElementById('scanProgress').style.display = 'block';
    document.getElementById('startScanBtn').style.display = 'none';
    document.getElementById('cancelScanBtn').style.display = 'inline-block';
    
    // Start scan
    const handleScanStart = (data) => {
        if (!data || data.conflict) {
            return;
        }
        if (!data.success) {
            alert('Failed to start scan: ' + (data.error || 'Unknown error'));
            scanModal.hide();
            return;
        }
        const scanResults = document.getElementById('scanResults');
        if (data.background) {
            scanResults.innerHTML = '<small class="text-muted">Scanning frequencies...</small>';
            const foundCount = document.getElementById('foundChannelsCount');
            if (foundCount) {
                foundCount.textContent = '0';
            }
        } else if (data.channels_found !== undefined) {
            updateScanProgress({progress: 100, channels_found: data.channels_found});
            completeScan({channels_found: data.channels_found, channels: data.channels || {}});
        }
    };

    fetch('/api/scan', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({
            signal_threshold: threshold,
            identify_names: identifyNames,
            background: true,
            regions: selectedRegions
        })
    })
    .then(response => response.json())
    .then(data => {
        if (data.conflict) {
            const proceed = confirm(data.message || 'Another scan is active and will be canceled. Continue?');
            if (!proceed) {
                scanModal.hide();
                return;
            }
            return fetch('/api/scan', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({
                    signal_threshold: threshold,
                    identify_names: identifyNames,
                    background: true,
                    force: true,
                    keep_channels: document.getElementById('keepChannelsOnCancel').checked,
                    regions: selectedRegions
                })
            }).then(response => response.json());
        }
        return data;
    })
    .then(handleScanStart)
    .catch(error => {
        console.error('Scan start error:', error);
        alert('Failed to start scan');
        scanModal.hide();
    });
}

function updateScanProgress(data) {
    const progressBar = document.getElementById('progressBar');
    const progressPercent = document.getElementById('progressPercent');
    const scanResults = document.getElementById('scanResults');
    const foundCount = document.getElementById('foundChannelsCount');
    
    const progressValue = data.progress !== undefined ? data.progress : 0;
    progressBar.style.width = progressValue + '%';
    progressPercent.textContent = progressValue + '%';
    if (foundCount) {
        foundCount.textContent = data.channels_found || 0;
    }
    
    const frequencyText = data.frequency_khz
        ? `<small class="text-muted">Scanning ${data.frequency_khz} kHz...</small>`
        : '<small class="text-muted">Scanning frequencies...</small>';

    scanResults.innerHTML = frequencyText;
    if (data.channels_found > 0) {
        scanResults.innerHTML += `<br><small class="text-success">Found ${data.channels_found} channels</small>`;
    }
}

function completeScan(data) {
    const scanResults = document.getElementById('scanResults');
    const finishBtn = document.getElementById('finishScanBtn');
    const foundCountEl = document.getElementById('foundChannelsCount');

    if (data.canceled) {
        scanResults.innerHTML = `<small class="text-warning">Scan canceled. Found ${data.channels_found || 0} channels</small>`;
        if (foundCountEl) {
            foundCountEl.textContent = data.channels_found || 0;
        }
    } else {
        const foundCount = data.channels_found !== undefined
            ? data.channels_found
            : (data.channels ? Object.keys(data.channels).length : 0);
        scanResults.innerHTML = `<small class="text-success">✓ Scan complete! Found ${foundCount} channels</small>`;
        if (foundCountEl) {
            foundCountEl.textContent = foundCount;
        }
    }
    finishBtn.style.display = 'block';
    document.getElementById('cancelScanBtn').style.display = 'none';
    updateScanFloatingButton({status: 'complete'});

    // Update the channel list with new channels
    if (data.channels) {
        updateChannelList(data.channels);
    }
}

function finishScan() {
    scanModal.hide();
    location.reload(); // Reload to refresh the channel list
}

function minimizeScan() {
    if (scanModal) {
        scanModal.hide();
    }
    updateScanFloatingButton({status: 'running'});
}

async function cancelScan() {
    const keepChannels = document.getElementById('keepChannelsOnCancel').checked;
    try {
        const response = await fetch('/api/scan/cancel', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({keep_channels: keepChannels})
        });
        const data = await response.json();
        if (!data.success) {
            alert(data.error || 'Failed to cancel scan');
            return;
        }
        const scanResults = document.getElementById('scanResults');
        const keepText = data.kept ? 'keeping channels found so far.' : 'discarding partial results.';
        scanResults.innerHTML = `<small class="text-warning">Scan canceled, ${keepText}</small>`;
        document.getElementById('cancelScanBtn').style.display = 'none';
        updateScanStatus({status: 'canceled'});
        updateScanFloatingButton({status: 'canceled'});
    } catch (error) {
        console.error('Cancel scan error:', error);
        alert('Failed to cancel scan');
    }
}

function updateScanFloatingButton(status) {
    const button = document.getElementById('scanFloatBtn');
    if (!button) {
        return;
    }
    if (status.status === 'running') {
        const progressText = status.progress !== undefined ? `${status.progress}%` : '--%';
        const freqText = status.frequency_khz ? `${status.frequency_khz} kHz` : 'frequencies';
        button.textContent = `Scanning ${freqText} (${progressText})`;
        button.style.display = 'inline-block';
    } else {
        button.style.display = 'none';
    }
}

async function quickScan() {
    try {
        const response = await fetch('/api/quick-scan', {method: 'POST'});
        const data = await response.json();
        
        if (data.success) {
            alert(`Quick scan complete! Found ${Object.keys(data.channels || {}).length} channels`);
            location.reload();
        } else {
            alert('Quick scan failed: ' + (data.error || 'No channels found'));
        }
    } catch (error) {
        console.error('Quick scan error:', error);
        alert('Quick scan failed');
    }
}

async function tuneChannel(channel) {
    try {
        const response = await fetch(`/api/tune/${channel}`);
        const data = await response.json();
        
        if (data.success) {
            currentChannel = channel;
            updateChannelInfo({current_channel: channel, is_streaming: false});
        } else {
            alert('Failed to tune channel');
        }
    } catch (error) {
        console.error('Tune error:', error);
    }
}

async function surfNext() {
  const r = await fetch('/api/surf/next', {method:'POST'});
  const d = await r.json();
  if (d.success) {
    currentChannel = d.channel;
    await startStream();
  } else {
    alert(d.error || 'Failed to surf next');
  }
}

async function surfPrev() {
  const r = await fetch('/api/surf/prev', {method:'POST'});
  const d = await r.json();
  if (d.success) {
    currentChannel = d.channel;
    await startStream();
  } else {
    alert(d.error || 'Failed to surf prev');
  }
}




async function startStream() {
    if (!currentChannel) {
        alert('Please select a channel first');
        return;
    }
    
    try {
        const response = await fetch(`/api/stream/${currentChannel}`);
        const data = await response.json();
        
        if (data.success) {
            isStreaming = true;
            updateUI();
            
            // Update video source
            const video = document.getElementById('videoPlayer');
            video.src = `/stream.ts?channel=${currentChannel}&t=${Date.now()}`;
            video.load();
            video.play();
        } else {
            alert('Failed to start stream');
        }
    } catch (error) {
        console.error('Stream start error:', error);
    }
}

async function stopStream() {
    try {
        const response = await fetch('/api/stop');
        const data = await response.json();
        
        if (data.success) {
            isStreaming = false;
            updateUI();
            
            const video = document.getElementById('videoPlayer');
            video.pause();
            video.src = '';
        }
    } catch (error) {
        console.error('Stream stop error:', error);
    }
}

// Update status periodically
setInterval(async () => {
    try {
        const response = await fetch('/api/status');
        if (!response.ok) {
            console.warn('Status endpoint returned', response.status);
            return;
        }
        const data = await response.json();
        updateChannelInfo(data);
    } catch (error) {
        console.error('Status update error:', error);
    }
}, 5000);

document.addEventListener('keydown', (e) => {
  if (e.key === 'ArrowRight') surfNext();
  if (e.key === 'ArrowLeft') surfPrev();
});


// Initialize on page load
document.addEventListener('DOMContentLoaded', function() {
    initSocket();
    updateUI();
    
    // Initialize signal threshold slider
    document.getElementById('signalThreshold').addEventListener('input', updateThresholdValue);
    updateThresholdValue();

    loadScanRegions();
});

async function loadScanRegions() {
    try {
        const response = await fetch('/api/scan/regions');
        if (!response.ok) {
            return;
        }
        const data = await response.json();
        const container = document.getElementById('scanRegionOptions');
        if (!container) {
            return;
        }
        if (!data.regions || data.regions.length === 0) {
            container.innerHTML = '<small class="text-muted">No regions available.</small>';
            return;
        }
        container.innerHTML = data.regions.map((region) => `
            <div class="form-check">
                <input class="form-check-input" type="checkbox" name="scanRegions" id="region-${region.id}" value="${region.id}">
                <label class="form-check-label" for="region-${region.id}">${region.name}</label>
            </div>
        `).join('');
    } catch (error) {
        console.error('Failed to load scan regions:', error);
    }
}
