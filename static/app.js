const $ = (id) => document.getElementById(id);
const toast = (message) => { const el = $('toast'); el.textContent = message; el.classList.add('show'); setTimeout(() => el.classList.remove('show'), 3200); };
const api = async (path, options = {}) => {
  const headers = {...(options.headers || {})};
  if (options.method && options.method !== 'GET') headers['X-CSRF-Token'] = CCTV.csrfToken;
  const response = await fetch(path, {...options, headers});
  if (!response.ok) throw new Error((await response.json().catch(() => ({}))).error || 'Request failed');
  return response;
};
function updateStats(d) {
  $('connection-dot').className = `status-dot ${d.camera_ok ? 'online' : 'offline'}`;
  $('connection-label').textContent = d.camera_ok ? 'Camera online' : 'Camera offline';
  $('alert-state').textContent = d.alerts_enabled ? 'Enabled' : 'Paused';
  $('schedule-state').textContent = d.schedule_active ? `Schedule active · ${d.schedule_label}` : `Outside schedule · ${d.schedule_label}`;
  $('motion-state').textContent = d.motion_active ? 'Motion detected' : 'Quiet';
  $('score-state').textContent = `Motion score ${d.avg_motion_score}%`;
  $('event-count').textContent = d.total_events;
  $('camera-state').textContent = d.camera_ok ? 'Online' : 'Offline';
  $('frame-count').textContent = `${d.frames_processed.toLocaleString()} frames processed`;
}
async function pollStats(){ try { updateStats(await (await api('/stats')).json()); } catch { $('connection-dot').className='status-dot offline'; $('connection-label').textContent='Connection lost'; } }
function formatTime(ts){ return new Intl.DateTimeFormat(undefined,{dateStyle:'medium',timeStyle:'short'}).format(new Date(ts)); }
async function loadEvents(){
  const root = $('events');
  try { const events = await (await api('/events?limit=24')).json(); root.innerHTML = '';
    if (!events.length) { root.innerHTML = '<p class="empty-state">No recorded events yet.</p>'; return; }
    events.forEach(event => { const card = document.createElement('button'); card.className='event-card';
      card.innerHTML = event.thumbnail_url ? `<img loading="lazy" src="${event.thumbnail_url}" alt="Event at ${formatTime(event.ts)}">` : '<span class="event-placeholder">No preview</span>';
      const meta=document.createElement('div');meta.className='event-card-info';meta.innerHTML=`<strong>${formatTime(event.ts)}</strong><small>Motion score ${Number(event.score).toFixed(2)}</small>`;card.append(meta);
      card.addEventListener('click', () => openPlayer(event)); root.append(card); });
  } catch { root.innerHTML='<p class="empty-state">Could not load recordings. Try refreshing.</p>'; }
}
function openPlayer(event){ const dialog=$('player-dialog'), video=$('event-player'); video.src=event.video_url; $('player-caption').textContent=formatTime(event.ts); dialog.showModal(); video.play().catch(()=>{}); }
$('player-dialog').querySelector('.close-button').onclick=()=>{const d=$('player-dialog');$('event-player').pause();$('event-player').removeAttribute('src');d.close();};
$('refresh-events').onclick=loadEvents;
if (CCTV.canOperate) {
  const toggle = async (enabled) => { try { await api('/alerts',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({enabled})}); toast(enabled ? 'Alerts enabled' : 'Alerts paused'); pollStats(); } catch(e) { toast(e.message); } };
  $('stop-alerts').onclick=()=>toggle(false); $('start-alerts').onclick=()=>toggle(true);
  $('reset-background').onclick=async()=>{try{await api('/reset_background',{method:'POST'});toast('Detection background reset');}catch(e){toast(e.message);}};
}
pollStats(); loadEvents(); setInterval(pollStats, 3000); setInterval(loadEvents, 60000);
