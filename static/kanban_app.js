(() => {
  'use strict';
  const cfg = window.VORTNOTES_KANBAN || {};
  const board = document.getElementById('vnKanbanBoard');
  const status = document.getElementById('vnKanbanStatus');
  const modal = document.getElementById('vnKanbanCardModal');
  const form = document.getElementById('vnKanbanCardForm');
  if (!board) return;

  let columns = Array.isArray(cfg.columns) ? cfg.columns.slice() : [];
  let cards = Array.isArray(cfg.cards) ? cfg.cards.slice() : [];
  const notes = new Map((Array.isArray(cfg.notes) ? cfg.notes : []).map(n => [Number(n.id), n]));
  const canEdit = !!cfg.canEdit;
  let draggingCardId = null;

  const $ = id => document.getElementById(id);
  const setStatus = text => { if (status) status.textContent = text || ''; };
  const csrfToken = () => document.querySelector('meta[name="csrf-token"]')?.getAttribute('content') || '';
  const jsonPost = async (url, data) => {
    const res = await fetch(url, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Accept': 'application/json',
        'X-Requested-With': 'XMLHttpRequest',
        'X-CSRFToken': csrfToken()
      },
      body: JSON.stringify(data || {})
    });
    const payload = await res.json().catch(() => ({}));
    if (!res.ok || payload.ok === false) throw new Error(payload.error || 'save_failed');
    return payload;
  };
  const escapeText = text => String(text || '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const noteTitle = id => {
    const note = notes.get(Number(id));
    return note ? (note.title || `Note #${note.id}`) : '';
  };
  const noteHref = id => `${cfg.noteUrlBase || '/notes/'}${Number(id)}`;

  function cardsFor(columnId){
    return cards
      .filter(card => Number(card.column_id) === Number(columnId))
      .sort((a, b) => (Number(a.display_order || 0) - Number(b.display_order || 0)) || (Number(a.id) - Number(b.id)));
  }

  function render(){
    board.innerHTML = '';
    columns.sort((a, b) => (Number(a.display_order || 0) - Number(b.display_order || 0)) || (Number(a.id) - Number(b.id)));
    columns.forEach(column => {
      const col = document.createElement('section');
      col.className = 'vn-kanban-column';
      col.dataset.columnId = column.id;
      const columnCards = cardsFor(column.id);
      col.innerHTML = `
        <div class="vn-kanban-column-head">
          <input class="vn-kanban-column-title" value="${escapeText(column.title)}" ${canEdit ? '' : 'disabled'}>
          <span class="vn-kanban-count">${columnCards.length}</span>
          ${canEdit ? '<button class="vn-icon-btn" type="button" data-column-delete title="Delete column">×</button>' : ''}
        </div>
        <div class="vn-kanban-cards" data-card-list></div>
        ${canEdit ? '<button class="btn vn-kanban-add-card" type="button" data-add-card>Add card</button>' : ''}
      `;
      const list = col.querySelector('[data-card-list]');
      columnCards.forEach(card => list.appendChild(renderCard(card)));
      if (canEdit){
        list.addEventListener('dragover', e => { e.preventDefault(); list.classList.add('is-drop-target'); });
        list.addEventListener('dragleave', () => list.classList.remove('is-drop-target'));
        list.addEventListener('drop', e => {
          e.preventDefault();
          list.classList.remove('is-drop-target');
          if (!draggingCardId) return;
          const targetCard = e.target.closest('.vn-kanban-card');
          const index = targetCard
            ? Array.from(list.querySelectorAll('.vn-kanban-card')).indexOf(targetCard)
            : list.querySelectorAll('.vn-kanban-card').length;
          moveCard(draggingCardId, column.id, index);
        });
        col.querySelector('[data-add-card]')?.addEventListener('click', () => openCardModal({column_id: column.id}));
        col.querySelector('[data-column-delete]')?.addEventListener('click', () => deleteColumn(column.id, column.title));
        const titleInput = col.querySelector('.vn-kanban-column-title');
        titleInput.addEventListener('change', () => saveColumn(column.id, titleInput.value));
      }
      board.appendChild(col);
    });
  }

  function renderCard(card){
    const el = document.createElement('article');
    el.className = 'vn-kanban-card';
    el.dataset.cardId = card.id;
    if (canEdit) el.draggable = true;
    const linked = card.note_id ? `<a href="${noteHref(card.note_id)}" onclick="event.stopPropagation()">📝 ${escapeText(noteTitle(card.note_id))}</a>` : '';
    el.innerHTML = `
      <h4>${escapeText(card.title)}</h4>
      ${card.body ? `<p>${escapeText(card.body)}</p>` : ''}
      ${linked ? `<div class="vn-kanban-note-link">${linked}</div>` : ''}
    `;
    el.addEventListener('click', () => openCardModal(card));
    if (canEdit){
      el.addEventListener('dragstart', e => {
        draggingCardId = Number(card.id);
        e.dataTransfer.effectAllowed = 'move';
        el.classList.add('is-dragging');
      });
      el.addEventListener('dragend', () => {
        draggingCardId = null;
        el.classList.remove('is-dragging');
      });
    }
    return el;
  }

  function openCardModal(card){
    if (!canEdit && !card.id) return;
    $('vnKanbanCardId').value = card.id || '';
    $('vnKanbanCardColumnId').value = card.column_id || '';
    $('vnKanbanCardTitle').value = card.title || '';
    $('vnKanbanCardBody').value = card.body || '';
    $('vnKanbanCardNote').value = card.note_id || '';
    $('vnKanbanDeleteCard').hidden = !card.id || !canEdit;
    modal.classList.remove('hidden');
    $('vnKanbanCardTitle').focus();
  }
  function closeCardModal(){ modal.classList.add('hidden'); }

  async function saveColumn(id, title){
    try{
      const data = await jsonPost(cfg.urls.columnSave, {id, title});
      const idx = columns.findIndex(c => Number(c.id) === Number(data.column.id));
      if (idx >= 0) columns[idx].title = data.column.title;
      else columns.push(data.column);
      setStatus('Column saved.');
      render();
    }catch(err){ setStatus(`Column could not be saved: ${err.message}`); render(); }
  }
  async function deleteColumn(id, title){
    if (!confirm(`Delete "${title}" and its cards?`)) return;
    try{
      await jsonPost(cfg.urls.columnDelete, {id});
      columns = columns.filter(c => Number(c.id) !== Number(id));
      cards = cards.filter(c => Number(c.column_id) !== Number(id));
      setStatus('Column deleted.');
      render();
    }catch(err){ setStatus(err.message === 'last_column' ? 'Keep at least one column.' : `Column could not be deleted: ${err.message}`); }
  }
  async function moveCard(id, columnId, index){
    const card = cards.find(c => Number(c.id) === Number(id));
    if (!card) return;
    card.column_id = Number(columnId);
    try{
      await jsonPost(cfg.urls.cardMove, {id, column_id: columnId, index});
      setStatus('Card moved.');
    }catch(err){ setStatus(`Card move failed: ${err.message}`); }
    render();
  }

  form?.addEventListener('submit', async e => {
    e.preventDefault();
    const payload = {
      id: $('vnKanbanCardId').value || null,
      column_id: $('vnKanbanCardColumnId').value,
      title: $('vnKanbanCardTitle').value,
      body: $('vnKanbanCardBody').value,
      note_id: $('vnKanbanCardNote').value || null
    };
    try{
      const data = await jsonPost(cfg.urls.cardSave, payload);
      const idx = cards.findIndex(c => Number(c.id) === Number(data.card.id));
      if (idx >= 0) cards[idx] = data.card;
      else cards.push(data.card);
      closeCardModal();
      setStatus('Card saved.');
      render();
    }catch(err){ setStatus(`Card could not be saved: ${err.message}`); }
  });
  $('vnKanbanDeleteCard')?.addEventListener('click', async () => {
    const id = Number($('vnKanbanCardId').value || 0);
    if (!id || !confirm('Delete this card?')) return;
    try{
      await jsonPost(cfg.urls.cardDelete, {id});
      cards = cards.filter(c => Number(c.id) !== id);
      closeCardModal();
      setStatus('Card deleted.');
      render();
    }catch(err){ setStatus(`Card could not be deleted: ${err.message}`); }
  });
  $('vnKanbanCardClose')?.addEventListener('click', closeCardModal);
  $('vnKanbanCardCancel')?.addEventListener('click', closeCardModal);
  modal?.addEventListener('click', closeCardModal);
  $('vnKanbanAddColumn')?.addEventListener('click', async () => {
    const title = prompt('Column name');
    if (title && title.trim()) await saveColumn(null, title.trim());
  });

  render();
})();
