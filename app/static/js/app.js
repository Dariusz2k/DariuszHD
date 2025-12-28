// HomeRun Clone JavaScript
let socket;
let currentChannel = null;
let isStreaming = false;
let scanModal;

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
    });
    
    socket.on('scan_complete', function(data) {
        completeScan(data);
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
    const scanStatusEl = document.getElementById('scanStatus');
    const statusMap = {
        'idle': 'Idle',
        'running': 'Scanning...',
        'complete': 'Complete',
        'error': 'Error'
    };
    
    scanStatusEl.textContent = statusMap[status.status] || status.status;
    
    if (status.status === 'running') {
        scanStatusEl.className = 'text-primary fw-bold';
    } else if (status.status === 'complete') {
        scanStatusEl.className = 'text-success';
    } else if (status.status === 'error') {
        scanStatusEl.className = 'text-danger';
    } else {
        scanStatusEl.className = 'text-muted';
    }
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
    scanModal = new bootstrap.Modal(document.getElementById('scanModal'), {
        backdrop: 'static',  // Prevent closing when clicking outside
        keyboard: false      // Prevent closing with ESC key
    });
    scanModal.show();

    // Reset modal state
    document.getElementById('scanProgress').style.display = 'none';
    document.getElementById('startScanBtn').style.display = 'block';
    document.getElementById('finishScanBtn').style.display = 'none';
}

function updateThresholdValue() {
    const threshold = document.getElementById('signalThreshold').value;
    document.getElementById('thresholdValue').textContent = threshold + '%';
}

function startFullScan() {
    const threshold = parseInt(document.getElementById('signalThreshold').value);
    const identifyNames = document.getElementById('identifyNames').checked;

    // Show progress UI
    document.getElementById('scanProgress').style.display = 'block';
    document.getElementById('startScanBtn').style.display = 'none';
    document.getElementById('cancelScanBtn').style.display = 'block';

    // Start scan
    fetch('/api/scan', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({
            signal_threshold: threshold,
            identify_names: identifyNames,
            background: true
        })
    })
    .then(response => response.json())
    .then(data => {
        if (!data.success) {
            if (data.can_cancel) {
                // Scan already in progress, show cancel button
                document.getElementById('cancelScanBtn').style.display = 'block';
            } else {
                alert('Failed to start scan: ' + (data.error || 'Unknown error'));
                scanModal.hide();
            }
        }
    })
    .catch(error => {
        console.error('Scan start error:', error);
        alert('Failed to start scan');
        scanModal.hide();
    });
}

function cancelScan() {
    if (!confirm('Are you sure you want to cancel the scan?')) {
        return;
    }

    fetch('/api/scan/cancel', {method: 'POST'})
    .then(response => response.json())
    .then(data => {
        if (data.success) {
            document.getElementById('scanResults').innerHTML = '<small class="text-warning">Cancelling scan...</small>';
        }
    })
    .catch(error => {
        console.error('Cancel error:', error);
    });
}

function updateScanProgress(data) {
    const progressBar = document.getElementById('progressBar');
    const progressPercent = document.getElementById('progressPercent');
    const scanResults = document.getElementById('scanResults');

    progressBar.style.width = data.progress + '%';
    progressPercent.textContent = data.progress + '%';

    let statusText = '';
    if (data.status) {
        statusText = `<div class="text-info mb-1"><strong>${data.status}</strong></div>`;
    }

    if (data.channels_found > 0) {
        scanResults.innerHTML = statusText + `<small class="text-success">Found ${data.channels_found} services</small>`;
    } else {
        scanResults.innerHTML = statusText + '<small class="text-muted">Scanning frequencies...</small>';
    }
}

function completeScan(data) {
    const scanResults = document.getElementById('scanResults');
    const finishBtn = document.getElementById('finishScanBtn');
    const cancelBtn = document.getElementById('cancelScanBtn');

    cancelBtn.style.display = 'none';

    if (data.success) {
        scanResults.innerHTML = `<small class="text-success">✓ Scan complete! Found ${data.channels_found} channels</small>`;
        finishBtn.style.display = 'block';

        // Update the channel list with new channels
        updateChannelList(data.channels);
    } else {
        scanResults.innerHTML = `<small class="text-danger">✗ ${data.error || 'Scan failed'}</small>`;
        finishBtn.style.display = 'block';
    }
}

function finishScan() {
    scanModal.hide();
    location.reload(); // Reload to refresh the channel list
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
        const response = await fetch(`/api/tune/${channel}`, {method: 'POST'});
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
        const response = await fetch(`/api/stream/${currentChannel}`, {method: 'POST'});
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
        alert('Failed to start stream: ' + error.message);
    }
}

async function stopStream() {
    try {
        const response = await fetch('/api/stop', {method: 'POST'});
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
});
