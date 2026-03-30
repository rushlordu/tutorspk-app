const boot = window.ROOM_BOOTSTRAP;
const socket = io();
let localStream = null;
let micEnabled = true;
let camEnabled = true;
let mediaRecorder = null;
let recordedChunks = [];
let drawing = false;
let lastPoint = null;

const peers = new Map();
const remoteVideos = document.getElementById('remoteVideos');
const localVideo = document.getElementById('localVideo');
const chatInput = document.getElementById('chatInput');
const chatMessages = document.getElementById('chatMessages');
const timerEl = document.getElementById('sessionTimer');
const board = document.getElementById('whiteboard');
const ctx = board.getContext('2d');
const observerBanner = document.getElementById('observerBanner');
ctx.lineWidth = 2;
ctx.lineCap = 'round';
ctx.strokeStyle = '#0f172a';

const rtcConfig = {
  iceServers: [{ urls: 'stun:stun.l.google.com:19302' }]
};

function appendChat(sender, message, createdAt='now', kind='chat') {
  const div = document.createElement('div');
  div.className = `chat-msg ${kind}`;
  const strong = document.createElement('strong');
  strong.textContent = `${sender}: `;
  const span = document.createElement('span');
  span.textContent = message;
  const small = document.createElement('small');
  small.textContent = ` ${createdAt}`;
  div.appendChild(strong);
  div.appendChild(span);
  div.appendChild(small);
  chatMessages.appendChild(div);
  chatMessages.scrollTop = chatMessages.scrollHeight;
}

function updateObserverBanner(data) {
  if (!observerBanner) return;
  if (!data || !data.count) {
    observerBanner.textContent = '';
    observerBanner.classList.add('hidden');
    return;
  }
  observerBanner.textContent = `Admin observer active: ${data.names.join(', ')}`;
  observerBanner.classList.remove('hidden');
}

function syncTimer() {
  if (!boot.startedAt) return;
  const start = new Date(boot.startedAt).getTime();
  setInterval(() => {
    const diff = Math.max(0, Date.now() - start);
    const total = Math.floor(diff / 1000);
    const h = String(Math.floor(total / 3600)).padStart(2, '0');
    const m = String(Math.floor((total % 3600) / 60)).padStart(2, '0');
    const s = String(total % 60).padStart(2, '0');
    timerEl.textContent = `${h}:${m}:${s}`;
  }, 1000);
}

async function startMedia() {
  if (boot.isObserver) return null;
  if (localStream) return localStream;
  localStream = await navigator.mediaDevices.getUserMedia({ video: true, audio: true });
  localVideo.srcObject = localStream;
  for (const peer of peers.values()) {
    if (!peer.hasTracks) {
      localStream.getTracks().forEach(track => peer.pc.addTrack(track, localStream));
      peer.hasTracks = true;
    }
  }
  return localStream;
}

function createRemoteTile(userId, label) {
  let tile = document.getElementById(`remote-tile-${userId}`);
  if (!tile) {
    tile = document.createElement('div');
    tile.className = 'video-tile';
    tile.id = `remote-tile-${userId}`;
    const small = document.createElement('small');
    small.id = `remote-label-${userId}`;
    const video = document.createElement('video');
    video.id = `remote-video-${userId}`;
    video.autoplay = true;
    video.playsInline = true;
    tile.appendChild(small);
    tile.appendChild(video);
    remoteVideos.appendChild(tile);
  }
  document.getElementById(`remote-label-${userId}`).textContent = label || `Participant ${userId}`;
  return document.getElementById(`remote-video-${userId}`);
}

function removePeer(userId) {
  const peer = peers.get(userId);
  if (peer) {
    peer.pc.close();
    peers.delete(userId);
  }
  document.getElementById(`remote-tile-${userId}`)?.remove();
}

