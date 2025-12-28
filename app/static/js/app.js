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
        statusText = `<div class="text-info mb-2"><strong>${data.status}</strong></div>`;
    }

    if (data.channels_found > 0) {
        let channelsText = `<div class="text-success mb-2"><strong>Total: ${data.channels_found} services</strong></div>`;

        // Show recently found channels
        if (data.recent_channels && data.recent_channels.length > 0) {
            channelsText += '<div class="mt-2"><small class="text-muted d-block mb-1">Recently found:</small>';
            channelsText += '<div style="max-height: 150px; overflow-y: auto;">';
            data.recent_channels.forEach(channel => {
                channelsText += `<div class="text-success mb-1" style="font-size: 0.9rem;"><strong>✓</strong> ${channel}</div>`;
            });
            channelsText += '</div></div>';
        }

        scanResults.innerHTML = statusText + channelsText;
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




// Global HLS instance
let hls = null;

async function startStream() {
    if (!currentChannel) {
        alert('Please select a channel first');
        return;
    }

    try {
        // Show buffering message
        const video = document.getElementById('videoPlayer');
        const bufferingMsg = document.createElement('div');
        bufferingMsg.id = 'bufferingMessage';
        bufferingMsg.style.cssText = 'position: absolute; top: 50%; left: 50%; transform: translate(-50%, -50%); background: rgba(0,0,0,0.8); color: white; padding: 20px; border-radius: 10px; font-size: 18px; z-index: 1000;';
        bufferingMsg.innerHTML = '<i class="bi bi-hourglass-split"></i> Building DVR buffer...<br><small>Please wait ~10 seconds</small>';
        video.parentElement.style.position = 'relative';
        video.parentElement.appendChild(bufferingMsg);

        const response = await fetch(`/api/stream/${currentChannel}`, {method: 'POST'});
        const data = await response.json();

        // Remove buffering message
        if (bufferingMsg.parentElement) {
            bufferingMsg.remove();
        }

        if (data.success) {
            isStreaming = true;
            updateUI();

            // Initialize HLS player with DVR mode
            const hlsUrl = `/hls/stream.m3u8?t=${Date.now()}`;

            if (Hls.isSupported()) {
                // Clean up existing HLS instance
                if (hls) {
                    hls.destroy();
                }

                hls = new Hls({
                    debug: false,
                    enableWorker: true,
                    // DVR Mode Configuration
                    liveSyncDuration: 0,  // Start from beginning, not live edge
                    liveMaxLatencyDuration: Infinity,  // Allow unlimited buffering
                    maxBufferLength: 60,  // Buffer up to 60 seconds
                    maxMaxBufferLength: 120,  // Max buffer 2 minutes
                    backBufferLength: 90,  // Keep 90 seconds of back buffer for rewinding
                    liveDurationInfinity: true  // Treat as DVR-capable live stream
                });

                hls.loadSource(hlsUrl);
                hls.attachMedia(video);

                hls.on(Hls.Events.MANIFEST_PARSED, function() {
                    console.log('HLS manifest loaded (DVR mode), starting from beginning');
                    // Start playback from the beginning of the buffer
                    video.currentTime = 0;
                    video.play().catch(err => {
                        console.error('Autoplay failed:', err);
                        // Show click-to-play message if autoplay blocked
                        alert('Click the video to start playback');
                    });
                });

                hls.on(Hls.Events.ERROR, function(event, data) {
                    console.error('HLS error:', data);
                    if (data.fatal) {
                        switch (data.type) {
                            case Hls.ErrorTypes.NETWORK_ERROR:
                                console.error('Fatal network error, trying to recover');
                                hls.startLoad();
                                break;
                            case Hls.ErrorTypes.MEDIA_ERROR:
                                console.error('Fatal media error, trying to recover');
                                hls.recoverMediaError();
                                break;
                            default:
                                console.error('Fatal error, cannot recover');
                                hls.destroy();
                                break;
                        }
                    }
                });
            } else if (video.canPlayType('application/vnd.apple.mpegurl')) {
                // Native HLS support (Safari)
                video.src = hlsUrl;
                video.addEventListener('loadedmetadata', function() {
                    video.currentTime = 0;  // Start from beginning
                    video.play();
                });
            } else {
                alert('HLS is not supported in your browser');
            }
        } else {
            alert('Failed to start stream');
        }
    } catch (error) {
        console.error('Stream start error:', error);
        // Remove buffering message on error
        const bufferingMsg = document.getElementById('bufferingMessage');
        if (bufferingMsg) bufferingMsg.remove();
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

            // Clean up HLS instance
            if (hls) {
                hls.destroy();
                hls = null;
            }

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
