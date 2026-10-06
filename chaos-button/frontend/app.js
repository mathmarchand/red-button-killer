const resourceListEl = document.getElementById('resource-list');
const resultEl = document.getElementById('kill-result');
const formTypeEl = document.getElementById('resource-type');

function toggleFields() {
  const type = formTypeEl.value;
  document.getElementById('maas-fields').style.display = type === 'maas' ? 'block' : 'none';
  document.getElementById('k8s-fields').style.display = type === 'k8s_pod' ? 'block' : 'none';
  document.getElementById('k8s-deployment-fields').style.display = type === 'k8s_deployment' ? 'block' : 'none';
}

async function loadResources() {
  const res = await fetch('/api/resources');
  const resources = await res.json();
  resourceListEl.innerHTML = '';
  resources.forEach((r) => {
    const li = document.createElement('li');
    li.innerHTML = `<strong>${r.name}</strong> <span class="tag">${r.type}</span> ` +
      `<button class="delete-btn" data-name="${r.name}">Delete</button>`;
    resourceListEl.appendChild(li);
  });
  document.querySelectorAll('.delete-btn').forEach((btn) => {
    btn.addEventListener('click', async (e) => {
      const name = e.target.getAttribute('data-name');
      await fetch(`/api/resources/${encodeURIComponent(name)}`, { method: 'DELETE' });
      loadResources();
    });
  });
}

document.getElementById('add-form').addEventListener('submit', async (e) => {
  e.preventDefault();
  const type = formTypeEl.value;
  const payload = { type, name: document.getElementById('res-name').value };

  if (type === 'maas') {
    payload.url = document.getElementById('maas-url').value;
    payload.oauth_key = document.getElementById('maas-oauth').value;
    payload.system_id = document.getElementById('maas-systemid').value;
  } else if (type === 'k8s_pod') {
    payload.kubeconfig = document.getElementById('k8s-kubeconfig').value;
    payload.namespace = document.getElementById('k8s-namespace').value;
    payload.pod_name = document.getElementById('k8s-podname').value;
  } else if (type === 'k8s_deployment') {
    payload.kubeconfig = document.getElementById('k8sdep-kubeconfig').value;
    payload.namespace = document.getElementById('k8sdep-namespace').value;
    payload.deployment_name = document.getElementById('k8sdep-deploymentname').value;
  }

  const res = await fetch('/api/resources', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });

  if (!res.ok) {
    const err = await res.json();
    alert('Error: ' + JSON.stringify(err.detail || res.statusText));
    return;
  }

  e.target.reset();
  toggleFields();
  loadResources();
});

document.getElementById('big-red-button').addEventListener('click', async () => {
  resultEl.textContent = 'Killing something at random...';
  resultEl.className = '';
  try {
    const res = await fetch('/api/kill', { method: 'POST' });
    const data = await res.json();
    if (!res.ok) {
      resultEl.textContent = 'Error: ' + (data.detail || res.statusText);
      resultEl.className = 'error';
      return;
    }
    resultEl.textContent = `☠️ Killed: ${data.killed} (${data.type}) — ${data.message}`;
    resultEl.className = 'success';
    loadResources();
  } catch (err) {
    resultEl.textContent = 'Request failed: ' + err;
    resultEl.className = 'error';
  }
});

formTypeEl.addEventListener('change', toggleFields);
toggleFields();
loadResources();