function createPeer(remoteUser) {
  const key = String(remoteUser.user_id || remoteUser.id || remoteUser);
  if (peers.has(key)) return peers.get(key);

  const pc = new RTCPeerConnection(rtcConfig);
  const stream = new MediaStream();
  const peer = { pc, stream, user: remoteUser, hasTracks: false };
  peers.set(key, peer);

  if (boot.isObserver) {
    pc.addTransceiver('video', { direction: 'recvonly' });
    pc.addTransceiver('audio', { direction: 'recvonly' });
  } else if (localStream) {
    localStream.getTracks().forEach(track => pc.addTrack(track, localStream));
    peer.hasTracks = true;
  }

  pc.ontrack = event => {
    event.streams[0].getTracks().forEach(track => {
      if (!peer.stream.getTracks().find(existing => existing.id === track.id)) {
        peer.stream.addTrack(track);
      }
    });
    const labelParts = [remoteUser.name || `Participant ${key}`];
    if (remoteUser.role === 'admin') labelParts.push('(observer)');
    const video = createRemoteTile(key, labelParts.join(' '));
    video.srcObject = peer.stream;
  };

  pc.onicecandidate = event => {
    if (event.candidate) {
      socket.emit('signal', {
        room_code: boot.roomCode,
        target_user_id: key,
        payload: { type: 'ice', candidate: event.candidate }
      });
    }
  };

  return peer;
}

async function initiateConnection(remoteUser) {
  const peer = createPeer(remoteUser);
  if (!boot.isObserver && !localStream) {
    await startMedia();
  }
  const offer = await peer.pc.createOffer();
  await peer.pc.setLocalDescription(offer);
  socket.emit('signal', {
    room_code: boot.roomCode,
    target_user_id: remoteUser.user_id,
    payload: { type: 'offer', sdp: offer }
  });
}

socket.on('connect', async () => {
  if (!boot.isObserver) {
    try { await startMedia(); } catch (err) { console.error(err); }
  }
  socket.emit('join_room', { room_code: boot.roomCode });
  syncTimer();
});

socket.on('room_state', async data => {
  redrawBoard(data.whiteboard || []);
  updateObserverBanner(data.observers || { count: 0, names: [] });
  if (data.users && data.users.length) {
    for (const user of data.users) {
      try { await initiateConnection(user); } catch (err) { console.error(err); }
    }
  }
});

socket.on('user_joined', data => {
  if (data.role === 'admin') {
    updateObserverBanner({ count: 1, names: [data.name] });
  }
});

socket.on('user_left', data => removePeer(String(data.user_id)));
socket.on('observer_state', data => updateObserverBanner(data));

socket.on('signal', async ({ sender, sender_name, sender_role, payload }) => {
  const remoteUser = { user_id: String(sender), name: sender_name, role: sender_role };
  const peer = createPeer(remoteUser);
  try {
    if (payload.type === 'offer') {
      if (!boot.isObserver && !localStream) {
        await startMedia();
      }
      await peer.pc.setRemoteDescription(new RTCSessionDescription(payload.sdp));
      const answer = await peer.pc.createAnswer();
      await peer.pc.setLocalDescription(answer);
      socket.emit('signal', {
        room_code: boot.roomCode,
        target_user_id: sender,
        payload: { type: 'answer', sdp: answer }
      });
    } else if (payload.type === 'answer') {
      await peer.pc.setRemoteDescription(new RTCSessionDescription(payload.sdp));
    } else if (payload.type === 'ice' && payload.candidate) {
      await peer.pc.addIceCandidate(new RTCIceCandidate(payload.candidate));
    }
  } catch (err) {
    console.error('Signal error', err);
  }
});

socket.on('chat_message', data => appendChat(data.sender, data.message, data.created_at, data.kind || 'chat'));
socket.on('system_message', data => appendChat('System', data.message, data.created_at, 'system'));
socket.on('policy_warning', data => alert(data.message));
socket.on('whiteboard_draw', event => drawLine(event.start, event.end, false));
socket.on('whiteboard_clear', clearBoard);

document.getElementById('sendChatBtn')?.addEventListener('click', () => {
  const message = chatInput.value.trim();
  if (!message) return;
  socket.emit('chat_message', { room_code: boot.roomCode, message });
  chatInput.value = '';
});

chatInput?.addEventListener('keydown', e => {
  if (e.key === 'Enter') {
    e.preventDefault();
    document.getElementById('sendChatBtn').click();
  }
});

document.getElementById('startMediaBtn')?.addEventListener('click', async () => {
  try { await startMedia(); } catch (err) { alert('Camera/mic permission denied or unavailable.'); }
});

