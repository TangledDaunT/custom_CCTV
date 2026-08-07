const $ = (id) => document.getElementById(id);
const toast = (message) => { const el = $('toast'); el.textContent = message; el.classList.add('show'); setTimeout(() => el.classList.remove('show'), 3200); };
const api = async (path, options = {}) => {
  const headers = {...(options.headers || {})};
  if (options.method && options.method !== 'GET') headers['X-CSRF-Token'] = CCTV.csrfToken;
  const response = await fetch(path, {...options, headers});
  if (!response.ok) throw new Error((await response.json().catch(() => ({}))).error || 'Request failed');
  return response;
};
function startLiveStream() {
  const video = $('feed');
  if (video.canPlayType('application/vnd.apple.mpegurl')) {
    video.src = CCTV.hlsUrl;
    video.play().catch(() => {});
    return;
  }
  if (window.Hls && Hls.isSupported()) {
    const hls = new Hls({lowLatencyMode: true, liveSyncDurationCount: 3});
    hls.loadSource(CCTV.hlsUrl);
    hls.attachMedia(video);
    hls.on(Hls.Events.MANIFEST_PARSED, () => video.play().catch(() => {}));
    hls.on(Hls.Events.ERROR, (_event, data) => {
      if (data.fatal) startMjpegFallback(video);
    });
    return;
  }
  startMjpegFallback(video);
}
function startMjpegFallback(video) {
  // Older browsers remain usable if they cannot play HLS.
  const image = document.createElement('img');
  image.id = 'feed'; image.src = CCTV.mjpegUrl; image.alt = 'Live CCTV camera feed';
  video.replaceWith(image);
}
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
  // gather filters
  const params = new URLSearchParams();
  params.set('limit', 24);
  const label = $('filter-label') ? $('filter-label').value : '';
  const flagged = $('filter-flagged') ? $('filter-flagged').value : '';
  const camera = $('filter-camera') ? $('filter-camera').value : '';
  if (label) params.set('label', label);
  if (flagged !== '') params.set('flagged', flagged);
  if (camera) params.set('camera', camera);
  try { const events = await (await api('/events?'+params.toString())).json(); root.innerHTML = '';
    if (!events.length) { root.innerHTML = '<p class="empty-state">No recorded events yet.</p>'; return; }
    events.forEach(event => { const card = document.createElement('div'); card.className='event-card';
      card.tabIndex = 0;
      const thumb = event.thumbnail_url ? `<img loading="lazy" src="${event.thumbnail_url}" alt="Event at ${formatTime(event.ts)}">` : '<span class="event-placeholder">No preview</span>';
      const metaHtml = `<div class="event-card-info"><strong>${formatTime(event.ts)}</strong><small>Motion score ${Number(event.score).toFixed(2)}</small></div>`;
      // action bar
      const actions = document.createElement('div'); actions.style.display='flex'; actions.style.justifyContent='space-between'; actions.style.padding='8px';
      const playBtn = document.createElement('button'); playBtn.className='text-button'; playBtn.textContent='Play'; playBtn.onclick = (e)=> { e.stopPropagation(); openPlayer(event); };
      const flagBtn = document.createElement('button'); flagBtn.className='text-button'; flagBtn.textContent = event.flagged ? 'Unflag' : 'Flag'; flagBtn.onclick = async (e)=>{ e.stopPropagation(); try{ const res = await api(`/events/${event.id}/flag`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({flagged: !event.flagged})}); const j = await res.json(); event.flagged = j.flagged; flagBtn.textContent = event.flagged ? 'Unflag' : 'Flag'; toast('Flag updated'); }catch(err){ toast(err.message); }};
      const shareBtn = document.createElement('button'); shareBtn.className='text-button'; shareBtn.textContent='Share'; shareBtn.onclick = async (e)=>{ e.stopPropagation(); try{ const res = await api(`/events/${event.id}/share`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({ttl:3600})}); const j = await res.json(); navigator.clipboard && navigator.clipboard.writeText(j.share_url).catch(()=>{}); toast('Share URL copied'); }catch(err){ toast(err.message); }};
      const downloadClip = document.createElement('a'); downloadClip.className='text-button'; downloadClip.textContent='Download clip'; downloadClip.href=event.video_download_url; downloadClip.download=''; downloadClip.onclick=(e)=>e.stopPropagation();
      actions.append(playBtn); actions.append(flagBtn); actions.append(shareBtn); actions.append(downloadClip);
      if (event.thumbnail_download_url) { const downloadPreview=document.createElement('a'); downloadPreview.className='text-button'; downloadPreview.textContent='Preview'; downloadPreview.href=event.thumbnail_download_url; downloadPreview.download=''; downloadPreview.onclick=(e)=>e.stopPropagation(); actions.append(downloadPreview); }
      card.innerHTML = thumb + metaHtml; card.appendChild(actions);
      card.addEventListener('click', () => openPlayer(event)); root.append(card); });
  } catch { root.innerHTML='<p class="empty-state">Could not load recordings. Try refreshing.</p>'; }
}
function openPlayer(event){ const dialog=$('player-dialog'), video=$('event-player'); video.src=event.video_url; $('player-caption').textContent=formatTime(event.ts); dialog.showModal(); video.play().catch(()=>{}); }
$('player-dialog').querySelector('.close-button').onclick=()=>{const d=$('player-dialog');$('event-player').pause();$('event-player').removeAttribute('src');d.close();};
$('refresh-events').onclick=loadEvents;
// filters
const applyFilters = ()=> loadEvents();
if ($('apply-filters')) $('apply-filters').onclick = applyFilters;
// snapshot
if ($('snapshot')) $('snapshot').onclick = async ()=>{ try{ const res = await api('/snapshot',{method:'POST'}); const j = await res.json(); const link=document.createElement('a'); link.href=j.download_url; link.download=''; link.click(); toast('Snapshot saved and downloaded'); }catch(e){ toast(e.message); } };
// sensitivity slider
if ($('sensitivity')){
  const s = $('sensitivity');
  s.onchange = async ()=>{ try{ await api('/settings/sensitivity',{method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({min_area: parseInt(s.value)})}); toast('Sensitivity updated'); }catch(e){ toast(e.message); } };
}
// fullscreen
if ($('fullscreen')) $('fullscreen').onclick = ()=>{ const el = document.querySelector('.video-frame'); if (document.fullscreenElement) document.exitFullscreen(); else el.requestFullscreen && el.requestFullscreen(); };
// onboarding
if ($('onboard')){
  const shown = localStorage.getItem('cctv_onboard_shown'); if (!shown){ $('onboard').showModal(); }
  $('dismiss-onboard').onclick = ()=>{ $('onboard').close(); localStorage.setItem('cctv_onboard_shown','1'); };
}

if (CCTV.canOperate) {
  const toggle = async (enabled) => { try { await api('/alerts',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({enabled})}); toast(enabled ? 'Alerts enabled' : 'Alerts paused'); pollStats(); } catch(e) { toast(e.message); } };
  $('stop-alerts').onclick=()=>toggle(false); $('start-alerts').onclick=()=>toggle(true);
  $('reset-background').onclick=async()=>{try{await api('/reset_background',{method:'POST'});toast('Detection background reset');}catch(e){toast(e.message);}};
}
startLiveStream(); pollStats(); loadEvents(); setInterval(pollStats, 3000); setInterval(loadEvents, 60000);
