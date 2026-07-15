(() => {
  'use strict';
  const grid = document.getElementById('vnCalendarGrid');
  if (!grid) return;
  const title = document.getElementById('vnCalendarTitle');
  const selectedTitle = document.getElementById('vnCalendarSelectedTitle');
  const dayList = document.getElementById('vnCalendarDayList');
  const form = document.getElementById('vnCalendarEventForm');
  const key = 'vortnotes.calendarLite.events.v1';
  let cursor = new Date();
  cursor.setDate(1);
  let selected = toKey(new Date());
  let editingId = '';
  let events = readEvents();

  function toKey(date){
    const y = date.getFullYear();
    const m = String(date.getMonth() + 1).padStart(2, '0');
    const d = String(date.getDate()).padStart(2, '0');
    return `${y}-${m}-${d}`;
  }
  function readEvents(){
    try { return JSON.parse(localStorage.getItem(key) || '[]').filter(e => e && e.date && e.title); }
    catch (_) { return []; }
  }
  function saveEvents(){ localStorage.setItem(key, JSON.stringify(events)); }
  function eventsFor(dateKey){
    return events.filter(e => e.date === dateKey).sort((a, b) => String(a.time || '').localeCompare(String(b.time || '')));
  }
  function monthLabel(date){
    return date.toLocaleDateString(undefined, {month: 'long', year: 'numeric'});
  }
  function escapeText(text){
    return String(text || '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  }
  function resetForm(){
    editingId = '';
    document.getElementById('vnCalendarEventId').value = '';
    document.getElementById('vnCalendarEventTitle').value = '';
    document.getElementById('vnCalendarEventTime').value = '';
    document.getElementById('vnCalendarEventDetails').value = '';
    document.getElementById('vnCalendarDelete').hidden = true;
  }
  function editEvent(id){
    const event = events.find(e => e.id === id);
    if (!event) return;
    editingId = event.id;
    document.getElementById('vnCalendarEventId').value = event.id;
    document.getElementById('vnCalendarEventTitle').value = event.title || '';
    document.getElementById('vnCalendarEventTime').value = event.time || '';
    document.getElementById('vnCalendarEventDetails').value = event.details || '';
    document.getElementById('vnCalendarDelete').hidden = false;
  }
  function renderDay(){
    const date = new Date(`${selected}T12:00:00`);
    selectedTitle.textContent = date.toLocaleDateString(undefined, {weekday: 'long', month: 'short', day: 'numeric'});
    const dayEvents = eventsFor(selected);
    dayList.innerHTML = '';
    if (!dayEvents.length){
      dayList.innerHTML = '<div class="muted vn-calendar-empty">No events for this day.</div>';
      return;
    }
    dayEvents.forEach(event => {
      const row = document.createElement('button');
      row.type = 'button';
      row.className = 'vn-calendar-event-row';
      row.innerHTML = `<strong>${escapeText(event.time || 'Anytime')}</strong><span>${escapeText(event.title)}</span>`;
      row.addEventListener('click', () => editEvent(event.id));
      dayList.appendChild(row);
    });
  }
  function render(){
    title.textContent = monthLabel(cursor);
    grid.innerHTML = '';
    const first = new Date(cursor);
    const start = new Date(first);
    start.setDate(first.getDate() - first.getDay());
    const todayKey = toKey(new Date());
    for (let i = 0; i < 42; i++){
      const date = new Date(start);
      date.setDate(start.getDate() + i);
      const dateKey = toKey(date);
      const dayEvents = eventsFor(dateKey);
      const cell = document.createElement('button');
      cell.type = 'button';
      cell.className = 'vn-calendar-cell';
      if (date.getMonth() !== cursor.getMonth()) cell.classList.add('is-outside');
      if (dateKey === todayKey) cell.classList.add('is-today');
      if (dateKey === selected) cell.classList.add('is-selected');
      cell.innerHTML = `
        <span class="vn-calendar-date">${date.getDate()}</span>
        <span class="vn-calendar-dots">${dayEvents.slice(0, 3).map(() => '<i></i>').join('')}</span>
        ${dayEvents.length > 3 ? `<span class="vn-calendar-more">+${dayEvents.length - 3}</span>` : ''}
      `;
      cell.addEventListener('click', () => { selected = dateKey; resetForm(); render(); });
      grid.appendChild(cell);
    }
    renderDay();
  }

  form?.addEventListener('submit', e => {
    e.preventDefault();
    const payload = {
      id: editingId || `${Date.now()}-${Math.random().toString(16).slice(2)}`,
      date: selected,
      title: document.getElementById('vnCalendarEventTitle').value.trim(),
      time: document.getElementById('vnCalendarEventTime').value,
      details: document.getElementById('vnCalendarEventDetails').value.trim()
    };
    if (!payload.title) return;
    const idx = events.findIndex(e => e.id === payload.id);
    if (idx >= 0) events[idx] = payload;
    else events.push(payload);
    saveEvents();
    resetForm();
    render();
  });
  document.getElementById('vnCalendarDelete')?.addEventListener('click', () => {
    if (!editingId || !confirm('Delete this event?')) return;
    events = events.filter(e => e.id !== editingId);
    saveEvents();
    resetForm();
    render();
  });
  document.getElementById('vnCalendarPrev')?.addEventListener('click', () => { cursor.setMonth(cursor.getMonth() - 1); render(); });
  document.getElementById('vnCalendarNext')?.addEventListener('click', () => { cursor.setMonth(cursor.getMonth() + 1); render(); });
  document.getElementById('vnCalendarToday')?.addEventListener('click', () => {
    const today = new Date();
    cursor = new Date(today.getFullYear(), today.getMonth(), 1);
    selected = toKey(today);
    resetForm();
    render();
  });

  render();
})();