document.getElementById('toggleMicBtn')?.addEventListener('click', () => {
  if (!localStream) return;
  micEnabled = !micEnabled;
  localStream.getAudioTracks().forEach(track => track.enabled = micEnabled);
});

document.getElementById('toggleCamBtn')?.addEventListener('click', () => {
  if (!localStream) return;
  camEnabled = !camEnabled;
  localStream.getVideoTracks().forEach(track => track.enabled = camEnabled);
});

document.getElementById('shareScreenBtn')?.addEventListener('click', async () => {
  try {
    const screen = await navigator.mediaDevices.getDisplayMedia({ video: true, audio: true });
    const screenTrack = screen.getVideoTracks()[0];
    localVideo.srcObject = screen;
    for (const peer of peers.values()) {
      const sender = peer.pc.getSenders().find(s => s.track && s.track.kind === 'video');
      if (sender) sender.replaceTrack(screenTrack);
    }
    screenTrack.onended = async () => {
      if (!localStream) return;
      localVideo.srcObject = localStream;
      const camTrack = localStream.getVideoTracks()[0];
      for (const peer of peers.values()) {
        const sender = peer.pc.getSenders().find(s => s.track && s.track.kind === 'video');
        if (sender && camTrack) sender.replaceTrack(camTrack);
      }
    };
  } catch (err) {
    console.error('Screen share cancelled', err);
  }
});

document.getElementById('recordBtn')?.addEventListener('click', async (e) => {
  if (!mediaRecorder) {
    if (!boot.isObserver) await startMedia();
    const combined = new MediaStream();
    if (localStream) localStream.getTracks().forEach(track => combined.addTrack(track));
    for (const peer of peers.values()) {
      peer.stream.getTracks().forEach(track => combined.addTrack(track));
    }
    if (combined.getTracks().length === 0) {
      alert('No media available to record yet.');
      return;
    }
    mediaRecorder = new MediaRecorder(combined, { mimeType: 'video/webm' });
    recordedChunks = [];
    mediaRecorder.ondataavailable = evt => evt.data.size && recordedChunks.push(evt.data);
    mediaRecorder.onstop = () => {
      const blob = new Blob(recordedChunks, { type: 'video/webm' });
      const file = new File([blob], `session-${boot.roomCode}.webm`, { type: 'video/webm' });
      const dt = new DataTransfer();
      dt.items.add(file);
      document.getElementById('recordingInput').files = dt.files;
      mediaRecorder = null;
      e.target.textContent = 'Start recording';
      alert('Recording ready. Click upload recording to save playback.');
    };
    mediaRecorder.start();
    e.target.textContent = 'Stop recording';
  } else {
    mediaRecorder.stop();
  }
});

function getPoint(event) {
  const rect = board.getBoundingClientRect();
  const source = event.touches ? event.touches[0] : event;
  return {
    x: ((source.clientX - rect.left) / rect.width) * board.width,
    y: ((source.clientY - rect.top) / rect.height) * board.height,
  };
}

function drawLine(start, end, emitEvent=true) {
  ctx.beginPath();
  ctx.moveTo(start.x, start.y);
  ctx.lineTo(end.x, end.y);
  ctx.stroke();
  if (emitEvent) socket.emit('whiteboard_draw', { room_code: boot.roomCode, event: { start, end } });
}

function clearBoard() {
  ctx.clearRect(0, 0, board.width, board.height);
}

function redrawBoard(events) {
  clearBoard();
  events.forEach(evt => drawLine(evt.start, evt.end, false));
}

['mousedown', 'touchstart'].forEach(type => board.addEventListener(type, event => {
  drawing = true;
  lastPoint = getPoint(event);
}));
['mousemove', 'touchmove'].forEach(type => board.addEventListener(type, event => {
  if (!drawing) return;
  event.preventDefault();
  const point = getPoint(event);
  drawLine(lastPoint, point, true);
  lastPoint = point;
}, { passive: false }));
['mouseup', 'mouseleave', 'touchend'].forEach(type => board.addEventListener(type, () => {
  drawing = false;
  lastPoint = null;
}));

document.getElementById('clearBoardBtn')?.addEventListener('click', () => {
  clearBoard();
  socket.emit('whiteboard_clear', { room_code: boot.roomCode });
});
