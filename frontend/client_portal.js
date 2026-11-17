import { SessionClient } from './shared/ui.js';
document.querySelectorAll('.portal-date').forEach(element => {
  const date = new Date(element.dateTime);
  if (!Number.isNaN(date.getTime())) element.textContent = new Intl.DateTimeFormat(undefined, element.dataset.kind === 'date' ? { month:'short', day:'numeric', year:'numeric' } : { hour:'numeric', minute:'2-digit' }).format(date);
});
// Search, filters and pagination are server-side, so every shared agreement is searchable.
if (location.hash === '#documents') { const url=new URL(location.href); url.hash=''; url.searchParams.set('filter','signed'); location.replace(url); }
window.addEventListener('pageshow', event => { if(event.persisted) location.reload(); });
document.querySelector('#portal-refresh').addEventListener('click', () => location.reload());
const menu=document.querySelector('#portal-menu');
const closeMenu=()=>{ document.body.dataset.menuOpen='false'; menu.setAttribute('aria-expanded','false'); menu.setAttribute('aria-label','Open navigation'); };
menu.addEventListener('click', () => { const open=menu.getAttribute('aria-expanded') !== 'true'; menu.setAttribute('aria-expanded',String(open)); menu.setAttribute('aria-label',open?'Close navigation':'Open navigation'); document.body.dataset.menuOpen=String(open); });
document.addEventListener('keydown', event => { if(event.key==='Escape') closeMenu(); });
document.querySelector('#portal-logout').addEventListener('click', async event => {
  const button=event.currentTarget; button.disabled=true;
  try { await new SessionClient().request(document.body.dataset.logoutUrl,{}); location.replace(document.body.dataset.loginUrl); }
  catch(error) { const message=document.querySelector('#portal-error'); message.textContent=error.message; message.hidden=false; button.disabled=false; }
});
