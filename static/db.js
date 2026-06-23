document.addEventListener('DOMContentLoaded', () => {
  const renameForm = document.getElementById('renameForm');
  const deleteForm = document.getElementById('deleteForm');
  const renameOld = document.getElementById('renameOld');
  const renameNew = document.getElementById('renameNew');
  const deleteName = document.getElementById('deleteName');

  function renameDb(oldName){
    const newName = window.prompt(
      "Rename DB:\n\nCurrent: " + oldName + "\n\nEnter new name (e.g. personal.db):",
      oldName
    );
    if (newName === null) return;
    const trimmed = (newName || "").trim();
    if (!trimmed){
      window.alert("Please enter a valid new name.");
      return;
    }
    renameOld.value = oldName;
    renameNew.value = trimmed;
    renameForm.submit();
  }

  function deleteDb(name){
    if (!window.confirm("Delete DB '" + name + "'? This cannot be undone.")) return;
    deleteName.value = name;
    deleteForm.submit();
  }

  document.querySelectorAll('[data-action="rename-db"]').forEach(btn => {
    btn.addEventListener('click', (e) => {
      e.preventDefault();
      renameDb(btn.getAttribute('data-name'));
    });
  });

    const pwdSetForm = document.getElementById('pwdSetForm');
  const pwdClearForm = document.getElementById('pwdClearForm');
  const pwdName = document.getElementById('pwdName');
  const pwdValue = document.getElementById('pwdValue');
  const pwdClearName = document.getElementById('pwdClearName');

  function setPassword(name){
    const p = window.prompt("Set/Change password for DB '" + name + "'\n\nEnter new password:", "");
    if (p === null) return;
    const trimmed = (p || "").trim();
    if (!trimmed){
      window.alert("Password cannot be empty.");
      return;
    }
    pwdName.value = name;
    pwdValue.value = trimmed;
    pwdSetForm.submit();
  }

  function clearPassword(name){
    if (!window.confirm("Clear password for DB '" + name + "'?")) return;
    pwdClearName.value = name;
    pwdClearForm.submit();
  }

  document.querySelectorAll('[data-action="pwd-db"]').forEach(btn => {
    btn.addEventListener('click', (e) => {
      e.preventDefault();
      setPassword(btn.getAttribute('data-name'));
    });
  });

  document.querySelectorAll('[data-action="pwd-clear-db"]').forEach(btn => {
    btn.addEventListener('click', (e) => {
      e.preventDefault();
      clearPassword(btn.getAttribute('data-name'));
    });
  });

document.querySelectorAll('[data-action="delete-db"]').forEach(btn => {
    btn.addEventListener('click', (e) => {
      e.preventDefault();
      deleteDb(btn.getAttribute('data-name'));
    });
  });
});
